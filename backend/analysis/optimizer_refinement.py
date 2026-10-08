"""Post-construction refinement: prune weak assets and swap in better ones.

Applied after any construction method (greedy, HRP, PCA, etc.) to improve
portfolio quality. Works with any scoring function from scoring.py.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from backend.analysis.scoring import SCORING_FUNCTIONS
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def prune_portfolio(
  portfolio_assets: list[str],
  daily_pnl: pd.DataFrame,
  target_size: int,
  score_fn: Callable,
  pool_assets: list[str] | None = None,
  seed_scores: dict[str, float] | None = None,
) -> list[str]:
  """Remove assets that hurt the portfolio, backfill with better ones.

  1. Compute base portfolio score.
  2. For each asset: remove it, recompute score.
  3. Sort by delta (most harmful first).
  4. Remove assets with positive delta (removing improves score).
  5. Backfill from pool if provided.
  6. Stop at target_size.
  """
  valid = [a for a in portfolio_assets if a in daily_pnl.columns]
  if len(valid) <= target_size:
    return valid

  current = list(valid)
  improved = True

  while improved and len(current) > target_size:
    improved = False
    base_score = score_fn(daily_pnl[current].sum(axis=1))

    # Find the asset whose removal improves score most
    best_removal = None
    best_improvement = 0.0

    for asset in current:
      without = [a for a in current if a != asset]
      if not without:
        continue
      sans_score = score_fn(daily_pnl[without].sum(axis=1))
      improvement = base_score - sans_score  # positive = removing helps
      if improvement > best_improvement:
        best_improvement = improvement
        best_removal = asset

    if best_removal is not None and best_improvement > 0:
      current.remove(best_removal)
      improved = True

  # Force-trim to target_size: remove least-damaging assets even if score worsens
  while len(current) > target_size:
    base_score = score_fn(daily_pnl[current].sum(axis=1))
    least_damage_asset = None
    least_damage_score = float("inf")
    for asset in current:
      without = [a for a in current if a != asset]
      if not without:
        continue
      sans_score = score_fn(daily_pnl[without].sum(axis=1))
      if sans_score < least_damage_score:
        least_damage_score = sans_score
        least_damage_asset = asset
    if least_damage_asset:
      current.remove(least_damage_asset)
    else:
      break  # safety valve

  # Backfill from pool if we're under target
  if pool_assets and len(current) < target_size:
    available = [a for a in pool_assets if a not in current and a in daily_pnl.columns]
    # Rank by seed score if available, else by individual PnL
    if seed_scores:
      available.sort(key=lambda a: seed_scores.get(a, float("inf")))
    else:
      available.sort(key=lambda a: -daily_pnl[a].sum())

    while len(current) < target_size and available:
      best_add = None
      best_score = float("inf")
      # Try top candidates, pick one that improves score most
      for cand in available[:20]:
        test = current + [cand]
        s = score_fn(daily_pnl[test].sum(axis=1))
        if s < best_score:
          best_score = s
          best_add = cand
      if best_add:
        current.append(best_add)
        available.remove(best_add)
      else:
        break

  return current


def swap_refine(
  portfolio_assets: list[str],
  daily_pnl: pd.DataFrame,
  pool_assets: list[str],
  score_fn: Callable,
  max_rounds: int = 3,
) -> list[str]:
  """Try swapping each portfolio asset with each pool asset.

  1. Compute base score.
  2. For each in-portfolio × out-of-portfolio pair: compute swap score.
  3. Execute the single best swap.
  4. Repeat up to max_rounds.
  5. Stop if no round produces improvement.
  """
  current = list(portfolio_assets)
  available = [a for a in pool_assets if a in daily_pnl.columns]

  for round_num in range(max_rounds):
    base_score = score_fn(daily_pnl[current].sum(axis=1))
    best_swap = None
    best_score = base_score

    for i, asset_out in enumerate(current):
      # Pre-compute portfolio without this asset
      without = [a for a in current if a != asset_out]
      if not without:
        continue
      without_pnl = daily_pnl[without].sum(axis=1)

      for asset_in in available:
        if asset_in in current:
          continue
        test_pnl = without_pnl + daily_pnl[asset_in]
        s = score_fn(test_pnl)
        if s < best_score:
          best_score = s
          best_swap = (asset_out, asset_in)

    if best_swap is None:
      break  # No improvement found

    out, inp = best_swap
    current[current.index(out)] = inp
    logger.info("  Refinement swap round %d: %s → %s (score %.4f → %.4f)",
                round_num + 1, out, inp, base_score, best_score)

  return current


def refine_candidates(
  candidates: list[dict],
  daily_pnl: pd.DataFrame,
  pool_columns: list[str],
  refinement_mode: str = "prune_and_swap",
  refinement_metric: str = "",
  objective: str = "stability",
  target_size: int = 10,
  seed_scores: dict[str, float] | None = None,
) -> list[dict]:
  """Apply refinement to a list of candidate portfolios.

  refinement_mode: "prune", "swap", "prune_and_swap"
  refinement_metric: scoring function name, empty = use objective
  """
  metric = refinement_metric or objective
  score_fn = SCORING_FUNCTIONS.get(metric, SCORING_FUNCTIONS.get("stability"))
  if score_fn is None:
    return candidates

  for candidate in candidates:
    assets = candidate.get("assets", [])
    if not assets:
      continue

    valid = [a for a in assets if a in daily_pnl.columns]
    pool = [a for a in pool_columns if a not in valid and a in daily_pnl.columns]

    if "prune" in refinement_mode:
      valid = prune_portfolio(valid, daily_pnl, target_size, score_fn, pool, seed_scores)
      # Update pool after prune (removed assets go back to pool)
      pool = [a for a in pool_columns if a not in valid and a in daily_pnl.columns]

    if "swap" in refinement_mode:
      valid = swap_refine(valid, daily_pnl, pool, score_fn)

    candidate["assets"] = valid
    # Recompute score after refinement
    if valid:
      candidate["score"] = float(score_fn(daily_pnl[valid].sum(axis=1)))
    candidate["refinement_applied"] = True

  return candidates
