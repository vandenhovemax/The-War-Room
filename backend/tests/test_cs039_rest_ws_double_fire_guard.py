"""CS-039: on_order_update and on_execution must de-dup REST+WS double-fire.

Bybit sends a WS private-stream fill event AND the REST order_history
poll returns the same fill — both arrive within milliseconds of each
other on market orders. Without idempotency, _on_entry_filled fires
twice (double-inserts a Position, double-fires on_position_opened)
and on_execution accumulates the same commission twice on the Order
and Position rows.

The previous DB-only guard (``prev_status != "filled"``) is TOCTOU-
unsafe: both threads read the stale status before either commits.

This test locks both invariants:
  * ``_on_entry_filled`` runs exactly once per order_link_id even when
    two concurrent on_order_update calls race on the same data.
  * ``on_execution`` for the same (order_link_id, execId) tuple only
    accumulates commission once.
"""
from __future__ import annotations

import inspect
import threading
from unittest.mock import MagicMock

from backend.exchange.order_manager import OrderManager


def _fresh_manager() -> OrderManager:
  """Build an OrderManager with stubs — enough for idempotency tests."""
  client = MagicMock()
  db_factory = MagicMock(return_value=MagicMock())
  return OrderManager(client, db_factory)


def test_fill_processed_set_exists():
  """CS-039: the idempotency set must exist on every instance."""
  mgr = _fresh_manager()
  assert hasattr(mgr, "_fill_processed_links"), (
    "CS-039 REGRESSION: OrderManager has no _fill_processed_links set. "
    "REST+WS double-fire protection has been removed — entry fills "
    "will double-insert positions under race conditions."
  )
  assert hasattr(mgr, "_fill_processed_lock"), (
    "CS-039 REGRESSION: OrderManager has no _fill_processed_lock. "
    "Idempotency check is no longer thread-safe."
  )


def test_on_order_update_source_uses_fill_processed_links():
  """Source-level lock: the fill-dispatch branch must consult
  _fill_processed_links under the lock, not just prev_status.
  """
  src = inspect.getsource(OrderManager.on_order_update)
  assert "_fill_processed_links" in src, (
    "CS-039 REGRESSION: on_order_update no longer checks "
    "_fill_processed_links. The old prev_status-only guard is TOCTOU-"
    "unsafe — two threads can both see prev_status='open' and both "
    "call _on_entry_filled → duplicate Position."
  )
  assert "_fill_processed_lock" in src, (
    "CS-039 REGRESSION: on_order_update no longer uses "
    "_fill_processed_lock. The idempotency claim must be atomic."
  )


def test_on_execution_source_dedups_per_execution():
  """on_execution must dedup on (orderLinkId, execId)."""
  src = inspect.getsource(OrderManager.on_execution)
  assert "_execution_processed_keys" in src, (
    "CS-039 REGRESSION: on_execution no longer dedups by execId. "
    "The same WS+REST fill will accumulate commission twice — order "
    "and position fees double-count on every market entry."
  )


def test_fill_idempotency_blocks_second_dispatch():
  """Behavioural: calling on_order_update twice with the same Filled
  payload must only dispatch _on_entry_filled once.
  """
  mgr = _fresh_manager()

  # Stub the entry-fill hook to count calls
  mgr._on_entry_filled = MagicMock()
  mgr._on_exit_filled = MagicMock()
  mgr._cancel_ttl_timer = MagicMock()
  mgr._log_event = MagicMock()

  # Build a fake db_order that reports role=entry, status transitions
  # from 'open' → 'filled' on first call. On the SECOND call we
  # deliberately leave prev_status='open' (simulating the TOCTOU race:
  # the REST thread read before the WS thread committed). Only the
  # lock-based set can prevent the second _on_entry_filled.
  fake_order = MagicMock()
  fake_order.status = "open"
  fake_order.role = "entry"

  fake_db = MagicMock()
  fake_db.query.return_value.filter.return_value.first.return_value = fake_order
  mgr._db_factory = MagicMock(return_value=fake_db)

  payload = {
    "orderLinkId": "test-link-123",
    "orderStatus": "Filled",
    "symbol": "BTCUSDT",
    "cumExecQty": "1.0",
    "avgPrice": "50000",
  }

  # First call — should dispatch
  mgr.on_order_update(payload)
  # Simulate the race: second caller still sees prev_status='open'
  fake_order.status = "open"
  # Second call — must be blocked by the idempotency set
  mgr.on_order_update(payload)

  assert mgr._on_entry_filled.call_count == 1, (
    f"CS-039 REGRESSION: _on_entry_filled fired "
    f"{mgr._on_entry_filled.call_count} times for the same "
    "orderLinkId. Idempotency guard is broken — live market fills "
    "will open duplicate positions."
  )


def test_execution_idempotency_blocks_second_dispatch():
  """Behavioural: on_execution with the same (orderLinkId, execId)
  must only update commission once.
  """
  mgr = _fresh_manager()

  fake_order = MagicMock()
  fake_order.commission = 0
  fake_order.filled_qty = 0
  fake_order.role = "entry"
  fake_order.position_id_fk = None
  fake_order.symbol = "BTCUSDT"
  fake_order.portfolio_id = 1

  fake_db = MagicMock()
  fake_db.query.return_value.filter.return_value.first.return_value = fake_order
  mgr._db_factory = MagicMock(return_value=fake_db)
  mgr._log_event = MagicMock()

  payload = {
    "orderLinkId": "test-link-456",
    "execId": "exec-abc-789",
    "execFee": "0.055",
    "execQty": "1.0",
    "execPrice": "50000",
    "cumExecQty": "1.0",
    "avgPrice": "50000",
  }

  mgr.on_execution(payload)
  mgr.on_execution(payload)  # Same (linkId, execId) — must be skipped

  # Commission should have been assigned exactly once (0.055, not 0.11)
  # Access the assigned value from the last mutation
  assert fake_order.commission == 0.055, (
    f"CS-039 REGRESSION: on_execution accumulated commission twice "
    f"for the same execId. Expected 0.055, got {fake_order.commission}."
  )


def test_concurrent_on_order_update_dispatches_once():
  """Thread-safety: fire on_order_update from 8 threads at once with
  identical payload. Only ONE must reach _on_entry_filled.
  """
  mgr = _fresh_manager()
  mgr._on_entry_filled = MagicMock()
  mgr._on_exit_filled = MagicMock()
  mgr._cancel_ttl_timer = MagicMock()
  mgr._log_event = MagicMock()

  fake_order = MagicMock()
  fake_order.status = "open"
  fake_order.role = "entry"

  fake_db = MagicMock()
  fake_db.query.return_value.filter.return_value.first.return_value = fake_order
  mgr._db_factory = MagicMock(return_value=fake_db)

  payload = {
    "orderLinkId": "race-link-xyz",
    "orderStatus": "Filled",
    "symbol": "BTCUSDT",
    "cumExecQty": "1.0",
    "avgPrice": "50000",
  }

  barrier = threading.Barrier(8)

  def worker():
    barrier.wait()
    # Reset prev_status for each thread — simulate the TOCTOU race
    # where every thread saw 'open' before any committed
    fake_order.status = "open"
    mgr.on_order_update(payload)

  threads = [threading.Thread(target=worker) for _ in range(8)]
  for t in threads:
    t.start()
  for t in threads:
    t.join()

  assert mgr._on_entry_filled.call_count == 1, (
    f"CS-039 RACE REGRESSION: _on_entry_filled fired "
    f"{mgr._on_entry_filled.call_count} times under 8-thread contention. "
    "The idempotency claim must be atomic (lock-guarded set)."
  )
