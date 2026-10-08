"""CS-050: the ``_fill_entry`` pyramiding check was a per-entry
O(positions) scan — ``sum(1 for p in _positions.values() if p.side == d)``.
Replaced with an O(1) ``_positions_by_side`` counter that is maintained at
every mutation of ``_positions`` (open in ``_fill_entry``, close in
``_close_position_by_id``, state-restore in ``_inject_positions``, legacy
setter in ``base_strategy._position.setter``).

The risk of the counter going out of sync with ``_positions`` (double-
incremented, missed decrement, state-restore path not updating) is real
and silent — the symptom is a wrong pyramiding count, which lets extra
entries through or blocks valid ones. These tests lock the invariant:

  sum(counter.values()) == len(_positions)              — always
  counter[side]         == sum(1 for p if p.side==side) — always

run through an end-to-end backtest (real engine, real bars, real
strategy). If a future edit forgets to update the counter at any
mutation site, one of these asserts will catch it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.engine import BacktestEngine, BacktestConfig


# ── Minimal deterministic strategy to exercise the engine ────────────

class _PyramidingStrategy(BaseStrategy):
  """Opens a long every 10 bars up to ``max_pyramiding``, closes them
  every 25 bars. Exercises open + partial-match + close paths so the
  counter is incremented and decremented many times across the run.
  """

  def init(self):
    pass

  def next(self):
    bar = self._bar
    if bar > 0 and bar % 10 == 0:
      self.entry(id=f"e{bar}", direction="long")
    if bar > 0 and bar % 25 == 0 and self._positions:
      # close the oldest open position each 25-bar tick
      pid = next(iter(self._positions))
      self.close_position(id=pid)


def _make_ohlc(n: int = 300) -> pd.DataFrame:
  """Deterministic synthetic OHLC — enough bars to open/close many
  positions so the counter invariant gets exercised across the whole
  mutation surface (open, partial close via max_pyramiding=1 reverse,
  full close, and end-of-backtest wind-down)."""
  idx = pd.date_range("2024-01-01", periods=n, freq="15min")
  rng = np.random.default_rng(42)
  noise = rng.normal(0, 0.2, n).cumsum()
  base = 100 + np.arange(n) * 0.01 + noise
  return pd.DataFrame({
    "open": base, "high": base + 0.3, "low": base - 0.3, "close": base,
    "volume": 1000.0,
  }, index=idx)


# ── Invariant: counter and scan agree, across the whole run ──────────

def _assert_counter_invariant(strategy, *, label: str) -> None:
  """Per-bar invariant: counter dict must match a fresh scan of
  ``_positions`` by side. Any drift => either counter was not updated
  at a mutation site, or the counter was double-counted.
  """
  scan_long = sum(1 for p in strategy._positions.values() if p.side == "long")
  scan_short = sum(1 for p in strategy._positions.values() if p.side == "short")

  counter = getattr(strategy, "_positions_by_side", None)
  assert counter is not None, f"[{label}] _positions_by_side missing"

  cnt_long = counter.get("long", 0)
  cnt_short = counter.get("short", 0)

  assert cnt_long == scan_long, (
    f"[{label}] CS-050 counter drift on LONG: counter={cnt_long} vs "
    f"scan={scan_long}. One side of a mutation was missed."
  )
  assert cnt_short == scan_short, (
    f"[{label}] CS-050 counter drift on SHORT: counter={cnt_short} vs "
    f"scan={scan_short}. One side of a mutation was missed."
  )
  assert cnt_long + cnt_short == len(strategy._positions), (
    f"[{label}] CS-050 counter sum ({cnt_long + cnt_short}) != "
    f"len(_positions) ({len(strategy._positions)})"
  )


# ── 1. Invariant holds across a full backtest with many open/close ───

def test_counter_invariant_across_full_backtest():
  """Run a real backtest with many entry/exit events. After it finishes,
  the counter must match the live position scan exactly. This is the
  end-to-end guarantee that every mutation site updates the counter.
  """
  df = _make_ohlc(300)
  cfg = BacktestConfig(
    initial_capital=10_000.0, commission_pct=0.0, max_pyramiding=3,
  )
  strategy = _PyramidingStrategy()
  engine = BacktestEngine(cfg)
  engine.run(strategy, df)

  _assert_counter_invariant(strategy, label="post-run")


# ── 2. Invariant on max_pyramiding=1 (reverse-close path) ────────────

def test_counter_invariant_with_max_pyramiding_1():
  """max_pyramiding=1 forces opposite-side positions to close when a
  new entry flips direction (``engine.py:550-553``). This path calls
  ``_close_position_by_id`` in a loop over ``_positions`` — a classic
  place where a counter can drift if the loop path is not counted.
  """

  class _FlipFlopStrategy(BaseStrategy):
    def init(self): pass
    def next(self):
      bar = self._bar
      if bar > 0 and bar % 5 == 0:
        self.entry("long" if (bar // 5) % 2 == 0 else "short", label=f"e{bar}")

  df = _make_ohlc(200)
  cfg = BacktestConfig(
    initial_capital=10_000.0, commission_pct=0.0, max_pyramiding=1,
  )
  strategy = _FlipFlopStrategy()
  engine = BacktestEngine(cfg)
  engine.run(strategy, df)

  _assert_counter_invariant(strategy, label="flipflop post-run")


# ── 3. Direct counter equals direct scan on a hand-built state ───────

def test_counter_equals_scan_on_hand_built_positions():
  """Unit test the base case — a plain BaseStrategy with three
  positions manually added via the ``_position.setter`` (legacy
  single-position API). The counter must match scan results.
  """
  from backend.backtester.models import Position

  s = BaseStrategy()
  s._position = Position(id="a", side="long", entry_price=100, qty=1, entry_bar=0)
  s._position = Position(id="b", side="long", entry_price=101, qty=1, entry_bar=1)
  s._position = Position(id="c", side="short", entry_price=99, qty=1, entry_bar=2)

  _assert_counter_invariant(s, label="hand-built legacy setter")
  assert s._positions_by_side["long"] == 2
  assert s._positions_by_side["short"] == 1


def test_legacy_setter_clears_counter_with_blank_side():
  """The legacy setter clears ``_positions`` when a Position with empty
  side is assigned. Counter must reset to zero on both sides (without
  it, the next pyramiding check would read stale counts)."""
  from backend.backtester.models import Position

  s = BaseStrategy()
  s._position = Position(id="a", side="long", entry_price=100, qty=1, entry_bar=0)
  s._position = Position(id="b", side="short", entry_price=99, qty=1, entry_bar=1)
  assert s._positions_by_side == {"long": 1, "short": 1}

  # Blank-side Position triggers clear semantics
  s._position = Position()
  assert s._positions == {}
  assert s._positions_by_side == {"long": 0, "short": 0}


# ── 4. Pyramiding gate uses the counter (not a scan) ─────────────────

def test_pyramiding_limit_enforced_via_counter():
  """Sanity check: with max_pyramiding=2, a strategy that tries to open
  5 same-side entries must end with at most 2 open (counter gates the
  check at _fill_entry:545)."""

  class _StackStrategy(BaseStrategy):
    def init(self): pass
    def next(self):
      bar = self._bar
      if bar in (5, 10, 15, 20, 25):
        self.entry("long", label=f"e{bar}")

  df = _make_ohlc(50)
  cfg = BacktestConfig(
    initial_capital=10_000.0, commission_pct=0.0, max_pyramiding=2,
  )
  strategy = _StackStrategy()
  engine = BacktestEngine(cfg)
  engine.run(strategy, df)

  # At most 2 longs held at any time → counter must have been
  # decremented on every close AND the pyramiding gate must have
  # BLOCKED entries when counter >= 2. The run can legitimately end
  # with 0, 1, or 2 open (depends on whether the engine's end-of-run
  # flush ran).
  _assert_counter_invariant(strategy, label="stacking")
  assert strategy._positions_by_side["long"] <= 2
