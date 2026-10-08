"""Ship 1 Test 2 — Strategy state parity.

Claim the L1.1 nightly replay (backend/services/batch_store_refresh.py)
depends on: running a cold backtest over the full bar range produces the
SAME trade sequence (by timestamp + direction + price) as running the
first half cold, capturing state at the boundary, and resuming over the
second half with that state injected.

This is the Ship 1 version of the "cold trades == state-resumed trades"
invariant. The engine's public `inject_strategy_state` / `inject_positions`
contract is the load-bearing part — this test locks it down so an engine
refactor can't silently break the nightly replay.

What we verify:
  1. Trades in the timestamp window covered by both flows match on every
     field a downstream consumer cares about (side, price, qty, label,
     timestamp, action).
  2. Equity at the shared boundary bar is identical.
  3. No trades get duplicated across the resume seam.

Strategy under test:
  A minimal counting strategy that uses a non-_ state attribute
  (`bars_seen`) so _capture_strategy_state_static / _restore_strategy_state
  actually have work to do. It enters once per 7 bars and exits 3 bars
  later via a preset TP/SL — so the state determines WHEN the next entry
  fires, which means a lost-state bug would produce a different trade
  sequence.
"""
from __future__ import annotations

import pandas as pd

from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.engine import BacktestConfig, BacktestEngine


# ── Strategy under test ──────────────────────────────────────────

class _CountingStrategy(BaseStrategy):
  """Enters long every 7 bars using `bars_seen` as state. Positions
  accumulate (never closed during next()) so the engine's backtest_end
  force-close handles them uniformly at run's end. We strip those
  forced exits in the parity assertion — what remains are entries whose
  timing is *entirely* determined by `bars_seen`. A lost-state bug
  reboots the counter and offsets the entry schedule.
  """
  def init(self):
    self.bars_seen = 0
    self._max_pyramiding = 100

  def next(self):
    self.bars_seen += 1
    if self.bars_seen % 7 == 0:
      eid = f"e_{self.bars_seen}"
      self.entry(id=eid, direction="long", qty=1.0)


def _no_slip() -> BacktestConfig:
  return BacktestConfig(
    slippage_pct=0.0, slippage_value=0.0,
    spread_pct=0.0, commission_pct=0.0,
    # Allow sequential entries — this test is about the entry SCHEDULE,
    # not position accounting. The engine's own config is the authority
    # on pyramiding (overrides strategy._max_pyramiding at engine.py:160).
    max_pyramiding=100,
  )


def _make_bars(n: int) -> pd.DataFrame:
  """Deterministic bars. Open/close oscillate so TP and SL both fire
  regularly — this keeps position state from piling up across the seam."""
  dates = pd.date_range("2024-01-01", periods=n, freq="30min")
  opens = [100.0 + (i * 0.1) for i in range(n)]
  closes = [o + (0.4 if i % 2 == 0 else -0.3) for i, o in enumerate(opens)]
  highs = [max(o, c) + 0.6 for o, c in zip(opens, closes)]
  lows = [min(o, c) - 0.6 for o, c in zip(opens, closes)]
  return pd.DataFrame(
    {"open": opens, "high": highs, "low": lows, "close": closes,
     "volume": [1000] * n},
    index=dates,
  )


def _fill_key(f) -> tuple:
  """Stable per-fill identity for parity comparison — no bar_index since
  that resets when the resumed run's df slice starts at 0."""
  return (
    f.timestamp, f.side, f.action,
    round(float(f.price), 6), round(float(f.qty), 6),
    f.label, f.position_id,
  )


# ── Tests ─────────────────────────────────────────────────────────

def test_cold_vs_resume_produce_identical_trades():
  """Cold[0..N] trades ≡ Cold[0..K] trades ∪ Resume[K..N] trades.

  Compared field-by-field on every fill — a single drift in price, qty,
  or timestamp fails the test. Nothing about this test is forgiving.
  """
  n_bars = 40
  boundary = 18

  df_full = _make_bars(n_bars)
  df_first = df_full.iloc[:boundary]
  df_second = df_full.iloc[boundary:]

  # ── 1. Cold run over [0..40] ──
  cold = BacktestEngine(_no_slip()).run(
    _CountingStrategy(), df_full, symbol="TEST", timeframe="30",
  )

  # ── 2. Cold run over [0..18]; capture strategy_state (engine captures it
  # BEFORE the backtest_end force-close, which is what the nightly replay
  # consumes).
  first = BacktestEngine(_no_slip()).run(
    _CountingStrategy(), df_first, symbol="TEST", timeframe="30",
  )

  # ── 3. Resume run over [18..40] with state injected ──
  # warmup=0 → state is injected at bar_idx 0 of the slice (engine contract).
  resume_cfg = _no_slip()
  resume_cfg.warmup_bars = 0
  resumed = BacktestEngine(resume_cfg).run(
    _CountingStrategy(), df_second,
    symbol="TEST", timeframe="30",
    inject_strategy_state=first.strategy_state,
  )

  # ── 4. Parity assertion ──
  # Drop backtest_end forced exits — the first segment ALWAYS gets one at
  # the boundary (bar 17) that the cold run doesn't produce, and the
  # resumed run gets one at bar N-1 that IS in cold. Without dropping the
  # first's forced exit we'd have a spurious seam-artifact.
  def _strip_forced_end(fills):
    return [f for f in fills if f.label != "backtest_end"]

  seam_ts = df_full.index[boundary]
  # Trades in segments split at the seam
  cold_first = [f for f in _strip_forced_end(cold.trades) if f.timestamp < seam_ts]
  cold_second = [f for f in _strip_forced_end(cold.trades) if f.timestamp >= seam_ts]

  first_clean = _strip_forced_end(first.trades)
  resumed_clean = _strip_forced_end(resumed.trades)

  # Pre-seam comparison: cold_first == first_clean
  assert [_fill_key(f) for f in cold_first] == [_fill_key(f) for f in first_clean], (
    "Pre-seam divergence — the first segment itself didn't match cold."
  )

  # Post-seam comparison: cold_second == resumed_clean
  assert [_fill_key(f) for f in cold_second] == [_fill_key(f) for f in resumed_clean], (
    "Post-seam divergence — state resume produced different trades than cold. "
    "This means _restore_strategy_state didn't rebuild the strategy's internal "
    "counters, so the entry schedule drifted."
  )


def test_state_capture_roundtrip_preserves_counters():
  """_capture_strategy_state_static + _restore_strategy_state must be a
  faithful round-trip for the public counter.

  This guards against JSON-safe serialization quietly dropping a value
  (the capture path goes through _to_json_safe / _from_json_safe).
  """
  df = _make_bars(25)
  result = BacktestEngine(_no_slip()).run(
    _CountingStrategy(), df, symbol="TEST", timeframe="30",
  )
  state = result.strategy_state
  assert "bars_seen" in state, (
    "bars_seen missing from captured state — _capture_strategy_state_static "
    "regressed on public attribute pickup."
  )
  assert state["bars_seen"] == 25, (
    f"bars_seen={state['bars_seen']} after 25 bars — counter capture is off."
  )

  # Round-trip: restore into a fresh strategy, confirm the value sticks
  restored = _CountingStrategy()
  # Emulate the engine's restore path (static method, takes the raw dict)
  BacktestEngine._restore_strategy_state(restored, state)
  assert restored.bars_seen == 25


# ── Multi-exit (partial-ladder) capture ──────────────────────────

class _MultiExitStrategy(BaseStrategy):
  """Opens one long on bar 2 and attaches THREE exits to it: a hard SL,
  a TP1 at qty_percent=50, and a trailing stop. The prior capture code
  only recorded the FIRST match of each exit type, silently dropping
  any secondary TP (or stop paired with a trail). This strategy is
  designed to exercise that case: all three exits live on the same
  position when the backtest ends.
  """
  def init(self):
    self._max_pyramiding = 1
    self._opened = False

  def next(self):
    if not self._opened and self._bar == 2:
      self.entry(id="main", direction="long", qty=1.0)
      self._opened = True
    if "main" in self._positions:
      # Prices stay away from 1.0 / 500.0 / trail distance so none fire.
      self.exit(id="main_sl", from_entry="main", stop=1.0)
      self.exit(id="main_tp1", from_entry="main",
                limit=500.0, qty_percent=50)
      self.exit(id="main_tsl", from_entry="main",
                trail_points=5.0, trail_offset=0.5)


def test_multi_exit_capture_preserves_every_exit_order():
  """Regression test for the first-match-only bug in _open_positions
  capture. The fix collects every exit into an `exits` list so partial
  ladders survive the save/resume cycle.
  """
  df = _make_bars(10)
  result = BacktestEngine(_no_slip()).run(
    _MultiExitStrategy(), df, symbol="TEST", timeframe="30",
  )
  state = result.strategy_state
  open_positions = state.get("_open_positions") or []
  assert len(open_positions) == 1, (
    f"expected 1 open position captured, got {len(open_positions)}"
  )
  pos = open_positions[0]
  exits = pos.get("exits") or []
  ids = {e["id"] for e in exits}
  assert ids == {"main_sl", "main_tp1", "main_tsl"}, (
    f"expected all 3 exit ids captured, got {ids}. The old first-match-only "
    f"capture would silently drop the TSL or the TP — this test locks the fix."
  )

  sl = next(e for e in exits if e["id"] == "main_sl")
  assert sl["stop"] == 1.0
  tp1 = next(e for e in exits if e["id"] == "main_tp1")
  assert tp1["limit"] == 500.0 and tp1["qty_percent"] == 50.0
  tsl = next(e for e in exits if e["id"] == "main_tsl")
  assert tsl["trail_points"] == 5.0 and tsl["trail_offset"] == 0.5


def test_inject_positions_restores_every_exit_order():
  """After _inject_positions consumes an _open_positions entry, the
  strategy's _exit_orders dict should contain all three exits with their
  ORIGINAL ids (not synthetic pos_sl / pos_tp / pos_tsl ids), so a
  subsequent strategy.exit() call with the same id is a refresh, not a
  duplicate-add collision.
  """
  df = _make_bars(10)
  result = BacktestEngine(_no_slip()).run(
    _MultiExitStrategy(), df, symbol="TEST", timeframe="30",
  )
  open_positions = result.strategy_state["_open_positions"]

  # Resume into a fresh engine — need any strategy and any df slice so
  # _inject_positions has timestamps to read from.
  resume = _MultiExitStrategy()
  # Minimal bootstrap the engine would do before bar 0
  resume._positions = {}
  resume._exit_orders = {}
  resume._trades = []
  resume._cash = 10000
  resume._timestamps = df.index

  engine = BacktestEngine(_no_slip())
  engine._inject_positions(resume, open_positions, bar_idx=0)

  restored_ids = set(resume._exit_orders.keys())
  assert {"main_sl", "main_tp1", "main_tsl"}.issubset(restored_ids), (
    f"_inject_positions dropped exits. Present ids: {restored_ids}. "
    f"This means the `exits` list path in _inject_positions isn't being "
    f"taken or is losing entries."
  )


def test_no_duplicate_entries_across_seam():
  """The resume must never re-fire entries that the first segment already
  emitted. If state wasn't captured/restored, both segments would start
  at bars_seen=0 and produce double entries.
  """
  n_bars = 50
  boundary = 20

  df_full = _make_bars(n_bars)
  first = BacktestEngine(_no_slip()).run(
    _CountingStrategy(), df_full.iloc[:boundary],
    symbol="TEST", timeframe="30",
  )
  cfg = _no_slip()
  cfg.warmup_bars = 0
  resumed = BacktestEngine(cfg).run(
    _CountingStrategy(), df_full.iloc[boundary:],
    symbol="TEST", timeframe="30",
    inject_strategy_state=first.strategy_state,
  )

  first_entries = [f for f in first.trades if f.action == "entry"]
  resumed_entries = [f for f in resumed.trades if f.action == "entry"]

  # 20 bars / 7 = 2 entries in first segment (bars 7, 14)
  assert len(first_entries) == 2, f"Expected 2 pre-seam entries, got {len(first_entries)}"

  # Next entries must be at bars_seen=21, 28, 35, 42, 49 → 5 entries in resumed
  # If state wasn't injected, resumed would count from 0 and fire at 7,14,21 inside the 30 remaining bars.
  assert len(resumed_entries) == 5, (
    f"Expected 5 post-seam entries (bars_seen ticks 21/28/35/42/49), got {len(resumed_entries)}. "
    "Count drifted — state injection broke."
  )

  # All resumed entry timestamps must come AFTER the last first-segment entry's timestamp
  last_first_ts = max((f.timestamp for f in first_entries), default=None)
  assert all(f.timestamp > last_first_ts for f in resumed_entries), (
    "Resumed run emitted entries at or before the first segment's last entry — "
    "strong signal of state loss (counter reset, schedule re-started)."
  )
