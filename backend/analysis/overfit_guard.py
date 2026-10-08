"""Overfitting Guard — statistical filters for seed scores.

Prevents the optimizer from selecting assets with suspiciously extreme
metrics that are likely overfitted (too good to be true). Applies 5
asset-level approaches as a chainable pipeline, plus a portfolio-level
walk-forward re-rank for optimizer candidates.

Pipeline order: Log Diminish -> Z-Score -> Bayesian Shrinkage -> Sigmoid Cap -> Normal Range Bonus
Each approach is independently toggleable; disabled approaches pass through.
"""
from __future__ import annotations

import math
import logging

import numpy as np
import pandas as pd

from backend.analysis.scoring import calculate_stability

logger = logging.getLogger(__name__)

# ── Metrics that the guard can transform ────────────────────────────
GUARD_METRICS = ("pf", "sortino", "win_rate", "max_dd", "net_profit",
                 "payoff", "sharpe", "calmar")

# Metrics where higher = better (standard direction)
_HIGHER_BETTER = {"pf", "sortino", "win_rate", "net_profit", "payoff",
                  "sharpe", "calmar"}


# ═══════════════════════════════════════════════════════════════════
#  Approach 1 — Diminishing Returns (Log Transform)
# ═══════════════════════════════════════════════════════════════════

def log_diminish(value: float, cap: float) -> float:
    """Compress *value* logarithmically relative to *cap*."""
    if value <= 0:
        return 0.0
    return math.log(1 + value) / math.log(1 + cap)


def log_diminish_dd(dd_abs: float, pool_dd_values: list[float]) -> float:
    """Inverted-U for drawdown: 'normal' DD scores highest."""
    arr = np.array(pool_dd_values, dtype=float)
    if len(arr) < 2:
        return 1.0
    p10 = float(np.percentile(arr, 10))
    p70 = float(np.percentile(arr, 70))
    mid = (p10 + p70) / 2
    spread = max(abs(p70 - p10), 1.0)
    dist = abs(dd_abs - mid) / spread
    return max(0.0, 1.0 - dist * 0.5)


def apply_log_diminish(
    asset_metrics: dict[str, dict],
    caps: dict[str, float],
) -> dict[str, dict]:
    """Apply log compression to every metric for every asset in-place.

    A cap of 0 disables compression for that metric (pass-through).
    """
    # Collect DD values for inverted-U
    dd_vals = [abs(m.get("max_dd", 0)) for m in asset_metrics.values()]

    for tag, m in asset_metrics.items():
        for metric in ("pf", "sortino", "payoff", "sharpe", "calmar"):
            cap = caps.get(metric, 4.0)
            if cap <= 0:
                continue  # 0 = disabled
            raw = m.get(metric, 0)
            m[metric] = log_diminish(raw, cap)
        # Win rate: treat as 0-100 scale
        wr_cap = caps.get("wr", 70.0)
        if wr_cap > 0:
            raw_wr = m.get("win_rate", 0)
            m["win_rate"] = log_diminish(raw_wr, wr_cap)
        # Max DD inverted-U
        m["max_dd"] = log_diminish_dd(abs(m.get("max_dd", 0)), dd_vals)
    return asset_metrics


# ═══════════════════════════════════════════════════════════════════
#  Approach 2 — Z-Score Suspicion Penalty
# ═══════════════════════════════════════════════════════════════════

def z_score_penalty(
    value: float,
    batch_mean: float,
    batch_std: float,
    z_threshold: float = 2.0,
    penalty_strength: float = 0.15,
) -> float:
    """Penalize values z_threshold+ std devs above batch mean.

    Returns a multiplier in [0.1, 1.0].
    """
    if batch_std <= 0:
        return 1.0
    z = (value - batch_mean) / batch_std
    if z <= z_threshold:
        return 1.0
    excess = z - z_threshold
    penalty = penalty_strength * excess
    return max(0.1, 1.0 - penalty)


def apply_z_score_penalty(
    asset_metrics: dict[str, dict],
    z_threshold: float = 2.0,
    penalty_strength: float = 0.15,
) -> dict[str, float]:
    """Compute per-asset penalty multiplier from z-scores within batches.

    Returns {asset_tag: combined_multiplier}.
    A threshold of 0 or strength of 0 disables the penalty (returns all 1.0).
    """
    if z_threshold <= 0 or penalty_strength <= 0:
        return {t: 1.0 for t in asset_metrics}

    # Group by batch
    batches: dict[int, list[str]] = {}
    for tag, m in asset_metrics.items():
        bid = m.get("batch_id", 0)
        batches.setdefault(bid, []).append(tag)

    multipliers: dict[str, float] = {}
    for bid, tags in batches.items():
        for metric in GUARD_METRICS:
            vals = [asset_metrics[t].get(metric, 0) for t in tags]
            arr = np.array(vals, dtype=float)
            if len(arr) < 2:
                for t in tags:
                    multipliers.setdefault(t, 1.0)
                continue
            bmean = float(np.mean(arr))
            bstd = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0

            for t, v in zip(tags, vals):
                if metric == "max_dd":
                    # Invert: penalize suspiciously SMALL DD (close to 0)
                    z_val = (bmean - v) / bstd if bstd > 0 else 0.0
                    if z_val <= z_threshold:
                        p = 1.0
                    else:
                        excess = z_val - z_threshold
                        p = max(0.1, 1.0 - penalty_strength * excess)
                else:
                    p = z_score_penalty(v, bmean, bstd, z_threshold, penalty_strength)
                multipliers[t] = multipliers.get(t, 1.0) * p
    return multipliers


# ═══════════════════════════════════════════════════════════════════
#  Approach 3 — Bayesian Shrinkage
# ═══════════════════════════════════════════════════════════════════

def bayesian_shrink(
    raw_value: float,
    batch_mean: float,
    n_trades: int,
    k: float = 5.0,
) -> float:
    """Shrink *raw_value* toward *batch_mean* based on trade count."""
    denom = n_trades + k
    if denom <= 0:
        return raw_value
    return (n_trades * raw_value + k * batch_mean) / denom


def apply_bayesian_shrinkage(
    asset_metrics: dict[str, dict],
    k: float = 5.0,
) -> dict[str, dict]:
    """Apply Bayesian shrinkage to all metrics within each batch.

    k of 0 disables shrinkage (pass-through).
    """
    if k <= 0:
        return asset_metrics

    batches: dict[int, list[str]] = {}
    for tag, m in asset_metrics.items():
        bid = m.get("batch_id", 0)
        batches.setdefault(bid, []).append(tag)

    for bid, tags in batches.items():
        for metric in GUARD_METRICS:
            vals = [asset_metrics[t].get(metric, 0) for t in tags]
            bmean = float(np.mean(vals))
            for t in tags:
                raw = asset_metrics[t].get(metric, 0)
                n = asset_metrics[t].get("n_trades", 10)
                asset_metrics[t][metric] = bayesian_shrink(raw, bmean, n, k)
    return asset_metrics


# ═══════════════════════════════════════════════════════════════════
#  Approach 4 — Metric Capping (Sigmoid / tanh)
# ═══════════════════════════════════════════════════════════════════

def sigmoid_cap(value: float, cap: float) -> float:
    """Smooth ceiling using tanh. Approaches *cap* asymptotically."""
    if value <= 0:
        return 0.0
    return cap * math.tanh(value / cap)


def apply_sigmoid_cap(
    asset_metrics: dict[str, dict],
    caps: dict[str, float],
) -> dict[str, dict]:
    """Apply tanh sigmoid capping to every metric for every asset.

    A cap of 0 disables capping for that metric (pass-through).
    """
    for tag, m in asset_metrics.items():
        for metric in ("pf", "sortino", "payoff", "sharpe", "calmar"):
            cap = caps.get(metric, 4.0)
            if cap <= 0:
                continue  # 0 = disabled
            raw = m.get(metric, 0)
            m[metric] = sigmoid_cap(raw, cap)
        wr_cap = caps.get("wr", 70.0)
        if wr_cap > 0:
            m["win_rate"] = sigmoid_cap(m.get("win_rate", 0), wr_cap)
    return asset_metrics


# ═══════════════════════════════════════════════════════════════════
#  Approach 5 — Normal Range Bonus (Inverted-U)
# ═══════════════════════════════════════════════════════════════════

def normal_range_bonus(
    value: float,
    pool_values: np.ndarray,
    good_pct_low: float = 20.0,
    good_pct_high: float = 60.0,
    penalty_scale: float = 0.3,
    bonus: float = 0.1,
) -> float:
    """Inverted-U multiplier: >1.0 for normal range, <1.0 for extremes."""
    if len(pool_values) < 2:
        return 1.0
    p_low = float(np.percentile(pool_values, good_pct_low))
    p_high = float(np.percentile(pool_values, good_pct_high))
    spread = max(abs(p_high - p_low), 1e-6)

    if p_low <= value <= p_high:
        return 1.0 + bonus

    if value < p_low:
        dist = (p_low - value) / spread
        return max(0.5, 1.0 - penalty_scale * dist)

    dist = (value - p_high) / spread
    return max(0.5, 1.0 - penalty_scale * dist)


def apply_normal_range_bonus(
    asset_metrics: dict[str, dict],
    pct_low: float = 20.0,
    pct_high: float = 60.0,
    penalty_scale: float = 0.3,
    bonus: float = 0.1,
) -> dict[str, float]:
    """Compute per-asset multiplier from normal-range bonus.

    Returns {asset_tag: combined_multiplier}.
    penalty_scale of 0 disables penalties (returns all 1.0).
    """
    tags = list(asset_metrics.keys())
    if not tags:
        return {}
    if penalty_scale <= 0 and bonus <= 0:
        return {t: 1.0 for t in tags}

    multipliers: dict[str, float] = {t: 1.0 for t in tags}

    for metric in GUARD_METRICS:
        vals = np.array([asset_metrics[t].get(metric, 0) for t in tags], dtype=float)
        if len(vals) < 2:
            continue

        for i, t in enumerate(tags):
            v = vals[i]
            if metric == "max_dd":
                # Invert: small DD (close to 0) should map to low percentile
                inv_vals = -vals  # flip sign so small abs DD = low value
                inv_v = -v
                m = normal_range_bonus(inv_v, inv_vals, pct_low, pct_high,
                                       penalty_scale, bonus)
            else:
                m = normal_range_bonus(v, vals, pct_low, pct_high,
                                       penalty_scale, bonus)
            multipliers[t] *= m
    return multipliers


# ═══════════════════════════════════════════════════════════════════
#  Orchestrator — apply_overfit_guard()
# ═══════════════════════════════════════════════════════════════════

def apply_overfit_guard(
    seed_scores: dict[str, float],
    asset_metrics: dict[str, dict],
    config: dict,
) -> dict[str, float]:
    """Apply the full overfit guard pipeline to seed scores.

    Parameters
    ----------
    seed_scores : {asset_tag: raw_score}
    asset_metrics : {asset_tag: {pf, sortino, win_rate, max_dd, ...}}
    config : toggles and parameter values from the request

    Returns
    -------
    Adjusted seed_scores dict (same keys, modified values).
    """
    if not seed_scores or not asset_metrics:
        return seed_scores

    # Work on a copy so originals aren't mutated.
    # CS-018: asset_metrics is dict[str, dict[str, float|int]] with only
    # primitive leaves — a two-level comprehension is equivalent to
    # deepcopy and roughly 30-50x faster.
    am = {k: dict(v) for k, v in asset_metrics.items()}

    # ── Approach 1: Diminishing Returns (Log) ──────────────────
    if config.get("log_enabled", False):
        caps = config.get("log_caps", {})
        am = apply_log_diminish(am, caps)
        logger.debug("  OG: Log diminish applied")

    # ── Approach 2: Z-Score Suspicion ──────────────────────────
    z_multipliers: dict[str, float] = {}
    if config.get("z_score_enabled", False):
        z_multipliers = apply_z_score_penalty(
            am,
            z_threshold=config.get("z_threshold", 2.0),
            penalty_strength=config.get("z_penalty_strength", 0.15),
        )
        logger.debug("  OG: Z-score penalty applied")

    # ── Approach 3: Bayesian Shrinkage ─────────────────────────
    if config.get("bayes_enabled", False):
        am = apply_bayesian_shrinkage(am, k=config.get("bayes_k", 5.0))
        logger.debug("  OG: Bayesian shrinkage applied (k=%.1f)", config.get("bayes_k", 5.0))

    # ── Approach 4: Sigmoid Cap ────────────────────────────────
    if config.get("sigmoid_enabled", False):
        sig_caps = config.get("sig_caps", {})
        am = apply_sigmoid_cap(am, sig_caps)
        logger.debug("  OG: Sigmoid cap applied")

    # ── Approach 5: Normal Range Bonus ─────────────────────────
    nr_multipliers: dict[str, float] = {}
    if config.get("normal_range_enabled", False):
        nr_multipliers = apply_normal_range_bonus(
            am,
            pct_low=config.get("nr_pct_low", 20.0),
            pct_high=config.get("nr_pct_high", 60.0),
            penalty_scale=config.get("nr_penalty_scale", 0.3),
            bonus=config.get("nr_bonus", 0.1),
        )
        logger.debug("  OG: Normal range bonus applied")

    # ── Rebuild scores from adjusted metrics ───────────────────
    # Re-score each asset as average of its adjusted metric values (rank-based)
    adjusted: dict[str, float] = {}
    all_tags = list(am.keys())
    if not all_tags:
        return seed_scores

    # Rank each metric across assets, average the ranks
    metric_ranks: dict[str, dict[str, float]] = {}
    for metric in GUARD_METRICS:
        vals = [(t, am[t].get(metric, 0)) for t in all_tags]
        # Sort: higher is better for all (DD already transformed)
        vals.sort(key=lambda x: x[1], reverse=True)
        for rank, (t, _) in enumerate(vals):
            metric_ranks.setdefault(t, {})[metric] = rank

    for t in all_tags:
        ranks = metric_ranks.get(t, {})
        avg_rank = sum(ranks.values()) / max(len(ranks), 1)
        # Convert rank to score (lower rank = higher score)
        score = max(0.001, 1.0 - avg_rank / max(len(all_tags), 1))

        # Apply z-score multiplier
        if t in z_multipliers:
            score *= z_multipliers[t]
        # Apply normal-range multiplier
        if t in nr_multipliers:
            score *= nr_multipliers[t]

        adjusted[t] = round(score, 6)

    # Blend: use adjusted scores for assets that exist in seed_scores
    result = dict(seed_scores)
    for t in result:
        if t in adjusted:
            result[t] = adjusted[t]
    return result


# ═══════════════════════════════════════════════════════════════════
#  Approach 6 — Walk-Forward Re-rank (portfolio-level)
# ═══════════════════════════════════════════════════════════════════

def _portfolio_daily_pnl(
    daily_pnl: pd.DataFrame,
    assets: list[str],
) -> pd.Series:
    """Sum daily PnL for a set of assets."""
    cols = [c for c in assets if c in daily_pnl.columns]
    if not cols:
        return pd.Series(dtype=float)
    return daily_pnl[cols].sum(axis=1)


def _score_validation(series: pd.Series, metric: str) -> float:
    """Score a portfolio PnL series on a single metric."""
    if series.empty or series.sum() == 0:
        return 0.0
    if metric == "stability":
        return calculate_stability(series)
    if metric == "sortino":
        neg = series[series < 0]
        dd_std = neg.std() if len(neg) > 0 else 1.0
        if dd_std <= 0:
            dd_std = 1.0
        return float(series.mean() / dd_std)
    if metric == "pf":
        gross_win = float(series[series > 0].sum())
        gross_loss = abs(float(series[series < 0].sum()))
        return gross_win / max(gross_loss, 1.0)
    if metric == "net_profit":
        return float(series.sum())
    if metric == "sharpe":
        std = series.std()
        if std <= 0:
            return 0.0
        # Crypto trades 24/7/365 — annualise against 365, matching the canonical
        # sharpe_ratio() in backend.analysis.scoring. The previous 252 factor
        # (equities trading days) inflated Sharpe by sqrt(365/252) ≈ 1.204.
        return float(series.mean() / std * (365 ** 0.5))
    # fallback: stability
    return calculate_stability(series)


def walk_forward_rerank(
    candidates: list[dict],
    daily_pnl: pd.DataFrame,
    split_pct: float = 0.70,
    metric: str = "stability",
) -> list[dict]:
    """Re-rank optimizer candidates by validation window performance.

    Parameters
    ----------
    candidates : list of candidate dicts (must have 'assets' key)
    daily_pnl : full IS daily PnL DataFrame
    split_pct : fraction used for training (rest = validation)
    metric : validation scoring metric

    Returns
    -------
    Same candidates list, sorted by validation score (best first).
    """
    if not candidates or daily_pnl.empty:
        return candidates
    if len(candidates) == 1:
        return candidates
    if split_pct <= 0:
        return candidates  # 0 = disabled

    n_rows = len(daily_pnl)
    split_idx = int(n_rows * split_pct)
    if split_idx < 5 or (n_rows - split_idx) < 5:
        logger.warning("WF re-rank: not enough data for split (n=%d, split=%d)",
                       n_rows, split_idx)
        return candidates

    val_pnl = daily_pnl.iloc[split_idx:]

    scored: list[tuple[float, int, dict]] = []
    for i, cand in enumerate(candidates):
        assets = cand.get("assets", [])
        port_pnl = _portfolio_daily_pnl(val_pnl, assets)
        vs = _score_validation(port_pnl, metric)
        scored.append((vs, i, cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, _, c in scored]
