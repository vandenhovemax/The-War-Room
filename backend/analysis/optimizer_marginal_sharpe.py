"""Marginal Sharpe Contribution portfolio construction.

Picks assets one at a time using marginal Sharpe — a formula that
naturally balances quality (high return) and diversification (low
correlation) in a single number. No separate objective needed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.scoring import calculate_stability
from backend.analysis.shared import _asset_pf  # noqa: F401
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def marginal_sharpe_select(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_assets: int,
  seed_scores: dict[str, float],
  max_seeds: int = 20,
  n_portfolios: int = 3,
) -> list[list[str]]:
  """Marginal Sharpe contribution selection.

  For each seed, build portfolio one asset at a time:
    - For each candidate, compute marginal Sharpe contribution
    - Pick the candidate with highest marginal Sharpe
  This naturally balances quality (high return) and diversification
  (low correlation) in one number.
  """
  # Filter to profitable
  all_assets = [a for a in daily_pnl.columns if _asset_pf(daily_pnl[a]) >= 1.0]
  if len(all_assets) <= n_assets:
    return [all_assets]

  # Get seeds (top ranked by seed score)
  seeds = sorted(all_assets, key=lambda a: seed_scores.get(a, float("inf")))
  seeds = seeds[:min(len(seeds), max_seeds)]

  # Pre-compute mean returns for all assets
  mean_returns = daily_pnl[all_assets].mean()
  pnl_vals = daily_pnl[all_assets].values
  col_idx = {a: i for i, a in enumerate(all_assets)}

  logger.info("  marginal_sharpe: %d assets, %d seeds, target=%d",
              len(all_assets), len(seeds), n_assets)

  candidates: list[dict] = []

  for seed in seeds:
    selected = [seed]
    combined = pnl_vals[:, col_idx[seed]].copy()

    while len(selected) < n_assets:
      remaining = [a for a in all_assets if a not in selected]
      if not remaining:
        break

      portfolio_return = float(combined.mean())
      portfolio_vol = float(combined.std())
      if portfolio_vol == 0:
        portfolio_vol = 1e-10

      best_cand = None
      best_marginal = -float("inf")

      for cand in remaining:
        cand_return = float(mean_returns[cand])
        # Compute mean correlation with current portfolio
        if cand in corr_matrix.columns:
          corrs = []
          for sel in selected:
            if sel in corr_matrix.columns:
              corrs.append(float(corr_matrix.loc[cand, sel]))
          mean_corr = np.mean(corrs) if corrs else 0.0
        else:
          mean_corr = 0.0

        # Marginal Sharpe: how much this asset improves risk-adjusted return
        marginal = (cand_return - portfolio_return * mean_corr) / portfolio_vol

        if marginal > best_marginal:
          best_marginal = marginal
          best_cand = cand

      if best_cand is None:
        break

      selected.append(best_cand)
      combined = combined + pnl_vals[:, col_idx[best_cand]]

    # Score final portfolio
    stab = float(calculate_stability(pd.Series(combined)))
    candidates.append({"assets": sorted(selected), "score": 1 - stab})

  if not candidates:
    return [seeds[:n_assets]]

  # Dedup and sort
  unique: dict[tuple, float] = {}
  for c in candidates:
    key = tuple(c["assets"])
    if key not in unique or c["score"] < unique[key]:
      unique[key] = c["score"]
  sorted_cands = sorted(unique.items(), key=lambda x: x[1])

  # Diversity filter
  accepted: list[list[str]] = []
  for asset_tuple, _ in sorted_cands:
    asset_set = set(asset_tuple)
    if all(len(asset_set.symmetric_difference(set(a))) >= 2 for a in accepted):
      accepted.append(list(asset_tuple))
    if len(accepted) >= n_portfolios:
      break

  if not accepted:
    accepted.append(list(sorted_cands[0][0]))

  return accepted


def marginal_sharpe_select_split(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_longs: int,
  n_shorts: int,
  seed_scores: dict[str, float],
  max_seeds: int = 20,
  n_portfolios: int = 3,
) -> list[dict]:
  """Marginal Sharpe for split L/S mode."""
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

  long_results = marginal_sharpe_select(
    daily_pnl[long_cols] if long_cols else daily_pnl,
    l_corr, n_longs, seed_scores, max_seeds, n_portfolios,
  )
  short_results = marginal_sharpe_select(
    daily_pnl[short_cols] if short_cols else daily_pnl,
    s_corr, n_shorts, seed_scores, max_seeds, n_portfolios,
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
