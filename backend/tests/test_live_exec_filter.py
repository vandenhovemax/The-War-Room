"""Live execution-time filter parity tests.

Verifies that StrategyExecutor._extract_signals enforces the same NYT
hour/weekday exclusion as optimization's _filter_trades_exec, so live
trading cannot open entries outside the saved session window.

Covers:
  - No filter (empty sets)       → entry allowed
  - Hour in exclude list         → entry blocked
  - Weekday in exclude list      → entry blocked
  - Both in window               → entry allowed
  - Fail-closed: filter active but no bar_nyt → entry blocked
  - Exit signals never blocked (even at excluded hour)
  - Side filter still runs independently
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.exchange.strategy_executor import (
  ActivePortfolio,
  StrategyExecutor,
)


# --- Fakes --------------------------------------------------------------

@dataclass
class _FakeExitOrder:
  from_entry: str = ""
  stop: float = 0.0
  limit: float = 0.0
  trail_points: float = 0.0
  trail_offset: float = 0.0
  qty: float = 0.0


class _FakeStrategy:
  """Minimal stand-in exposing only what _extract_signals touches."""
  def __init__(self, pending: list[dict]) -> None:
    self._pending_orders = pending
    self._exit_orders: dict = {}


class _FakeExecutor(StrategyExecutor):
  """Overrides _has_open_position so we don't need a real DB session."""
  def __init__(self) -> None:  # type: ignore[no-untyped-def]
    # Skip the real __init__ — we don't need aggregator/db for this unit.
    self._active: dict[int, ActivePortfolio] = {}

  def _has_open_position(self, portfolio_id, symbol, direction):  # noqa
    return False


# --- Helpers ------------------------------------------------------------

def _entry(direction: str = "long", entry_id: str = "E1") -> dict:
  return {"action": "entry", "direction": direction, "id": entry_id, "qty": 1}


def _exit(entry_id: str = "E1") -> dict:
  return {"action": "exit", "direction": "long", "id": entry_id, "qty": 1}


def _run(
  pending: list[dict],
  *,
  side_filter: str = "Both",
  bar_nyt: datetime | None = None,
  excl_h: set[int] | None = None,
  excl_d: set[int] | None = None,
):
  ex = _FakeExecutor()
  strat = _FakeStrategy(pending)
  return ex._extract_signals(
    strat,
    portfolio_id=1,
    symbol="BTCUSDT",
    side_filter=side_filter,
    batch_id=None,
    bar_nyt=bar_nyt,
    exec_exclude_hours=excl_h,
    exec_exclude_days=excl_d,
  )


# --- Tests --------------------------------------------------------------

class TestExecFilterParity:

  def test_no_filter_entry_allowed(self):
    """Empty exclude sets ⇒ entry passes through regardless of hour."""
    sigs = _run([_entry()], bar_nyt=datetime(2026, 4, 20, 3, 0))
    assert any(s.action == "entry" for s in sigs)

  def test_excluded_hour_blocks_entry(self):
    """Bar hour=3 with exclude_hours={3} ⇒ entry blocked."""
    sigs = _run(
      [_entry()],
      bar_nyt=datetime(2026, 4, 20, 3, 0),  # NYT 3am
      excl_h={3},
    )
    assert not any(s.action == "entry" for s in sigs)

  def test_allowed_hour_passes(self):
    """Bar hour=10 with exclude_hours={3} ⇒ entry allowed."""
    sigs = _run(
      [_entry()],
      bar_nyt=datetime(2026, 4, 20, 10, 30),
      excl_h={3},
    )
    assert any(s.action == "entry" for s in sigs)

  def test_excluded_weekday_blocks_entry(self):
    """Monday (weekday=0) + exclude_days={0} ⇒ entry blocked."""
    # 2026-04-20 is a Monday
    monday = datetime(2026, 4, 20, 12, 0)
    assert monday.weekday() == 0
    sigs = _run([_entry()], bar_nyt=monday, excl_d={0})
    assert not any(s.action == "entry" for s in sigs)

  def test_nyt_9_to_16_window(self):
    """Session 30 real-world rule: NYT 9-16 only. Exclude {0-8, 17-23}."""
    excl = {0, 1, 2, 3, 4, 5, 6, 7, 8, 17, 18, 19, 20, 21, 22, 23}
    # 10am — allowed
    assert any(
      s.action == "entry"
      for s in _run([_entry()], bar_nyt=datetime(2026, 4, 20, 10, 0), excl_h=excl)
    )
    # 8am — blocked
    assert not any(
      s.action == "entry"
      for s in _run([_entry()], bar_nyt=datetime(2026, 4, 20, 8, 30), excl_h=excl)
    )
    # 17:00 — blocked (exclusive upper bound: hour 17 is outside 9-16)
    assert not any(
      s.action == "entry"
      for s in _run([_entry()], bar_nyt=datetime(2026, 4, 20, 17, 0), excl_h=excl)
    )

  def test_fail_closed_when_no_bar_timestamp(self):
    """If filter is configured but bar_nyt=None, block the entry (safety)."""
    sigs = _run([_entry()], bar_nyt=None, excl_h={3})
    assert not any(s.action == "entry" for s in sigs)

  def test_exits_never_blocked(self):
    """Exit signals must always pass through, regardless of the clock."""
    # Excluded hour — but this is an EXIT, so it must still emit.
    sigs = _run(
      [_exit()],
      bar_nyt=datetime(2026, 4, 20, 3, 0),
      excl_h={0, 1, 2, 3, 4, 5},
    )
    # _extract_signals also emits register_exit signals from _exit_orders;
    # here we only care that the exit-action signal is preserved:
    actions = [s.action for s in sigs]
    assert "exit" in actions

  def test_side_filter_runs_before_exec_filter(self):
    """Long-only asset + short entry at allowed hour ⇒ still blocked by side."""
    sigs = _run(
      [_entry(direction="short")],
      side_filter="Long",
      bar_nyt=datetime(2026, 4, 20, 12, 0),
    )
    assert not any(s.action == "entry" for s in sigs)


class TestActivePortfolioExecFields:

  def test_defaults_empty_sets(self):
    ap = ActivePortfolio(
      portfolio_id=1, strategy_source="", parameters={},
      base_tf="30", extra_tfs=[], assets=[],
    )
    assert ap.exec_exclude_hours == set()
    assert ap.exec_exclude_days == set()

  def test_exec_fields_populated(self):
    ap = ActivePortfolio(
      portfolio_id=1, strategy_source="", parameters={},
      base_tf="30", extra_tfs=[], assets=[],
      exec_exclude_hours={0, 1, 2},
      exec_exclude_days={0, 6},
    )
    assert ap.exec_exclude_hours == {0, 1, 2}
    assert ap.exec_exclude_days == {0, 6}


class TestOptimizationParity:
  """Verify the live filter matches backend.analysis.batch_selection._filter_trades_exec
  bit-for-bit: same hour check, same weekday semantics (Mon=0..Sun=6)."""

  def test_matches_filter_trades_exec(self):
    from backend.analysis.batch_selection import _filter_trades_exec

    # Build two synthetic "trades" with entry_time at NYT 3am and 10am
    trade_a = SimpleNamespace(entry_time=datetime(2026, 4, 20, 3, 0))   # Mon 3am
    trade_b = SimpleNamespace(entry_time=datetime(2026, 4, 20, 10, 0))  # Mon 10am

    excl_h = {3}
    excl_d = None
    kept = _filter_trades_exec([trade_a, trade_b], excl_h, excl_d)
    assert kept == [trade_b]  # 3am filtered out

    # Live filter should give the same verdict for the same inputs
    live_allows_a = any(
      s.action == "entry"
      for s in _run([_entry()], bar_nyt=trade_a.entry_time, excl_h=excl_h)
    )
    live_allows_b = any(
      s.action == "entry"
      for s in _run([_entry()], bar_nyt=trade_b.entry_time, excl_h=excl_h)
    )
    assert live_allows_a is False  # blocked — same as optimization
    assert live_allows_b is True   # allowed — same as optimization
