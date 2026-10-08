"""OG v2 (Mar 12) — Optimizer with first-round speed changes (numpy vectorization).

LEGACY-FROZEN: A/B engine variant used via optimizer_engine="og_v2" dispatch
in cross_strategy.py. DO NOT MODIFY — saved runs depend on reproducibility.

Extracted from commit 8543ee7 (2026-03-12). Includes numpy pre-extraction but NOT
batch scoring. Shared imports (scoring, seeds) come from current versions.
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
  _fast_stability,
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

  # --- Pre-extract numpy arrays for fast inner-loop access ---
  _corr_vals = corr_matrix.values
  _corr_idx: dict[str, int] = {a: i for i, a in enumerate(assets)}

  # Pre-compute asset PF cache (avoids recalculating per-asset profit factor)
  _pf_cache: dict[str, float] = {}
  if daily_pnl is not None:
    for a in daily_pnl.columns:
      _pf_cache[a] = _asset_pf(daily_pnl[a])

  # Pre-extract daily PnL as numpy 2D array for fast stability computation
  _daily_vals: np.ndarray | None = None
  _daily_col_idx: dict[str, int] = {}
  if daily_pnl is not None and not daily_pnl.empty:
    _daily_vals = daily_pnl.values  # shape (n_days, n_assets)
    _daily_col_idx = {a: i for i, a in enumerate(daily_pnl.columns)}

  # Pre-compute per-asset PnL sums for negative-penalty check
  _pnl_sums: dict[str, float] = {}
  if not streamlit_logic and _daily_vals is not None:
    for a, ci in _daily_col_idx.items():
      _pnl_sums[a] = float(_daily_vals[:, ci].sum())

  # Sort by seed_scores if provided, else by average absolute correlation
  if seed_scores:
    seeds = sorted(assets, key=lambda a: seed_scores.get(a, float("inf")))[:min(len(assets), max_seeds)]
  else:
    avg_corr = corr_matrix.abs().mean()
    seeds = avg_corr.sort_values().index.tolist()[:min(len(assets), max_seeds)]

  seed_set = set(seeds)

  # Hard gate: never allow assets with PF < 1.0 (net losers) into the pool
  if daily_pnl is not None:
    seed_set = {a for a in seed_set if _pf_cache.get(a, 0.0) >= 1.0}
    seeds = [s for s in seeds if s in seed_set]

  # When expand_pool=True, greedy picks from full profitable pool; seeds only control starting points
  if expand_pool and daily_pnl is not None:
    greedy_pool = {a for a in assets if _pf_cache.get(a, 0.0) >= 1.0}
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
    selected_set: set[str] = {seed}
    selected_corr_idx = [_corr_idx[seed]]
    # Maintain running combined PnL as numpy array (for stability computation)
    _combined_vals = _daily_vals[:, _daily_col_idx[seed]].copy() if (_daily_vals is not None and seed in _daily_col_idx) else None

    while len(selected) < n_assets:
      remaining = [a for a in greedy_pool if a not in selected_set]
      if not remaining:
        break

      # --- Vectorized correlation scoring (single numpy op for all candidates) ---
      rem_corr_idx = np.array([_corr_idx[a] for a in remaining])
      sel_corr_idx = np.array(selected_corr_idx)
      corr_block = _corr_vals[np.ix_(rem_corr_idx, sel_corr_idx)]  # (N_rem, N_sel)

      if mode == "anti_correlated":
        scores = corr_block.mean(axis=1)
      elif mode == "hybrid":
        scores = corr_block.mean(axis=1) + 0.2 * np.abs(corr_block).mean(axis=1)
      else:  # uncorrelated
        scores = np.abs(corr_block).mean(axis=1)

      # Stability penalty (per-candidate — uses pre-extracted numpy arrays)
      if prioritize_stability and _combined_vals is not None:
        for ri, cand in enumerate(remaining):
          if cand in _daily_col_idx:
            test_combined = _combined_vals + _daily_vals[:, _daily_col_idx[cand]]
            stab = _fast_stability(test_combined)
            scores[ri] += 1.5 * (1 - stab)

      # Negative PnL penalty (vectorized)
      if _pnl_sums:
        neg_penalties = np.array([2.0 if _pnl_sums.get(a, 0) < 0 else 0.0 for a in remaining])
        scores += neg_penalties

      # Seed prior injection (vectorized)
      if seed_prior_weight > 0 and seed_scores:
        priors = np.array([seed_scores.get(a, _max_seed) / _max_seed for a in remaining])
        scores += seed_prior_weight * priors

      best_ri = int(np.argmin(scores))
      best = remaining[best_ri]
      selected.append(best)
      selected_set.add(best)
      selected_corr_idx.append(_corr_idx[best])
      if _combined_vals is not None and best in _daily_col_idx:
        _combined_vals = _combined_vals + _daily_vals[:, _daily_col_idx[best]]

    # Recalculate FINAL portfolio score (matches Streamlit lines 580-598)
    sel_idx_arr = np.array([_corr_idx[s] for s in selected])
    pf_corr_vals = _corr_vals[np.ix_(sel_idx_arr, sel_idx_arr)]
    n_sel = len(selected)
    if n_sel > 1:
      mask = np.triu(np.ones((n_sel, n_sel), dtype=bool), k=1)
      upper_vals = pf_corr_vals[mask]
      base_score = float(np.abs(upper_vals).mean())
    else:
      base_score = 0.0
    final_score = base_score
    if prioritize_stability and _daily_vals is not None:
      valid_sel = [c for c in selected if c in _daily_col_idx]
      if valid_sel:
        combined_arr = sum(_daily_vals[:, _daily_col_idx[c]] for c in valid_sel)
        stab = calculate_stability(pd.Series(combined_arr))
      else:
        stab = 0
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
        sel_set_inner: set[str] = {s}
        sel_corr_idx_inner = [_corr_idx[s]]
        # Running combined PnL for stability
        _comb_inner = _daily_vals[:, _daily_col_idx[s]].copy() if (_daily_vals is not None and s in _daily_col_idx) else None
        while len(sel) < n_assets:
          rem = [a for a in reduced if a not in sel_set_inner]
          if not rem:
            break
          # Vectorized correlation scoring
          rem_ci = np.array([_corr_idx[a] for a in rem])
          sel_ci = np.array(sel_corr_idx_inner)
          cb = _corr_vals[np.ix_(rem_ci, sel_ci)]
          if mode == "anti_correlated":
            sc_arr = cb.mean(axis=1)
          elif mode == "hybrid":
            sc_arr = cb.mean(axis=1) + 0.2 * np.abs(cb).mean(axis=1)
          else:
            sc_arr = np.abs(cb).mean(axis=1)
          # Stability penalty
          if prioritize_stability and _comb_inner is not None:
            for ri, cand in enumerate(rem):
              if cand in _daily_col_idx:
                test_c = _comb_inner + _daily_vals[:, _daily_col_idx[cand]]
                sc_arr[ri] += 1.5 * (1 - _fast_stability(test_c))
          best_ri = int(np.argmin(sc_arr))
          b = rem[best_ri]
          sel.append(b)
          sel_set_inner.add(b)
          sel_corr_idx_inner.append(_corr_idx[b])
          if _comb_inner is not None and b in _daily_col_idx:
            _comb_inner = _comb_inner + _daily_vals[:, _daily_col_idx[b]]
        if len(sel) == n_assets:
          sel_set_check = set(sel)
          if all(len(sel_set_check.symmetric_difference(set(a))) >= 2 for a in accepted):
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
  precomputed_seeds: dict[str, float] | None = None,
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
  if precomputed_seeds is not None:
    _seed_scores = {a: precomputed_seeds[a] for a in assets if a in precomputed_seeds}
    for a in assets:
      if a not in _seed_scores:
        _seed_scores[a] = float("inf")
  else:
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
