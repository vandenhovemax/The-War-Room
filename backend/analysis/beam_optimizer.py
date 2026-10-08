"""Beam Search Optimizer — directional batches with multi-layer portfolio building.

L1:   Per-batch asset selection (keep K diverse variants)
L1.5: Harmony gate — filter batch combos by inter-batch affinity (optional)
L2:   User-defined batch groupings (exhaustive combo search)
L2.5: Harmony gate — check group-output affinity before L3 (optional)
L3:   Global portfolio assembly (exhaustive combo search)
"""
from __future__ import annotations

import itertools
import random

import numpy as np
import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Cap Cartesian product enumeration to prevent exponential blowup.
# Beyond this, random sampling from the product space is used instead.
_MAX_COMBOS = 10_000

from backend.analysis.correlation import get_correlation_matrix
from backend.analysis.harmony import (
    HarmonyResult,
    filter_batches_by_harmony,
    filter_groups_by_harmony,
)
from backend.analysis.optimizer import _asset_pf, optimize_portfolio
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
  fast_mode: bool = False,
  # --- Harmony Gate (L1.5 / L2.5) ---
  harmony_enabled: bool = False,
  harmony_strength_l15: int = 50,
  harmony_strength_l25: int = 50,
  harmony_weights: dict[str, float] | None = None,
  harmony_per_group: dict[int, bool] | None = None,  # manual mode: per-group L1.5 toggle
  seed: int = 42,
) -> list[dict]:
  """Run 3-layer beam search optimization for directional batches."""
  # Per-call seeded RNG for reproducible random sampling at L2/L3 combo caps.
  # A module-level RNG would drift across concurrent optimization runs —
  # each call gets its own so results are reproducible per-run.
  _rng = random.Random(seed)
  # Sort group contents for order-invariant results
  l2_groups = [sorted(g) for g in l2_groups]

  # Collect all batch IDs from groups + ungrouped
  grouped_bids = set(b for group in l2_groups for b in group)
  all_bids = set(asset_batch_map.values())

  # --- Pre-compute shared data once for all layers ---
  full_corr = get_correlation_matrix(daily_pnl, corr_method)

  _pre_metrics = seed_metrics or ["sortino"]
  precomputed_seeds = compute_seed_scores(
    daily_pnl, daily_pnl.columns.tolist(), _pre_metrics, external_scores,
  )

  # Pre-extract numpy arrays for fast combo scoring
  _asset_arrays: dict[str, np.ndarray] = {
    col: daily_pnl[col].values for col in daily_pnl.columns
  }

  # === L1: Per-batch asset selection ===
  l1_variants: dict[int, list[dict]] = {}
  # CS-016: track batches that enter L1 with no usable assets so the L1→L2
  # summary can surface them instead of silently skipping.
  starved_no_assets: list[int] = []

  for bid in all_bids:
    batch_cols = [c for c in daily_pnl.columns if asset_batch_map.get(c) == bid]
    if not batch_cols:
      starved_no_assets.append(bid)
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
    corr = full_corr.loc[batch_cols, batch_cols]

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
        precomputed_seeds=precomputed_seeds,
        fast_mode=fast_mode,
      )
    else:
      # Over-generate for dedup headroom
      raw = optimize_portfolio(
        batch_pnl, corr, n_per_batch, obj_l1,
        n_portfolios=k_l1 * 2,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        prioritize_stability=prioritize_stability,
        external_scores=external_scores,
        precomputed_seeds=precomputed_seeds,
        fast_mode=fast_mode,
      )
    deduped = _deduplicate_variants(raw, threshold=0.7)
    l1_variants[bid] = deduped[:k_l1]

  # CS-016: warn-loud on any batch that survived L1 but dedup-collapsed to
  # zero variants — before the fix these disappeared from every downstream
  # portfolio with no log line at all. Also log the input→surviving count.
  starved_after_dedup: list[int] = [
    bid for bid, variants in l1_variants.items() if not variants
  ]
  if starved_no_assets:
    logger.warning(
      "CS-016 BatchStarved: %d batch(es) had no usable assets in L1 "
      "(no daily_pnl columns mapped): %s. These batches were silently "
      "skipped pre-fix; they now appear in result['starved_batches'].",
      len(starved_no_assets), sorted(starved_no_assets),
    )
  if starved_after_dedup:
    logger.warning(
      "CS-016 BatchStarved: %d batch(es) collapsed to 0 variants after "
      "Jaccard dedup (threshold=0.7, k_l1*2 over-generation): %s. Any L2 "
      "group containing these batches will be skipped — surfacing on "
      "result['starved_batches'].",
      len(starved_after_dedup), sorted(starved_after_dedup),
    )
  logger.info(
    "L1 summary: %d input batches → %d with variants → %d survived dedup "
    "(starved: no_assets=%d, post_dedup=%d)",
    len(all_bids), len(l1_variants),
    sum(1 for v in l1_variants.values() if v),
    len(starved_no_assets), len(starved_after_dedup),
  )

  # === L1.5: Harmony gate — filter batches by inter-batch affinity ===
  l15_harmony_results: dict[int, HarmonyResult] = {}  # per-group harmony data
  if harmony_enabled and harmony_strength_l15 > 0:
    for gidx, group_bids in enumerate(l2_groups):
      # In manual mode, check per-group toggle; in auto, apply to all groups
      apply_l15 = True
      if harmony_per_group is not None:
        apply_l15 = harmony_per_group.get(gidx, False)

      if apply_l15 and len(group_bids) >= 2:
        filtered, hr = filter_batches_by_harmony(
          l1_variants, daily_pnl, asset_batch_map, group_bids,
          strength=harmony_strength_l15,
          weights=harmony_weights,
        )
        l15_harmony_results[gidx] = hr
        # Replace l1_variants for the batches in this group
        for bid in group_bids:
          if bid in filtered:
            l1_variants[bid] = filtered[bid]
          elif bid in l1_variants and bid not in filtered:
            # Batch was removed by harmony gate
            del l1_variants[bid]
        logger.info(
          "L1.5 group %d: harmony=%.3f, all_pass=%s, failed=%s",
          gidx, hr.mean_harmony, hr.all_pass, hr.failed_pairs,
        )

  # === L2: User-defined group combinations ===
  l2_results: list[list[dict]] = []

  for group_bids in l2_groups:
    if l1_quality_gate:
      # Quality Gate: pool ALL L1 survivors, let L2 optimize from full pool
      pool = [a for bid in group_bids for v in l1_variants.get(bid, []) for a in v["assets"]]
      pool = list(dict.fromkeys(pool))  # deduplicate preserving order
      pool.sort()
      if not pool:
        continue
      group_pnl = daily_pnl[[c for c in pool if c in daily_pnl.columns]]
      if group_pnl.empty:
        continue
      valid_cols = [c for c in pool if c in full_corr.columns]
      corr = full_corr.loc[valid_cols, valid_cols]
      target = min(n_per_group, len(group_pnl.columns))
      raw = optimize_portfolio(
        group_pnl, corr, target, obj_l2,
        n_portfolios=k_l2,
        seed_metrics=seed_metrics, max_seeds=max_seeds,
        prioritize_stability=prioritize_stability,
        external_scores=external_scores,
        precomputed_seeds=precomputed_seeds,
        fast_mode=fast_mode,
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
        # CS-016: name the starving batches instead of silently `continue`.
        _missing = [
          gb for gb, vl in zip(group_bids, group_variant_lists) if not vl
        ]
        logger.warning(
          "CS-016 L2 group skipped: group %s dropped because batch(es) %s "
          "have zero L1 variants (starved at L1 — see prior BatchStarved "
          "warnings). Every portfolio that would have used this group is "
          "absent from L3.",
          sorted(group_bids), sorted(_missing),
        )
        continue

      # Cap combo enumeration to prevent exponential blowup
      total_combos = 1
      for vl in group_variant_lists:
        total_combos *= len(vl)

      if total_combos > _MAX_COMBOS:
        logger.info("L2 combo cap: %d total > %d max, sampling %d random combos",
                     total_combos, _MAX_COMBOS, _MAX_COMBOS)
        combos = [
          tuple(_rng.choice(vl) for vl in group_variant_lists)
          for _ in range(_MAX_COMBOS)
        ]
      else:
        combos = list(itertools.product(*group_variant_lists))

      scored_top: list[dict] = []
      for combo in combos:
        merged = list(dict.fromkeys(
          a for v in combo for a in v["assets"]
        ))
        merged.sort()
        score = _score_combo(daily_pnl, merged, obj_l2, full_corr, _asset_arrays)
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

  # === L2.5: Harmony gate — check group-output affinity before L3 ===
  l25_harmony: HarmonyResult | None = None
  if harmony_enabled and harmony_strength_l25 > 0 and len(l2_results) >= 2:
    l2_results, l25_harmony = filter_groups_by_harmony(
      l2_results, daily_pnl,
      strength=harmony_strength_l25,
      weights=harmony_weights,
    )
    logger.info(
      "L2.5: harmony=%.3f, all_pass=%s, failed=%s",
      l25_harmony.mean_harmony, l25_harmony.all_pass, l25_harmony.failed_pairs,
    )

  # === L3: Global assembly ===
  total_l3 = 1
  for lr in l2_results:
    total_l3 *= len(lr)

  if total_l3 > _MAX_COMBOS:
    logger.info("L3 combo cap: %d total > %d max, sampling %d random combos",
                 total_l3, _MAX_COMBOS, _MAX_COMBOS)
    combos = [
      tuple(_rng.choice(lr) for lr in l2_results)
      for _ in range(_MAX_COMBOS)
    ]
  else:
    combos = list(itertools.product(*l2_results))

  # If L2.5 found bad group pairs, build a penalty map for L3 scoring
  _l25_penalty: dict[tuple[int, int], float] = {}
  if l25_harmony and not l25_harmony.all_pass:
    for p in l25_harmony.pairs:
      if not p.passes_gate:
        # Penalty proportional to how far below threshold
        penalty = max(0, (harmony_strength_l25 / 100.0) - p.harmony_score) * 0.5
        _l25_penalty[(p.id_a, p.id_b)] = penalty

  scored_final: list[dict] = []
  for combo in combos:
    merged = list(dict.fromkeys(
      a for v in combo for a in v["assets"]
    ))
    merged.sort()
    score = _score_combo(daily_pnl, merged, obj_l3, full_corr, _asset_arrays)

    # Apply L2.5 harmony penalty: penalize combos that include poorly-harmonized groups
    if _l25_penalty:
      group_indices = list(range(len(combo)))
      for gi in group_indices:
        for gj in group_indices:
          if gi < gj:
            pen = _l25_penalty.get((gi, gj), 0.0)
            score += pen  # higher score = worse (lower-is-better convention)

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
  results = _build_results(accepted, daily_pnl)

  # Attach harmony metadata if enabled
  if harmony_enabled:
    harmony_meta = {
      "enabled": True,
      "strength_l15": harmony_strength_l15,
      "strength_l25": harmony_strength_l25,
      "l15_results": {str(k): v.to_dict() for k, v in l15_harmony_results.items()},
      "l25_result": l25_harmony.to_dict() if l25_harmony else None,
    }
    for r in results:
      r["harmony"] = harmony_meta

  # CS-015: surface the RNG seed on every result so two reruns of the same
  # preset can be verified bit-identical. Without this the seed is implicit
  # (the function default or a caller override) and a silently-changed seed
  # would silently change every WFAS number.
  for r in results:
    r["rng_seed"] = int(seed)

  # CS-016: attach starvation metadata on every result so the UI can
  # surface dropped batches prominently. Pre-fix these were silent.
  _starved = {
    "no_assets": sorted(starved_no_assets),
    "post_dedup": sorted(starved_after_dedup),
  }
  for r in results:
    r["starved_batches"] = _starved

  return results


def _score_combo(
  daily_pnl: pd.DataFrame,
  assets: list[str],
  objective: str,
  full_corr: pd.DataFrame,
  asset_arrays: dict[str, np.ndarray] | None = None,
) -> float:
  """Score a combined set of assets with any objective (lower = better)."""
  cols = [c for c in assets if c in daily_pnl.columns]
  if not cols:
    return float("inf")

  if asset_arrays:
    combined = pd.Series(sum(asset_arrays[c] for c in cols), index=daily_pnl.index)
  else:
    combined = daily_pnl[cols].sum(axis=1)

  # PnL-based objectives
  if objective in SCORING_FUNCTIONS:
    return SCORING_FUNCTIONS[objective](combined)

  # Correlation-based objectives
  if len(cols) < 2:
    return 0.0
  corr = full_corr.loc[cols, cols]
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
