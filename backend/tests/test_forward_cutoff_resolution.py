"""T-002 — `get_wfas_forward_cutoff` resolution.

Regression tests for the cross-wiring bug that caused the portfolio page
to over-append 3 years of phantom forward-period trades on P23/P24.

The bug: `wfo_per_window[-1].window_num` was being used as a lookup key
into `wfo_windows_meta`. But on hybrid portfolios those two structures
use DIFFERENT numbering schemes:
  * `wfo_windows_meta` = historical optimizer windows, numbered 1..N chrono.
  * `wfo_per_window`   = refresh continuation state, window_num=1 = latest
                         refresh cycle (a 1-day window written each night).

Joining per_window[-1].window_num=1 → windows_meta.window_num=1 landed on
the FIRST historical window (oos_end years in the past) instead of the
latest. The fix takes max(windows_meta.oos_end, effective_selected_records.dt).
"""
from __future__ import annotations

from datetime import date

from backend.analysis.portfolio_wfo_replay import get_wfas_forward_cutoff


# ── T1. Reproduces the P23/P24 bug (primary regression) ───────────────

def test_cross_wired_per_window_does_not_regress_cutoff():
  """P24-shaped input: per_window window_num=1 but windows_meta window_num=1
  is years in the past. Must not return the stale date."""
  settings = {
    "wfo_per_window": [
      # Refresh state: window_num=1 means "latest refresh cycle", 1-day window.
      {"window_num": 1, "oos_start": "2026-04-23", "oos_end": "2026-04-24"},
    ],
    "wfo_windows_meta": [
      {"window_num": 1, "oos_start": "2022-10-01", "oos_end": "2023-03-31"},
      {"window_num": 2, "oos_start": "2023-04-01", "oos_end": "2023-09-30"},
      {"window_num": 8, "oos_start": "2026-03-31", "oos_end": "2026-04-23"},
    ],
    "step2_result": {
      "selected_records": [
        {"dt": "2022-11-08T11:00:00", "pnl": 100, "symbol": "X", "side": "short"},
        {"dt": "2026-03-29T12:00:00", "pnl": 50,  "symbol": "Y", "side": "short"},
      ],
    },
  }
  result = get_wfas_forward_cutoff(settings)
  assert result is not None
  # HYBRID portfolios (selected_records present) ignore windows_meta
  # entirely (2026-05-25 fix): the trailing meta stub's oos_end is the
  # save date, which would create a coverage gap between the last actual
  # record and the stub end. The committed boundary is the records' max dt.
  assert result == date(2026, 3, 29), (
    f"Expected max selected_records dt (2026-03-29), got {result}. "
    f"The cross-wiring bug would return 2023-03-31 here; the pre-2026-05-25 "
    f"behaviour would return the meta stub's 2026-04-23."
  )


# ── T2. Windows-meta only (non-hybrid path) ───────────────────────────

def test_windows_meta_only_returns_latest_oos_end():
  # T-006 gate: non-hybrid meta entries only count when their window_num
  # appears in wfo_per_window (excludes the optimizer's trailing stub).
  settings = {
    "wfo_windows_meta": [
      {"window_num": 1, "oos_end": "2023-03-31"},
      {"window_num": 2, "oos_end": "2023-09-30"},
      {"window_num": 3, "oos_end": "2024-03-31"},
    ],
    "wfo_per_window": [
      {"window_num": 1}, {"window_num": 2}, {"window_num": 3},
    ],
  }
  assert get_wfas_forward_cutoff(settings) == date(2024, 3, 31)


# ── T3. Selected-records only (pure-hybrid path) ──────────────────────

def test_selected_records_only_returns_latest_dt():
  settings = {
    "step2_result": {
      "selected_records": [
        {"dt": "2023-05-01T00:00:00"},
        {"dt": "2024-07-15T12:30:00"},
        {"dt": "2024-01-10T08:00:00"},
      ],
    },
  }
  assert get_wfas_forward_cutoff(settings) == date(2024, 7, 15)


# ── T4. Both sources — takes the LATEST ───────────────────────────────

def test_takes_max_of_both_sources_records_newer():
  settings = {
    "wfo_windows_meta": [
      {"window_num": 1, "oos_end": "2023-03-31"},
    ],
    "step2_result": {
      "selected_records": [{"dt": "2026-03-29T12:00:00"}],
    },
  }
  assert get_wfas_forward_cutoff(settings) == date(2026, 3, 29)


def test_hybrid_ignores_meta_even_when_meta_is_newer():
  # 2026-05-25 fix: a hybrid portfolio's committed boundary is its records'
  # max dt. A newer meta entry is by construction a trailing stub (oos_end
  # = save date) and would create a coverage gap if honoured.
  settings = {
    "wfo_windows_meta": [
      {"window_num": 8, "oos_end": "2026-04-23"},
    ],
    "step2_result": {
      "selected_records": [{"dt": "2026-03-29T12:00:00"}],
    },
  }
  assert get_wfas_forward_cutoff(settings) == date(2026, 3, 29)


# ── T5. A3 merge — refresh_step2_result extends the cutoff ────────────

def test_refresh_step2_result_advances_cutoff():
  """A3: when refresh appends new records beyond save-time, the cutoff
  must advance accordingly via the merged view."""
  settings = {
    "step2_result": {
      "selected_records": [{"dt": "2026-03-29T12:00:00"}],
    },
    "refresh_step2_result": {
      "selected_records": [
        {"dt": "2026-03-29T12:00:00"},
        {"dt": "2026-04-20T14:00:00"},
      ],
    },
  }
  assert get_wfas_forward_cutoff(settings) == date(2026, 4, 20)


# ── T6. Edge cases — nothing returns None ─────────────────────────────

def test_empty_settings_returns_none():
  assert get_wfas_forward_cutoff({}) is None


def test_non_dict_returns_none():
  assert get_wfas_forward_cutoff("nope") is None
  assert get_wfas_forward_cutoff(None) is None


def test_malformed_dates_skipped_not_fatal():
  settings = {
    "wfo_windows_meta": [
      {"window_num": 1, "oos_end": "not-a-date"},
      {"window_num": 2, "oos_end": "2024-06-30"},
      {"window_num": 3},  # missing oos_end
    ],
    "wfo_per_window": [
      {"window_num": 1}, {"window_num": 2}, {"window_num": 3},
    ],
  }
  assert get_wfas_forward_cutoff(settings) == date(2024, 6, 30)


def test_malformed_records_skipped_not_fatal():
  settings = {
    "step2_result": {
      "selected_records": [
        {"dt": "bad"},
        {},
        {"dt": "2025-08-15T00:00:00"},
      ],
    },
  }
  assert get_wfas_forward_cutoff(settings) == date(2025, 8, 15)
