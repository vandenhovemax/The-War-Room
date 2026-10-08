"""CS-071: WFO window generation must be deterministic across
calendar days when the caller pins ``replay_as_of``.

The three window generators in ``wfo_window_gen`` end with a final
IS-only window that extends to ``clock.today()``. Re-running a saved
preset tomorrow therefore adds one more day of IS data, changing the
candidate pool for the trailing window, Step 1 picks, and OOS
evaluation of earlier windows whose IS overlaps.

Fix: each generator accepts ``today: date | None = None``. When set,
it replaces ``clock.today()`` — every replay of a saved preset pins
the same cutoff and produces identical windows.

This test asserts that same inputs + same ``replay_as_of`` = same
windows, on two independent calls with arbitrary calendar deltas
in between.
"""
from __future__ import annotations

from datetime import date

from backend.analysis.wfo_window_gen import (
  generate_overlapping_windows,
  generate_rolling_windows,
  generate_static_windows,
)


def test_rolling_windows_deterministic_when_today_pinned():
  kw = dict(
    is_months=12,
    oos_months=3,
    last_oos_end="2024-12-31",
    data_start="2021-01-01",
  )
  a = generate_rolling_windows(**kw, today=date(2025, 6, 30))
  b = generate_rolling_windows(**kw, today=date(2025, 6, 30))
  assert a == b, (
    "CS-071 REGRESSION: rolling windows are not deterministic with "
    "pinned today — two identical calls produced different output. "
    "The generator must be a pure function of its arguments when "
    "today is supplied."
  )


def test_rolling_windows_differ_across_pinned_days():
  """Sanity: different pinned todays must actually produce different
  trailing windows (otherwise the fix is a no-op).
  """
  kw = dict(
    is_months=12,
    oos_months=3,
    last_oos_end="2024-12-31",
    data_start="2021-01-01",
  )
  a = generate_rolling_windows(**kw, today=date(2025, 6, 30))
  b = generate_rolling_windows(**kw, today=date(2025, 12, 31))
  # Trailing IS-only window must differ between the two
  trailing_a = [w for w in a if w.get("oos_start") is None]
  trailing_b = [w for w in b if w.get("oos_start") is None]
  assert trailing_a != trailing_b, (
    "CS-071 FIXTURE BROKEN: different pinned todays produce identical "
    "trailing windows — the regression this test is meant to catch "
    "cannot be detected."
  )


def test_static_windows_deterministic_when_today_pinned():
  kw = dict(
    is_start="2022-01-01",
    oos_end_fixed="2025-01-01",
    is_months=12,
    step_months=3,
    min_oos_months=3,
  )
  a = generate_static_windows(**kw, today=date(2025, 6, 30))
  b = generate_static_windows(**kw, today=date(2025, 6, 30))
  assert a == b, (
    "CS-071 REGRESSION: static windows are not deterministic with "
    "pinned today."
  )


def test_overlapping_windows_deterministic_when_today_pinned():
  kw = dict(
    is_months=12,
    oos_total_months=6,
    last_oos_end="2024-12-31",
    data_start="2021-01-01",
  )
  a = generate_overlapping_windows(**kw, today=date(2025, 6, 30))
  b = generate_overlapping_windows(**kw, today=date(2025, 6, 30))
  assert a == b, (
    "CS-071 REGRESSION: overlapping windows are not deterministic "
    "with pinned today."
  )


def test_wfo_request_exposes_replay_as_of():
  """The WFO request schema must expose ``replay_as_of`` so callers
  can persist it alongside the saved preset.
  """
  from backend.api.routes.wfo import WFOAssetSelectionRequest
  fields = WFOAssetSelectionRequest.model_fields
  assert "replay_as_of" in fields, (
    "CS-071 REGRESSION: WFOAssetSelectionRequest no longer exposes "
    "``replay_as_of``. Without this field, saved presets cannot pin "
    "the final-window cutoff — replays silently diverge across days."
  )
  # Default must be None (additive — the generator falls back to
  # clock.today() only when the field is absent)
  default = fields["replay_as_of"].default
  assert default is None, (
    f"CS-071 REGRESSION: replay_as_of default is {default!r}, "
    "expected None. A non-None default would silently freeze all "
    "WFO runs to the hardcoded value."
  )
