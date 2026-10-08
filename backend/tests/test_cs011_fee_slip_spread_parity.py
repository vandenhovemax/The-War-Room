"""CS-011: pin engine cost arithmetic — commission, slippage, spread.

The engine applies three cost adjustments to every round-trip trade:

  1. **Slippage** — entry fills *worse* (long higher, short lower); exit
     fills *worse* the other way. Configurable as percentage-of-price
     or fixed-pips or percentage-of-risk.
  2. **Spread** — half-spread on entry, half-spread on exit. Combined
     with slippage in additive fashion.
  3. **Commission** — applied once on entry (debits cash) and once on
     exit (debited from the trade's ``pnl`` field).

Before this file, every existing engine test zeroed all three costs via
a ``_no_slip`` helper. A bug that double-applied or zero-applied costs
could ship silently — only the absolute $ would move, and no test
asserted absolute $.

Each test here uses a deterministic single trade with a TP that fires
exactly (no gap-through), then asserts the `OrderFill.pnl` field and
the strategy's final ``_cash`` value against the closed-form formula.
Tolerance is 1e-9 — the point is to catch *any* drift, not just big
ones.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.engine import BacktestConfig, BacktestEngine


# ── Synthetic OHLCV that guarantees entry@100 and TP@110 fire ─────────

def _ohlcv_for_tp_at_110() -> pd.DataFrame:
  """7 bars. Bar 3 open = 100.0 (entry fill). Bar 5 high = 115.0 so a
  long TP at 110.0 fires exactly at 110 (no gap-through since open < 110).
  """
  dates = pd.date_range("2024-01-01", periods=7, freq="30min")
  # bar:         0      1      2      3      4      5      6
  opens   =    [99.0, 99.5, 100.0, 100.0, 101.0, 105.0, 111.0]
  highs   =    [99.5, 100.0, 100.3, 100.5, 105.0, 115.0, 112.0]
  lows    =    [98.0, 99.0, 99.5, 99.8, 100.5, 104.0, 110.0]
  closes  =    [99.3, 99.8, 100.0, 100.3, 104.0, 112.0, 111.5]
  return pd.DataFrame(
    {"open": opens, "high": highs, "low": lows, "close": closes,
     "volume": [1000] * 7},
    index=dates,
  )


def _ohlcv_for_short_tp_at_90() -> pd.DataFrame:
  """Mirror of above — short fills at bar 3 open = 100, TP at 90 fires
  at bar 5 when low reaches 85 (exit is at limit=90 since open > 90).
  """
  dates = pd.date_range("2024-01-01", periods=7, freq="30min")
  opens   = [101.0, 100.5, 100.0, 100.0, 99.0, 95.0, 89.0]
  highs   = [102.0, 101.0, 100.5, 100.2, 99.5, 96.0, 90.5]
  lows    = [100.5, 100.0, 99.7, 99.5, 95.0, 85.0, 88.0]
  closes  = [100.7, 100.2, 100.0, 99.7, 96.0, 88.0, 88.5]
  return pd.DataFrame(
    {"open": opens, "high": highs, "low": lows, "close": closes,
     "volume": [1000] * 7},
    index=dates,
  )


class _LongAtBar2TPat110(BaseStrategy):
  """Bar 2: enter long qty=1.0 with TP at 110."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="e", direction="long", qty=1.0)
      self.exit(id="tp", from_entry="e", limit=110.0)


class _ShortAtBar2TPat90(BaseStrategy):
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="e", direction="short", qty=1.0)
      self.exit(id="tp", from_entry="e", limit=90.0)


def _run(strategy_cls, df, **cfg_overrides) -> tuple[list, BaseStrategy]:
  defaults = dict(
    initial_capital=10000.0,
    commission_pct=0.0, slippage_pct=0.0, slippage_value=0.0, spread_pct=0.0,
    warmup_bars=0,
  )
  defaults.update(cfg_overrides)
  cfg = BacktestConfig(**defaults)
  eng = BacktestEngine(cfg)
  strat = strategy_cls()
  result = eng.run(strat, df)
  return result.trades, strat


# ── Baseline: zero costs — engine is a clean adder ────────────────────

def test_long_zero_costs_matches_price_diff():
  df = _ohlcv_for_tp_at_110()
  trades, strat = _run(_LongAtBar2TPat110, df)
  exits = [t for t in trades if t.action == "exit"]
  assert len(exits) == 1, "CS-011: expected exactly one exit fill"
  f = exits[0]
  # Expected: exit 110, entry 100, qty 1 → pnl = 10.0, commission 0
  assert f.price == pytest.approx(110.0)
  assert f.entry_price == pytest.approx(100.0)
  assert f.pnl == pytest.approx(10.0, abs=1e-9)
  assert f.commission == pytest.approx(0.0, abs=1e-9)
  # Cash delta = +10 (since both commissions are zero)
  assert strat._cash == pytest.approx(10010.0, abs=1e-9)


def test_short_zero_costs_matches_price_diff():
  df = _ohlcv_for_short_tp_at_90()
  trades, strat = _run(_ShortAtBar2TPat90, df)
  exits = [t for t in trades if t.action == "exit"]
  assert len(exits) == 1
  f = exits[0]
  assert f.price == pytest.approx(90.0)
  assert f.entry_price == pytest.approx(100.0)
  assert f.pnl == pytest.approx(10.0, abs=1e-9)
  assert strat._cash == pytest.approx(10010.0, abs=1e-9)


# ── Commission only — applied exactly twice (entry + exit) ────────────

def test_long_commission_only_charged_twice():
  """With ``commission_pct=0.1`` (10 bps):
     entry_comm = 100 * 1 * 0.001 = 0.10
     exit_comm  = 110 * 1 * 0.001 = 0.11
     fill.pnl   = (110-100)*1 - exit_comm = 9.89
     cash       = 10000 - 0.10 + 9.89 = 10009.79
  """
  df = _ohlcv_for_tp_at_110()
  trades, strat = _run(_LongAtBar2TPat110, df, commission_pct=0.1)
  exits = [t for t in trades if t.action == "exit"]
  f = exits[0]
  assert f.commission == pytest.approx(0.11, abs=1e-9)
  assert f.pnl == pytest.approx(10.0 - 0.11, abs=1e-9)
  # Entry commission never appears in OrderFill.pnl — only in cash.
  assert strat._cash == pytest.approx(10000.0 - 0.10 + (10.0 - 0.11), abs=1e-9)


# ── Slippage only — entry+exit both get worse fills ───────────────────

def test_long_slippage_pct_symmetric_on_both_legs():
  """slippage_pct=0.05 (5 bps) on long:
     entry fills at 100 * (1 + 0.0005) = 100.05
     exit  fills at 110 * (1 - 0.0005) = 109.945
     fill.pnl = (109.945 - 100.05)*1 - 0 = 9.895
     cash = 10000 + 9.895 = 10009.895
  """
  df = _ohlcv_for_tp_at_110()
  trades, strat = _run(_LongAtBar2TPat110, df, slippage_pct=0.05)
  exits = [t for t in trades if t.action == "exit"]
  f = exits[0]
  expected_entry = 100.0 * (1 + 0.0005)
  expected_exit = 110.0 * (1 - 0.0005)
  assert f.entry_price == pytest.approx(expected_entry, abs=1e-9)
  assert f.price == pytest.approx(expected_exit, abs=1e-9)
  assert f.pnl == pytest.approx(expected_exit - expected_entry, abs=1e-9)
  assert strat._cash == pytest.approx(
    10000.0 + (expected_exit - expected_entry), abs=1e-9,
  )


def test_short_slippage_pct_flips_sign():
  """Short: entry fills LOWER (100 - slip), exit fills HIGHER (90 + slip)."""
  df = _ohlcv_for_short_tp_at_90()
  trades, strat = _run(_ShortAtBar2TPat90, df, slippage_pct=0.05)
  f = [t for t in trades if t.action == "exit"][0]
  expected_entry = 100.0 * (1 - 0.0005)
  expected_exit = 90.0 * (1 + 0.0005)
  assert f.entry_price == pytest.approx(expected_entry, abs=1e-9)
  assert f.price == pytest.approx(expected_exit, abs=1e-9)
  # Short pnl = (entry - exit) * qty
  assert f.pnl == pytest.approx(expected_entry - expected_exit, abs=1e-9)


# ── Spread only — half-spread on entry, half on exit ─────────────────

def test_long_spread_half_applied_each_leg():
  """spread_pct=0.1 → half = 0.1 / 200 = 0.0005 fractional.
     entry = 100 * (1 + 0.0005) = 100.05
     exit  = 110 * (1 - 0.0005) = 109.945
     Same numeric result as 5bp slippage — but distinct code path,
     and must remain distinct so the costs *add* when both set.
  """
  df = _ohlcv_for_tp_at_110()
  _, strat = _run(_LongAtBar2TPat110, df, spread_pct=0.1)
  # Final cash should show identical delta to the slippage-only case.
  expected_delta = 110.0 * (1 - 0.0005) - 100.0 * (1 + 0.0005)
  assert strat._cash == pytest.approx(10000.0 + expected_delta, abs=1e-9)


# ── Combined — costs stack (slippage + spread + commission) ───────────

def test_long_all_three_costs_stack_additively():
  """With slippage 5bp, spread 10bp, commission 5bp — the cost
  adjustments must stack. The engine applies slippage *then* spread on
  top of it (see engine._fill_entry lines 565-570), so:

     entry_slip = 100 * (1 + 0.0005) = 100.05
     entry_full = 100.05 * (1 + 0.0005) = 100.100025
     exit_slip  = 110 * (1 - 0.0005) = 109.945
     exit_full  = 109.945 * (1 - 0.0005) = 109.890028...
     entry_comm = 100.100025 * 1 * 0.0005 = 0.0500500125
     exit_comm  = 109.890028 * 1 * 0.0005 = 0.0549450...
     fill.pnl   = (exit_full - entry_full)*1 - exit_comm
     cash       = 10000 - entry_comm + fill.pnl
  """
  df = _ohlcv_for_tp_at_110()
  trades, strat = _run(
    _LongAtBar2TPat110, df,
    slippage_pct=0.05, spread_pct=0.1, commission_pct=0.05,
  )
  f = [t for t in trades if t.action == "exit"][0]

  entry_full = 100.0 * (1 + 0.0005) * (1 + 0.0005)
  exit_full = 110.0 * (1 - 0.0005) * (1 - 0.0005)
  entry_comm = entry_full * 1.0 * 0.0005
  exit_comm = exit_full * 1.0 * 0.0005

  assert f.entry_price == pytest.approx(entry_full, abs=1e-9)
  assert f.price == pytest.approx(exit_full, abs=1e-9)
  assert f.commission == pytest.approx(exit_comm, abs=1e-9)
  assert f.pnl == pytest.approx(
    (exit_full - entry_full) * 1.0 - exit_comm, abs=1e-9,
  )
  # Cash tracks entry commission (not in OrderFill.pnl) plus fill pnl.
  expected_cash = 10000.0 - entry_comm + ((exit_full - entry_full) - exit_comm)
  assert strat._cash == pytest.approx(expected_cash, abs=1e-9)


# ── Regression: commission scales linearly with qty ───────────────────

def test_commission_scales_with_qty():
  """Doubling qty must exactly double commission on both legs."""
  df = _ohlcv_for_tp_at_110()

  class _QtyTwo(BaseStrategy):
    def init(self): pass
    def next(self):
      if self._bar == 2:
        self.entry(id="e", direction="long", qty=2.0)
        self.exit(id="tp", from_entry="e", limit=110.0)

  trades, strat = _run(_QtyTwo, df, commission_pct=0.1)
  f = [t for t in trades if t.action == "exit"][0]
  # Exit commission = exit_px * qty * c = 110 * 2 * 0.001 = 0.22
  assert f.commission == pytest.approx(0.22, abs=1e-9)
  # Pnl = (110-100)*2 - 0.22 = 19.78
  assert f.pnl == pytest.approx(20.0 - 0.22, abs=1e-9)
  entry_comm_doubled = 100.0 * 2.0 * 0.001  # 0.20
  assert strat._cash == pytest.approx(
    10000.0 - entry_comm_doubled + (20.0 - 0.22), abs=1e-9,
  )


# ── Regression: zero-cost config produces bit-identical baseline ──────

def test_zero_costs_produces_exact_price_diff_pnl():
  """Defence-in-depth: explicit zeros on every knob must leave pnl
  equal to raw price diff to the floating-point limit. A regression
  that silently defaults any cost to a nonzero value trips this test.
  """
  df = _ohlcv_for_tp_at_110()
  _, strat = _run(
    _LongAtBar2TPat110, df,
    commission_pct=0.0, slippage_pct=0.0, slippage_value=0.0, spread_pct=0.0,
  )
  assert strat._cash == pytest.approx(10010.0, abs=1e-12)
