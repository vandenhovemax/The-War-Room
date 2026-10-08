"""Engine coordinator: partial-start teardown contract.

CS-003 regression: if ``start()`` failed mid-way (e.g. after the
WebSocket manager had been started but before ``_running`` was set true),
the orphaned WebSocket and any already-scheduled background tasks stayed
alive. The next ``start()`` then tried to create duplicates.

Fix: the ``except`` branch now calls ``_teardown_partial_start()``,
which:
  - cancels background tasks (if any were created)
  - stops the WebSocket manager (if any was created)
  - clears all component refs so the next start() is a clean slate
  - swallows secondary exceptions so one failed cleanup step cannot
    mask another
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.exchange.engine_coordinator import EngineCoordinator


def _make_coordinator() -> EngineCoordinator:
  """Build a coordinator without running start() — we test teardown in isolation.

  Patches ``get_settings`` so the constructor doesn't pull a real config.
  """
  db_factory = MagicMock()
  with patch("backend.exchange.engine_coordinator.get_settings", return_value=MagicMock()):
    return EngineCoordinator(db_factory=db_factory)


def test_teardown_clears_ws_manager_ref_and_calls_stop():
  coord = _make_coordinator()
  ws = MagicMock()
  coord._ws_manager = ws

  coord._teardown_partial_start()

  ws.stop.assert_called_once()
  assert coord._ws_manager is None


def test_teardown_cancels_pending_background_tasks():
  coord = _make_coordinator()
  t = MagicMock()
  t.done.return_value = False
  coord._reconciliation_task = t
  coord._fill_poll_task = None  # not-yet-started task is fine

  coord._teardown_partial_start()

  t.cancel.assert_called_once()
  assert coord._reconciliation_task is None
  assert coord._fill_poll_task is None


def test_teardown_does_not_cancel_done_tasks():
  coord = _make_coordinator()
  t = MagicMock()
  t.done.return_value = True
  coord._kelly_recompute_task = t

  coord._teardown_partial_start()

  t.cancel.assert_not_called()
  assert coord._kelly_recompute_task is None


def test_teardown_clears_all_component_refs():
  coord = _make_coordinator()
  # Simulate a start() that got partway through
  coord._trading_client = MagicMock()
  coord._position_manager = MagicMock()
  coord._tsl_tracker = MagicMock()
  coord._safety_monitor = MagicMock()
  coord._order_manager = MagicMock()
  coord._paper_simulator = MagicMock()
  coord._candle_aggregator = MagicMock()
  coord._strategy_executor = MagicMock()

  coord._teardown_partial_start()

  assert coord._trading_client is None
  assert coord._position_manager is None
  assert coord._tsl_tracker is None
  assert coord._safety_monitor is None
  assert coord._order_manager is None
  assert coord._paper_simulator is None
  assert coord._candle_aggregator is None
  assert coord._strategy_executor is None


def test_teardown_swallows_ws_stop_exception():
  """A ws.stop() failure must not prevent the rest of teardown from running."""
  coord = _make_coordinator()
  ws = MagicMock()
  ws.stop.side_effect = RuntimeError("boom")
  coord._ws_manager = ws
  coord._order_manager = MagicMock()

  coord._teardown_partial_start()  # must not raise

  assert coord._ws_manager is None
  assert coord._order_manager is None


def test_teardown_swallows_task_cancel_exception():
  coord = _make_coordinator()
  t = MagicMock()
  t.done.return_value = False
  t.cancel.side_effect = RuntimeError("already cancelled")
  coord._wallet_refresh_task = t

  coord._teardown_partial_start()  # must not raise

  assert coord._wallet_refresh_task is None


def test_teardown_no_op_on_fresh_coordinator():
  """Calling teardown on a never-started coordinator is safe."""
  coord = _make_coordinator()
  coord._teardown_partial_start()  # must not raise
  assert coord._ws_manager is None
  assert coord._order_manager is None


def test_teardown_leaves_running_flag_false():
  """Teardown must not set _running=True."""
  coord = _make_coordinator()
  assert coord._running is False
  coord._teardown_partial_start()
  assert coord._running is False


def test_teardown_mixed_initialized_and_uninitialized():
  """Realistic mid-start failure: ws + order_manager set, other refs still None."""
  coord = _make_coordinator()
  coord._ws_manager = MagicMock()
  coord._order_manager = MagicMock()
  # Strategy executor and candle aggregator never got constructed
  assert coord._strategy_executor is None

  coord._teardown_partial_start()

  assert coord._ws_manager is None
  assert coord._order_manager is None
  # No-op on already-None refs — must not raise
  assert coord._strategy_executor is None
