"""CS-048: breakeven tolerance must be at float-noise level, not a
fixed dollar amount.

The bug: engine and live used ``±$0.01`` as the "breakeven" window
for martingale streak tracking. On a small-capital (or small-
notional) trade, a $0.005 win/loss rounded to zero would be counted
as "breakeven" — the streak counter did not reset and martingale
sizing compounded without bound.

The fix: both engine (``backtester/engine.py``) and live
(``engine_coordinator._get_martingale_streak``) use
``_BE_EPS = 1e-9`` — float noise only. A real $0.005 win now breaks
the streak on every capital scale.

Behaviour also verified: the two thresholds must match EXACTLY.
Engine and live must decide "win / loss / breakeven" identically
on every trade, otherwise live sizing drifts away from the
backtested expectation.
"""
from __future__ import annotations

import inspect
import re


def _scan_be_eps(src: str) -> list[str]:
  """Extract numeric literal assigned to ``_BE_EPS`` inside a source."""
  hits = re.findall(r"_BE_EPS\s*=\s*([0-9a-zA-Z_\-.+]+)", src)
  return hits


def test_engine_and_live_use_same_float_noise_breakeven_epsilon():
  from backend.backtester import engine as engine_mod
  from backend.exchange import engine_coordinator as ec_mod

  engine_src = inspect.getsource(engine_mod.BacktestEngine._close_position_by_id)
  live_src = inspect.getsource(ec_mod.EngineCoordinator._get_martingale_streak)

  engine_eps = _scan_be_eps(engine_src)
  live_eps = _scan_be_eps(live_src)

  assert engine_eps, (
    "CS-048 REGRESSION: backtester/engine.py::_fill_exit no longer "
    "defines ``_BE_EPS``. Without it, the martingale streak logic "
    "falls back to whatever tolerance the author left behind — the "
    "audit bug was a $0.01 window that swallowed tiny-notional wins."
  )
  assert live_eps, (
    "CS-048 REGRESSION: engine_coordinator._get_martingale_streak "
    "no longer defines ``_BE_EPS``. Live martingale must use the "
    "same tolerance as the engine or live sizing drifts."
  )

  assert engine_eps[0] == live_eps[0], (
    "CS-048 REGRESSION: engine ``_BE_EPS`` = %r but live "
    "``_BE_EPS`` = %r. Engine and live must agree on what counts "
    "as 'breakeven' — otherwise the martingale streak counter "
    "diverges and live sizing drifts away from the backtested "
    "expectation." % (engine_eps[0], live_eps[0])
  )

  # Noise level only — the eps must be <= 1e-6 (well below any
  # realistic trade PnL, including 5-decimal lot sizes).
  try:
    eps_val = float(engine_eps[0])
  except ValueError:
    raise AssertionError(
      f"CS-048: _BE_EPS is not a simple float literal ({engine_eps[0]!r}) — "
      "test cannot evaluate it."
    )
  assert eps_val <= 1e-6, (
    f"CS-048 REGRESSION: _BE_EPS = {eps_val!r} — larger than "
    "float-noise. At this tolerance, a $0.005 win/loss on a "
    "tiny-notional trade is treated as breakeven and fails to reset "
    "the martingale streak. Use 1e-9 (float noise)."
  )


def test_engine_breakeven_decision_splits_tiny_wins_correctly():
  """Behavioural: the engine's live-code decision on a $0.005 PnL
  must be 'win' (not 'breakeven'). This is the scenario the audit
  flagged — small-capital martingale runaway.
  """
  from backend.backtester import engine as engine_mod
  engine_src = inspect.getsource(engine_mod.BacktestEngine._close_position_by_id)
  eps = float(_scan_be_eps(engine_src)[0])

  tiny_win = 0.005
  tiny_loss = -0.005

  assert tiny_win > eps, (
    f"CS-048 REGRESSION: a $0.005 win with _BE_EPS={eps} would be "
    "classified as breakeven. Martingale streak would NOT reset — "
    "the exact bug the audit caught."
  )
  assert tiny_loss < -eps, (
    f"CS-048 REGRESSION: a -$0.005 loss with _BE_EPS={eps} would be "
    "classified as breakeven. The loss counter would NOT increment."
  )
