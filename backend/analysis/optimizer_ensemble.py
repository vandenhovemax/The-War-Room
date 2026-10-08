"""Ensemble Consensus portfolio construction.

Runs 3 different methods internally and keeps assets that multiple
methods agree on. Most robust — no single method's blind spot dominates.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from backend.analysis.scoring import calculate_stability
from backend.analysis.shared import _asset_pf  # noqa: F401
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def ensemble_select(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_assets: int,
  seed_scores: dict[str, float],
  max_seeds: int = 20,
  n_portfolios: int = 3,
  seed_metrics: list[str] | None = None,
  external_scores: dict | None = None,
) -> list[list[str]]:
  """Ensemble consensus selection.

  1. Run 3 different internal methods.
  2. Count asset frequency across methods.
  3. Consensus core: assets in 3/3 methods.
  4. Majority picks: assets in 2/3 methods.
  5. Fill remaining by seed score.
  """
  from backend.analysis.hrp_optimizer import hrp_select_assets
  from backend.analysis.optimizer import optimize_portfolio

  logger.info("  ensemble: running 3 internal methods for consensus...")

  method_results: list[list[str]] = []

  # Method 1: Greedy with hybrid objective (correlation path)
  try:
    r1 = optimize_portfolio(
      daily_pnl, corr_matrix, n_assets, "hybrid", 1,
      seed_metrics=seed_metrics, max_seeds=max_seeds,
      external_scores=external_scores,
      precomputed_seeds=seed_scores,
    )
    if r1 and r1[0].get("assets"):
      method_results.append(r1[0]["assets"])
  except Exception as e:
    logger.warning("  ensemble method 1 (hybrid) failed: %s", e)

  # Method 2: Greedy with stability objective (PnL path)
  try:
    r2 = optimize_portfolio(
      daily_pnl, corr_matrix, n_assets, "stability", 1,
      seed_metrics=seed_metrics, max_seeds=max_seeds,
      external_scores=external_scores,
      precomputed_seeds=seed_scores,
    )
    if r2 and r2[0].get("assets"):
      method_results.append(r2[0]["assets"])
  except Exception as e:
    logger.warning("  ensemble method 2 (stability) failed: %s", e)

  # Method 3: HRP (clustering path)
  try:
    r3 = hrp_select_assets(
      corr_matrix, daily_pnl, n_assets, 1,
      seed_metrics=seed_metrics, max_seeds=max_seeds,
      external_scores=external_scores,
    )
    if r3:
      method_results.append(r3[0])
  except Exception as e:
    logger.warning("  ensemble method 3 (HRP) failed: %s", e)

  if not method_results:
    # All methods failed — fallback to seed ranking
    ranked = sorted(daily_pnl.columns.tolist(),
                    key=lambda a: seed_scores.get(a, float("inf")))
    return [ranked[:n_assets]]

  # Build consensus portfolios
  all_results: list[list[str]] = []

  # Variant 1: Primary consensus
  portfolio = _build_consensus(method_results, n_assets, seed_scores, daily_pnl)
  all_results.append(portfolio)

  # Variant 2: Run with different internal methods
  if n_portfolios > 1:
    method_results_v2: list[list[str]] = []
    try:
      r_sortino = optimize_portfolio(
        daily_pnl, corr_matrix, n_assets, "max_sortino", 1,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        precomputed_seeds=seed_scores,
      )
      if r_sortino and r_sortino[0].get("assets"):
        method_results_v2.append(r_sortino[0]["assets"])
    except Exception as e:
      logger.warning("  ensemble method v2 (sortino) failed: %s", e)
    try:
      r_uncorr = optimize_portfolio(
        daily_pnl, corr_matrix, n_assets, "uncorrelated", 1,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        precomputed_seeds=seed_scores,
      )
      if r_uncorr and r_uncorr[0].get("assets"):
        method_results_v2.append(r_uncorr[0]["assets"])
    except Exception as e:
      logger.warning("  ensemble method v2 (uncorrelated) failed: %s", e)
    # Reuse HRP from variant 1
    if len(method_results) >= 3:
      method_results_v2.append(method_results[2])

    if method_results_v2:
      portfolio_v2 = _build_consensus(method_results_v2, n_assets, seed_scores, daily_pnl)
      if set(portfolio_v2) != set(all_results[0]):
        all_results.append(portfolio_v2)

  # Variant 3: Exclude consensus core and rebuild
  if n_portfolios > 2 and len(all_results) >= 1:
    # Exclude top-voted assets from first portfolio
    exclude = set(all_results[0][:3])
    filtered_methods = []
    for mr in method_results:
      filtered_methods.append([a for a in mr if a not in exclude])
    portfolio_v3 = _build_consensus(filtered_methods, n_assets, seed_scores, daily_pnl)
    key = set(portfolio_v3)
    if all(len(key.symmetric_difference(set(p))) >= 2 for p in all_results):
      all_results.append(portfolio_v3)

  return all_results[:n_portfolios]


def _build_consensus(
  method_results: list[list[str]],
  n_assets: int,
  seed_scores: dict[str, float],
  daily_pnl: pd.DataFrame,
) -> list[str]:
  """Build a consensus portfolio from multiple method results."""
  # Count frequency
  counter = Counter(a for r in method_results for a in r)

  # Sort by frequency (desc) then seed score (asc)
  all_candidates = sorted(
    counter.keys(),
    key=lambda a: (-counter[a], seed_scores.get(a, float("inf"))),
  )

  selected: list[str] = []
  for asset in all_candidates:
    if len(selected) >= n_assets:
      break
    if asset in daily_pnl.columns:
      selected.append(asset)

  # Fill remaining with best-ranked assets not yet selected
  if len(selected) < n_assets:
    remaining = [a for a in daily_pnl.columns
                 if a not in selected and _asset_pf(daily_pnl[a]) >= 1.0]
    remaining.sort(key=lambda a: seed_scores.get(a, float("inf")))
    selected.extend(remaining[:n_assets - len(selected)])

  return selected[:n_assets]


def ensemble_select_split(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_longs: int,
  n_shorts: int,
  seed_scores: dict[str, float],
  max_seeds: int = 20,
  n_portfolios: int = 3,
  seed_metrics: list[str] | None = None,
  external_scores: dict | None = None,
) -> list[dict]:
  """Ensemble consensus for split L/S mode."""
  long_cols = [c for c in daily_pnl.columns if c.endswith("_Long")]
  short_cols = [c for c in daily_pnl.columns if c.endswith("_Short")]

  l_corr = corr_matrix.loc[
    [c for c in long_cols if c in corr_matrix.columns],
    [c for c in long_cols if c in corr_matrix.columns],
  ] if long_cols else corr_matrix
  s_corr = corr_matrix.loc[
    [c for c in short_cols if c in corr_matrix.columns],
    [c for c in short_cols if c in corr_matrix.columns],
  ] if short_cols else corr_matrix

  long_results = ensemble_select(
    daily_pnl[long_cols] if long_cols else daily_pnl,
    l_corr, n_longs, seed_scores, max_seeds, n_portfolios,
    seed_metrics, external_scores,
  )
  short_results = ensemble_select(
    daily_pnl[short_cols] if short_cols else daily_pnl,
    s_corr, n_shorts, seed_scores, max_seeds, n_portfolios,
    seed_metrics, external_scores,
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
