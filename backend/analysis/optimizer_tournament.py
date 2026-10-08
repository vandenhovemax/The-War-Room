"""Tournament Draft portfolio construction.

Runs many random drafts, each picking from small random groups while
blending quality (seed ranking) and diversification (correlation).
Explores far more combinations than iterative methods.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.scoring import SCORING_FUNCTIONS, calculate_stability
from backend.analysis.shared import _asset_pf  # noqa: F401
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def tournament_select(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_assets: int,
  seed_scores: dict[str, float],
  objective: str = "stability",
  n_portfolios: int = 3,
  n_rounds: int = 200,
  tournament_size: int = 5,
  quality_weight: float = 0.5,
) -> list[list[str]]:
  """Tournament draft selection.

  For each round:
    1. Start empty.
    2. For each slot: sample tournament_size candidates, pick best blend
       of quality and diversification.
    3. Score completed portfolio.
  Return top n_portfolios diverse portfolios.
  """
  # Filter to profitable
  all_assets = [a for a in daily_pnl.columns if _asset_pf(daily_pnl[a]) >= 1.0]
  if len(all_assets) <= n_assets:
    return [all_assets]

  score_fn = SCORING_FUNCTIONS.get(objective, SCORING_FUNCTIONS.get("stability"))
  rng = np.random.default_rng(42)

  # Pre-compute normalized seed ranks (0 = best, 1 = worst)
  ranked = sorted(all_assets, key=lambda a: seed_scores.get(a, float("inf")))
  rank_map = {a: i / max(len(ranked) - 1, 1) for i, a in enumerate(ranked)}

  # Pre-extract correlation values for fast access
  corr_vals = corr_matrix.values
  corr_idx = {a: i for i, a in enumerate(corr_matrix.columns)}

  logger.info("  tournament: %d assets, %d rounds, size=%d, quality_w=%.2f",
              len(all_assets), n_rounds, tournament_size, quality_weight)

  candidates: list[dict] = []
  t_size = min(tournament_size, len(all_assets))

  for _ in range(n_rounds):
    selected: list[str] = []
    selected_corr_idx: list[int] = []

    for slot in range(n_assets):
      remaining = [a for a in all_assets if a not in selected]
      if not remaining:
        break

      # Sample tournament group
      group_size = min(t_size, len(remaining))
      group = list(rng.choice(remaining, size=group_size, replace=False))

      best_cand = None
      best_blend = float("inf")

      for cand in group:
        # Quality component (lower = better)
        q_score = rank_map.get(cand, 1.0)

        # Correlation component (lower = more diverse)
        if selected_corr_idx and cand in corr_idx:
          ci = corr_idx[cand]
          corrs = corr_vals[ci, selected_corr_idx]
          c_score = float(np.abs(corrs).mean())
        else:
          c_score = 0.0

        blend = quality_weight * q_score + (1 - quality_weight) * c_score
        if blend < best_blend:
          best_blend = blend
          best_cand = cand

      if best_cand:
        selected.append(best_cand)
        if best_cand in corr_idx:
          selected_corr_idx.append(corr_idx[best_cand])

    if len(selected) == n_assets:
      # Score the portfolio
      combined = daily_pnl[selected].sum(axis=1)
      score = float(score_fn(combined))
      candidates.append({"assets": sorted(selected), "score": score})

  if not candidates:
    return [ranked[:n_assets]]

  # Sort by score (lower = better)
  candidates.sort(key=lambda c: c["score"])

  # Diversity filter: accept portfolios differing by >= 2 assets
  accepted: list[list[str]] = []
  for c in candidates:
    asset_set = set(c["assets"])
    if all(len(asset_set.symmetric_difference(set(a))) >= 2 for a in accepted):
      accepted.append(c["assets"])
    if len(accepted) >= n_portfolios:
      break

  # Fallback
  if not accepted:
    accepted.append(candidates[0]["assets"])

  return accepted


def tournament_select_split(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_longs: int,
  n_shorts: int,
  seed_scores: dict[str, float],
  objective: str = "stability",
  n_portfolios: int = 3,
  n_rounds: int = 200,
  tournament_size: int = 5,
  quality_weight: float = 0.5,
) -> list[dict]:
  """Tournament draft for split L/S mode."""
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

  long_results = tournament_select(
    daily_pnl[long_cols] if long_cols else daily_pnl,
    l_corr, n_longs, seed_scores, objective, n_portfolios,
    n_rounds, tournament_size, quality_weight,
  )
  short_results = tournament_select(
    daily_pnl[short_cols] if short_cols else daily_pnl,
    s_corr, n_shorts, seed_scores, objective, n_portfolios,
    n_rounds, tournament_size, quality_weight,
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
