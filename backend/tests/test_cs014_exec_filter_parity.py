"""CS-014: the three inline exec-filter copies in ``wfo.py`` were
replaced by calls to ``is_excluded_by_exec_filter`` (canonical helper in
``batch_selection``). This test locks the semantics by re-implementing
each of the three old inline forms and asserting they produce the exact
same include/exclude decision as the canonical helper across a matrix
of inputs. If a future refactor drifts the canonical away from any of
the three original shapes, this test fails loudly.

Why this matters:
  - Site A: diagnostic counter — wrong count → wrong session-filter
    logs, but no trade-level impact.
  - Site B: _backfill_position_count — wrong count → WFAS windows show
    wrong position totals in saved-run detail views.
  - Site C: _cross_window_aggregate — wrong filter → mis-classifies
    OOS exec-eligible trades and therefore the headline WFAS number.
    This is the one that matters for the optimizer.

The test runs every (entry_time, exclude_hours, exclude_days) triple
against all three old inline forms and the canonical. Any divergence
— even a single input — fails.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.analysis.batch_selection import is_excluded_by_exec_filter


# ── Re-implementations of the three pre-CS-014 inline forms ──────────

def _site_a_diag_counter_old(
  entry_time, exclude_hours: set[int] | None, exclude_days: set[int] | None
) -> bool:
  """Old form from wfo.py:~677 (diagnostic `sum(...)` counter).

  Returned True if the trade COUNTS (included). Inverse of the filter.
  """
  return bool(
    entry_time
    and (not exclude_hours or entry_time.hour not in exclude_hours)
    and (not exclude_days or entry_time.weekday() not in exclude_days)
  )


def _site_b_backfill_old(
  entry_time, has_ef: bool, ex_hours: set[int] | None, ex_days: set[int] | None
) -> bool:
  """Old form from wfo.py:~1729 (`_backfill_position_count` tuple loop).

  Returns True if the trade should CONTINUE (be dropped). False = keep.
  Note: upstream code already guards ``if not entry_time: continue`` so
  this is only called with truthy entry_time.
  """
  if not has_ef:
    return False
  if ex_hours and entry_time.hour in ex_hours:
    return True
  if ex_days and entry_time.weekday() in ex_days:
    return True
  return False


def _site_c_cross_window_old(
  entry_time,
  has_exec_filter: bool,
  exec_exclude_hours: set[int] | None,
  exec_exclude_days: set[int] | None,
) -> bool:
  """Old form from wfo.py:~2965 (`_cross_window_aggregate`).

  Returns True if the trade should CONTINUE (be dropped). False = keep.
  Note: old code had ``and t.entry_time`` guard — None entry_time was
  NEVER filtered here (window-range check downstream handles it).
  """
  if not (has_exec_filter and entry_time):
    return False
  if exec_exclude_hours and entry_time.hour in exec_exclude_hours:
    return True
  if exec_exclude_days and entry_time.weekday() in exec_exclude_days:
    return True
  return False


# ── Input matrix ─────────────────────────────────────────────────────

MONDAY_2AM = datetime(2026, 3, 30, 2, 0)     # hour=2, weekday=0
MONDAY_9AM = datetime(2026, 3, 30, 9, 0)     # hour=9, weekday=0
TUESDAY_3PM = datetime(2026, 3, 31, 15, 0)   # hour=15, weekday=1
FRIDAY_11PM = datetime(2026, 4, 3, 23, 0)    # hour=23, weekday=4
SATURDAY_8AM = datetime(2026, 4, 4, 8, 0)    # hour=8, weekday=5

TIMES = [None, MONDAY_2AM, MONDAY_9AM, TUESDAY_3PM, FRIDAY_11PM, SATURDAY_8AM]

HOURS_CASES: list[set[int] | None] = [
  None, set(), {0, 1, 2}, {9, 10, 11, 12, 13, 14, 15, 16},
  {23}, set(range(0, 24)),  # empty & full
]

DAYS_CASES: list[set[int] | None] = [
  None, set(), {0}, {5, 6}, {0, 5, 6}, set(range(7)),
]


# ── Site A parity: diag counter ──────────────────────────────────────

@pytest.mark.parametrize("entry_time", TIMES)
@pytest.mark.parametrize("excl_hours", HOURS_CASES)
@pytest.mark.parametrize("excl_days", DAYS_CASES)
def test_site_a_diag_counter_parity(entry_time, excl_hours, excl_days):
  """Site A: old `sum(1 for t in trades if …)` counter included a trade
  iff it was NOT excluded by the filter. Canonical says True=exclude.
  """
  old_includes = _site_a_diag_counter_old(entry_time, excl_hours, excl_days)
  canonical_excludes = is_excluded_by_exec_filter(entry_time, excl_hours, excl_days)
  assert old_includes == (not canonical_excludes), (
    f"CS-014 Site A parity broken: entry_time={entry_time} "
    f"hours={excl_hours} days={excl_days} — old_includes={old_includes} "
    f"canonical_excludes={canonical_excludes}"
  )


# ── Site B parity: _backfill_position_count ──────────────────────────

@pytest.mark.parametrize("entry_time", [t for t in TIMES if t is not None])
@pytest.mark.parametrize("has_ef", [True, False])
@pytest.mark.parametrize("excl_hours", HOURS_CASES)
@pytest.mark.parametrize("excl_days", DAYS_CASES)
def test_site_b_backfill_parity(entry_time, has_ef, excl_hours, excl_days):
  """Site B replacement: ``has_ef and is_excluded_by_exec_filter(...)``.
  Old form returned True to drop. New form must match.
  """
  old_drops = _site_b_backfill_old(entry_time, has_ef, excl_hours, excl_days)
  new_drops = has_ef and is_excluded_by_exec_filter(entry_time, excl_hours, excl_days)
  assert old_drops == new_drops, (
    f"CS-014 Site B parity broken: entry_time={entry_time} "
    f"has_ef={has_ef} hours={excl_hours} days={excl_days} — "
    f"old_drops={old_drops} new_drops={new_drops}"
  )


# ── Site C parity: _cross_window_aggregate ───────────────────────────

@pytest.mark.parametrize("entry_time", TIMES)
@pytest.mark.parametrize("has_exec_filter", [True, False])
@pytest.mark.parametrize("excl_hours", HOURS_CASES)
@pytest.mark.parametrize("excl_days", DAYS_CASES)
def test_site_c_cross_window_parity(
  entry_time, has_exec_filter, excl_hours, excl_days
):
  """Site C replacement preserves the ``and t.entry_time`` guard — when
  entry_time is None, the filter is a no-op at this site (None trades
  are handled by the window-range check downstream, not here).
  """
  old_drops = _site_c_cross_window_old(entry_time, has_exec_filter, excl_hours, excl_days)
  new_drops = (
    has_exec_filter
    and entry_time is not None
    and is_excluded_by_exec_filter(entry_time, excl_hours, excl_days)
  )
  assert old_drops == new_drops, (
    f"CS-014 Site C parity broken: entry_time={entry_time} "
    f"has_exec_filter={has_exec_filter} hours={excl_hours} days={excl_days}"
    f" — old_drops={old_drops} new_drops={new_drops}"
  )


# ── Canonical filter + _filter_trades_exec integration ───────────────

def _make_trade(dt):
  return SimpleNamespace(entry_time=dt, symbol="BTCUSDT", side="Buy", net_profit=1.0)


def test_filter_trades_exec_uses_canonical_predicate():
  """``_filter_trades_exec`` must delegate to ``is_excluded_by_exec_filter``.
  Lock that by feeding the same input through both and comparing output.
  """
  from backend.analysis.batch_selection import _filter_trades_exec

  trades = [_make_trade(t) for t in TIMES if t is not None]
  excl_hours = {9, 10}
  excl_days = {5, 6}

  via_filter = _filter_trades_exec(trades, excl_hours, excl_days)
  via_predicate = [
    t for t in trades
    if not is_excluded_by_exec_filter(t.entry_time, excl_hours, excl_days)
  ]
  assert [t.entry_time for t in via_filter] == [t.entry_time for t in via_predicate]


def test_empty_filter_returns_input_unchanged():
  """The canonical fast-path: no hours and no days → return input list.
  Must not exclude any trade, including trades with None entry_time."""
  from backend.analysis.batch_selection import _filter_trades_exec

  trades = [_make_trade(t) for t in TIMES]
  assert _filter_trades_exec(trades, None, None) is trades
  assert _filter_trades_exec(trades, set(), set()) is trades
