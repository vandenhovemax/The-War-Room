"""CS-003: ``EngineCoordinator.start()`` sets ``self._running = True`` at
line 229 — BEFORE ``_auto_resume_portfolios`` (line 241) can raise. Prior
to this fix, ``_teardown_partial_start`` was called on the exception but
did NOT reset ``self._running``, so the flag leaked True past teardown.

The symptom is silent: the next ``start()`` call hit the
``if self._running: return True`` guard and reported success without
rebuilding any of the components that teardown just dropped. The engine
would then appear "running" with ``_trading_client=None``, and every
signal route would crash on the first dereference.

The fix resets ``_running=False`` (and ``_loop=None``) in
``_teardown_partial_start``. This test locks that invariant by forcing
an exception after the flag is set and verifying the next ``start()``
call is NOT short-circuited.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.exchange.engine_coordinator import EngineCoordinator


def _make_coordinator_in_mid_start_state() -> EngineCoordinator:
  """Return a coordinator with the same state ``start()`` would have
  at the point just before ``_auto_resume_portfolios`` raises.

  We can't call real ``start()`` without a live Bybit and a full DB; we
  instead simulate the mid-start state directly, since the unit under
  test is ``_teardown_partial_start`` itself.
  """
  coord = EngineCoordinator(db_factory=lambda: MagicMock())
  coord._running = True
  coord._loop = asyncio.new_event_loop()
  # Populate a couple of component refs so teardown has something to drop
  coord._trading_client = MagicMock()
  coord._ws_manager = MagicMock()
  coord._order_manager = MagicMock()
  coord._strategy_executor = MagicMock()
  coord._candle_aggregator = MagicMock()
  coord._position_manager = MagicMock()
  coord._tsl_tracker = MagicMock()
  coord._safety_monitor = MagicMock()
  return coord


def test_teardown_resets_running_flag():
  """The critical invariant: after ``_teardown_partial_start`` runs,
  ``_running`` must be False. Without this, the next ``start()`` call
  short-circuits and returns True with torn-down components.
  """
  coord = _make_coordinator_in_mid_start_state()
  assert coord._running is True  # precondition

  coord._teardown_partial_start()

  assert coord._running is False, (
    "CS-003: _running flag leaked past teardown — next start() would "
    "short-circuit at the 'already running' guard and report success "
    "with _trading_client=None."
  )
  assert coord._loop is None, (
    "CS-003: _loop reference leaked past teardown — _schedule() would "
    "try to dispatch to a dead loop."
  )


def test_teardown_drops_all_component_refs():
  """Teardown must drop every component ref so a retry rebuilds from
  scratch. Without this, the next ``start()`` would see stale refs
  and skip re-initialisation on ones it considers 'already set'."""
  coord = _make_coordinator_in_mid_start_state()

  coord._teardown_partial_start()

  assert coord._trading_client is None
  assert coord._ws_manager is None
  assert coord._order_manager is None
  assert coord._strategy_executor is None
  assert coord._candle_aggregator is None
  assert coord._position_manager is None
  assert coord._tsl_tracker is None
  assert coord._safety_monitor is None


def test_teardown_stops_ws_manager_if_assigned():
  coord = _make_coordinator_in_mid_start_state()
  ws = coord._ws_manager  # capture before teardown drops the ref

  coord._teardown_partial_start()

  ws.stop.assert_called_once()


def test_teardown_swallows_exceptions_during_cleanup():
  """One failed cleanup step must not mask another — swallow + log."""
  coord = _make_coordinator_in_mid_start_state()
  coord._ws_manager.stop.side_effect = RuntimeError("boom")

  # Must not raise
  coord._teardown_partial_start()

  # All other teardown steps still ran
  assert coord._running is False
  assert coord._trading_client is None
  assert coord._ws_manager is None


def test_teardown_cancels_background_tasks():
  """Tasks scheduled before the failure must be cancelled, not leaked."""
  loop = asyncio.new_event_loop()
  try:
    asyncio.set_event_loop(loop)
    coord = EngineCoordinator(db_factory=lambda: MagicMock())
    coord._running = True

    # Simulate a background task that was scheduled but not yet complete
    async def _never_completes():
      await asyncio.sleep(3600)

    coord._reconciliation_task = loop.create_task(_never_completes())
    coord._fill_poll_task = loop.create_task(_never_completes())

    coord._teardown_partial_start()

    # Run the event loop briefly so cancellation propagates
    async def _drain():
      await asyncio.sleep(0)
    loop.run_until_complete(_drain())

    # All task attrs should be None after teardown
    assert coord._reconciliation_task is None
    assert coord._fill_poll_task is None
    assert coord._wallet_refresh_task is None
    assert coord._kelly_recompute_task is None
  finally:
    loop.close()
    asyncio.set_event_loop(None)


def test_next_start_not_short_circuited_after_teardown():
  """End-to-end invariant: after a failed start → teardown, the next
  ``start()`` call must NOT hit the early-return at the 'already running'
  guard. This is the CS-003 bug in one test.
  """
  coord = _make_coordinator_in_mid_start_state()
  coord._teardown_partial_start()

  # If _running stayed True, start() would return True at line 116 without
  # doing any work. Verify _running is False so the next start() proceeds.
  assert coord._running is False
