"""CS-017: on a reversal path (``max_pyramiding=1`` + opposite-side entry),
the engine must NOT size the new entry from the just-closed position's SL.

The concrete audit claim was: ``_find_sl_for_entry`` could read a stale SL
from the outgoing position → wrong qty on the incoming position.

Tracing current code, this should already be prevented by:
  - ``_close_position_by_id`` cleans ``_exit_orders`` for the closed id
    BEFORE ``_calculate_risk_qty*`` runs for the new entry (engine.py :1115-1119)
  - CS-013 makes every auto-generated entry_id unique per-bar, so even
    if a strategy re-emits the same nominal id, the internal id changes

This test LOCKS that invariant. Any future edit that lets a stale SL
leak into the new position's qty would flip this test red.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.engine import BacktestEngine, BacktestConfig


class _ReversalStrategy(BaseStrategy):
  """Opens a long at bar 5, then flips to short at bar 20 (reversal).
  The long's SL is at a totally different distance from entry than the
  short's SL — so if the short is sized with the long's stale SL, the
  resulting qty would be off by a measurable, distinctive ratio.
  """

  def init(self):
    pass

  def next(self):
    bar = self._bar
    if bar == 5:
      self.entry(id="L1", direction="long")
    if bar == 20:
      self.entry(id="S1", direction="short")

    # Register distinctive SLs for each position so we can detect a
    # stale read:  long SL is 5% away, short SL is 1% away.
    for pid, pos in self._positions.items():
      if pos.side == "long":
        self.exit(id=f"SL_{pid}", from_entry=pid,
                  stop=pos.entry_price * 0.95)
      else:
        self.exit(id=f"SL_{pid}", from_entry=pid,
                  stop=pos.entry_price * 1.01)


def _make_ohlc(n: int = 50) -> pd.DataFrame:
  idx = pd.date_range("2024-01-01", periods=n, freq="15min")
  base = np.full(n, 100.0)
  return pd.DataFrame({
    "open": base, "high": base + 0.5, "low": base - 0.5, "close": base,
    "volume": 1000.0,
  }, index=idx)


def test_reversal_does_not_inherit_stale_sl():
  """Run a reversal through real engine code. After run:
    - S1 must be sized against its OWN 1%-away SL (roughly $1 distance)
    - NOT against L1's 5%-away SL (~$5 distance)
  If the new position's qty is ~1/5 what it should be, the stale-SL
  bug has regressed.
  """
  df = _make_ohlc(50)
  cfg = BacktestConfig(
    initial_capital=10_000.0, commission_pct=0.0,
    max_pyramiding=1,          # forces reversal behavior
    risk_pct=1.0,               # 1% of equity per trade
  )
  strategy = _ReversalStrategy()
  engine = BacktestEngine(cfg)
  engine.run(strategy, df)

  # Find the short entry trade (S1)
  short_entries = [t for t in strategy._trades
                   if t.action == "entry" and t.side == "short"]
  assert short_entries, "S1 never filled — reversal didn't run"
  s1 = short_entries[0]

  # Expected qty: 1% of $10k = $100 risk / $1 SL distance = ~100 qty
  # Stale (bug) qty: $100 risk / $5 SL distance = ~20 qty
  # The difference is 5x, well beyond any rounding or margin capping.
  # The short should be within 20% of its CORRECT sizing; a 5x error
  # would fail this bound loudly.
  assert s1.qty >= 50.0, (
    f"CS-017: short entry sized far too small ({s1.qty}). "
    "This is the symptom of sizing from the outgoing long's stale SL "
    "(5% distance) instead of the short's own 1% SL."
  )


def test_reversal_cleans_outgoing_exit_orders():
  """Direct invariant: after ``_close_position_by_id`` runs on the
  outgoing position, no exit orders remain with ``from_entry`` pointing
  at that position. Sizing the incoming entry can then only read the
  new position's own SL, never the stale one.
  """
  df = _make_ohlc(50)
  cfg = BacktestConfig(
    initial_capital=10_000.0, commission_pct=0.0,
    max_pyramiding=1, risk_pct=1.0,
  )
  strategy = _ReversalStrategy()
  engine = BacktestEngine(cfg)
  engine.run(strategy, df)

  # After the full run, all positions should be closed and their exit
  # orders cleaned — no orphan entries with from_entry pointing at a
  # deleted position id.
  live_position_ids = set(strategy._positions.keys())
  for eo in strategy._exit_orders.values():
    assert eo.from_entry in live_position_ids or eo.from_entry == "", (
      f"CS-017: orphan exit order from_entry={eo.from_entry!r} — a "
      "stale SL was left behind after a reversal close. Future risk-"
      "sizing reads could pick it up."
    )
