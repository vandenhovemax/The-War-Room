"""CS-043: ``TrailingStopTracker.update_extreme`` must be a strict
monotonic ratchet — never regress the extreme against the position's
favourable direction.

The audit's concern: the bar-close writer in ``engine_coordinator``
calls ``tsl_tracker.update_extreme(pos_id, bar_high_or_low)``. If the
intra-bar ticker stream has already pushed the extreme further than
the bar-close value (ticker sees the wick, the bar-close aggregate
smooths it), a naive assignment would overwrite the better extreme
with a worse one — the trailing stop would relax backward. Every
retail TSL invariant (and the backtester) forbids that.

The fix that must remain in ``tsl_tracker.update_extreme``:

  * Buy  (long)   → ``extreme = max(extreme, new)``
  * Sell (short)  → ``extreme = min(extreme, new)``

This test locks the invariant with both a source-inspection probe
and an end-to-end behavioural test that flips the inputs in each
direction.
"""
from __future__ import annotations

import inspect


def test_update_extreme_source_uses_monotonic_ratchet():
  from backend.exchange.tsl_tracker import TrailingStopTracker
  src = inspect.getsource(TrailingStopTracker.update_extreme)
  # Long: must use max(); short: must use min(). If either direction
  # regresses to a bare assignment, the audit bug returns.
  assert "max(pos.extreme_price" in src, (
    "CS-043 REGRESSION: long-side update_extreme is no longer a "
    "monotonic ratchet (``max(extreme, new)``). A bar-close write "
    "could now OVERWRITE a better ticker-derived high — the trailing "
    "stop would relax backward and every long position is affected."
  )
  assert "min(pos.extreme_price" in src, (
    "CS-043 REGRESSION: short-side update_extreme is no longer a "
    "monotonic ratchet (``min(extreme, new)``). Short trailing stops "
    "would relax upward — exactly the bug the audit flagged."
  )


def test_update_extreme_long_does_not_regress():
  from backend.exchange.tsl_tracker import TrailingStopTracker
  t = TrailingStopTracker()
  t.register(
    position_db_id=1,
    portfolio_id=1,
    symbol="BTCUSDT",
    side="Buy",
    entry_price=100.0,
    trail_points=5.0,
    trail_offset=0.0,
  )
  # Ticker pushes extreme up to 120 intra-bar.
  t.update_extreme(1, 120.0)
  status = [s for s in t.get_status() if s["position_id"] == 1][0]
  assert status["extreme_price"] == 120.0

  # Bar-close writer arrives with a smaller bar-high of 115.
  # The extreme must NOT regress.
  t.update_extreme(1, 115.0)
  status = [s for s in t.get_status() if s["position_id"] == 1][0]
  assert status["extreme_price"] == 120.0, (
    "CS-043 REGRESSION: long extreme regressed from 120.0 → %.4f on "
    "a lower bar-close high. The trailing stop would relax backward."
    % status["extreme_price"]
  )


def test_update_extreme_short_does_not_regress():
  from backend.exchange.tsl_tracker import TrailingStopTracker
  t = TrailingStopTracker()
  t.register(
    position_db_id=2,
    portfolio_id=1,
    symbol="BTCUSDT",
    side="Sell",
    entry_price=100.0,
    trail_points=5.0,
    trail_offset=0.0,
  )
  # Ticker pushes extreme down to 80 intra-bar.
  t.update_extreme(2, 80.0)
  status = [s for s in t.get_status() if s["position_id"] == 2][0]
  assert status["extreme_price"] == 80.0

  # Bar-close writer arrives with a higher bar-low of 85.
  # The extreme must NOT regress.
  t.update_extreme(2, 85.0)
  status = [s for s in t.get_status() if s["position_id"] == 2][0]
  assert status["extreme_price"] == 80.0, (
    "CS-043 REGRESSION: short extreme regressed from 80.0 → %.4f on "
    "a higher bar-close low. The trailing stop would relax upward."
    % status["extreme_price"]
  )


def test_update_extreme_still_advances_in_favourable_direction():
  """Sanity: the ratchet must still ACCEPT a better value.
  Otherwise the test above would pass for a broken no-op.
  """
  from backend.exchange.tsl_tracker import TrailingStopTracker
  t = TrailingStopTracker()
  t.register(
    position_db_id=3, portfolio_id=1, symbol="BTCUSDT", side="Buy",
    entry_price=100.0, trail_points=5.0, trail_offset=0.0,
  )
  t.update_extreme(3, 110.0)
  t.update_extreme(3, 125.0)  # Better high.
  status = [s for s in t.get_status() if s["position_id"] == 3][0]
  assert status["extreme_price"] == 125.0, (
    "CS-043 FIXTURE: update_extreme failed to advance on a better "
    "high. Either the ratchet is a no-op (bug) or the test is broken."
  )
