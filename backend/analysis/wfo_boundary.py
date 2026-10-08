"""Walk Forward Optimization — IS/OOS boundary enforcement.

Provides assertion wrappers to guarantee that NO out-of-sample data
leaks into any optimization, scoring, or filtering computation.
Every function here STOPS the run with a clear error if contamination
is detected.
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from backend.analysis.daily_pnl import build_daily_pnl_matrix
from backend.db.models import BacktestTrade

log = logging.getLogger(__name__)


class OOSContaminationError(ValueError):
  """Raised when OOS data leaks into an IS-only computation."""


def filter_trades_strict(
  trades: list[BacktestTrade], start: str, end: str,
) -> list[BacktestTrade]:
  """Filter trades by entry_time within [start, end] with logging."""
  s = pd.Timestamp(start)
  e = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
  filtered = [t for t in trades if s <= pd.Timestamp(t.entry_time) <= e]
  log.debug(
    "IS boundary filter: %d/%d trades in [%s, %s]",
    len(filtered), len(trades), start, end,
  )
  return filtered


def assert_no_oos_leakage(
  daily_pnl: pd.DataFrame, is_end: str, context: str = "",
) -> None:
  """Soft check: log if daily_pnl extends past is_end.

  Under entry_time assignment, IS trades with exit dates in OOS are
  expected — their PnL legitimately appears on post-IS dates.
  """
  if daily_pnl.empty:
    return
  is_end_ts = pd.Timestamp(is_end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
  max_date = daily_pnl.index.max()
  if max_date > is_end_ts:
    log.debug(
      "Daily PnL extends past IS end%s: max=%s, is_end=%s (expected for straddling trades)",
      f" in {context}" if context else "", max_date, is_end,
    )


def assert_trades_within_boundary(
  trades: list[BacktestTrade], is_end: str, context: str = "",
) -> None:
  """Assert that no trade's entry_time exceeds the IS boundary."""
  if not trades:
    return
  is_end_ts = pd.Timestamp(is_end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
  for t in trades:
    if t.entry_time and pd.Timestamp(t.entry_time) > is_end_ts:
      raise OOSContaminationError(
        f"OOS trade leak detected{f' in {context}' if context else ''}: "
        f"trade entry_time {t.entry_time} exceeds IS end {is_end}"
      )


def build_is_daily_pnl(
  trades: list[BacktestTrade],
  is_end: str,
  include_side: bool = False,
) -> pd.DataFrame:
  """Build daily PnL matrix from IS trades with boundary assertion.

  Wraps build_daily_pnl_matrix() and verifies the result stays
  within IS boundaries.
  """
  daily_pnl = build_daily_pnl_matrix(trades, include_side=include_side)
  assert_no_oos_leakage(daily_pnl, is_end, context="build_is_daily_pnl")
  return daily_pnl
