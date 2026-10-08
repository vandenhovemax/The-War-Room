from __future__ import annotations

from collections import Counter
from datetime import date, datetime

from dateutil.relativedelta import relativedelta


def _as_date(value) -> date | None:
  if isinstance(value, date) and not isinstance(value, datetime):
    return value
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, str):
    try:
      return date.fromisoformat(value)
    except ValueError:
      try:
        return datetime.fromisoformat(value).date()
      except ValueError:
        return None
  return None


def _parse_window(window: dict) -> tuple[date, date, int] | None:
  if not isinstance(window, dict):
    return None
  start = _as_date(window.get("oos_start"))
  end = _as_date(window.get("oos_end"))
  if start is None or end is None:
    return None
  span_days = (end - start).days
  if span_days <= 0:
    return None
  return start, end, span_days


def next_phase1_restart(windows_meta: list[dict] | None, today: date) -> date | None:
  current_day = _as_date(today)
  if current_day is None or not isinstance(windows_meta, list):
    return None

  windows = []
  for window in windows_meta:
    parsed = _parse_window(window)
    if parsed is not None:
      windows.append(parsed)

  if len(windows) < 2:
    return None

  windows.sort(key=lambda item: item[0])

  counts = Counter(span_days for _, _, span_days in windows)
  if not counts:
    return None
  max_count = max(counts.values())
  dominant_span_days = max(
    span_days for span_days, count in counts.items()
    if count == max_count
  )

  full_windows = [
    window for window in windows
    if window[2] >= dominant_span_days * 0.8
  ]
  if len(full_windows) < 2:
    return None

  span_months = max(1, round(dominant_span_days / 30.44))
  boundary = full_windows[0][0]
  step = relativedelta(months=span_months)
  while boundary < current_day:
    boundary = boundary + step
  return boundary
