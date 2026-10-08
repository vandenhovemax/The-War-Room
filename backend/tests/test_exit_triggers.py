"""Tests for exit order trigger mechanics — SL, TP, TSL, gap-through, partial close.

Verifies the engine's _process_exit_orders(), _check_exit_trigger(),
_check_trailing_stop(), _resolve_exit_qty(), and _close_position_by_id()
code paths that fire on the bar AFTER an entry.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.engine import BacktestConfig, BacktestEngine


# ── Helper: zero-slippage config ────────────────────────────────────────

def _no_slip(**overrides) -> BacktestConfig:
  """BacktestConfig with ALL slippage/spread/commission disabled."""
  defaults = dict(
    slippage_pct=0.0, slippage_value=0.0, spread_pct=0.0, commission_pct=0.0,
  )
  defaults.update(overrides)
  return BacktestConfig(**defaults)


# ── Synthetic OHLCV generators ──────────────────────────────────────────

def _make_ohlcv(n_bars: int = 20) -> pd.DataFrame:
  """Predictable OHLCV: open[i]=100+i, close[i]=100.5+i, high=close+0.3, low=open-0.3."""
  dates = pd.date_range("2024-01-01", periods=n_bars, freq="30min")
  opens = [100.0 + i for i in range(n_bars)]
  closes = [100.5 + i for i in range(n_bars)]
  highs = [c + 0.3 for c in closes]
  lows = [o - 0.3 for o in opens]
  volume = [1000] * n_bars
  return pd.DataFrame(
    {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume},
    index=dates,
  )


def _make_ohlcv_custom(bars: list[dict]) -> pd.DataFrame:
  """Build OHLCV from a list of {open, high, low, close} dicts."""
  dates = pd.date_range("2024-01-01", periods=len(bars), freq="30min")
  return pd.DataFrame(bars, index=dates).assign(volume=1000)


# ── Test strategies ─────────────────────────────────────────────────────

class _LongWithSLAt103(BaseStrategy):
  """Enters long at bar 2 with SL at 103.0."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="long_sl", direction="long", qty=1.0)
      self.exit(id="sl_exit", from_entry="long_sl", stop=103.0)


class _LongWithTPAt108(BaseStrategy):
  """Enters long at bar 2 with TP at 108.0."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="long_tp", direction="long", qty=1.0)
      self.exit(id="tp_exit", from_entry="long_tp", limit=108.0)


class _LongWithSLAndTP(BaseStrategy):
  """Enters long at bar 2 with SL=101.0 and TP=108.0."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="long_both", direction="long", qty=1.0)
      self.exit(id="exit_both", from_entry="long_both", stop=101.0, limit=108.0)


class _ShortWithSLAt108(BaseStrategy):
  """Enters short at bar 2 with SL at 108.0."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="short_sl", direction="short", qty=1.0)
      self.exit(id="sl_short_exit", from_entry="short_sl", stop=108.0)


class _ShortWithTPAt100(BaseStrategy):
  """Enters short at bar 2 with TP at 100.0."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="short_tp", direction="short", qty=1.0)
      self.exit(id="tp_short_exit", from_entry="short_tp", limit=100.0)


class _LongWithTSL(BaseStrategy):
  """Enters long at bar 2 with trailing stop of 2.0 points."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="long_tsl", direction="long", qty=1.0)
      self.exit(id="tsl_exit", from_entry="long_tsl", trail_points=2.0)


class _LongWithTSLAndOffset(BaseStrategy):
  """Enters long at bar 2 with trailing stop of 1.5 points, activation offset 3.0."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="long_tsl_off", direction="long", qty=1.0)
      self.exit(id="tsl_off_exit", from_entry="long_tsl_off",
                trail_points=1.5, trail_offset=3.0)


class _LongPartialClose(BaseStrategy):
  """Enters long at bar 2, partial TP at 50% when price hits 108."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="long_partial", direction="long", qty=2.0)
      self.exit(id="partial_tp", from_entry="long_partial",
                limit=108.0, qty_percent=50)
      self.exit(id="full_sl", from_entry="long_partial", stop=100.0)


class _LongCloseModePlusSL(BaseStrategy):
  """Enters long at bar 2 (close mode), SL at 101.0."""
  def init(self): pass
  def next(self):
    if self._bar == 2:
      self.entry(id="close_sl", direction="long", qty=1.0)
      self.exit(id="close_sl_exit", from_entry="close_sl", stop=101.0)


# ── Tests: Stop Loss ───────────────────────────────────────────────────

class TestStopLossTrigger:
  """Verify SL fires correctly against bar's high/low."""

  def test_long_sl_triggers_on_bar_low(self):
    """Long SL at 103.0 should fire when bar_low <= 103.0."""
    # Build bars where bar 4 has low that touches SL
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},  # bar 0
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},  # bar 1
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},  # bar 2: entry signal
      {"open": 106, "high": 107, "low": 105, "close": 106.5},    # bar 3: entry fills here
      {"open": 106.5, "high": 107, "low": 105, "close": 106},    # bar 4: SL not hit (low=105)
      {"open": 106, "high": 106.5, "low": 102.5, "close": 103},  # bar 5: SL hit (low=102.5)
      {"open": 103, "high": 104, "low": 102, "close": 103.5},    # bar 6
    ]
    df = _make_ohlcv_custom(bars)
    config = _no_slip()
    engine = BacktestEngine(config)
    strategy = _LongWithSLAt103()
    result = engine.run(strategy, df, symbol="TEST", timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1, f"Expected 1 exit, got {len(exits)}"
    assert exits[0].price == pytest.approx(103.0, abs=1e-6), (
      f"SL should fill at 103.0, got {exits[0].price}"
    )
    assert exits[0].bar_index == 5  # SL fires on bar 5

  def test_long_sl_does_not_trigger_above(self):
    """Long SL at 103.0 should NOT fire when bar_low > 103.0."""
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105, "close": 106.5},     # bar 3: fill
      {"open": 106.5, "high": 108, "low": 105, "close": 107.5},   # bar 4: low=105 > 103
      {"open": 107.5, "high": 109, "low": 106, "close": 108.5},   # bar 5: low=106 > 103
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongWithSLAt103(), df, timeframe="30")

    # Only exits should be the backtest_end forced close
    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1  # backtest_end close
    assert exits[0].label == "backtest_end"

  def test_short_sl_triggers_on_bar_high(self):
    """Short SL at 108.0 should fire when bar_high >= 108.0."""
    bars = [
      {"open": 106, "high": 107, "low": 105, "close": 106.5},
      {"open": 106.5, "high": 107, "low": 105.5, "close": 106},
      {"open": 106, "high": 107, "low": 105, "close": 105.5},     # bar 2: signal
      {"open": 105.5, "high": 106, "low": 104, "close": 105},     # bar 3: fill
      {"open": 105, "high": 107, "low": 104, "close": 106},       # bar 4: high=107 < 108
      {"open": 106, "high": 108.5, "low": 105, "close": 107},     # bar 5: high=108.5 >= 108
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_ShortWithSLAt108(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    assert exits[0].price == pytest.approx(108.0, abs=1e-6)
    assert exits[0].bar_index == 5


# ── Tests: Take Profit ─────────────────────────────────────────────────

class TestTakeProfitTrigger:
  """Verify TP fires correctly."""

  def test_long_tp_triggers_on_bar_high(self):
    """Long TP at 108.0 should fire when bar_high >= 108.0."""
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105, "close": 106.5},     # bar 3: fill
      {"open": 106.5, "high": 107.5, "low": 106, "close": 107},   # bar 4: high=107.5 < 108
      {"open": 107, "high": 108.5, "low": 106.5, "close": 108},   # bar 5: high=108.5 >= 108
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongWithTPAt108(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    assert exits[0].price == pytest.approx(108.0, abs=1e-6)
    assert exits[0].bar_index == 5

  def test_short_tp_triggers_on_bar_low(self):
    """Short TP at 100.0 should fire when bar_low <= 100.0."""
    bars = [
      {"open": 106, "high": 107, "low": 105, "close": 106.5},
      {"open": 106.5, "high": 107, "low": 105.5, "close": 106},
      {"open": 106, "high": 107, "low": 105, "close": 105.5},     # bar 2: signal
      {"open": 105.5, "high": 106, "low": 104, "close": 105},     # bar 3: fill
      {"open": 105, "high": 106, "low": 101, "close": 102},       # bar 4: low=101 > 100
      {"open": 102, "high": 103, "low": 99.5, "close": 101},      # bar 5: low=99.5 <= 100
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_ShortWithTPAt100(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    assert exits[0].price == pytest.approx(100.0, abs=1e-6)
    assert exits[0].bar_index == 5

  def test_sl_fires_before_tp_on_same_bar(self):
    """When both SL and TP could trigger on same bar, SL wins (conservative)."""
    # Bar where both conditions met: low <= SL AND high >= TP
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105, "close": 106.5},     # bar 3: fill
      {"open": 106.5, "high": 109, "low": 100, "close": 105},     # bar 4: both hit
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongWithSLAndTP(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    # Engine checks stop first at engine.py:768-772, so SL fills before TP
    assert exits[0].price == pytest.approx(101.0, abs=1e-6)


# ── Tests: Gap Through ─────────────────────────────────────────────────

class TestGapThrough:
  """Verify gap-through fills at bar open when price opens past stop/limit."""

  def test_long_sl_gap_through_fills_at_open(self):
    """Long SL at 103.0, bar opens at 101.0 (gap down) → fill at 101.0."""
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105.5, "close": 106.5},   # bar 3: fill
      {"open": 101, "high": 102, "low": 100, "close": 101.5},     # bar 4: gap through SL
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongWithSLAt103(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    # Gap through: bar_open (101.0) <= SL (103.0) → fill at bar_open
    assert exits[0].price == pytest.approx(101.0, abs=1e-6)
    assert exits[0].bar_index == 4

  def test_long_tp_gap_through_fills_at_open(self):
    """Long TP at 108.0, bar opens at 110.0 (gap up) → fill at 110.0."""
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105.5, "close": 106.5},   # bar 3: fill
      {"open": 110, "high": 111, "low": 109, "close": 110.5},     # bar 4: gap through TP
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongWithTPAt108(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    assert exits[0].price == pytest.approx(110.0, abs=1e-6)
    assert exits[0].bar_index == 4

  def test_short_sl_gap_through(self):
    """Short SL at 108.0, bar opens at 110.0 → fill at 110.0."""
    bars = [
      {"open": 106, "high": 107, "low": 105, "close": 106.5},
      {"open": 106.5, "high": 107, "low": 105.5, "close": 106},
      {"open": 106, "high": 107, "low": 105, "close": 105.5},     # bar 2: signal
      {"open": 105.5, "high": 106, "low": 104, "close": 105},     # bar 3: fill
      {"open": 110, "high": 111, "low": 109, "close": 110.5},     # bar 4: gap through SL
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_ShortWithSLAt108(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    assert exits[0].price == pytest.approx(110.0, abs=1e-6)


# ── Tests: Trailing Stop ───────────────────────────────────────────────

class TestTrailingStop:
  """Verify TSL tracking and trigger behavior."""

  def test_long_tsl_tracks_and_triggers(self):
    """TSL trail_points=2.0: tracks highest price, triggers on retrace.

    Note: trail_extreme starts at 0 (ExitOrder default) and updates to bar_high
    each bar. The entry fills at bar 3 open, then _process_exit_orders on bar 3
    sets trail_extreme to bar 3's high. So we need bars where the low never drops
    below (trail_extreme - trail_points) until the bar we want the trigger.
    """
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105.5, "close": 106.5},   # bar 3: fill at 106. extreme=107, trail=105
      {"open": 106.5, "high": 109, "low": 107.5, "close": 108.5}, # bar 4: extreme=109, trail=107, low=107.5 > 107
      {"open": 108.5, "high": 110, "low": 108.5, "close": 109.5}, # bar 5: extreme=110, trail=108, low=108.5 > 108
      {"open": 109, "high": 109.5, "low": 107.5, "close": 108},   # bar 6: extreme=110, trail=108, low=107.5 <= 108 → trigger
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongWithTSL(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    # Trail stop = 110 (extreme) - 2.0 (trail_points) = 108.0
    assert exits[0].price == pytest.approx(108.0, abs=1e-6)
    assert exits[0].bar_index == 6

  def test_long_tsl_does_not_trigger_before_retrace(self):
    """TSL should not trigger while price keeps rising."""
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105.5, "close": 106.5},   # bar 3: fill
      {"open": 106.5, "high": 109, "low": 107.5, "close": 108.5}, # bar 4: up
      {"open": 108.5, "high": 110, "low": 108.5, "close": 109.5}, # bar 5: up
      {"open": 109.5, "high": 111, "low": 109.5, "close": 110.5}, # bar 6: up
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongWithTSL(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    # Only backtest_end exit, no TSL trigger
    assert len(exits) == 1
    assert exits[0].label == "backtest_end"

  def test_long_tsl_with_offset_waits_for_profit(self):
    """TSL with trail_offset=3.0 should not activate until price > entry+3.0.

    Entry at bar 3 open = 106. Offset = 3.0 → activates when extreme > 109.
    trail_points = 1.5 → trail_stop = extreme - 1.5.
    """
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105.5, "close": 106.5},   # bar 3: fill at 106.0
      # Price rises to 108 but retraces: not activated (108 < 106+3=109)
      {"open": 106.5, "high": 108, "low": 106, "close": 107},     # bar 4: not activated
      # Price rises to 110 (> 109): activates. extreme=110, trail=110-1.5=108.5
      {"open": 107, "high": 110, "low": 109, "close": 109.5},     # bar 5: activate, low=109 > 108.5
      # Retrace: low=108 <= 108.5 → trigger
      {"open": 109, "high": 109.5, "low": 108, "close": 108.5},   # bar 6: trigger
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongWithTSLAndOffset(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    # Trail stop = 110 (extreme) - 1.5 (trail_points) = 108.5
    assert exits[0].price == pytest.approx(108.5, abs=1e-6)
    assert exits[0].bar_index == 6


# ── Tests: Partial Close ───────────────────────────────────────────────

class TestPartialClose:
  """Verify qty_percent partial closes work correctly."""

  def test_partial_tp_closes_half(self):
    """qty_percent=50 with qty=2.0 should close 1.0 unit at TP."""
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105, "close": 106.5},     # bar 3: fill at 106 qty=2
      {"open": 106.5, "high": 109, "low": 106, "close": 108.5},   # bar 4: high=109 >= TP=108
      {"open": 108.5, "high": 109, "low": 108, "close": 108.5},   # bar 5: position still open
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongPartialClose(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    # Should have partial TP exit + backtest_end
    partial_exits = [e for e in exits if e.label != "backtest_end"]
    assert len(partial_exits) >= 1
    # Partial close should be 50% of 2.0 = 1.0
    assert partial_exits[0].qty == pytest.approx(1.0, abs=1e-6)
    assert partial_exits[0].price == pytest.approx(108.0, abs=1e-6)

  def test_partial_close_leaves_remaining_position(self):
    """After partial TP, remaining position should still be open for SL."""
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},
      {"open": 106, "high": 107, "low": 105, "close": 106.5},     # bar 3: fill qty=2
      {"open": 106.5, "high": 109, "low": 106, "close": 108.5},   # bar 4: TP hit, close 1.0
      {"open": 108.5, "high": 109, "low": 99, "close": 99.5},     # bar 5: SL hit on rest
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongPartialClose(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    # Should have TP partial (1.0) + SL remaining (1.0)
    non_end = [e for e in exits if e.label != "backtest_end"]
    assert len(non_end) == 2
    tp_exit = [e for e in non_end if e.price == pytest.approx(108.0, abs=1e-1)]
    sl_exit = [e for e in non_end if e.price == pytest.approx(100.0, abs=1e-1)]
    assert len(tp_exit) == 1
    assert len(sl_exit) == 1
    assert tp_exit[0].qty == pytest.approx(1.0, abs=1e-6)
    assert sl_exit[0].qty == pytest.approx(1.0, abs=1e-6)


# ── Tests: Close Mode + Exit Interaction ────────────────────────────────

class TestCloseModeExitInteraction:
  """Verify that close-mode entries + exit orders interact correctly.

  In close mode:
    1. Entry fills at bar N close
    2. _register_exits_for_close_fills() registers SL at bar N
    3. On bar N+1, _process_exit_orders() checks SL against N+1's OHLC
  """

  def test_close_mode_sl_fires_on_next_bar(self):
    """Close-mode entry at bar 2 close, SL should check on bar 3+."""
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal + fill at 105.5
      {"open": 105.5, "high": 106, "low": 104, "close": 105},     # bar 3: SL not hit (low=104 > 101)
      {"open": 105, "high": 105.5, "low": 100.5, "close": 101},   # bar 4: SL hit (low=100.5 <= 101)
    ]
    df = _make_ohlcv_custom(bars)
    config = _no_slip(process_orders_on_close=True)
    result = BacktestEngine(config).run(_LongCloseModePlusSL(), df, timeframe="30")

    entries = [t for t in result.trades if t.action == "entry"]
    assert len(entries) == 1
    assert entries[0].price == pytest.approx(105.5, abs=1e-6)  # filled at close
    assert entries[0].bar_index == 2

    exits = [t for t in result.trades if t.action == "exit" and t.label != "backtest_end"]
    assert len(exits) == 1
    assert exits[0].price == pytest.approx(101.0, abs=1e-6)  # SL fires
    assert exits[0].bar_index == 4

  def test_close_mode_entry_with_immediate_gap_sl(self):
    """Close-mode entry at bar 2 close, bar 3 opens below SL → gap-through."""
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal + fill at 105.5
      {"open": 99, "high": 100, "low": 98, "close": 99.5},        # bar 3: opens below SL=101
    ]
    df = _make_ohlcv_custom(bars)
    config = _no_slip(process_orders_on_close=True)
    result = BacktestEngine(config).run(_LongCloseModePlusSL(), df, timeframe="30")

    entries = [t for t in result.trades if t.action == "entry"]
    assert len(entries) == 1
    assert entries[0].price == pytest.approx(105.5, abs=1e-6)

    exits = [t for t in result.trades if t.action == "exit" and t.label != "backtest_end"]
    assert len(exits) == 1
    # Gap-through: bar_open (99.0) < SL (101.0) → fill at bar_open
    assert exits[0].price == pytest.approx(99.0, abs=1e-6)
    assert exits[0].bar_index == 3


# ── Tests: MAE/MFE on Exit ─────────────────────────────────────────────

class TestMAEMFEOnExit:
  """Verify MAE/MFE values are correct on exit fills."""

  def test_long_mae_mfe_calculated(self):
    """MAE = (lowest - entry) * qty, MFE = (highest - entry) * qty.

    Note: _process_exit_orders runs BEFORE _update_position_extremes each bar.
    So when SL fires on a bar, that bar's OHLC is NOT included in extremes.
    We account for this by keeping the SL trigger bar separate from the
    extreme-setting bars.
    """
    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},   # bar 2: signal
      {"open": 106, "high": 107, "low": 105.5, "close": 106.5},   # bar 3: fill at 106
      {"open": 106.5, "high": 110, "low": 103.5, "close": 108},   # bar 4: extremes set (low > 103)
      {"open": 108, "high": 108.5, "low": 104, "close": 107},     # bar 5: more extremes (low > 103)
      {"open": 107, "high": 107.5, "low": 102.5, "close": 103},   # bar 6: SL hit (low=102.5 < 103)
    ]
    df = _make_ohlcv_custom(bars)
    result = BacktestEngine(_no_slip()).run(_LongWithSLAt103(), df, timeframe="30")

    exits = [t for t in result.trades if t.action == "exit"]
    assert len(exits) == 1
    exit_fill = exits[0]
    assert exit_fill.bar_index == 6
    entry_price = 106.0
    qty = 1.0
    # Extremes tracked through bars 3-5 (bar 6 SL fires before update):
    # extreme_low: min(105.5, 103.5, 104) = 103.5
    # extreme_high: max(107, 110, 108.5) = 110
    expected_mae = (103.5 - entry_price) * qty  # -2.5
    expected_mfe = (110 - entry_price) * qty    # 4.0
    assert exit_fill.mae == pytest.approx(expected_mae, abs=0.5)
    assert exit_fill.mfe == pytest.approx(expected_mfe, abs=0.5)


# ── Tests: Portfolio Engine Exit Interaction ─────────────────────────────

class TestPortfolioEngineExits:
  """Verify exit orders work correctly in portfolio engine."""

  def test_portfolio_sl_fires(self):
    """Portfolio engine should process SL exit orders."""
    from backend.backtester.portfolio_engine import run_portfolio_backtest

    bars = [
      {"open": 104, "high": 105, "low": 103.5, "close": 104.5},
      {"open": 104.5, "high": 106, "low": 104, "close": 105.5},
      {"open": 105, "high": 106, "low": 104.5, "close": 105.5},
      {"open": 106, "high": 107, "low": 105, "close": 106.5},
      {"open": 106.5, "high": 107, "low": 105, "close": 106},
      {"open": 106, "high": 106.5, "low": 102.5, "close": 103},   # bar 5: SL hit
      {"open": 103, "high": 104, "low": 102, "close": 103.5},
    ]
    df = _make_ohlcv_custom(bars)
    source = '''
from backend.backtester.base_strategy import BaseStrategy

class TranspiledStrategy(BaseStrategy):
  def init(self):
    self._opens = self._df["open"].values
    self._highs = self._df["high"].values
    self._lows = self._df["low"].values
    self._closes = self._df["close"].values
    self._volumes = self._df["volume"].values
    self._timestamps = self._df.index
    self._hours = self._df.index.hour
    self._minutes = self._df.index.minute
    self._seconds = self._df.index.second
    import numpy as np
    _pm = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 1}
    self._dayofweek_pine = np.array([_pm[d] for d in self._df.index.dayofweek])
    self._days = self._df.index.day
    self._months = self._df.index.month
    self._years = self._df.index.year

  def next(self):
    if self._bar == 2:
      self.entry(id="port_sl", direction="long", qty=1.0)
      self.exit(id="sl_exit", from_entry="port_sl", stop=103.0)
'''
    config = _no_slip()
    result = run_portfolio_backtest(
      strategy_source=source,
      symbol_dfs={"TESTUSDT": df},
      config=config,
      timeframe="30",
    )

    trades = result.per_symbol_trades.get("TESTUSDT", [])
    exits = [t for t in trades if t.action == "exit" and t.label != "backtest_end"]
    assert len(exits) == 1
    assert exits[0].price == pytest.approx(103.0, abs=1e-6)
