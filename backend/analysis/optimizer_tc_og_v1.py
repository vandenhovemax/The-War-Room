"""OG v1 (Mar 11) — Original trade-count optimizer before speed changes.

LEGACY-FROZEN: A/B engine variant used via optimizer_engine="og_v1" dispatch
in cross_strategy.py. DO NOT MODIFY — saved runs depend on reproducibility.

Extracted from commit 81d1cb2 (2026-03-11). Search algorithms preserved exactly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.optimizer_og_v1 import optimize_portfolio
from backend.analysis.optimizer_split_og_v1 import optimize_split_portfolio
from backend.analysis.scoring import SCORING_FUNCTIONS, calculate_stability


def _classify_assets(
  assets: list[str], trade_counts: dict[str, int],
  low_threshold: int, high_threshold: int,
) -> tuple[list[str], list[str], list[str]]:
  """Split assets into Low (<=N), Mid (between), High (>=M) pools."""
  pool_low, pool_mid, pool_high = [], [], []
  for a in assets:
    n = trade_counts.get(a, 0)
    if low_threshold > 0 and n <= low_threshold:
      pool_low.append(a)
    elif high_threshold > 0 and n >= high_threshold:
      pool_high.append(a)
    else:
      pool_mid.append(a)
  return pool_low, pool_mid, pool_high


def _enumerate_allocations(
  n_target: int, max_low: int, min_high: int,
  n_pool_low: int, n_pool_high: int,
) -> list[tuple[int, int, int]]:
  """Enumerate valid (n_low, n_high_min, n_flex) allocations."""
  allocations = []
  for n_low in range(0, min(max_low, n_pool_low) + 1):
    remaining = n_target - n_low
    actual_min_high = max(min_high, 0)
    if actual_min_high > n_pool_high or remaining < actual_min_high:
      continue
    allocations.append((n_low, actual_min_high, remaining - actual_min_high))
  return allocations


def _score_low_trade_asset(
  a: str, portfolio: list[str], corr_matrix: pd.DataFrame,
  objective: str, daily_returns: pd.DataFrame | None,
  prioritize_stability: bool,
) -> float:
  """Score a low-trade asset against an existing portfolio (lower = better)."""
  if not portfolio:
    return 0.0
  corrs = corr_matrix.loc[a, portfolio]
  if objective == "anti_correlated":
    score = float(corrs.mean())
  elif objective == "hybrid":
    score = float(corrs.mean() + 0.2 * corrs.abs().mean())
  else:
    score = float(corrs.abs().mean())
  if prioritize_stability and daily_returns is not None:
    test = daily_returns[[*portfolio, a]].sum(axis=1)
    score += 1.5 * (1 - calculate_stability(test))
  return score


def optimize_with_trade_constraints(
  daily_pnl: pd.DataFrame, corr_matrix: pd.DataFrame,
  n_assets: int, trade_counts: dict[str, int],
  tc: dict, objective: str = "hybrid", n_portfolios: int = 3,
  seed_metrics: list[str] | None = None, max_seeds: int = 20,
  prioritize_stability: bool = False, external_scores: dict | None = None,
  streamlit_logic: bool = False,
  expand_pool: bool = True,
  seed_prior_weight: float = 0.0,
  seed_weights: dict[str, float] | None = None,
  **kwargs,
) -> list[dict]:
  """Pool-partitioned optimizer for combined (non-split) portfolios.

  tc: {low_threshold, max_low, high_threshold, min_high}
  """
  low_thresh = tc.get("low_threshold", 0)
  max_low = tc.get("max_low", 999)
  high_thresh = tc.get("high_threshold", 0)
  min_high = tc.get("min_high", 0)

  if max_low >= n_assets and min_high <= 0:
    return optimize_portfolio(
      daily_pnl, corr_matrix, n_assets, objective, n_portfolios,
      seed_metrics=seed_metrics, max_seeds=max_seeds,
      prioritize_stability=prioritize_stability, external_scores=external_scores,
      streamlit_logic=streamlit_logic,
      expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
      seed_weights=seed_weights,
    )

  assets = corr_matrix.columns.tolist()
  pool_low, pool_mid, pool_high = _classify_assets(
    assets, trade_counts, low_thresh, high_thresh,
  )
  allocations = _enumerate_allocations(
    n_assets, max_low, min_high, len(pool_low), len(pool_high),
  )
  if not allocations:
    return []

  all_candidates: list[list[str]] = []
  for n_low, n_high_min, n_flex in allocations:
    non_low = pool_high + pool_mid
    n_non_low = n_high_min + n_flex
    if n_non_low > 0 and len(non_low) >= n_non_low:
      sub_corr = corr_matrix.loc[non_low, non_low]
      sub_pnl = daily_pnl[non_low]
      results = optimize_portfolio(
        sub_pnl, sub_corr, n_non_low, objective, max(n_portfolios, 3),
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        prioritize_stability=prioritize_stability, external_scores=external_scores,
        streamlit_logic=streamlit_logic,
        expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
        seed_weights=seed_weights,
      )
      non_low_portfolios = [r["assets"] for r in results]
    elif n_non_low == 0:
      non_low_portfolios = [[]]
    else:
      continue

    for portfolio in non_low_portfolios:
      if n_low == 0:
        all_candidates.append(sorted(portfolio))
        continue
      if not pool_low or not portfolio:
        continue
      scores = [
        (a, _score_low_trade_asset(
          a, portfolio, corr_matrix, objective, daily_pnl, prioritize_stability,
        ))
        for a in pool_low if a not in portfolio
      ]
      scores.sort(key=lambda x: x[1])
      best_low = [a for a, _ in scores[:n_low]]
      all_candidates.append(sorted(portfolio + best_low))

  # Deduplicate and score
  unique: dict[tuple, float] = {}
  for portfolio in all_candidates:
    key = tuple(portfolio)
    if key in unique:
      continue
    cols = [c for c in portfolio if c in daily_pnl.columns]
    if cols:
      combined = daily_pnl[cols].sum(axis=1)
      if objective in SCORING_FUNCTIONS:
        unique[key] = SCORING_FUNCTIONS[objective](combined)
      else:
        unique[key] = 1 - calculate_stability(combined)
    else:
      unique[key] = 1.0

  sorted_results = sorted(unique.items(), key=lambda x: x[1])
  # Diversity filter: accept only portfolios differing by ≥2 assets
  accepted_tuples: list[tuple] = []
  for assets_tuple, score in sorted_results:
    asset_set = set(assets_tuple)
    if all(len(asset_set.symmetric_difference(set(a))) >= 2 for a in accepted_tuples):
      accepted_tuples.append(assets_tuple)
    if len(accepted_tuples) >= n_portfolios:
      break
  if len(accepted_tuples) < n_portfolios:
    for assets_tuple, _ in sorted_results:
      if assets_tuple not in accepted_tuples:
        accepted_tuples.append(assets_tuple)
      if len(accepted_tuples) >= n_portfolios:
        break
  # Convert to dict format with metrics
  final = []
  for assets_tuple in accepted_tuples:
    assets_list = list(assets_tuple)
    cols = [c for c in assets_list if c in daily_pnl.columns]
    combined = daily_pnl[cols].sum(axis=1) if cols else pd.Series()
    cum = combined.cumsum()
    gp = float(combined[combined > 0].sum()) if not combined.empty else 0
    gl = float(abs(combined[combined < 0].sum())) if not combined.empty else 0
    pf = min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
    peak = np.maximum.accumulate(cum.values) if not cum.empty else np.array([0])
    max_dd = float((cum.values - peak).min()) if not cum.empty else 0
    final.append({
      "assets": assets_list,
      "metrics": {
        "net_profit": float(cum.iloc[-1]) if not cum.empty else 0,
        "profit_factor": float(pf),
        "max_drawdown": float(max_dd),
        "stability": float(calculate_stability(combined)) if not combined.empty else 0,
        "n_assets": len(assets_list),
      },
    })
  return final


def _has_real_constraints(tc: dict | None) -> bool:
  """Check if TC config has meaningful constraints (not just defaults)."""
  if not tc:
    return False
  return tc.get("max_low", 999) < 999 or tc.get("min_high", 0) > 0


def _get_allocations_for_side(
  side_assets: list[str], n_target: int, tc: dict | None,
  trade_counts: dict[str, int],
) -> list[dict] | list[str]:
  """Classify assets and enumerate valid allocations for one side (L or S).

  Returns list of allocation dicts with pool info, or a flat list of assets
  if no real constraints exist.
  """
  if not tc or not _has_real_constraints(tc):
    return side_assets  # No constraint — return full pool

  low_thresh = tc.get("low_threshold", 0)
  max_low = tc.get("max_low", 999)
  high_thresh = tc.get("high_threshold", 0)
  min_high = tc.get("min_high", 0)
  p_low, p_mid, p_high = _classify_assets(
    side_assets, trade_counts, low_thresh, high_thresh,
  )
  allocs = _enumerate_allocations(n_target, max_low, min_high, len(p_low), len(p_high))
  if not allocs:
    return []
  result = []
  for n_low, n_high_min, n_flex in allocs:
    result.append({
      "pool_low": p_low, "pool_mid": p_mid, "pool_high": p_high,
      "n_low": n_low, "n_high_min": n_high_min, "n_flex": n_flex,
    })
  return result


def optimize_split_with_trade_constraints(
  daily_pnl: pd.DataFrame, corr_matrix: pd.DataFrame,
  n_longs: int, n_shorts: int, trade_counts: dict[str, int],
  tc_longs: dict | None, tc_shorts: dict | None,
  within_objective: str = "uncorrelated", combined_objective: str = "stability",
  n_portfolios: int = 3, seed_metrics: list[str] | None = None,
  max_seeds: int = 20, prioritize_stability: bool = False,
  external_scores: dict | None = None,
  streamlit_logic: bool = False,
  expand_pool: bool = True,
  seed_prior_weight: float = 0.0,
  seed_weights: dict[str, float] | None = None,
  **kwargs,
) -> list[dict]:
  """Pool-partitioned optimizer for split Long/Short portfolios.

  Applies TC constraints independently per direction:
  1. Classifies each side's assets into Low/Mid/High pools
  2. Enumerates valid allocation combos (n_low, n_high_min, n_flex)
  3. Runs split optimizer per combo on high+mid pool
  4. Appends best low-trade assets per direction
  5. Deduplicates across all combos
  """
  all_assets = corr_matrix.columns.tolist()
  long_assets = [a for a in all_assets if a.endswith("_Long")]
  short_assets = [a for a in all_assets if a.endswith("_Short")]

  # If no real constraints on either side, use the normal split optimizer
  if not _has_real_constraints(tc_longs) and not _has_real_constraints(tc_shorts):
    return optimize_split_portfolio(
      corr_matrix, n_longs, n_shorts,
      within_objective=within_objective, combined_objective=combined_objective,
      n_portfolios=n_portfolios, daily_returns=daily_pnl,
      prioritize_stability=prioritize_stability, seed_metrics=seed_metrics,
      max_seeds=max_seeds, external_scores=external_scores,
      streamlit_logic=streamlit_logic,
      expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
      seed_weights=seed_weights,
    )

  # Get allocations for each side
  long_allocs = _get_allocations_for_side(long_assets, n_longs, tc_longs, trade_counts)
  short_allocs = _get_allocations_for_side(short_assets, n_shorts, tc_shorts, trade_counts)

  if not long_allocs or not short_allocs:
    return []

  # Wrap flat lists (no constraints on that side) into compatible dict format
  if long_allocs and isinstance(long_allocs[0], str):
    long_allocs = [{"pool_low": [], "pool_mid": long_allocs, "pool_high": [],
                    "n_low": 0, "n_high_min": 0, "n_flex": n_longs}]
  if short_allocs and isinstance(short_allocs[0], str):
    short_allocs = [{"pool_low": [], "pool_mid": short_allocs, "pool_high": [],
                     "n_low": 0, "n_high_min": 0, "n_flex": n_shorts}]

  # Run optimizer for each combination of long/short allocations
  all_candidates: list[dict] = []

  for la in long_allocs:
    for sa in short_allocs:
      # Build eligible pools (high + mid) for this combo
      long_eligible = la["pool_high"] + la["pool_mid"]
      short_eligible = sa["pool_high"] + sa["pool_mid"]
      n_long_main = la["n_high_min"] + la["n_flex"]
      n_short_main = sa["n_high_min"] + sa["n_flex"]

      if len(long_eligible) < n_long_main or len(short_eligible) < n_short_main:
        continue

      eligible = long_eligible + short_eligible
      sub_corr = corr_matrix.loc[eligible, eligible]
      sub_dr = daily_pnl[eligible] if daily_pnl is not None else None

      # Run split optimizer on the eligible pool
      results = optimize_split_portfolio(
        sub_corr, n_long_main, n_short_main,
        within_objective=within_objective, combined_objective=combined_objective,
        n_portfolios=max(n_portfolios, 3), daily_returns=sub_dr,
        prioritize_stability=prioritize_stability, seed_metrics=seed_metrics,
        max_seeds=max_seeds, external_scores=external_scores,
        streamlit_logic=streamlit_logic,
        expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
        seed_weights=seed_weights,
      )

      # Append best low-trade assets to each result
      for r in results:
        longs_base = list(r.get("longs", []))
        shorts_base = list(r.get("shorts", []))

        # Add best low-trade longs
        if la["n_low"] > 0 and la["pool_low"]:
          low_scores = []
          for a in la["pool_low"]:
            if a in longs_base:
              continue
            low_scores.append((
              a,
              _score_low_trade_asset(
                a, longs_base, corr_matrix, within_objective,
                daily_pnl, prioritize_stability,
              ),
            ))
          low_scores.sort(key=lambda x: x[1])
          longs_base = longs_base + [a for a, _ in low_scores[:la["n_low"]]]

        # Add best low-trade shorts
        if sa["n_low"] > 0 and sa["pool_low"]:
          low_scores = []
          for a in sa["pool_low"]:
            if a in shorts_base:
              continue
            low_scores.append((
              a,
              _score_low_trade_asset(
                a, shorts_base, corr_matrix, within_objective,
                daily_pnl, prioritize_stability,
              ),
            ))
          low_scores.sort(key=lambda x: x[1])
          shorts_base = shorts_base + [a for a, _ in low_scores[:sa["n_low"]]]

        final_selection = longs_base + shorts_base
        r2 = 0.0
        score = r.get("score", 0.0)
        metrics: dict = {}
        if daily_pnl is not None:
          combined_pnl = daily_pnl[final_selection].sum(axis=1)
          r2 = calculate_stability(combined_pnl)
          cum = combined_pnl.cumsum()
          peak = np.maximum.accumulate(cum.values)
          max_dd = float((cum.values - peak).min())
          gp = float(combined_pnl[combined_pnl > 0].sum())
          gl = abs(float(combined_pnl[combined_pnl < 0].sum()))
          pf = min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
          metrics = {
            "net_profit": float(cum.iloc[-1]) if not cum.empty else 0,
            "profit_factor": float(pf),
            "max_drawdown": float(max_dd),
            "stability": float(r2),
            "n_assets": len(final_selection),
          }

        all_candidates.append({
          "longs": sorted(longs_base),
          "shorts": sorted(shorts_base),
          "assets": sorted(final_selection),
          "score": score,
          "stability_r2": r2,
          "metrics": metrics,
        })

  # Deduplicate and sort (apply stability blend matching optimize_split_portfolio)
  unique: dict[tuple, dict] = {}
  for c in all_candidates:
    key = tuple(sorted(c["longs"] + c["shorts"]))
    if key not in unique:
      if prioritize_stability:
        c["score"] = 0.3 * c["score"] + 0.7 * (1 - c.get("stability_r2", 0))
      unique[key] = c
  sorted_results = sorted(unique.values(), key=lambda x: x["score"])
  # Diversity filter: accept only portfolios differing by ≥2 assets
  accepted: list[dict] = []
  for c in sorted_results:
    c_set = set(c["assets"])
    if all(len(c_set.symmetric_difference(set(a["assets"]))) >= 2 for a in accepted):
      accepted.append(c)
    if len(accepted) >= n_portfolios:
      break
  if len(accepted) < n_portfolios:
    for c in sorted_results:
      if c not in accepted:
        accepted.append(c)
      if len(accepted) >= n_portfolios:
        break
  return accepted
