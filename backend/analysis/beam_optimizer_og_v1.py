"""OG v1 (Mar 11) — Original beam search optimizer before speed changes.

LEGACY-FROZEN: A/B engine variant used via optimizer_engine="og_v1" dispatch
in cross_strategy.py. DO NOT MODIFY — saved runs depend on reproducibility.

Extracted from commit 81d1cb2 (2026-03-11). Search algorithms preserved exactly.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from backend.analysis.correlation import get_correlation_matrix
from backend.analysis.optimizer_og_v1 import _asset_pf, optimize_portfolio
from backend.analysis.optimizer_seeds import compute_seed_scores
from backend.analysis.scoring import SCORING_FUNCTIONS, calculate_stability

# Minimum PF to keep an asset even in quality-gate mode
_MIN_GATE_PF = 0.5


def run_beam_optimization(
  daily_pnl: pd.DataFrame,
  asset_batch_map: dict[str, int],
  l2_groups: list[list[int]],
  n_per_batch: int,
  obj_l1: str,
  obj_l2: str,
  obj_l3: str,
  k_l1: int,
  k_l2: int,
  k_l3: int,
  corr_method: str,
  seed_metrics: list[str],
  max_seeds: int,
  prioritize_stability: bool,
  external_scores: dict | None,
  tc_config: dict | None = None,
  trade_counts: dict[str, int] | None = None,
  l1_quality_gate: bool = False,
  n_per_group: int = 15,
  **kwargs,
) -> list[dict]:
  """Run 3-layer beam search optimization for directional batches."""
  # Collect all batch IDs from groups + ungrouped
  grouped_bids = set(b for group in l2_groups for b in group)
  all_bids = set(asset_batch_map.values())

  # === L1: Per-batch asset selection ===
  l1_variants: dict[int, list[dict]] = {}

  for bid in all_bids:
    batch_cols = [c for c in daily_pnl.columns if asset_batch_map.get(c) == bid]
    if not batch_cols:
      continue

    # Quality Gate mode: pass batch assets through but still filter out clearly poor ones
    if l1_quality_gate:
      batch_pnl_qg = daily_pnl[batch_cols]
      gated = [c for c in batch_cols if _asset_pf(batch_pnl_qg[c]) >= _MIN_GATE_PF]
      if not gated:
        gated = batch_cols  # fallback: keep all if nothing passes
      l1_variants[bid] = [{"assets": gated}]
      continue

    if len(batch_cols) <= n_per_batch:
      # Fewer assets than requested — single variant with all assets
      l1_variants[bid] = [{"assets": batch_cols}]
      continue

    batch_pnl = daily_pnl[batch_cols]
    corr = get_correlation_matrix(batch_pnl, corr_method)

    # Use TC-constrained optimizer when position-count constraints are active
    if tc_config and trade_counts:
      from backend.analysis.optimizer_tc import optimize_with_trade_constraints
      batch_tc = {c: trade_counts.get(c, 0) for c in batch_cols}
      raw = optimize_with_trade_constraints(
        batch_pnl, corr, n_per_batch, batch_tc, tc_config, obj_l1,
        n_portfolios=k_l1 * 2,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        prioritize_stability=prioritize_stability,
        external_scores=external_scores,
      )
    else:
      # Over-generate for dedup headroom
      raw = optimize_portfolio(
        batch_pnl, corr, n_per_batch, obj_l1,
        n_portfolios=k_l1 * 2,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        prioritize_stability=prioritize_stability,
        external_scores=external_scores,
      )
    deduped = _deduplicate_variants(raw, threshold=0.7)
    l1_variants[bid] = deduped[:k_l1]

  # === L2: User-defined group combinations ===
  l2_results: list[list[dict]] = []

  for group_bids in l2_groups:
    if l1_quality_gate:
      # Quality Gate: pool ALL L1 survivors, let L2 optimize from full pool
      pool = [a for bid in group_bids for v in l1_variants.get(bid, []) for a in v["assets"]]
      pool = list(dict.fromkeys(pool))  # deduplicate preserving order
      if not pool:
        continue
      group_pnl = daily_pnl[[c for c in pool if c in daily_pnl.columns]]
      if group_pnl.empty:
        continue
      corr = get_correlation_matrix(group_pnl, corr_method)
      target = min(n_per_group, len(group_pnl.columns))
      raw = optimize_portfolio(
        group_pnl, corr, target, obj_l2,
        n_portfolios=k_l2,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        prioritize_stability=prioritize_stability,
        external_scores=external_scores,
      )
      scored = [
        {"assets": r["assets"], "score": 0, "source_bids": group_bids}
        for r in raw
      ]
      l2_results.append(scored[:k_l2])
    else:
      # Top N mode: enumerate combos of L1 variants
      group_variant_lists = [l1_variants.get(bid, []) for bid in group_bids]
      # Skip empty groups
      if not all(group_variant_lists):
        continue
      combos = list(itertools.product(*group_variant_lists))
      scored_top: list[dict] = []
      for combo in combos:
        merged = list(dict.fromkeys(
          a for v in combo for a in v["assets"]
        ))
        score = _score_combo(daily_pnl, merged, obj_l2, corr_method)
        # L1 rank bonus: penalize lower-ranked L1 variants slightly
        rank_penalty = sum(
          group_variant_lists[i].index(v) if v in group_variant_lists[i] else 0
          for i, v in enumerate(combo)
        ) * 0.005
        scored_top.append({
          "assets": merged,
          "score": score + rank_penalty,
          "source_bids": group_bids,
        })
      scored_top.sort(key=lambda x: x["score"])
      l2_results.append(scored_top[:k_l2])

  # Auto solo-group for ungrouped batches
  for bid in all_bids - grouped_bids:
    variants = l1_variants.get(bid, [])
    if variants:
      l2_results.append([
        {"assets": v["assets"], "score": 0, "source_bids": [bid]}
        for v in variants
      ])

  if not l2_results:
    return []

  # === L3: Global assembly ===
  combos = list(itertools.product(*l2_results))
  scored_final: list[dict] = []
  for combo in combos:
    merged = list(dict.fromkeys(
      a for v in combo for a in v["assets"]
    ))
    score = _score_combo(daily_pnl, merged, obj_l3, corr_method)
    scored_final.append({"assets": merged, "score": score})

  scored_final.sort(key=lambda x: x["score"])
  # Diversity filter: accept only portfolios differing by ≥2 assets
  accepted: list[dict] = []
  for c in scored_final:
    c_set = set(c["assets"])
    if all(len(c_set.symmetric_difference(set(a["assets"]))) >= 2 for a in accepted):
      accepted.append(c)
    if len(accepted) >= k_l3:
      break
  # Fallback: fill with remaining unique if diversity too strict
  if len(accepted) < k_l3:
    for c in scored_final:
      if c not in accepted:
        accepted.append(c)
      if len(accepted) >= k_l3:
        break
  return _build_results(accepted, daily_pnl)


def _score_combo(
  daily_pnl: pd.DataFrame,
  assets: list[str],
  objective: str,
  corr_method: str,
) -> float:
  """Score a combined set of assets with any objective (lower = better)."""
  cols = [c for c in assets if c in daily_pnl.columns]
  if not cols:
    return float("inf")
  combined = daily_pnl[cols].sum(axis=1)

  # PnL-based objectives
  if objective in SCORING_FUNCTIONS:
    return SCORING_FUNCTIONS[objective](combined)

  # Correlation-based objectives
  if len(cols) < 2:
    return 0.0
  corr = get_correlation_matrix(daily_pnl[cols], corr_method)
  mask = np.triu(np.ones(corr.shape), k=1).astype(bool)
  upper = corr.where(mask).stack()
  if upper.empty:
    return 0.0

  if objective == "uncorrelated":
    return float(upper.abs().mean())
  if objective == "anti_correlated":
    return float(upper.mean())
  # hybrid (default)
  return float(upper.mean() + 0.2 * upper.abs().mean())


def _jaccard_similarity(a: list[str], b: list[str]) -> float:
  """Jaccard index: |intersection| / |union|."""
  sa, sb = set(a), set(b)
  union = sa | sb
  if not union:
    return 1.0
  return len(sa & sb) / len(union)


def _deduplicate_variants(
  variants: list[dict], threshold: float = 0.7,
) -> list[dict]:
  """Remove variants that are too similar (Jaccard > threshold)."""
  kept: list[dict] = []
  for v in variants:
    assets = v.get("assets", [])
    is_dup = any(
      _jaccard_similarity(assets, k.get("assets", [])) > threshold
      for k in kept
    )
    if not is_dup:
      kept.append(v)
  return kept


def _build_results(
  scored: list[dict], daily_pnl: pd.DataFrame,
) -> list[dict]:
  """Build final result dicts with portfolio metrics."""
  results: list[dict] = []
  for s in scored:
    assets = s["assets"]
    cols = [c for c in assets if c in daily_pnl.columns]
    if not cols:
      continue
    combined = daily_pnl[cols].sum(axis=1)
    cum = combined.cumsum()
    gp = float(combined[combined > 0].sum())
    gl = float(abs(combined[combined < 0].sum()))
    dd = float((cum - cum.cummax()).min()) if not cum.empty else 0
    stab = float(calculate_stability(combined)) if len(combined) > 1 else 0
    results.append({
      "assets": assets,
      "metrics": {
        "net_profit": round(float(cum.iloc[-1]) if not cum.empty else 0, 2),
        "profit_factor": round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0),
        "max_drawdown": round(dd, 2),
        "stability": round(stab, 4),
        "n_assets": len(cols),
      },
    })
  return results
