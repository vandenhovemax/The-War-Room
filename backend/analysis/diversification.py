"""Diversification algorithm dispatcher.

Routes portfolio optimization to the selected algorithm:
- 'greedy': Existing correlation/PnL-based greedy optimizer (default)
- 'hrp': Hierarchical Risk Parity
- 'pca': PCA-based clustering
- 'quality_first': Quality-first with correlation guardrail
- 'tournament': Tournament draft selection
- 'marginal_sharpe': Marginal Sharpe contribution
- 'ensemble': Ensemble consensus (runs multiple methods, votes)
"""
from __future__ import annotations

import pandas as pd

from backend.analysis.hrp_optimizer import hrp_select_assets, hrp_select_split
from backend.analysis.optimizer import optimize_portfolio
from backend.analysis.optimizer_split import optimize_split_portfolio
from backend.analysis.optimizer_tc import (
  optimize_split_with_trade_constraints,
  optimize_with_trade_constraints,
)
from backend.analysis.pca_optimizer import pca_select_assets, pca_select_split
from backend.utils.logger import get_logger

_logger = get_logger(__name__)

# CS-026: decision — OG v1/v2 are **FROZEN pinned experiments**, not legacy
# deletable code. They reproduce historical research runs for strategies that
# were optimized with those engines, so deleting them would silently
# invalidate saved comparisons. But any fix to the "current" optimizer does
# NOT land in these trees. Live routes still dispatch to them when
# optimizer_engine="og_v1"/"og_v2", so we emit a loud one-line warning on
# every such dispatch — invisible use is the failure mode the audit called
# out (see AUDIT_MASTER.md CS-026 / FN-009).


def _get_optimizer_funcs(engine: str):
  """Return (optimize_portfolio, optimize_split, optimize_tc, optimize_split_tc) for engine."""
  if engine == "og_v1":
    _logger.warning(
      "CS-026 LEGACY-FROZEN: optimizer_engine='og_v1' dispatched. This is a "
      "pinned A/B variant that does NOT receive fixes to the current "
      "optimizer. Use engine='current' for live/tradeable research."
    )
    from backend.analysis.optimizer_og_v1 import optimize_portfolio as _op
    from backend.analysis.optimizer_split_og_v1 import optimize_split_portfolio as _osp
    from backend.analysis.optimizer_tc_og_v1 import (
      optimize_split_with_trade_constraints as _ostc,
      optimize_with_trade_constraints as _otc,
    )
    return _op, _osp, _otc, _ostc
  if engine == "og_v2":
    _logger.warning(
      "CS-026 LEGACY-FROZEN: optimizer_engine='og_v2' dispatched. This is a "
      "pinned A/B variant that does NOT receive fixes to the current "
      "optimizer. Use engine='current' for live/tradeable research."
    )
    from backend.analysis.optimizer_og_v2 import optimize_portfolio as _op
    from backend.analysis.optimizer_split_og_v2 import optimize_split_portfolio as _osp
    from backend.analysis.optimizer_tc_og_v2 import (
      optimize_split_with_trade_constraints as _ostc,
      optimize_with_trade_constraints as _otc,
    )
    return _op, _osp, _otc, _ostc
  # "current" or "fast" — use the default imports above
  return optimize_portfolio, optimize_split_portfolio, optimize_with_trade_constraints, optimize_split_with_trade_constraints


def _apply_dd_filter(
  candidates: list[dict],
  daily_pnl: pd.DataFrame,
  max_dd_pct: float,
) -> list[dict]:
  """Remove candidates whose max drawdown exceeds max_dd_pct of peak equity.

  max_dd_pct=0 → disabled (passthrough).
  max_dd_pct=30.0 → reject portfolios with >30% drawdown from peak.
  """
  if max_dd_pct <= 0 or not candidates:
    return candidates

  import numpy as np

  filtered = []
  for c in candidates:
    assets = c.get("assets", [])
    valid = [a for a in assets if a in daily_pnl.columns]
    if not valid:
      filtered.append(c)  # keep if we can't evaluate
      continue
    combined = daily_pnl[valid].sum(axis=1)
    cum = combined.cumsum()
    peak = np.maximum.accumulate(cum.values)
    max_dd = float((cum.values - peak).min())
    peak_val = float(peak.max())
    if peak_val > 0:
      dd_pct = abs(max_dd) / peak_val * 100
    else:
      dd_pct = float("inf")  # all-negative equity = infinite drawdown
    if dd_pct > max_dd_pct:
      continue  # reject this candidate
    filtered.append(c)
  return filtered if filtered else candidates[:1]  # fallback: keep best if all filtered


def _diverse_select(
  candidates: list[dict],
  n_portfolios: int,
  max_overlap: float | None = None,
) -> list[dict]:
  """Jaccard-overlap-based diversity filter for portfolio candidates.

  Greedy selection: pick best-scored first, then only accept next candidates
  whose Jaccard overlap with ALL already-selected is <= max_overlap.

  max_overlap=None → passthrough (no additional filtering).
  max_overlap=0.5  → candidates must share at most 50% of assets with every accepted.
  max_overlap=0.0  → candidates must be completely disjoint.
  """
  if max_overlap is None or len(candidates) <= 1:
    return candidates[:n_portfolios]

  def _get_assets(c: dict) -> set[str]:
    return set(c.get("assets", []))

  def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
      return 1.0
    return len(a & b) / len(union)

  selected: list[dict] = [candidates[0]]
  selected_sets: list[set[str]] = [_get_assets(candidates[0])]

  for c in candidates[1:]:
    if len(selected) >= n_portfolios:
      break
    c_set = _get_assets(c)
    if all(_jaccard(c_set, s) <= max_overlap for s in selected_sets):
      selected.append(c)
      selected_sets.append(c_set)

  # Fallback: if Jaccard filter too strict, fill with remaining unique
  if len(selected) < n_portfolios:
    for c in candidates:
      if c not in selected:
        selected.append(c)
      if len(selected) >= n_portfolios:
        break

  return selected


def diversify_portfolio(
  div_algo: str,
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_assets: int,
  objective: str = "hybrid",
  n_portfolios: int = 3,
  seed_metrics: list[str] | None = None,
  max_seeds: int = 20,
  prioritize_stability: bool = False,
  external_scores: dict | None = None,
  trade_counts: dict | None = None,
  tc_constraints: dict | None = None,
  streamlit_logic: bool = False,
  max_overlap: float | None = None,
  expand_pool: bool = True,
  seed_prior_weight: float = 0.0,
  seed_weights: dict[str, float] | None = None,
  max_dd_pct: float = 0.0,
  precomputed_seeds: dict[str, float] | None = None,
  fast_mode: bool = False,
  engine: str = "current",
  # Genetic algorithm params
  genetic_generations: int = 50,
  genetic_population: int = 100,
  genetic_mutation_rate: float = 0.10,
  genetic_crossover_rate: float = 0.70,
  genetic_elite_pct: float = 0.10,
) -> list[dict]:
  """Non-split portfolio dispatcher. Returns list[dict] with assets + metrics."""
  # Only route to HRP/PCA when streamlit_logic is off (pure correlation mode
  # should always use greedy to match the Streamlit benchmark behavior)
  if not streamlit_logic:
    if div_algo == "hrp":
      raw = hrp_select_assets(
        corr_matrix, daily_pnl, n_assets, n_portfolios,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        external_scores=external_scores,
      )
      result = _enrich_raw_lists(raw, daily_pnl)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "pca":
      raw = pca_select_assets(
        daily_pnl, n_assets, n_portfolios, seed_metrics,
        max_seeds=max_seeds, external_scores=external_scores,
      )
      result = _enrich_raw_lists(raw, daily_pnl)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "quality_first":
      from backend.analysis.optimizer_quality_first import quality_first_select
      _seeds = precomputed_seeds or {}
      raw = quality_first_select(daily_pnl, corr_matrix, n_assets, _seeds, n_portfolios)
      result = _enrich_raw_lists(raw, daily_pnl)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "tournament":
      from backend.analysis.optimizer_tournament import tournament_select
      _seeds = precomputed_seeds or {}
      raw = tournament_select(daily_pnl, corr_matrix, n_assets, _seeds, objective, n_portfolios)
      result = _enrich_raw_lists(raw, daily_pnl)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "genetic":
      from backend.analysis.optimizer_genetic import genetic_select
      _seeds = precomputed_seeds or {}
      raw = genetic_select(
        daily_pnl, corr_matrix, n_assets, _seeds, objective, n_portfolios,
        n_generations=genetic_generations, population_size=genetic_population,
        mutation_rate=genetic_mutation_rate, crossover_rate=genetic_crossover_rate,
        elite_pct=genetic_elite_pct,
      )
      result = _enrich_raw_lists(raw, daily_pnl)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "marginal_sharpe":
      from backend.analysis.optimizer_marginal_sharpe import marginal_sharpe_select
      _seeds = precomputed_seeds or {}
      raw = marginal_sharpe_select(daily_pnl, corr_matrix, n_assets, _seeds, max_seeds, n_portfolios)
      result = _enrich_raw_lists(raw, daily_pnl)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "ensemble":
      from backend.analysis.optimizer_ensemble import ensemble_select
      _seeds = precomputed_seeds or {}
      raw = ensemble_select(daily_pnl, corr_matrix, n_assets, _seeds, max_seeds, n_portfolios, seed_metrics, external_scores)
      result = _enrich_raw_lists(raw, daily_pnl)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "walk_forward":
      from backend.analysis.optimizer_walk_forward import walk_forward_select
      raw = walk_forward_select(
        daily_pnl, corr_matrix, n_assets, objective=objective,
        n_portfolios=n_portfolios, seed_metrics=seed_metrics,
        max_seeds=max_seeds, prioritize_stability=prioritize_stability,
        external_scores=external_scores, streamlit_logic=streamlit_logic,
        max_overlap=max_overlap, expand_pool=expand_pool,
        seed_prior_weight=seed_prior_weight, seed_weights=seed_weights,
        max_dd_pct=max_dd_pct, engine=engine,
      )
      result = _apply_dd_filter(raw, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

  # Select optimizer functions based on engine
  _op, _osp, _otc, _ostc = _get_optimizer_funcs(engine)

  # Default: greedy
  if tc_constraints and trade_counts:
    result = _otc(
      daily_pnl, corr_matrix, n_assets, trade_counts, tc_constraints,
      objective, n_portfolios, seed_metrics=seed_metrics,
      max_seeds=max_seeds, prioritize_stability=prioritize_stability,
      external_scores=external_scores, streamlit_logic=streamlit_logic,
      expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
      seed_weights=seed_weights,
      precomputed_seeds=precomputed_seeds,
      fast_mode=fast_mode,
    )
    result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
    return _diverse_select(result, n_portfolios, max_overlap)
  result = _op(
    daily_pnl, corr_matrix, n_assets, objective, n_portfolios,
    seed_metrics=seed_metrics, max_seeds=max_seeds,
    prioritize_stability=prioritize_stability,
    external_scores=external_scores,
    streamlit_logic=streamlit_logic,
    expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
    seed_weights=seed_weights,
    precomputed_seeds=precomputed_seeds,
    fast_mode=fast_mode,
  )
  result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
  return _diverse_select(result, n_portfolios, max_overlap)


def diversify_split_portfolio(
  div_algo: str,
  daily_pnl: pd.DataFrame,
  corr_matrix: pd.DataFrame,
  n_longs: int,
  n_shorts: int,
  within_objective: str = "uncorrelated",
  combined_objective: str = "stability",
  n_portfolios: int = 3,
  seed_metrics: list[str] | None = None,
  max_seeds: int = 20,
  prioritize_stability: bool = False,
  external_scores: dict | None = None,
  trade_counts: dict | None = None,
  tc_longs: dict | None = None,
  tc_shorts: dict | None = None,
  streamlit_logic: bool = False,
  max_overlap: float | None = None,
  expand_pool: bool = True,
  seed_prior_weight: float = 0.0,
  seed_weights: dict[str, float] | None = None,
  max_dd_pct: float = 0.0,
  precomputed_seeds: dict[str, float] | None = None,
  fast_mode: bool = False,
  engine: str = "current",
  # Genetic algorithm params
  genetic_generations: int = 50,
  genetic_population: int = 100,
  genetic_mutation_rate: float = 0.10,
  genetic_crossover_rate: float = 0.70,
  genetic_elite_pct: float = 0.10,
) -> list[dict]:
  """Split L/S portfolio dispatcher. Returns list[dict]."""
  # Only route to HRP/PCA when streamlit_logic is off
  if not streamlit_logic:
    if div_algo == "hrp":
      result = hrp_select_split(
        corr_matrix, daily_pnl, n_longs, n_shorts, n_portfolios,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        external_scores=external_scores,
      )
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "pca":
      result = pca_select_split(
        daily_pnl, n_longs, n_shorts, n_portfolios, seed_metrics,
        max_seeds=max_seeds, external_scores=external_scores,
      )
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "quality_first":
      from backend.analysis.optimizer_quality_first import quality_first_select_split
      _seeds = precomputed_seeds or {}
      result = quality_first_select_split(daily_pnl, corr_matrix, n_longs, n_shorts, _seeds, n_portfolios)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "tournament":
      from backend.analysis.optimizer_tournament import tournament_select_split
      _seeds = precomputed_seeds or {}
      result = tournament_select_split(daily_pnl, corr_matrix, n_longs, n_shorts, _seeds, within_objective, n_portfolios)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "genetic":
      from backend.analysis.optimizer_genetic import genetic_select_split
      _seeds = precomputed_seeds or {}
      result = genetic_select_split(
        daily_pnl, corr_matrix, n_longs, n_shorts, _seeds, within_objective, n_portfolios,
        n_generations=genetic_generations, population_size=genetic_population,
        mutation_rate=genetic_mutation_rate, crossover_rate=genetic_crossover_rate,
        elite_pct=genetic_elite_pct,
      )
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "marginal_sharpe":
      from backend.analysis.optimizer_marginal_sharpe import marginal_sharpe_select_split
      _seeds = precomputed_seeds or {}
      result = marginal_sharpe_select_split(daily_pnl, corr_matrix, n_longs, n_shorts, _seeds, max_seeds, n_portfolios)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "ensemble":
      from backend.analysis.optimizer_ensemble import ensemble_select_split
      _seeds = precomputed_seeds or {}
      result = ensemble_select_split(daily_pnl, corr_matrix, n_longs, n_shorts, _seeds, max_seeds, n_portfolios, seed_metrics, external_scores)
      result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

    if div_algo == "walk_forward":
      from backend.analysis.optimizer_walk_forward import walk_forward_select_split
      raw = walk_forward_select_split(
        daily_pnl, corr_matrix, n_longs, n_shorts,
        within_objective=within_objective, combined_objective=combined_objective,
        n_portfolios=n_portfolios, seed_metrics=seed_metrics,
        max_seeds=max_seeds, prioritize_stability=prioritize_stability,
        external_scores=external_scores, streamlit_logic=streamlit_logic,
        max_overlap=max_overlap, expand_pool=expand_pool,
        seed_prior_weight=seed_prior_weight, seed_weights=seed_weights,
        max_dd_pct=max_dd_pct, engine=engine,
      )
      result = _apply_dd_filter(raw, daily_pnl, max_dd_pct)
      return _diverse_select(result, n_portfolios, max_overlap)

  # Select optimizer functions based on engine
  _op, _osp, _otc, _ostc = _get_optimizer_funcs(engine)

  # Default: greedy
  tc_active = bool(tc_longs or tc_shorts)
  if tc_active and trade_counts:
    result = _ostc(
      daily_pnl, corr_matrix, n_longs, n_shorts, trade_counts,
      tc_longs, tc_shorts, within_objective, combined_objective,
      n_portfolios, seed_metrics=seed_metrics, max_seeds=max_seeds,
      prioritize_stability=prioritize_stability,
      external_scores=external_scores, streamlit_logic=streamlit_logic,
      expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
      seed_weights=seed_weights,
      precomputed_seeds=precomputed_seeds,
      fast_mode=fast_mode,
    )
    result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
    return _diverse_select(result, n_portfolios, max_overlap)
  result = _osp(
    corr_matrix, n_longs, n_shorts,
    within_objective=within_objective,
    combined_objective=combined_objective,
    n_portfolios=n_portfolios, daily_returns=daily_pnl,
    prioritize_stability=prioritize_stability,
    seed_metrics=seed_metrics, max_seeds=max_seeds,
    external_scores=external_scores,
    streamlit_logic=streamlit_logic,
    expand_pool=expand_pool, seed_prior_weight=seed_prior_weight,
    seed_weights=seed_weights,
    precomputed_seeds=precomputed_seeds,
    fast_mode=fast_mode,
  )
  result = _apply_dd_filter(result, daily_pnl, max_dd_pct)
  return _diverse_select(result, n_portfolios, max_overlap)


def _enrich_raw_lists(
  raw: list[list[str]], daily_pnl: pd.DataFrame,
) -> list[dict]:
  """Convert HRP/PCA raw asset lists into optimizer-compatible dicts."""
  import numpy as np

  from backend.analysis.scoring import calculate_stability

  results = []
  for assets in raw:
    valid = [a for a in assets if a in daily_pnl.columns]
    if not valid:
      results.append({"assets": assets, "metrics": {}})
      continue
    combined = daily_pnl[valid].sum(axis=1)
    cum = combined.cumsum()
    peak = np.maximum.accumulate(cum.values)
    max_dd = float((cum.values - peak).min())
    gp = float(combined[combined > 0].sum())
    gl = abs(float(combined[combined < 0].sum()))
    pf = min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
    stab = float(calculate_stability(combined))
    results.append({
      "assets": assets,
      "metrics": {
        "net_profit": float(cum.iloc[-1]) if not cum.empty else 0,
        "profit_factor": float(pf),
        "max_drawdown": float(max_dd),
        "stability": stab,
        "n_assets": len(assets),
      },
    })
  return results
