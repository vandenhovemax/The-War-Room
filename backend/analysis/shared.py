"""Shared utilities used across multiple optimizer and analysis modules."""
from __future__ import annotations

import math

import pandas as pd


def _asset_pf(series: pd.Series) -> float:
  """Profit factor for a single asset's daily PnL series.

  Strict-zero convention: gp sums strictly positive PnL, gl sums strictly
  negative PnL. Capped at 10.0. All optimizer/WFO call sites import this
  single implementation (see Tier 1.3 audit / X01).
  """
  gp = float(series[series > 0].sum())
  gl = abs(float(series[series < 0].sum()))
  return min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)


def daily_ann_factor(
  index: pd.DatetimeIndex | None = None,
  pnl: pd.Series | None = None,
) -> float:
  """Annualization factor for daily PnL series (Sortino, Sharpe, etc.).

  Detects whether the data trades on weekends (crypto = 365 trading days)
  or is weekday-only (equities = 252 trading days).

  Detection logic: checks whether any Saturday/Sunday dates have non-zero
  PnL values. Zero-filled weekends (from build_daily_pnl_matrix reindex)
  don't count — only actual trading activity does.

  If no index/pnl is provided, defaults to 365 (crypto assumption).
  """
  if index is None or len(index) < 10:
    return math.sqrt(365)

  try:
    day_of_week = index.dayofweek  # Mon=0 .. Sun=6
    weekend_mask = day_of_week >= 5

    if pnl is not None and len(pnl) == len(index):
      # Check if any weekend day has actual trading activity (non-zero PnL)
      has_weekend_trading = bool((pnl[weekend_mask].abs() > 0).any())
    else:
      # Fallback: just check if weekend dates exist in the index
      has_weekend_trading = bool(weekend_mask.any())
  except Exception:
    return math.sqrt(365)

  return math.sqrt(365) if has_weekend_trading else math.sqrt(252)
