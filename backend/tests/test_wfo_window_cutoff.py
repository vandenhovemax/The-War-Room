"""WFO window generation: deterministic ``today`` cutoff override.

CS-071 regression: ``generate_*_windows`` used to read ``_today()``
(a wall-clock helper) for the final IS-only window. Same settings run on
two different days produced differently-sized final windows — breaking
reproducible WFAS replay.

The fix threads an optional ``today: date | None = None`` kwarg through
all three generators. When passed, the final-window cutoff is pinned to
that date instead of wall clock. Default remains wall-clock-today to
preserve existing behavior.
"""
from __future__ import annotations

from datetime import date

from backend.analysis.wfo_window_gen import (
  generate_overlapping_windows,
  generate_rolling_windows,
  generate_static_windows,
)


# --- Rolling (Mode A) ---------------------------------------------------

def test_rolling_today_kwarg_pins_final_is_only_window():
  """Explicit cutoff → final window's is_end matches exactly."""
  windows = generate_rolling_windows(
    is_months=12, oos_months=3,
    last_oos_end="2024-12-31", data_start="2022-01-01",
    today=date(2025, 6, 30),
  )
  assert windows, "expected at least the final IS-only window"
  final = windows[-1]
  assert final["oos_start"] is None
  assert final["is_end"] == "2025-06-30"


def test_rolling_same_cutoff_produces_identical_output():
  """Determinism: two runs with the same cutoff return the same list."""
  kw = dict(is_months=12, oos_months=3, last_oos_end="2024-12-31", data_start="2022-01-01")
  a = generate_rolling_windows(**kw, today=date(2025, 6, 30))
  b = generate_rolling_windows(**kw, today=date(2025, 6, 30))
  assert a == b


def test_rolling_different_cutoff_shifts_final_window():
  """Different cutoffs → different final is_end."""
  kw = dict(is_months=12, oos_months=3, last_oos_end="2024-12-31", data_start="2022-01-01")
  a = generate_rolling_windows(**kw, today=date(2025, 6, 30))
  b = generate_rolling_windows(**kw, today=date(2025, 12, 31))
  assert a[-1]["is_end"] != b[-1]["is_end"]
  assert a[-1]["is_end"] == "2025-06-30"
  assert b[-1]["is_end"] == "2025-12-31"


# --- Static (Mode B) ----------------------------------------------------

def test_static_today_kwarg_pins_final_is_only_window():
  windows = generate_static_windows(
    is_start="2020-01-01", oos_end_fixed="2024-12-31",
    is_months=12, step_months=6, min_oos_months=3,
    today=date(2025, 6, 30),
  )
  assert windows[-1]["oos_start"] is None
  assert windows[-1]["is_end"] == "2025-06-30"


# --- Overlapping (Mode C) -----------------------------------------------

def test_overlapping_today_kwarg_pins_final_is_only_window():
  windows = generate_overlapping_windows(
    is_months=12, oos_total_months=6,
    last_oos_end="2024-12-31", data_start="2022-01-01",
    today=date(2025, 9, 15),
  )
  assert windows[-1]["oos_start"] is None
  assert windows[-1]["is_end"] == "2025-09-15"


# --- Backwards compat: no cutoff → wall clock --------------------------

def test_rolling_without_today_uses_wall_clock(monkeypatch):
  """Omitting ``today`` falls through to ``_today()`` — existing behavior."""
  import backend.analysis.wfo_window_gen as mod
  monkeypatch.setattr(mod, "_today", lambda: date(2030, 4, 1))
  windows = generate_rolling_windows(
    is_months=12, oos_months=3,
    last_oos_end="2024-12-31", data_start="2022-01-01",
  )
  assert windows[-1]["is_end"] == "2030-04-01"
