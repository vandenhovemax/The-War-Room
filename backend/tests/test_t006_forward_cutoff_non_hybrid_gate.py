"""T-006 regression tests for `get_wfas_forward_cutoff` in
`backend/analysis/portfolio_wfo_replay.py`.

Bug surfaced after T-003 step 5: every non-hybrid portfolio in
production has a trailing W_N+1 entry in `wfo_windows_meta` (the
optimizer save flow's "live trailing" stub) without a matching entry
in `wfo_per_window`. Pre-T-003, a cross-wiring bug masked the gap by
returning the FIRST window's oos_end as the cutoff, which made
forward-append pull everything from years ago. T-003 step 5 fixed
the cross-wiring to use `max(meta.oos_end)`, which exposed the stub:
forward_start jumped past the committed period and trades in the
W_N.oos_end → stub.oos_end gap became invisible to BOTH
`_load_replay_non_hybrid` AND `load_wfas_forward_append`.

T-006 fix: for NON-HYBRID portfolios, only consider meta windows
whose `window_num` appears in `wfo_per_window`. Hybrid portfolios
(those with `selected_records`) keep T-003 step 5's logic intact.

These tests pin both invariants:
  1. Non-hybrid: meta-only trailing stub MUST be excluded from cutoff.
  2. Hybrid: cutoff continues to track max(meta + selected_records.dt).
"""
from __future__ import annotations

from datetime import date

from backend.analysis.portfolio_wfo_replay import get_wfas_forward_cutoff


def test_get_wfas_forward_cutoff_ignores_meta_only_trailing_window_for_non_hybrid():
  """For non-hybrid portfolios, a windows_meta entry that doesn't
  appear in wfo_per_window MUST be ignored. The cutoff must come from
  the LAST window that has BOTH a meta entry AND a per_window entry.
  """
  settings = {
    "wfo_windows_meta": [
      {"window_num": 1, "oos_start": "2026-01-01", "oos_end": "2026-03-31"},
      # Trailing stub: in meta, NOT in per_window. T-006 says "ignore".
      {"window_num": 2, "oos_start": "2026-03-31", "oos_end": "2026-04-22"},
    ],
    "wfo_per_window": [
      {"window_num": 1, "assets": ["B1_BTCUSDT_long"]},
      # No entry for window_num=2 — the stub.
    ],
    # No selected_records → non-hybrid path.
  }
  cutoff = get_wfas_forward_cutoff(settings)
  assert cutoff == date(2026, 3, 31), \
    f"expected 2026-03-31 (W1 only — W2 stub must be excluded), got {cutoff}"


def test_get_wfas_forward_cutoff_hybrid_path_unchanged():
  """Hybrid path (selected_records present) MUST keep T-003 step 5's
  behavior: cutoff = max of meta.oos_end ∪ selected_records.dt. The
  per_window gating only applies to non-hybrid.
  """
  settings = {
    "wfo_windows_meta": [
      {"window_num": 1, "oos_start": "2026-01-01", "oos_end": "2026-03-31"},
    ],
    "wfo_per_window": [{"window_num": 1, "assets": []}],
    "step2_result": {
      "selected_records": [
        {"dt": "2026-04-15", "run_id": 1, "symbol": "BTC", "side": "long"},
      ],
    },
  }
  cutoff = get_wfas_forward_cutoff(settings)
  assert cutoff == date(2026, 4, 15), \
    f"expected 2026-04-15 (max of meta + selected_records), got {cutoff}"


def test_get_wfas_forward_cutoff_non_hybrid_with_no_per_window_returns_none():
  """Defensive: if a non-hybrid portfolio has windows_meta but NO
  wfo_per_window at all, the gate excludes everything → cutoff is None.
  Caller treats None as "no committed forward boundary; nothing to
  show forward-period". Better than letting a stub-only meta drive
  forward_start past the data.
  """
  settings = {
    "wfo_windows_meta": [
      {"window_num": 1, "oos_start": "2026-01-01", "oos_end": "2026-03-31"},
    ],
    "wfo_per_window": [],
    # No selected_records → non-hybrid path.
  }
  cutoff = get_wfas_forward_cutoff(settings)
  assert cutoff is None, f"expected None (no per_window → nothing committed), got {cutoff}"
