"""OG v1 (Mar 11) — Original greedy portfolio optimizer before speed changes.

LEGACY-FROZEN: this file is an A/B engine variant used via
optimizer_engine="og_v1". DO NOT MODIFY. Routed from:
  - backend.api.routes.cross_strategy (optimizer_engine dispatch)
  - backend.analysis.diversification
  - backend.analysis.optimizer_tc_og_v1
Saved research runs rely on reproducing exact historical outputs, so edits
here silently invalidate stored comparisons.

Extracted from commit 81d1cb2 (2026-03-11). Search algorithms preserved exactly
as they were. Shared imports (scoring, seeds) come from current versions.
"""
from __future__ import annotations

import logging
import time as _time

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from backend.analysis.optimizer_seeds import compute_seed_scores
from backend.analysis.scoring import (
  SCORING_FUNCTIONS,
  calculate_stability,
)


def _asset_pf(series: pd.Series) -> float:
  """Profit factor for a single asset's daily PnL series."""
  gp = float(series[series > 0].sum())
  gl = abs(float(series[series < 0].sum()))
  return min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)


def _greedy_search(
  pnl_matrix: pd.DataFrame,
  n_assets: int,
  score_fn,
  n_seeds: int = 10,
  n_portfolios: int = 3,
  seed_scores: dict[str, float] | None = None,
  expand_pool: bool = True,
  seed_prior_weight: float = 0.0,
  **kwargs,
) -> list[list[str]]:
  """Core greedy search: start from top-ranked seeds, iteratively add
  the candidate that improves the objective most.

  expand_pool: when True, greedy picks from ALL profitable assets (not just seeds).
  seed_prior_weight: soft tiebreaker weight for seed quality (0 = off).
  """
  assets = pnl_matrix.columns.tolist()
  if n_assets >= len(assets):
    return [assets]

  # Rank individual assets by seed_scores if provided, else by score_fn
  if seed_scores:
    scores = {a: seed_scores.get(a, float("inf")) for a in assets}
  else:
    scores = {a: score_fn(pnl_matrix[a]) for a in assets}
  seeds = sorted(scores, key=lambda k: scores[k])[:min(len(assets), n_seeds)]

  seed_set = set(seeds)
  # When expand_pool=True, greedy picks from full pool; seeds only control starting points
  greedy_pool = set(assets) if expand_pool else seed_set

  # Normalize seed scores for prior injection
  _max_seed = max(seed_scores.values()) if seed_scores and seed_prior_weight > 0 else 1.0
  _max_seed = _max_seed if _max_seed > 0 else 1.0

  candidates = []
  for seed in seeds:
    selected = [seed]
    combined = pnl_matrix[seed].copy()

    while len(selected) < n_assets:
      remaining = [a for a in greedy_pool if a not in selected]
      best, best_score = None, float("inf")
      for cand in remaining:
        test = combined + pnl_matrix[cand]
        s = score_fn(test)
        # Inject seed prior as soft tiebreaker
        if seed_prior_weight > 0 and seed_scores:
          normalized_prior = seed_scores.get(cand, _max_seed) / _max_seed
          s += seed_prior_weight * normalized_prior
        if s < best_score:
          best_score = s
          best = cand
      if best:
        selected.append(best)
        combined += pnl_matrix[best]
      else:
        break
    final_score = score_fn(combined)
    candidates.append({"assets": sorted(selected), "score": final_score})

  # Deduplicate preserving best score, then sort (lower = better)
  unique: dict[tuple, float] = {}
  for c in candidates:
    key = tuple(c["assets"])
    if key not in unique or c["score"] < unique[key]:
      unique[key] = c["score"]
  sorted_candidates = sorted(unique.items(), key=lambda x: x[1])

  # Diversity filter: only accept portfolios that differ by ≥2 assets from all accepted
  accepted: list[list[str]] = []
  for asset_tuple, _ in sorted_candidates:
    asset_set = set(asset_tuple)
    if all(len(asset_set.symmetric_difference(set(a))) >= 2 for a in accepted):
      accepted.append(list(asset_tuple))
    if len(accepted) >= n_portfolios:
      break

  # If not enough diverse portfolios, try exclusion-based re-runs
  if len(accepted) < n_portfolios and accepted:
    excluded: set[str] = set()
    for prev in accepted:
      # Exclude the last-added asset from the best portfolio
      if prev:
        excluded.add(prev[-1])
    remaining_pool = [a for a in pnl_matrix.columns if a not in excluded]
    if len(remaining_pool) >= n_assets:
      extra_pnl = pnl_matrix[remaining_pool]
      for seed_a in remaining_pool[:min(len(remaining_pool), n_seeds)]:
        sel = [seed_a]
        comb = extra_pnl[seed_a].copy()
        while len(sel) < n_assets:
          rem = [a for a in remaining_pool if a not in sel]
          b, bs = None, float("inf")
          for ca in rem:
            s = score_fn(comb + extra_pnl[ca])
            if s < bs:
              bs, b = s, ca
          if b:
            sel.append(b)
            comb += extra_pnl[b]
          else:
            break
        sel_set = set(sorted(sel))
        if all(len(sel_set.symmetric_difference(set(a))) >= 2 for a in accepted):
          accepted.append(sorted(sel))
        if len(accepted) >= n_portfolios:
          break

  # Fallback: if diversity filter too strict, fill with remaining unique
  if len(accepted) < n_portfolios:
    for asset_tuple, _ in sorted_candidates:
      if list(asset_tuple) not in accepted:
        accepted.append(list(asset_tuple))
      if len(accepted) >= n_portfolios:
        break

  return accepted


def optimize_correlation(
  corr_matrix: pd.DataFrame,
  n_assets: int,
  mode: str = "uncorrelated",
  n_portfolios: int = 3,
  seed_scores: dict[str, float] | None = None,
  max_seeds: int = 20,
  prioritize_stability: bool = False,
  daily_pnl: pd.DataFrame | None = None,
  streamlit_logic: bool = False,
  expand_pool: bool = True,
  seed_prior_weight: float = 0.0,
  **kwargs,
) -> list[list[str]]:
  """Correlation-based optimization (uncorrelated, anti-correlated, hybrid).

  Returns asset lists sorted by final portfolio score (lower = better).
  Matches Streamlit: recalculates upper-triangle score after building.

  expand_pool: when True, greedy picks from ALL profitable assets (not just seeds).
  seed_prior_weight: soft tiebreaker weight for seed quality (0 = off).
  """
  assets = corr_matrix.columns.tolist()
  if n_assets >= len(assets):
    return [assets]

  _opt_t0 = _time.perf_counter()
  candidate_portfolios: list[dict] = []
  # Sort by seed_scores if provided, else by average absolute correlation
  if seed_scores:
    seeds = sorted(assets, key=lambda a: seed_scores.get(a, float("inf")))[:min(len(assets), max_seeds)]
  else:
    avg_corr = corr_matrix.abs().mean()
    seeds = avg_corr.sort_values().index.tolist()[:min(len(assets), max_seeds)]

  seed_set = set(seeds)

  # Hard gate: never allow assets with PF < 1.0 (net losers) into the pool
  if daily_pnl is not None:
    seed_set = {a for a in seed_set if a in daily_pnl.columns and _asset_pf(daily_pnl[a]) >= 1.0}
    seeds = [s for s in seeds if s in seed_set]

  # When expand_pool=True, greedy picks from full profitable pool; seeds only control starting points
  if expand_pool and daily_pnl is not None:
    greedy_pool = {a for a in assets if a in daily_pnl.columns and _asset_pf(daily_pnl[a]) >= 1.0}
  else:
    greedy_pool = seed_set

  # Normalize seed scores for prior injection
  _max_seed = max(seed_scores.values()) if seed_scores and seed_prior_weight > 0 else 1.0
  _max_seed = _max_seed if _max_seed > 0 else 1.0

  logger.info("  optimize_correlation: mode=%s, %d seeds, %d greedy_pool, n_assets=%d, stability=%s, expand=%s",
              mode, len(seeds), len(greedy_pool), n_assets, prioritize_stability, expand_pool)

  for _si, seed in enumerate(seeds):
    if (_si + 1) % 20 == 0 or _si == 0:
      logger.info("  Greedy search: seed %d/%d (%.1fs)...", _si + 1, len(seeds),
                  _time.perf_counter() - _opt_t0)
    selected = [seed]
    while len(selected) < n_assets:
      remaining = [a for a in greedy_pool if a not in selected]
      best, best_score = None, float("inf")
      for cand in remaining:
        # Average correlation with already-selected assets
        corrs = [corr_matrix.loc[cand, s] for s in selected]
        if mode == "anti_correlated":
          score = np.mean(corrs)  # Prefer negative correlation
        elif mode == "hybrid":
          # Match Streamlit: signed_mean + 0.2 * abs_mean
          score = np.mean(corrs) + 0.2 * np.mean([abs(c) for c in corrs])
        else:  # uncorrelated
          score = np.mean([abs(c) for c in corrs])  # Prefer near-zero
        # Penalize unstable equity when prioritize_stability is on
        if prioritize_stability and daily_pnl is not None and cand in daily_pnl.columns:
          test_combined = daily_pnl[[*selected, cand]].sum(axis=1)
          stab = calculate_stability(test_combined)
          # Match Streamlit: linear add with 1.5x penalty
          score = score + 1.5 * (1 - stab)

        # Penalize negative PnL assets to prioritize profitability
        if not streamlit_logic and daily_pnl is not None and cand in daily_pnl.columns:
          cand_pnl = daily_pnl[cand].sum()
          if cand_pnl < 0:
            score += 2.0  # Hefty penalty to disfavor losing assets

        # Inject seed prior as soft tiebreaker
        if seed_prior_weight > 0 and seed_scores:
          normalized_prior = seed_scores.get(cand, _max_seed) / _max_seed
          score += seed_prior_weight * normalized_prior

        if score < best_score:
          best_score = score
          best = cand
      if best:
        selected.append(best)
      else:
        break

    # Recalculate FINAL portfolio score (matches Streamlit lines 580-598)
    pf_corr = corr_matrix.loc[selected, selected]
    mask = np.triu(np.ones(pf_corr.shape), k=1).astype(bool)
    if n_assets > 1:
      upper_tri = pf_corr.where(mask).stack()
      if mode == "anti_correlated":
        base_score = float(upper_tri.mean())
      elif mode == "hybrid":
        base_score = float(upper_tri.mean() + 0.2 * upper_tri.abs().mean())
      else:
        base_score = float(upper_tri.abs().mean())
    else:
      base_score = 0.0
    final_score = base_score
    if prioritize_stability and daily_pnl is not None:
      combined = daily_pnl[[c for c in selected if c in daily_pnl.columns]].sum(axis=1)
      stab = calculate_stability(combined) if not combined.empty else 0
      final_score = base_score + 1.5 * (1 - stab)
    candidate_portfolios.append({"assets": sorted(selected), "score": final_score})

  logger.info("  Greedy search complete: %d candidates from %d seeds (%.1fs)",
              len(candidate_portfolios), len(seeds), _time.perf_counter() - _opt_t0)

  # Dedup and sort by score (lower = better)
  unique: dict[tuple, float] = {}
  for c in candidate_portfolios:
    key = tuple(c["assets"])
    if key not in unique or c["score"] < unique[key]:
      unique[key] = c["score"]
  sorted_candidates = sorted(unique.items(), key=lambda x: x[1])

  # Diversity filter: only accept portfolios that differ by ≥2 assets
  accepted: list[list[str]] = []
  for asset_tuple, _ in sorted_candidates:
    asset_set = set(asset_tuple)
    if all(len(asset_set.symmetric_difference(set(a))) >= 2 for a in accepted):
      accepted.append(list(asset_tuple))
    if len(accepted) >= n_portfolios:
      break
  # Exclusion-based re-runs: force diversity by excluding top assets
  if len(accepted) < n_portfolios and accepted:
    from collections import Counter
    asset_freq = Counter(a for p in accepted for a in p)
    for attempt in range(min(5, n_portfolios - len(accepted) + 2)):
      exclude = set(sorted(asset_freq, key=lambda x: asset_freq[x], reverse=True)[:attempt + 1])
      reduced = [a for a in greedy_pool if a not in exclude]
      if len(reduced) < n_assets:
        continue
      for s in [a for a in seeds if a not in exclude][:max_seeds]:
        sel = [s]
        while len(sel) < n_assets:
          rem = [a for a in reduced if a not in sel]
          b, bs = None, float("inf")
          for cand in rem:
            corrs = [corr_matrix.loc[cand, x] for x in sel]
            if mode == "anti_correlated":
              sc = np.mean(corrs)
            elif mode == "hybrid":
              sc = np.mean(corrs) + 0.2 * np.mean([abs(c) for c in corrs])
            else:
              sc = np.mean([abs(c) for c in corrs])
            if prioritize_stability and daily_pnl is not None and cand in daily_pnl.columns:
              sc += 1.5 * (1 - calculate_stability(daily_pnl[[*sel, cand]].sum(axis=1)))
            if sc < bs:
              bs, b = sc, cand
          if b:
            sel.append(b)
          else:
            break
        if len(sel) == n_assets:
          sel_set = set(sel)
          if all(len(sel_set.symmetric_difference(set(a))) >= 2 for a in accepted):
            accepted.append(sorted(sel))
          if len(accepted) >= n_portfolios:
            break
      if len(accepted) >= n_portfolios:
        break

  # Fallback: fill with remaining unique if diversity too strict
  if len(accepted) < n_portfolios:
    for asset_tuple, _ in sorted_candidates:
      if list(asset_tuple) not in accepted:
        accepted.append(list(asset_tuple))
      if len(accepted) >= n_portfolios:
        break
  return accepted


def optimize_portfolio(
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_assets: int,
  objective: str = "hybrid",
  n_portfolios: int = 3,
  seed_metrics: list[str] | None = None,
  max_seeds: int = 20,
  prioritize_stability: bool = False,
  external_scores: dict | None = None,
  streamlit_logic: bool = False,
  expand_pool: bool = True,
  seed_prior_weight: float = 0.0,
  seed_weights: dict[str, float] | None = None,
  **kwargs,
) -> list[dict]:
  """Master optimizer dispatcher. Returns candidate portfolios with metrics.

  objective: "hybrid", "uncorrelated", "anti_correlated",
             "min_max_drawdown", "max_sortino", "max_calmar",
             "max_recovery", "min_ulcer", "max_profit_factor",
             "stability", "max_sharpe", etc.
  expand_pool: when True, greedy picks from ALL profitable assets.
  seed_prior_weight: soft tiebreaker weight for seed quality (0 = off).
  seed_weights: per-metric weights for seed scoring {metric: weight}.
  """
  logger.info("optimize_portfolio: objective=%s, n_assets=%d, seeds=%d, stability=%s, total_cols=%d, expand=%s, prior=%.2f",
              objective, n_assets, max_seeds, prioritize_stability, daily_pnl.shape[1], expand_pool, seed_prior_weight)
  # Early exit for empty input or zero-asset request
  if daily_pnl.empty or n_assets <= 0:
    return []
  # Hard gate: exclude assets with PF < 1.0 before any optimization
  all_cols = daily_pnl.columns.tolist()
  profitable = [a for a in all_cols if _asset_pf(daily_pnl[a]) >= 1.0]
  if profitable:
    daily_pnl = daily_pnl[profitable]
    corr_matrix = corr_matrix.loc[
      [c for c in profitable if c in corr_matrix.columns],
      [c for c in profitable if c in corr_matrix.columns],
    ]

  # Pre-compute seed scores for ranking (default to sortino like blueprint)
  _metrics = seed_metrics or ["sortino"]
  assets = daily_pnl.columns.tolist()
  _seed_scores = compute_seed_scores(daily_pnl, assets, _metrics, external_scores, metric_weights=seed_weights)

  # Choose optimizer
  corr_objectives = {"hybrid", "uncorrelated", "anti_correlated"}
  if objective in corr_objectives:
    raw = optimize_correlation(
      corr_matrix, n_assets, objective, n_portfolios,
      seed_scores=_seed_scores, max_seeds=max_seeds,
      prioritize_stability=prioritize_stability, daily_pnl=daily_pnl,
      streamlit_logic=streamlit_logic,
      expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
    )
  elif objective in SCORING_FUNCTIONS:
    raw = _greedy_search(
      daily_pnl, n_assets, SCORING_FUNCTIONS[objective],
      n_seeds=max_seeds, n_portfolios=n_portfolios,
      seed_scores=_seed_scores,
      expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
    )
  else:
    raw = optimize_correlation(
      corr_matrix, n_assets, "uncorrelated", n_portfolios,
      seed_scores=_seed_scores, max_seeds=max_seeds,
      prioritize_stability=prioritize_stability, daily_pnl=daily_pnl,
      streamlit_logic=streamlit_logic,
      expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
    )

  # Score each candidate with metrics + objective-based sorting
  results = []
  for assets in raw:
    combined = daily_pnl[assets].sum(axis=1)
    cum = combined.cumsum()
    peak = np.maximum.accumulate(cum.values)
    max_dd = float((cum.values - peak).min())

    gross_profit = combined[combined > 0].sum()
    gross_loss = abs(combined[combined < 0].sum())
    pf = min(gross_profit / gross_loss, 10.0) if gross_loss > 0 else (10.0 if gross_profit > 0 else 0.0)
    stab = float(calculate_stability(combined))

    # Compute objective-specific score for sorting (lower = better)
    if objective in SCORING_FUNCTIONS:
      obj_score = SCORING_FUNCTIONS[objective](combined)
    else:
      # Correlation objectives: use stability as tiebreaker (already sorted by corr score)
      obj_score = 1 - stab  # Lower = more stable = better

    results.append({
      "assets": assets,
      "metrics": {
        "net_profit": float(cum.iloc[-1]) if not cum.empty else 0,
        "profit_factor": float(pf),
        "max_drawdown": float(max_dd),
        "stability": stab,
        "n_assets": len(assets),
      },
      "_obj_score": obj_score,
    })

  # Sort by objective score (lower = better), matching Streamlit behavior
  results.sort(key=lambda r: r["_obj_score"])
  # Remove internal scoring key before returning
  for r in results:
    r.pop("_obj_score", None)
  return results
