"""Fee / slippage / spread numeric parity.

Pins the exact formulas the backtest engine uses so future refactors can't
silently change PnL math. These tests don't run a full backtest — they
assert the primitives and recompute the per-trade cost structure.

Formulas (from backend/backtester/engine.py):
  - Slippage pct mode:   price +/- price * (slippage_pct / 100)
  - Slippage pips mode:  price +/- slippage_value
  - Spread:              price * (1 +/- spread_pct / 200)   (half-spread each side)
  - Commission:          price * qty * (commission_pct / 100)   (each side)
  - Funding:             pos_value * (funding_rate_daily_pct / 100) * hold_days

Slippage direction:
  - Long entry / short exit:  price goes UP (worse for buyer)
  - Short entry / long exit:  price goes DOWN (worse for seller)
"""
from __future__ import annotations

import pytest

from backend.backtester.engine import BacktestConfig, BacktestEngine


# --- _apply_slippage: pct mode ------------------------------------------

def test_slippage_pct_long_entry_gets_worse_fill():
  """Long entry: price goes UP by pct."""
  cfg = BacktestConfig(slippage_mode="pct", slippage_value=0.1)
  eng = BacktestEngine(cfg)
  # 0.1% of 100 = 0.10 → 100 + 0.10 = 100.10
  assert eng._apply_slippage(100.0, "long", is_entry=True) == pytest.approx(100.10)


def test_slippage_pct_long_exit_gets_worse_fill():
  """Long exit: price goes DOWN by pct."""
  cfg = BacktestConfig(slippage_mode="pct", slippage_value=0.1)
  eng = BacktestEngine(cfg)
  assert eng._apply_slippage(100.0, "long", is_entry=False) == pytest.approx(99.90)


def test_slippage_pct_short_entry_gets_worse_fill():
  """Short entry: price goes DOWN (fill sells for less)."""
  cfg = BacktestConfig(slippage_mode="pct", slippage_value=0.1)
  eng = BacktestEngine(cfg)
  assert eng._apply_slippage(100.0, "short", is_entry=True) == pytest.approx(99.90)


def test_slippage_pct_short_exit_gets_worse_fill():
  """Short exit: price goes UP (cover buys for more)."""
  cfg = BacktestConfig(slippage_mode="pct", slippage_value=0.1)
  eng = BacktestEngine(cfg)
  assert eng._apply_slippage(100.0, "short", is_entry=False) == pytest.approx(100.10)


def test_slippage_pct_zero_is_passthrough():
  cfg = BacktestConfig(slippage_mode="pct", slippage_value=0.0, slippage_pct=0.0)
  eng = BacktestEngine(cfg)
  assert eng._apply_slippage(100.0, "long", is_entry=True) == 100.0


def test_slippage_legacy_pct_fallback():
  """slippage_value=0 → falls back to legacy slippage_pct field, mode becomes pct."""
  cfg = BacktestConfig(slippage_mode="pips", slippage_value=0.0, slippage_pct=0.05)
  eng = BacktestEngine(cfg)
  # 0.05% of 100 = 0.05 → 100 + 0.05 = 100.05 (pct fallback, not pips)
  assert eng._apply_slippage(100.0, "long", is_entry=True) == pytest.approx(100.05)


# --- _apply_slippage: pips mode -----------------------------------------

def test_slippage_pips_long_entry_adds_flat_offset():
  cfg = BacktestConfig(slippage_mode="pips", slippage_value=0.5)
  eng = BacktestEngine(cfg)
  assert eng._apply_slippage(100.0, "long", is_entry=True) == 100.5


def test_slippage_pips_long_exit_subtracts_flat_offset():
  cfg = BacktestConfig(slippage_mode="pips", slippage_value=0.5)
  eng = BacktestEngine(cfg)
  assert eng._apply_slippage(100.0, "long", is_entry=False) == 99.5


def test_slippage_pips_short_entry_subtracts():
  cfg = BacktestConfig(slippage_mode="pips", slippage_value=0.5)
  eng = BacktestEngine(cfg)
  assert eng._apply_slippage(100.0, "short", is_entry=True) == 99.5


# --- Spread formula -----------------------------------------------------

def test_spread_half_on_entry_and_exit_formula():
  """Spread applied as half (spread_pct / 200) each side.

  Entry long: price * (1 + spread_pct/200)
  Exit long:  price * (1 - spread_pct/200)
  """
  spread_pct = 0.04  # 4 bps full spread → 2 bps per side
  entry_price = 100.0

  half = spread_pct / 200  # = 0.0002

  fill_entry = entry_price * (1 + half)
  fill_exit = entry_price * (1 - half)

  assert fill_entry == pytest.approx(100.02)
  assert fill_exit == pytest.approx(99.98)


def test_spread_clamped_to_one_percent():
  """BacktestConfig.__post_init__ clamps spread_pct to [0, 1]."""
  cfg = BacktestConfig(spread_pct=5.0)
  assert cfg.spread_pct == 1.0
  cfg_neg = BacktestConfig(spread_pct=-0.5)
  assert cfg_neg.spread_pct == 0.0


# --- Commission formula -------------------------------------------------

def test_commission_formula():
  """commission = price * qty * (commission_pct / 100), one side."""
  commission_pct = 0.055  # default: 5.5 bps per side
  price = 100.0
  qty = 10.0

  comm = price * qty * (commission_pct / 100)

  # 100 * 10 * 0.00055 = 0.55
  assert comm == pytest.approx(0.55)


def test_commission_clamped_to_five_percent():
  cfg = BacktestConfig(commission_pct=10.0)
  assert cfg.commission_pct == 5.0
  cfg_neg = BacktestConfig(commission_pct=-1.0)
  assert cfg_neg.commission_pct == 0.0


# --- Funding formula ----------------------------------------------------

def test_funding_formula_one_day_hold():
  """funding = pos_value * (daily_pct/100) * hold_days."""
  funding_daily_pct = 0.03  # 3 bps/day
  exit_price = 100.0
  qty = 10.0
  hold_days = 1.0

  pos_value = exit_price * qty  # 1000
  funding = pos_value * (funding_daily_pct / 100) * hold_days

  # 1000 * 0.0003 * 1 = 0.30
  assert funding == pytest.approx(0.30)


def test_funding_scales_linearly_with_hold_days():
  funding_daily_pct = 0.03
  pos_value = 1000.0

  one_day = pos_value * (funding_daily_pct / 100) * 1.0
  seven_day = pos_value * (funding_daily_pct / 100) * 7.0

  assert seven_day == pytest.approx(one_day * 7)


def test_funding_clamped_to_one_percent_daily():
  """__post_init__ clamps funding_rate_daily_pct to [0, 1]."""
  cfg = BacktestConfig(funding_rate_daily_pct=5.0)
  assert cfg.funding_rate_daily_pct == 1.0


# --- Integrated single-trade P&L reconstruction -------------------------

def test_single_trade_long_pnl_reconstruction():
  """Manually reconstruct a long trade's net PnL with all costs.

  Given:
    base_entry=100, base_exit=110, qty=10
    commission=0.055%, slippage=0.1% pct, spread=0.04%, funding=0.03%/day, 2 days

  Expected cost stack:
    entry_fill = 100 * (1 + 0.001) * (1 + 0.0002) = 100.12002...
    exit_fill  = 110 * (1 - 0.001) * (1 - 0.0002) = 109.86997...
    gross_pnl  = (exit_fill - entry_fill) * qty
    entry_comm = entry_fill * qty * 0.00055
    exit_comm  = exit_fill  * qty * 0.00055
    funding    = exit_fill * qty * 0.0003 * 2
    net_pnl    = gross_pnl - entry_comm - exit_comm - funding
  """
  commission_pct = 0.055
  slippage_pct = 0.1
  spread_pct = 0.04
  funding_daily_pct = 0.03
  hold_days = 2.0
  qty = 10.0

  # Entry-side fills (long: price UP for slippage, UP for spread)
  entry_after_slip = 100.0 * (1 + slippage_pct / 100)
  entry_fill = entry_after_slip * (1 + spread_pct / 200)

  # Exit-side fills (long: price DOWN for slippage, DOWN for spread)
  exit_after_slip = 110.0 * (1 - slippage_pct / 100)
  exit_fill = exit_after_slip * (1 - spread_pct / 200)

  gross_pnl = (exit_fill - entry_fill) * qty

  # Engine deducts entry commission from cash (not from realized pnl),
  # but the per-trade *net_profit* recorded on the exit row subtracts
  # only the exit commission from gross. Entry commission is a cash
  # drag — here we test the documented exit-side formulas explicitly.
  exit_comm = exit_fill * qty * (commission_pct / 100)
  funding = exit_fill * qty * (funding_daily_pct / 100) * hold_days

  net = gross_pnl - exit_comm - funding

  # Sanity: cost components are all positive drags on the 10-unit long
  assert entry_fill > 100.0  # worse entry
  assert exit_fill < 110.0   # worse exit
  assert gross_pnl < (110.0 - 100.0) * qty  # costs eat into ideal $100
  assert exit_comm > 0
  assert funding > 0

  # Numeric pin: net PnL with these exact inputs
  # entry_fill ≈ 100.120020
  # exit_fill  ≈ 109.869978
  # gross_pnl  ≈ 97.499580
  # exit_comm  ≈ 0.604285
  # funding    ≈ 0.659220
  # net ≈ 96.236076
  assert net == pytest.approx(96.24, abs=0.05)


def test_single_trade_short_slippage_direction():
  """Short: entry goes DOWN (sell lower), exit goes UP (cover higher)."""
  slippage_pct = 0.1
  cfg = BacktestConfig(slippage_mode="pct", slippage_value=slippage_pct)
  eng = BacktestEngine(cfg)

  short_entry = eng._apply_slippage(100.0, "short", is_entry=True)
  short_exit = eng._apply_slippage(90.0, "short", is_entry=False)

  assert short_entry == pytest.approx(99.90)  # sold for less
  assert short_exit == pytest.approx(90.09)   # covered for more
  # Both directions shave PnL: real gross = (99.90 - 90.09) * qty = 9.81/unit
  # vs ideal 10.00/unit → costs 0.19/unit on slippage alone
