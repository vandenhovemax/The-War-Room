"""CS-063: a single canonical WFO rolling-window generator.

The audit found two near-identical implementations of the same
load-bearing primitive:

  * ``backend.analysis.wfo_window_gen.generate_rolling_windows``
    (3 prod call sites, 7 test files)
  * ``backend.analysis.wfo_asset_selection.generate_wfo_windows``
    (2 call sites, tests only)

Both took ``(is_months, oos_months, last_oos_end, data_start)``,
stepped by ``oos_months``, stopped at ``last_oos``, emitted the
same dict shape, and appended a final IS-only window terminated
by ``_today()``. A bug fixed in one would not reach the other —
and the optimiser WFO path vs the batch-selection WFO path could
emit different window grids for the same inputs once they drifted.

Fix: keep ``generate_rolling_windows`` as the canonical
implementation (more call sites, more tests). Replace
``generate_wfo_windows`` with a thin passthrough so existing
imports keep working.
"""
from __future__ import annotations

import inspect


def test_passthrough_preserves_window_shape():
  from backend.analysis.wfo_asset_selection import generate_wfo_windows
  from backend.analysis.wfo_window_gen import generate_rolling_windows

  kwargs = dict(
    is_months=3, oos_months=1,
    last_oos_end="2024-12-31", data_start="2023-01-01",
  )
  a = generate_wfo_windows(**kwargs)
  b = generate_rolling_windows(**kwargs)

  assert a == b, (
    "CS-063 REGRESSION: generate_wfo_windows and generate_rolling_"
    "windows no longer return identical output on identical inputs. "
    "The optimiser and batch-selection WFO paths can now emit "
    "different window grids silently."
  )


def test_passthrough_preserves_last_window_is_only():
  from backend.analysis.wfo_asset_selection import generate_wfo_windows

  windows = generate_wfo_windows(
    is_months=3, oos_months=1,
    last_oos_end="2024-12-31", data_start="2023-01-01",
  )
  assert windows, "CS-063 FIXTURE: no windows generated."
  last = windows[-1]
  # The canonical implementation appends a final IS-only window
  # (oos_start / oos_end = None) terminated by the clock cutoff.
  assert last["oos_start"] is None and last["oos_end"] is None, (
    "CS-063 REGRESSION: the final IS-only window is missing from "
    "generate_wfo_windows output. A user switching between WFO modes "
    "would see a different window count without any config change."
  )


def test_multiple_window_counts_equal_across_configs():
  """Cover several (IS, OOS, range) configurations — catches drift
  that only surfaces at specific cadences.
  """
  from backend.analysis.wfo_asset_selection import generate_wfo_windows
  from backend.analysis.wfo_window_gen import generate_rolling_windows

  configs = [
    dict(is_months=6, oos_months=2, last_oos_end="2024-06-30", data_start="2022-01-01"),
    dict(is_months=12, oos_months=3, last_oos_end="2025-03-31", data_start="2020-01-01"),
    dict(is_months=2, oos_months=1, last_oos_end="2024-12-31", data_start="2024-01-01"),
  ]
  for cfg in configs:
    a = generate_wfo_windows(**cfg)
    b = generate_rolling_windows(**cfg)
    assert a == b, (
      "CS-063 REGRESSION: generator divergence on config %r. Expected "
      "identical output — the passthrough must not shadow the "
      "canonical implementation." % cfg
    )


def test_no_duplicate_generate_rolling_implementation_left_behind():
  """The audit concern is silent drift. The fix requires that
  ``wfo_asset_selection.generate_wfo_windows`` is a passthrough
  (body < 10 lines, delegates to ``generate_rolling_windows``), not
  a reimplementation.
  """
  from backend.analysis import wfo_asset_selection as m
  src = inspect.getsource(m.generate_wfo_windows)

  # The passthrough must delegate. Accept either an aliased import
  # or a direct call to the canonical name.
  delegates = (
    "generate_rolling_windows(" in src
    or "_generate_rolling_windows_canonical(" in src
  )
  assert delegates, (
    "CS-063 REGRESSION: generate_wfo_windows no longer delegates to "
    "generate_rolling_windows — the two implementations are back and "
    "can drift apart silently."
  )

  # And the body must not re-implement the rolling loop.
  assert "while True:" not in src, (
    "CS-063 REGRESSION: generate_wfo_windows contains its own "
    "rolling loop again. This is exactly the duplication the audit "
    "flagged — replace the body with a thin passthrough."
  )
