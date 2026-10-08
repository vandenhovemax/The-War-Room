"""Quality First portfolio construction.

Starts with highest-quality assets by seed ranking, then removes
correlated pairs — dropping the weaker one. Quality is the goal,
correlation is the guardrail.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.scoring import calculate_stability
from backend.analysis.shared import _asset_pf  # noqa: F401
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def _portfolio_metrics(daily_pnl: pd.DataFrame, assets: list[str]) -> dict:
  """Compute basic portfolio metrics for scoring."""
  valid = [a for a in assets if a in daily_pnl.columns]
  if not valid:
    return {"score": float("inf")}
  combined = daily_pnl[valid].sum(axis=1)
  stab = float(calculate_stability(combined))
  return {"score": 1 - stab, "stability": stab}


def quality_first_select(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_assets: int,
  seed_scores: dict[str, float],
  n_portfolios: int = 3,
  corr_threshold: float = 0.6,
) -> list[list[str]]:
  """Quality-first asset selection.

  1. Rank ALL assets by seed score (lower = better quality).
  2. Take top N candidates.
  3. Remove correlated pairs (drop weaker one).
  4. Backfill from remaining pool.
  5. Repeat until stable.
  """
  # Filter to profitable assets
  all_assets = [a for a in daily_pnl.columns if _asset_pf(daily_pnl[a]) >= 1.0]
  # Rank by seed score
  ranked = sorted(all_assets, key=lambda a: seed_scores.get(a, float("inf")))

  logger.info("  quality_first: %d assets ranked, target=%d, threshold=%.2f",
              len(ranked), n_assets, corr_threshold)

  results: list[list[str]] = []
  seen: set[tuple[str, ...]] = set()

  for variant in range(n_portfolios):
    # Create variant pool
    if variant == 0:
      pool = list(ranked)
    elif variant == 1:
      # Reverse tie-order for assets with similar scores
      pool = list(ranked)
      # Swap adjacent pairs with similar scores
      for i in range(0, len(pool) - 1, 2):
        a, b = pool[i], pool[i + 1]
        if abs(seed_scores.get(a, 0) - seed_scores.get(b, 0)) < 0.5:
          pool[i], pool[i + 1] = b, a
    else:
      # Exclude top 3 and rebuild
      pool = list(ranked[3:])

    selected = _build_quality_portfolio(
      pool, corr_matrix, n_assets, seed_scores, corr_threshold,
    )
    key = tuple(sorted(selected))
    if key not in seen:
      seen.add(key)
      results.append(selected)

  # Fallback if not enough variants
  if not results:
    results.append(ranked[:n_assets])

  return results


def _build_quality_portfolio(
  pool: list[str],
  corr_matrix: pd.DataFrame,
  n_assets: int,
  seed_scores: dict[str, float],
  corr_threshold: float,
) -> list[str]:
  """Build one quality-first portfolio."""
  if len(pool) <= n_assets:
    return list(pool)

  # Start with top N by quality
  selected = list(pool[:n_assets])
  pool_remaining = list(pool[n_assets:])

  # Iteratively remove correlated pairs
  max_iterations = 50
  for _ in range(max_iterations):
    worst_pair = None
    worst_corr = corr_threshold

    for i in range(len(selected)):
      for j in range(i + 1, len(selected)):
        a, b = selected[i], selected[j]
        if a in corr_matrix.columns and b in corr_matrix.columns:
          c = abs(float(corr_matrix.loc[a, b]))
          if c > worst_corr:
            worst_corr = c
            worst_pair = (a, b)

    if worst_pair is None:
      break

    # Drop the weaker asset (higher seed score = worse)
    a, b = worst_pair
    drop = a if seed_scores.get(a, float("inf")) > seed_scores.get(b, float("inf")) else b
    selected.remove(drop)

    # Backfill with next best from pool
    if pool_remaining:
      selected.append(pool_remaining.pop(0))

  return selected


def quality_first_select_split(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_longs: int,
  n_shorts: int,
  seed_scores: dict[str, float],
  n_portfolios: int = 3,
  corr_threshold: float = 0.6,
) -> list[dict]:
  """Quality-first for split L/S mode."""
  long_cols = [c for c in daily_pnl.columns if c.endswith("_Long")]
  short_cols = [c for c in daily_pnl.columns if c.endswith("_Short")]

  long_results = quality_first_select(
    daily_pnl[long_cols] if long_cols else daily_pnl,
    corr_matrix.loc[
      [c for c in long_cols if c in corr_matrix.columns],
      [c for c in long_cols if c in corr_matrix.columns],
    ] if long_cols else corr_matrix,
    n_longs, seed_scores, n_portfolios, corr_threshold,
  )
  short_results = quality_first_select(
    daily_pnl[short_cols] if short_cols else daily_pnl,
    corr_matrix.loc[
      [c for c in short_cols if c in corr_matrix.columns],
      [c for c in short_cols if c in corr_matrix.columns],
    ] if short_cols else corr_matrix,
    n_shorts, seed_scores, n_portfolios, corr_threshold,
  )

  results = []
  for i in range(min(len(long_results), len(short_results), n_portfolios)):
    sl = long_results[i] if i < len(long_results) else long_results[0]
    ss = short_results[i] if i < len(short_results) else short_results[0]
    combined = sorted(sl) + sorted(ss)
    valid = [c for c in combined if c in daily_pnl.columns]
    if valid:
      cpnl = daily_pnl[valid].sum(axis=1)
      stab = float(calculate_stability(cpnl))
      cum = cpnl.cumsum()
      peak = np.maximum.accumulate(cum.values)
      max_dd = float((cum.values - peak).min())
      gp = float(cpnl[cpnl > 0].sum())
      gl = abs(float(cpnl[cpnl < 0].sum()))
      pf = min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
      results.append({
        "longs": sorted(sl), "shorts": sorted(ss),
        "assets": sorted(combined), "score": 1 - stab,
        "stability_r2": stab,
        "metrics": {
          "net_profit": float(cum.iloc[-1]) if not cum.empty else 0,
          "profit_factor": float(pf),
          "max_drawdown": float(max_dd),
          "stability": stab,
          "n_assets": len(combined),
        },
      })

  return results if results else []
