"""Walk Forward Optimization — Window generation for all time modes.

Three modes:
  - Rolling (Normal): fixed IS + OOS lengths, step = OOS length
  - Static: fixed IS start + fixed OOS end, IS grows / OOS shrinks
  - Overlapping: IS + OOS split into H1/H2, step = OOS/2
"""
from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta

from backend.utils.clock import today as _today


def generate_rolling_windows(
  is_months: int,
  oos_months: int,
  last_oos_end: str,
  data_start: str,
  today: date | None = None,
) -> list[dict]:
  """Mode A: Normal rolling windows.

  Step = oos_months. Final window is IS-only (no OOS).

  ``today`` — optional cutoff for the final IS-only window. When omitted,
  the wall-clock today from ``backend.utils.clock`` is used. Pass an
  explicit ``date`` to make window generation deterministic (e.g. for
  reproducible WFAS replay or unit tests) without touching global state.
  """
  last_oos = date.fromisoformat(last_oos_end)
  d_start = date.fromisoformat(data_start)
  cutoff = today if today is not None else _today()
  windows: list[dict] = []
  win_num = 1
  is_start = d_start

  while True:
    is_end = is_start + relativedelta(months=is_months) - relativedelta(days=1)
    oos_start = is_end + relativedelta(days=1)
    oos_end = oos_start + relativedelta(months=oos_months) - relativedelta(days=1)

    if oos_end > last_oos:
      break

    windows.append({
      "window_num": win_num,
      "is_start": is_start.isoformat(),
      "is_end": is_end.isoformat(),
      "oos_start": oos_start.isoformat(),
      "oos_end": oos_end.isoformat(),
    })
    win_num += 1
    is_start = is_start + relativedelta(months=oos_months)

  # Final IS-only window (extends to today / cutoff)
  final_is_start = is_start
  final_is_end = cutoff
  if final_is_end > final_is_start:
    windows.append({
      "window_num": win_num,
      "is_start": final_is_start.isoformat(),
      "is_end": final_is_end.isoformat(),
      "oos_start": None,
      "oos_end": None,
    })

  return windows


def generate_static_windows(
  is_start: str,
  oos_end_fixed: str,
  is_months: int,
  step_months: int = 3,
  min_oos_months: int = 3,
  today: date | None = None,
) -> list[dict]:
  """Mode B: Static — fixed IS start, fixed OOS end.

  IS grows by step_months each iteration, OOS shrinks.
  Stops when OOS would be shorter than min_oos_months.

  ``today`` — optional cutoff for the final IS-only window. See
  ``generate_rolling_windows`` for details.
  """
  d_start = date.fromisoformat(is_start)
  oos_end = date.fromisoformat(oos_end_fixed)
  cutoff = today if today is not None else _today()
  windows: list[dict] = []
  win_num = 1
  extra_months = 0

  while True:
    is_end = d_start + relativedelta(months=is_months + extra_months) - relativedelta(days=1)
    oos_start = is_end + relativedelta(days=1)

    # Check if remaining OOS period is long enough
    remaining_oos = relativedelta(oos_end, oos_start)
    remaining_months = remaining_oos.years * 12 + remaining_oos.months
    if oos_start >= oos_end or remaining_months < min_oos_months:
      break

    windows.append({
      "window_num": win_num,
      "is_start": d_start.isoformat(),
      "is_end": is_end.isoformat(),
      "oos_start": oos_start.isoformat(),
      "oos_end": oos_end.isoformat(),
    })
    win_num += 1
    extra_months += step_months

  # Final IS-only window (IS covers full period to today / cutoff)
  if windows:
    final_is_end = cutoff
    if final_is_end > d_start:
      windows.append({
        "window_num": win_num,
        "is_start": d_start.isoformat(),
        "is_end": final_is_end.isoformat(),
        "oos_start": None,
        "oos_end": None,
      })

  return windows


def generate_overlapping_windows(
  is_months: int,
  oos_total_months: int,
  last_oos_end: str,
  data_start: str,
  today: date | None = None,
) -> list[dict]:
  """Mode C: Overlapping dual-phase windows.

  OOS is split into H1 (first half) and H2 (second half).
  Step = oos_total_months / 2. OOS-H1 of seg N overlaps with
  OOS-H2 of seg N-1.

  Example (is=12mo, oos=6mo):
    Seg 1: IS Jan-Dec 2024 | H1 Jan-Mar 2025 | H2 Apr-Jun 2025
    Seg 2: IS Apr 2024-Mar 2025 | H1 Apr-Jun 2025 | H2 Jul-Sep 2025

  ``today`` — optional cutoff for the final IS-only window. See
  ``generate_rolling_windows`` for details.
  """
  last_oos = date.fromisoformat(last_oos_end)
  d_start = date.fromisoformat(data_start)
  cutoff = today if today is not None else _today()
  half_months = oos_total_months // 2
  if half_months < 1:
    half_months = 1

  windows: list[dict] = []
  win_num = 1
  is_start = d_start

  while True:
    is_end = is_start + relativedelta(months=is_months) - relativedelta(days=1)
    # H1 starts right after IS
    h1_start = is_end + relativedelta(days=1)
    h1_end = h1_start + relativedelta(months=half_months) - relativedelta(days=1)
    # H2 starts right after H1
    h2_start = h1_end + relativedelta(days=1)
    h2_end = h2_start + relativedelta(months=half_months) - relativedelta(days=1)

    if h2_end > last_oos:
      break

    windows.append({
      "window_num": win_num,
      "is_start": is_start.isoformat(),
      "is_end": is_end.isoformat(),
      "oos_start": h1_start.isoformat(),
      "oos_end": h2_end.isoformat(),
      "oos_h1_start": h1_start.isoformat(),
      "oos_h1_end": h1_end.isoformat(),
      "oos_h2_start": h2_start.isoformat(),
      "oos_h2_end": h2_end.isoformat(),
    })
    win_num += 1
    # Step forward by half the OOS period
    is_start = is_start + relativedelta(months=half_months)

  # Final IS-only window
  final_is_start = is_start
  final_is_end = cutoff
  if final_is_end > final_is_start:
    windows.append({
      "window_num": win_num,
      "is_start": final_is_start.isoformat(),
      "is_end": final_is_end.isoformat(),
      "oos_start": None,
      "oos_end": None,
      "oos_h1_start": None,
      "oos_h1_end": None,
      "oos_h2_start": None,
      "oos_h2_end": None,
    })

  return windows
