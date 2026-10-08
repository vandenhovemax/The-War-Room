"""Batch Harmony Gate — measures inter-batch affinity for portfolio construction.

Computes 5 pairwise metrics between batch (or group) equity curves:
  1. Return Correlation         — do they move together?
  2. Correlation Stability      — is the relationship reliable?
  3. Drawdown Overlap           — do they bleed at the same time?
  4. Stress Correlation         — does diversification vanish in bad times?
  5. Complementarity Ratio      — is the combo actually better than the parts?

Used at two points in the beam optimizer:
  L1.5 — between L1 (per-batch) and L2 (group combos), operates on batch curves
  L2.5 — between L2 (group results) and L3 (global assembly), operates on group curves

Both auto mode (silent pruning) and manual mode (advisory feedback) call the
same `harmony_gate()` function.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Default metric weights (sum to 1.0)
DEFAULT_WEIGHTS: dict[str, float] = {
    "return_correlation": 0.20,
    "correlation_stability": 0.15,
    "drawdown_overlap": 0.25,
    "stress_correlation": 0.25,
    "complementarity_ratio": 0.15,
}


@dataclass
class PairHarmony:
    """Harmony analysis for one pair of curves."""
    id_a: int | str
    id_b: int | str
    return_correlation: float = 0.0
    correlation_stability: float = 0.0
    drawdown_overlap: float = 0.0
    stress_correlation: float = 0.0
    complementarity_ratio: float = 0.0
    harmony_score: float = 0.0
    passes_gate: bool = True

    def to_dict(self) -> dict:
        return {
            "id_a": self.id_a,
            "id_b": self.id_b,
            "return_correlation": round(self.return_correlation, 4),
            "correlation_stability": round(self.correlation_stability, 4),
            "drawdown_overlap": round(self.drawdown_overlap, 4),
            "stress_correlation": round(self.stress_correlation, 4),
            "complementarity_ratio": round(self.complementarity_ratio, 4),
            "harmony_score": round(self.harmony_score, 4),
            "passes_gate": self.passes_gate,
        }


@dataclass
class HarmonyResult:
    """Full harmony analysis across N curves."""
    pairs: list[PairHarmony] = field(default_factory=list)
    matrix: dict[tuple, float] = field(default_factory=dict)  # (id_a, id_b) -> score
    mean_harmony: float = 0.0
    all_pass: bool = True
    failed_pairs: list[tuple] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pairs": [p.to_dict() for p in self.pairs],
            "mean_harmony": round(self.mean_harmony, 4),
            "all_pass": self.all_pass,
            "failed_pairs": [list(fp) for fp in self.failed_pairs],
        }


# ---------------------------------------------------------------------------
# Individual metric functions
# ---------------------------------------------------------------------------

def _return_correlation(pnl_a: pd.Series, pnl_b: pd.Series) -> float:
    """Pearson correlation of daily PnL.  Range: -1 to +1."""
    if len(pnl_a) < 10 or len(pnl_b) < 10:
        return 0.0
    corr = pnl_a.corr(pnl_b)
    return 0.0 if np.isnan(corr) else float(corr)


def _correlation_stability(
    pnl_a: pd.Series, pnl_b: pd.Series, window: int = 60,
) -> float:
    """Std of rolling-window correlations.  Lower = more stable.  Range: 0 to ~1."""
    if len(pnl_a) < window * 2:
        # Not enough data for rolling analysis — assume moderately stable
        return 0.3
    rolling_corr = pnl_a.rolling(window).corr(pnl_b).dropna()
    if rolling_corr.empty:
        return 0.5
    std = float(rolling_corr.std())
    return 0.0 if np.isnan(std) else min(std, 1.0)


def _drawdown_overlap(
    pnl_a: pd.Series, pnl_b: pd.Series, threshold: float = 0.02,
) -> float:
    """Fraction of days where BOTH curves are simultaneously in drawdown.

    Drawdown defined as equity being > `threshold` (2%) below its running peak.
    Range: 0 to 1.  Lower = better.
    """
    cum_a = pnl_a.cumsum()
    cum_b = pnl_b.cumsum()

    if cum_a.empty or cum_b.empty:
        return 0.0

    peak_a = cum_a.cummax()
    peak_b = cum_b.cummax()

    # Avoid division by zero for peaks near zero
    safe_peak_a = peak_a.replace(0, np.nan)
    safe_peak_b = peak_b.replace(0, np.nan)

    dd_pct_a = (cum_a - peak_a) / safe_peak_a.abs().clip(lower=1.0)
    dd_pct_b = (cum_b - peak_b) / safe_peak_b.abs().clip(lower=1.0)

    in_dd_a = dd_pct_a < -threshold
    in_dd_b = dd_pct_b < -threshold

    both_dd = in_dd_a & in_dd_b
    # Denominator: days where at least one is in drawdown
    either_dd = in_dd_a | in_dd_b
    n_either = either_dd.sum()
    if n_either == 0:
        return 0.0  # neither ever draws down
    return float(both_dd.sum()) / float(n_either)


def _stress_correlation(
    pnl_a: pd.Series, pnl_b: pd.Series, quantile: float = 0.20,
) -> float:
    """Correlation during the worst-quantile days (by combined PnL).

    High stress correlation = diversification fails when you need it most.
    Range: -1 to +1.
    """
    combined = pnl_a + pnl_b
    if len(combined) < 20:
        return 0.0
    threshold = combined.quantile(quantile)
    stress_mask = combined <= threshold
    if stress_mask.sum() < 5:
        return 0.0
    corr = pnl_a[stress_mask].corr(pnl_b[stress_mask])
    return 0.0 if np.isnan(corr) else float(corr)


def _complementarity_ratio(pnl_a: pd.Series, pnl_b: pd.Series) -> float:
    """Combined Sharpe / max(individual Sharpes).

    > 1.0 means the pair is better together.  Range: ~0 to 3+.
    Capped at 3.0 for normalization.
    """
    def _sharpe(s: pd.Series) -> float:
        if s.empty or len(s) < 2:
            return 0.0
        m, sd = s.mean(), s.std()
        if sd == 0:
            return 0.0
        return m / sd

    sh_a = _sharpe(pnl_a)
    sh_b = _sharpe(pnl_b)
    sh_combined = _sharpe(pnl_a + pnl_b)

    max_individual = max(sh_a, sh_b)
    if max_individual <= 0:
        # Both individually negative — if combined is positive, that's great
        return min(max(sh_combined, 0.0), 3.0)
    return min(sh_combined / max_individual, 3.0)


# ---------------------------------------------------------------------------
# Composite harmony score
# ---------------------------------------------------------------------------

def _compute_pair_harmony(
    pnl_a: pd.Series,
    pnl_b: pd.Series,
    id_a: int | str,
    id_b: int | str,
    strength: int,
    weights: dict[str, float],
    window: int,
    dd_threshold: float,
    stress_quantile: float,
) -> PairHarmony:
    """Compute all 5 metrics and composite score for one pair."""
    rc = _return_correlation(pnl_a, pnl_b)
    cs = _correlation_stability(pnl_a, pnl_b, window)
    do = _drawdown_overlap(pnl_a, pnl_b, dd_threshold)
    sc = _stress_correlation(pnl_a, pnl_b, stress_quantile)
    cr = _complementarity_ratio(pnl_a, pnl_b)

    # Normalize each metric to 0-1 (higher = more harmonious)
    n_rc = (1.0 - abs(rc))                  # low |correlation| is good
    n_cs = (1.0 - cs)                       # low stability std is good
    n_do = (1.0 - do)                       # low drawdown overlap is good
    n_sc = (1.0 - max(sc, 0.0))            # low stress corr is good (ignore negative)
    n_cr = min(cr / 2.0, 1.0)             # complementarity 2.0+ → 1.0

    harmony = (
        weights.get("return_correlation", 0.20) * n_rc
        + weights.get("correlation_stability", 0.15) * n_cs
        + weights.get("drawdown_overlap", 0.25) * n_do
        + weights.get("stress_correlation", 0.25) * n_sc
        + weights.get("complementarity_ratio", 0.15) * n_cr
    )

    # Strength maps to threshold: 0 → 0.0, 25 → 0.25, 50 → 0.50, 75 → 0.75, 100 → 1.0
    threshold = strength / 100.0
    passes = harmony >= threshold

    return PairHarmony(
        id_a=id_a,
        id_b=id_b,
        return_correlation=rc,
        correlation_stability=cs,
        drawdown_overlap=do,
        stress_correlation=sc,
        complementarity_ratio=cr,
        harmony_score=harmony,
        passes_gate=passes,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def harmony_gate(
    curves: dict[int | str, pd.Series],
    strength: int = 50,
    weights: dict[str, float] | None = None,
    window: int = 60,
    dd_threshold: float = 0.02,
    stress_quantile: float = 0.20,
) -> HarmonyResult:
    """Compute pairwise harmony for a set of equity curves.

    Parameters
    ----------
    curves : dict mapping ID (batch_id or group_id) to daily PnL Series
    strength : 0-100 gate strength (0 = off / everything passes)
    weights : per-metric weights (default: see DEFAULT_WEIGHTS)
    window : rolling window size for correlation stability (days)
    dd_threshold : drawdown threshold for overlap calculation (fraction)
    stress_quantile : worst-quantile fraction for stress correlation

    Returns
    -------
    HarmonyResult with pairwise scores, pass/fail, and summary.
    """
    if strength <= 0:
        # Gate disabled — everything passes, skip computation
        ids = list(curves.keys())
        pairs = []
        for i, id_a in enumerate(ids):
            for id_b in ids[i + 1:]:
                pairs.append(PairHarmony(id_a=id_a, id_b=id_b, passes_gate=True, harmony_score=1.0))
        return HarmonyResult(pairs=pairs, mean_harmony=1.0, all_pass=True)

    w = weights or DEFAULT_WEIGHTS.copy()

    ids = sorted(curves.keys(), key=str)
    pairs: list[PairHarmony] = []
    matrix: dict[tuple, float] = {}

    for i, id_a in enumerate(ids):
        for id_b in ids[i + 1:]:
            ph = _compute_pair_harmony(
                curves[id_a], curves[id_b],
                id_a, id_b,
                strength, w, window, dd_threshold, stress_quantile,
            )
            pairs.append(ph)
            matrix[(id_a, id_b)] = ph.harmony_score
            matrix[(id_b, id_a)] = ph.harmony_score

    failed = [(p.id_a, p.id_b) for p in pairs if not p.passes_gate]
    mean_h = float(np.mean([p.harmony_score for p in pairs])) if pairs else 1.0

    return HarmonyResult(
        pairs=pairs,
        matrix=matrix,
        mean_harmony=mean_h,
        all_pass=len(failed) == 0,
        failed_pairs=failed,
    )


# ---------------------------------------------------------------------------
# Beam-optimizer helpers: build batch/group curves from daily_pnl
# ---------------------------------------------------------------------------

def build_batch_curves(
    daily_pnl: pd.DataFrame,
    asset_batch_map: dict[str, int],
) -> dict[int, pd.Series]:
    """Aggregate asset-level daily PnL into batch-level curves."""
    batch_curves: dict[int, pd.Series] = {}
    for bid in set(asset_batch_map.values()):
        cols = [c for c in daily_pnl.columns if asset_batch_map.get(c) == bid]
        if cols:
            batch_curves[bid] = daily_pnl[cols].sum(axis=1)
    return batch_curves


def build_group_curves(
    daily_pnl: pd.DataFrame,
    l2_results: list[list[dict]],
) -> dict[int, pd.Series]:
    """Build group-level equity curves from L2 results (best variant per group)."""
    group_curves: dict[int, pd.Series] = {}
    for gidx, group_variants in enumerate(l2_results):
        if not group_variants:
            continue
        # Use the best (first) variant from each group
        best = group_variants[0]
        cols = [c for c in best["assets"] if c in daily_pnl.columns]
        if cols:
            group_curves[gidx] = daily_pnl[cols].sum(axis=1)
    return group_curves


def filter_batches_by_harmony(
    l1_variants: dict[int, list[dict]],
    daily_pnl: pd.DataFrame,
    asset_batch_map: dict[str, int],
    group_bids: list[int],
    strength: int,
    weights: dict[str, float] | None = None,
    window: int = 60,
    dd_threshold: float = 0.02,
    stress_quantile: float = 0.20,
) -> tuple[dict[int, list[dict]], HarmonyResult]:
    """L1.5: Filter batch combinations within a group by harmony.

    Removes batches whose *average* harmony with all other group members
    falls below the strength threshold. Keeps at least 2 batches.

    Returns (filtered_variants, harmony_result).
    """
    if strength <= 0 or len(group_bids) < 2:
        batch_curves = build_batch_curves(daily_pnl, asset_batch_map)
        group_curves = {bid: batch_curves[bid] for bid in group_bids if bid in batch_curves}
        hr = harmony_gate(group_curves, strength=0)
        return l1_variants, hr

    # Build batch-level PnL curves for batches in this group
    batch_curves = build_batch_curves(daily_pnl, asset_batch_map)
    group_curves = {bid: batch_curves[bid] for bid in group_bids if bid in batch_curves}

    if len(group_curves) < 2:
        return l1_variants, HarmonyResult(mean_harmony=1.0, all_pass=True)

    hr = harmony_gate(
        group_curves, strength=strength, weights=weights,
        window=window, dd_threshold=dd_threshold, stress_quantile=stress_quantile,
    )

    if hr.all_pass:
        return l1_variants, hr

    # Compute average harmony per batch within the group
    batch_avg_harmony: dict[int, float] = {bid: 0.0 for bid in group_curves}
    pair_count: dict[int, int] = {bid: 0 for bid in group_curves}

    for p in hr.pairs:
        batch_avg_harmony[p.id_a] = batch_avg_harmony.get(p.id_a, 0.0) + p.harmony_score
        batch_avg_harmony[p.id_b] = batch_avg_harmony.get(p.id_b, 0.0) + p.harmony_score
        pair_count[p.id_a] = pair_count.get(p.id_a, 0) + 1
        pair_count[p.id_b] = pair_count.get(p.id_b, 0) + 1

    for bid in batch_avg_harmony:
        if pair_count[bid] > 0:
            batch_avg_harmony[bid] /= pair_count[bid]

    # Sort batches by average harmony (best first), keep those above threshold
    threshold = strength / 100.0
    sorted_bids = sorted(group_curves.keys(), key=lambda b: batch_avg_harmony.get(b, 0), reverse=True)
    kept_bids = [bid for bid in sorted_bids if batch_avg_harmony.get(bid, 0) >= threshold]

    # Always keep at least 2 batches (the best two by harmony)
    if len(kept_bids) < 2:
        kept_bids = sorted_bids[:2]

    removed_bids = set(group_curves.keys()) - set(kept_bids)
    if removed_bids:
        logger.info(
            "L1.5 harmony gate removed batches %s from group (avg harmony below %.2f)",
            removed_bids, threshold,
        )

    # Filter l1_variants to only kept batches
    filtered = {bid: l1_variants[bid] for bid in kept_bids if bid in l1_variants}
    return filtered, hr


def filter_groups_by_harmony(
    l2_results: list[list[dict]],
    daily_pnl: pd.DataFrame,
    strength: int,
    weights: dict[str, float] | None = None,
    window: int = 60,
    dd_threshold: float = 0.02,
    stress_quantile: float = 0.20,
) -> tuple[list[list[dict]], HarmonyResult]:
    """L2.5: Check harmony between group portfolio outputs before L3.

    Instead of removing groups entirely (too aggressive), this penalizes
    group combinations by injecting a harmony-aware score adjustment into L3.
    Returns (l2_results, harmony_result) where the result provides advisory
    data and the l2_results may have low-harmony variants pruned.
    """
    if strength <= 0 or len(l2_results) < 2:
        hr = harmony_gate({}, strength=0)
        return l2_results, hr

    group_curves = build_group_curves(daily_pnl, l2_results)

    if len(group_curves) < 2:
        return l2_results, HarmonyResult(mean_harmony=1.0, all_pass=True)

    hr = harmony_gate(
        group_curves, strength=strength, weights=weights,
        window=window, dd_threshold=dd_threshold, stress_quantile=stress_quantile,
    )

    # L2.5 doesn't remove groups (too destructive). Instead it annotates
    # each group combo with harmony data so L3 scoring can incorporate it.
    # The harmony_result is returned for:
    #   - Auto mode: L3 scoring uses it as a penalty
    #   - Manual mode: frontend displays pass/fail per group pair
    return l2_results, hr
