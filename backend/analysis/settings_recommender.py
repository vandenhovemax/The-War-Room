"""Settings Recommender — analyse IS data and propose optimal optimizer settings.

Two modes:
  Quick  — heuristic profiling of the asset pool (seconds)
  Thorough — walk-forward cross-validation sweep over setting combos (minutes)

NEVER looks at OOS data. All analysis uses IS period only.
"""
from __future__ import annotations

import copy
import itertools
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backend.analysis.scoring import calculate_stability

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────

GUARD_METRICS = ("pf", "sortino", "win_rate", "max_dd", "net_profit",
                 "payoff", "sharpe", "calmar")

DEFAULT_ALGOS_TO_SWEEP = ["greedy", "quality_first", "tournament", "hrp"]
ALL_ALGOS = ["greedy", "hrp", "pca", "quality_first", "tournament",
             "marginal_sharpe", "ensemble", "walk_forward"]

DEFAULT_OBJECTIVES_TO_SWEEP = ["stability", "hybrid", "max_sortino",
                               "max_sharpe", "max_calmar"]

# ─── Data classes ─────────────────────────────────────────────────────


@dataclass
class PoolProfile:
    """Statistical profile of the asset pool."""
    n_assets: int = 0
    n_batches: int = 0
    n_longs: int = 0
    n_shorts: int = 0
    # Trade counts
    trade_count_median: float = 0.0
    trade_count_mean: float = 0.0
    trade_count_min: int = 0
    trade_count_max: int = 0
    trade_count_p25: float = 0.0
    trade_count_p75: float = 0.0
    pct_below_10_trades: float = 0.0
    pct_below_20_trades: float = 0.0
    # Metric distributions (per metric: mean, std, skew, kurtosis, outlier_pct)
    metric_stats: dict[str, dict] = field(default_factory=dict)
    # Correlation structure
    corr_mean_abs: float = 0.0
    corr_median_abs: float = 0.0
    corr_p90_abs: float = 0.0
    corr_density_above_50: float = 0.0  # % of pairs with |corr| > 0.50
    corr_density_above_30: float = 0.0
    # Quality spread
    pf_spread: float = 0.0  # p90 - p10 of PF
    quality_gini: float = 0.0  # Gini coefficient of PFs
    # Batch balance
    batch_sizes: dict[int, int] = field(default_factory=dict)
    batch_balance_ratio: float = 0.0  # min_size / max_size
    # Outlier counts
    n_suspicious: int = 0  # assets with ≥3 metrics > 2σ
    suspicious_tags: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    """A single settings recommendation with reasoning."""
    setting: str
    value: object
    confidence: float  # 0-1
    reason: str
    category: str  # "algorithm", "objective", "overfitting_guard", "seed", "filter"


@dataclass
class RecommenderResult:
    """Complete recommender output."""
    mode: str  # "quick" or "thorough"
    profile: PoolProfile
    recommendations: list[Recommendation] = field(default_factory=list)
    # Thorough mode: cross-validation results
    cv_results: list[dict] = field(default_factory=list)
    # Top N setting presets from thorough mode
    top_presets: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0


# ═══════════════════════════════════════════════════════════════════════
#  Pool Profiling
# ═══════════════════════════════════════════════════════════════════════

def _compute_asset_metrics(
    daily_pnl: pd.DataFrame,
    trades: list,
    asset_batch_map: dict[str, int],
    trade_key_fn: Callable | None = None,
) -> dict[str, dict]:
    """Compute per-asset metrics from daily PnL (same as OG pipeline)."""
    metrics: dict[str, dict] = {}
    for col in daily_pnl.columns:
        s = daily_pnl[col]
        pos = s[s > 0]
        neg = s[s < 0]
        cum = s.cumsum()
        dd = cum - cum.cummax()
        # Count trades for this asset
        if trade_key_fn:
            n_tr = sum(1 for t in trades if trade_key_fn(t) == col)
        else:
            n_tr = sum(1 for t in trades
                       if getattr(t, "_tagged_key", None) == col)
            if n_tr == 0:
                # Fallback: count non-zero PnL days as proxy
                n_tr = int((s != 0).sum())

        metrics[col] = {
            "pf": float(pos.sum() / max(abs(neg.sum()), 1.0)) if len(neg) > 0 else float(pos.sum()) if len(pos) > 0 else 0.0,
            "sortino": float(s.mean() / neg.std()) if len(neg) > 1 and neg.std() > 0 else 0.0,
            "win_rate": float(len(pos) / max(len(s[s != 0]), 1) * 100),
            "max_dd": float(dd.min()) if len(dd) > 0 else 0.0,
            "net_profit": float(s.sum()),
            "payoff": float(pos.mean() / abs(neg.mean())) if len(neg) > 0 and neg.mean() != 0 else 0.0,
            "sharpe": float(s.mean() / s.std() * (365 ** 0.5)) if s.std() > 0 else 0.0,
            "calmar": float(s.sum() / abs(dd.min())) if dd.min() < 0 else 0.0,
            "n_trades": max(n_tr, 1),
            "batch_id": asset_batch_map.get(col, 0),
        }
    return metrics


def profile_pool(
    daily_pnl: pd.DataFrame,
    trades: list,
    asset_batch_map: dict[str, int],
    corr_matrix: pd.DataFrame | None = None,
    trade_key_fn: Callable | None = None,
) -> tuple[PoolProfile, dict[str, dict]]:
    """Analyse the IS pool and return a statistical profile.

    Returns (profile, asset_metrics) — asset_metrics reused by recommender.
    """
    prof = PoolProfile()
    cols = daily_pnl.columns.tolist()
    prof.n_assets = len(cols)
    prof.n_longs = sum(1 for c in cols if c.endswith("_Long"))
    prof.n_shorts = sum(1 for c in cols if c.endswith("_Short"))
    batch_ids = set(asset_batch_map.get(c, 0) for c in cols)
    prof.n_batches = len(batch_ids - {0})
    prof.batch_sizes = {}
    for c in cols:
        bid = asset_batch_map.get(c, 0)
        prof.batch_sizes[bid] = prof.batch_sizes.get(bid, 0) + 1
    sizes = list(prof.batch_sizes.values())
    prof.batch_balance_ratio = min(sizes) / max(max(sizes), 1) if sizes else 0.0

    # Compute per-asset metrics
    am = _compute_asset_metrics(daily_pnl, trades, asset_batch_map, trade_key_fn)

    # Trade count stats
    tc = np.array([am[t]["n_trades"] for t in am])
    if len(tc) > 0:
        prof.trade_count_median = float(np.median(tc))
        prof.trade_count_mean = float(np.mean(tc))
        prof.trade_count_min = int(tc.min())
        prof.trade_count_max = int(tc.max())
        prof.trade_count_p25 = float(np.percentile(tc, 25))
        prof.trade_count_p75 = float(np.percentile(tc, 75))
        prof.pct_below_10_trades = float(np.mean(tc < 10) * 100)
        prof.pct_below_20_trades = float(np.mean(tc < 20) * 100)

    # Metric distributions
    for metric in GUARD_METRICS:
        vals = np.array([am[t].get(metric, 0) for t in am], dtype=float)
        vals = vals[~np.isnan(vals)]
        if len(vals) < 3:
            continue
        mean_v = float(np.mean(vals))
        std_v = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
        # Skewness
        if std_v > 0:
            skew_v = float(np.mean(((vals - mean_v) / std_v) ** 3))
            kurt_v = float(np.mean(((vals - mean_v) / std_v) ** 4) - 3)
        else:
            skew_v = 0.0
            kurt_v = 0.0
        # Outlier %: assets > 2σ above mean
        outlier_pct = float(np.mean(vals > mean_v + 2 * std_v) * 100) if std_v > 0 else 0.0
        prof.metric_stats[metric] = {
            "mean": round(mean_v, 4),
            "std": round(std_v, 4),
            "skew": round(skew_v, 4),
            "kurtosis": round(kurt_v, 4),
            "p10": round(float(np.percentile(vals, 10)), 4),
            "p50": round(float(np.percentile(vals, 50)), 4),
            "p90": round(float(np.percentile(vals, 90)), 4),
            "outlier_pct": round(outlier_pct, 2),
        }

    # PF quality spread & Gini
    pf_vals = np.array([am[t].get("pf", 0) for t in am], dtype=float)
    pf_vals = pf_vals[pf_vals > 0]
    if len(pf_vals) > 1:
        prof.pf_spread = float(np.percentile(pf_vals, 90) - np.percentile(pf_vals, 10))
        # Gini coefficient
        sorted_pf = np.sort(pf_vals)
        n = len(sorted_pf)
        idx = np.arange(1, n + 1)
        prof.quality_gini = float((2 * np.sum(idx * sorted_pf) / (n * np.sum(sorted_pf))) - (n + 1) / n)

    # Correlation structure
    if corr_matrix is not None and not corr_matrix.empty:
        # Upper triangle only (exclude diagonal)
        mask = np.triu(np.ones(corr_matrix.shape, dtype=bool), k=1)
        corr_vals = np.abs(corr_matrix.values[mask])
        if len(corr_vals) > 0:
            prof.corr_mean_abs = float(np.mean(corr_vals))
            prof.corr_median_abs = float(np.median(corr_vals))
            prof.corr_p90_abs = float(np.percentile(corr_vals, 90))
            prof.corr_density_above_50 = float(np.mean(corr_vals > 0.50) * 100)
            prof.corr_density_above_30 = float(np.mean(corr_vals > 0.30) * 100)

    # Suspicious outlier detection (≥3 metrics > 2σ in their batch)
    batches: dict[int, list[str]] = {}
    for tag in am:
        bid = am[tag].get("batch_id", 0)
        batches.setdefault(bid, []).append(tag)

    for bid, tags in batches.items():
        if len(tags) < 3:
            continue
        for tag in tags:
            outlier_count = 0
            for metric in ("pf", "sortino", "win_rate", "payoff", "sharpe", "calmar"):
                vals = [am[t].get(metric, 0) for t in tags]
                arr = np.array(vals, dtype=float)
                bmean = float(np.mean(arr))
                bstd = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
                if bstd > 0 and am[tag].get(metric, 0) > bmean + 2 * bstd:
                    outlier_count += 1
            # Check inverted DD
            dd_vals = [abs(am[t].get("max_dd", 0)) for t in tags]
            dd_arr = np.array(dd_vals, dtype=float)
            dd_mean = float(np.mean(dd_arr))
            dd_std = float(np.std(dd_arr, ddof=1)) if len(dd_arr) > 1 else 0.0
            asset_dd = abs(am[tag].get("max_dd", 0))
            if dd_std > 0 and (dd_mean - asset_dd) / dd_std > 2.0:
                outlier_count += 1
            if outlier_count >= 3:
                prof.n_suspicious += 1
                prof.suspicious_tags.append(tag)

    return prof, am


# ═══════════════════════════════════════════════════════════════════════
#  Quick Mode — Heuristic Recommendations
# ═══════════════════════════════════════════════════════════════════════

def recommend_quick(
    profile: PoolProfile,
    asset_metrics: dict[str, dict],
    split_mode: bool = True,
    allowed_algos: list[str] | None = None,
) -> list[Recommendation]:
    """Generate settings recommendations from pool profile using heuristics.

    Rules are calibrated for crypto backtesting pools with 5-25 trades/asset.
    If allowed_algos is set, only algorithms in that list will be recommended.
    """
    recs: list[Recommendation] = []
    tc_med = profile.trade_count_median
    n = profile.n_assets

    # ── Algorithm ──────────────────────────────────────────────────
    _allowed = set(allowed_algos) if allowed_algos else None

    if _allowed and "hrp" in _allowed and profile.corr_density_above_50 > 15:
        chosen_algo, conf = "hrp", 0.8
        reason = (f"High correlation density ({profile.corr_density_above_50:.0f}% pairs > 0.50). "
                  "HRP naturally handles clustered correlation structures.")
    elif _allowed and "quality_first" in _allowed and profile.n_suspicious > n * 0.10:
        chosen_algo, conf = "quality_first", 0.85
        reason = (f"{profile.n_suspicious} suspicious outliers detected ({profile.n_suspicious/n*100:.0f}% of pool). "
                  "Quality First ranks by quality then prunes correlated pairs.")
    elif _allowed and "tournament" in _allowed and n > 60:
        chosen_algo, conf = "tournament", 0.7
        reason = f"Large pool ({n} assets). Tournament draft samples broadly for diversity."
    elif not _allowed:
        # No constraint — use full heuristic
        if profile.corr_density_above_50 > 15:
            chosen_algo, conf = "hrp", 0.8
            reason = (f"High correlation density ({profile.corr_density_above_50:.0f}% pairs > 0.50). "
                      "HRP naturally handles clustered correlation structures.")
        elif profile.n_suspicious > n * 0.10:
            chosen_algo, conf = "quality_first", 0.85
            reason = (f"{profile.n_suspicious} suspicious outliers detected ({profile.n_suspicious/n*100:.0f}% of pool). "
                      "Quality First ranks by quality then prunes correlated pairs.")
        elif n > 60:
            chosen_algo, conf = "tournament", 0.7
            reason = f"Large pool ({n} assets). Tournament draft samples broadly for diversity."
        else:
            chosen_algo, conf = "greedy", 0.65
            reason = (f"Moderate pool ({n} assets, {profile.corr_density_above_50:.0f}% high-corr pairs). "
                      "Iterative optimizer with refinement gives good customizability.")
    else:
        # Heuristic pick not in allowed list — use first allowed algo
        chosen_algo = allowed_algos[0] if allowed_algos else "greedy"
        conf = 0.6
        reason = (f"Selected from your allowed algorithms. "
                  f"Pool has {n} assets, {profile.corr_density_above_50:.0f}% high-corr pairs.")

    recs.append(Recommendation("div_algo", chosen_algo, conf, reason, "algorithm"))

    # ── Objective ──────────────────────────────────────────────────
    if tc_med < 15:
        recs.append(Recommendation(
            "objective", "stability", 0.85,
            f"Low trade counts (median {tc_med:.0f}). Stability rewards consistent equity curves, "
            "harder to game with few trades than Sortino/Sharpe.",
            "objective",
        ))
    elif profile.corr_density_above_30 > 30:
        recs.append(Recommendation(
            "objective", "hybrid", 0.75,
            f"Moderate correlations ({profile.corr_density_above_30:.0f}% > 0.30). "
            "Hybrid blends quality + correlation for balanced selection.",
            "objective",
        ))
    else:
        recs.append(Recommendation(
            "objective", "stability", 0.70,
            "Stability is the safest default — rewards consistent equity curves "
            "rather than extreme metrics that may be overfitted.",
            "objective",
        ))

    # ── Seed Prior Weight ──────────────────────────────────────────
    if profile.quality_gini > 0.3:
        spw = 0.20
        conf = 0.8
        reason = f"High quality spread (Gini={profile.quality_gini:.2f}). " \
                 "Need stronger seed prior to prevent mediocre-but-uncorrelated assets from dominating."
    elif profile.quality_gini > 0.15:
        spw = 0.12
        conf = 0.7
        reason = f"Moderate quality spread (Gini={profile.quality_gini:.2f}). " \
                 "Mild seed prior ensures quality isn't completely ignored."
    else:
        spw = 0.05
        conf = 0.6
        reason = f"Low quality spread (Gini={profile.quality_gini:.2f}). " \
                 "Assets are similar quality; minimal seed prior needed."
    recs.append(Recommendation("seed_prior_weight", spw, conf, reason, "seed"))

    # ── Overfitting Guard: Master Toggle ───────────────────────────
    guard_needed = tc_med < 25 or profile.n_suspicious > 0
    recs.append(Recommendation(
        "overfit_guard_enabled", guard_needed, 0.90 if guard_needed else 0.5,
        f"Trade count median={tc_med:.0f}, suspicious outliers={profile.n_suspicious}. "
        + ("Low trade counts + outliers strongly suggest enabling the guard."
           if guard_needed else "Pool looks clean, guard optional."),
        "overfitting_guard",
    ))

    if guard_needed:
        # ── Approach 1: Log Diminish ──────────────────────────────
        pf_stats = profile.metric_stats.get("pf", {})
        pf_skew = pf_stats.get("skew", 0)
        if pf_skew > 1.5 or pf_stats.get("p90", 0) > 4.0:
            recs.append(Recommendation(
                "of_log_enabled", True, 0.85,
                f"PF distribution is right-skewed (skew={pf_skew:.1f}, p90={pf_stats.get('p90', 0):.1f}). "
                "Log compression dampens extreme PFs so PF 8 doesn't wildly outrank PF 3.",
                "overfitting_guard",
            ))
            # Suggest caps based on percentiles
            pf_cap = min(max(pf_stats.get("p90", 4.0), 3.0), 6.0)
            recs.append(Recommendation(
                "of_log_cap_pf", round(pf_cap, 1), 0.7,
                f"PF cap at p90={pf_cap:.1f} — anything above this gets heavily compressed.",
                "overfitting_guard",
            ))
        else:
            recs.append(Recommendation(
                "of_log_enabled", False, 0.6,
                f"PF distribution not heavily skewed (skew={pf_skew:.1f}). "
                "Log compression not critical.",
                "overfitting_guard",
            ))

        # ── Approach 2: Z-Score Penalty ───────────────────────────
        if profile.n_suspicious > 0:
            z_threshold = 1.5 if profile.n_suspicious > n * 0.05 else 2.0
            z_strength = 0.20 if tc_med < 15 else 0.15
            recs.append(Recommendation(
                "of_z_score_enabled", True, 0.85,
                f"{profile.n_suspicious} assets are 2σ+ outliers on ≥3 metrics. "
                f"Z-score penalty catches per-batch outliers aggressively.",
                "overfitting_guard",
            ))
            recs.append(Recommendation(
                "of_z_threshold", z_threshold, 0.75,
                f"Threshold {z_threshold} — {'aggressive' if z_threshold < 2.0 else 'moderate'} "
                f"for a pool with {profile.n_suspicious} suspicious assets.",
                "overfitting_guard",
            ))
            recs.append(Recommendation(
                "of_z_penalty_strength", z_strength, 0.7,
                f"Strength {z_strength} — {'strong' if z_strength >= 0.20 else 'moderate'} "
                f"given median {tc_med:.0f} trades/asset.",
                "overfitting_guard",
            ))
        else:
            recs.append(Recommendation(
                "of_z_score_enabled", False, 0.6,
                "No multi-metric outliers detected. Z-score not critical.",
                "overfitting_guard",
            ))

        # ── Approach 3: Bayesian Shrinkage ────────────────────────
        if tc_med < 15:
            k = 7.0
            conf = 0.85
            reason = f"Very low trade counts (median {tc_med:.0f}). " \
                     f"k={k:.0f} pulls metrics ~41% toward batch mean at 10 trades."
        elif tc_med < 25:
            k = 5.0
            conf = 0.75
            reason = f"Low trade counts (median {tc_med:.0f}). " \
                     f"k={k:.0f} gives ~33% shrinkage at 10 trades — moderate correction."
        else:
            k = 0.0
            conf = 0.6
            reason = f"Adequate trade counts (median {tc_med:.0f}). " \
                     "Bayesian shrinkage not critical."
        recs.append(Recommendation(
            "of_bayes_enabled", k > 0, conf,
            reason, "overfitting_guard",
        ))
        if k > 0:
            recs.append(Recommendation("of_bayes_k", k, conf, reason, "overfitting_guard"))

        # ── Approach 4: Sigmoid Cap ───────────────────────────────
        # Only if Log Diminish is OFF (they overlap)
        log_rec = next((r for r in recs if r.setting == "of_log_enabled"), None)
        log_on = log_rec and log_rec.value is True
        if not log_on:
            sortino_stats = profile.metric_stats.get("sortino", {})
            if sortino_stats.get("p90", 0) > 3.0:
                recs.append(Recommendation(
                    "of_sigmoid_enabled", True, 0.7,
                    f"Sortino p90={sortino_stats.get('p90', 0):.1f}. "
                    "Sigmoid cap provides sharper wall than log for extreme values.",
                    "overfitting_guard",
                ))
            else:
                recs.append(Recommendation(
                    "of_sigmoid_enabled", False, 0.6,
                    "Metric distributions moderate. Sigmoid cap not needed (log already recommended or metrics OK).",
                    "overfitting_guard",
                ))
        else:
            recs.append(Recommendation(
                "of_sigmoid_enabled", False, 0.7,
                "Log diminish already recommended — sigmoid overlaps. Keep one off to avoid double-compression.",
                "overfitting_guard",
            ))

        # ── Approach 5: Normal Range Bonus ────────────────────────
        dd_stats = profile.metric_stats.get("max_dd", {})
        dd_outlier_pct = dd_stats.get("outlier_pct", 0)
        if dd_outlier_pct > 3 or profile.n_suspicious > 0:
            recs.append(Recommendation(
                "of_normal_range_enabled", True, 0.80,
                f"Drawdown outlier rate={dd_outlier_pct:.1f}%. "
                "Normal Range Bonus penalizes suspiciously low DD (the classic overfitting signal).",
                "overfitting_guard",
            ))
            # Widen the good range slightly for small pools
            pct_low = 15 if n < 50 else 20
            pct_high = 65 if tc_med < 15 else 60
            recs.append(Recommendation(
                "of_nr_pct_low", float(pct_low), 0.65,
                f"Good range {pct_low}-{pct_high}th percentile, adjusted for pool size={n}.",
                "overfitting_guard",
            ))
            recs.append(Recommendation(
                "of_nr_pct_high", float(pct_high), 0.65,
                f"Good range {pct_low}-{pct_high}th percentile, adjusted for pool size={n}.",
                "overfitting_guard",
            ))
        else:
            recs.append(Recommendation(
                "of_normal_range_enabled", False, 0.55,
                "Drawdown distribution looks normal. Normal range bonus not critical.",
                "overfitting_guard",
            ))

        # ── Approach 6: WF Re-rank ───────────────────────────────
        recs.append(Recommendation(
            "of_wf_rerank_enabled", True, 0.90,
            "Walk-forward re-rank is the gold standard for portfolio-level overfitting detection. "
            "Always recommended when guard is enabled.",
            "overfitting_guard",
        ))
        recs.append(Recommendation(
            "of_wf_split_pct", 75.0, 0.75,
            "75/25 split preserves enough training data while giving meaningful validation.",
            "overfitting_guard",
        ))
        recs.append(Recommendation(
            "of_wf_validation_metric", "stability", 0.8,
            "Stability as validation metric — hardest to game, rewards consistency.",
            "overfitting_guard",
        ))

    # ── Refinement ────────────────────────────────────────────────
    recs.append(Recommendation(
        "refinement_enabled", True, 0.80,
        "Prune+Swap fixes the greedy's 'no going back' problem. "
        "Leave-one-out pruning removes deadweight, swap hill-climbing finds better fits.",
        "algorithm",
    ))
    recs.append(Recommendation(
        "refinement_mode", "prune_and_swap", 0.75,
        "Prune+Swap is the full refinement — prune alone misses swap opportunities.",
        "algorithm",
    ))

    # ── Correlation Jitter ────────────────────────────────────────
    if profile.corr_density_above_30 > 20:
        recs.append(Recommendation(
            "corr_jitter_enabled", True, 0.65,
            f"Moderate+ correlation density ({profile.corr_density_above_30:.0f}% pairs > 0.30). "
            "Jitter creates diverse candidate portfolios from similar correlation structures.",
            "algorithm",
        ))
        recs.append(Recommendation(
            "corr_jitter_temperature", 0.03, 0.6,
            "Temperature 0.03 — subtle perturbation, doesn't distort the matrix.",
            "algorithm",
        ))
    else:
        recs.append(Recommendation(
            "corr_jitter_enabled", False, 0.5,
            "Low correlation density — jitter less impactful.",
            "algorithm",
        ))

    # ── Seed Metrics ──────────────────────────────────────────────
    if tc_med < 15:
        recs.append(Recommendation(
            "seed_metrics", ["stability", "sortino"],
            0.75,
            "With low trade counts, stability as primary seed metric is more robust than "
            "Sortino/Sharpe alone (which can be inflated by 1-2 big wins).",
            "seed",
        ))
    else:
        recs.append(Recommendation(
            "seed_metrics", ["sortino", "sharpe"],
            0.65,
            "Adequate trade counts — Sortino + Sharpe give balanced risk-adjusted ranking.",
            "seed",
        ))

    # ── Filters ───────────────────────────────────────────────────
    pf_p10 = profile.metric_stats.get("pf", {}).get("p10", 1.0)
    if pf_p10 > 1.5:
        min_pf = 1.3
    elif pf_p10 > 1.0:
        min_pf = 1.0
    else:
        min_pf = 0.0
    recs.append(Recommendation(
        "min_pf", min_pf, 0.7,
        f"PF p10={pf_p10:.1f}. Min PF={min_pf:.1f} removes bottom performers "
        "without being so aggressive that it shrinks the pool too much.",
        "filter",
    ))

    min_trades_rec = max(3, int(tc_med * 0.3))
    recs.append(Recommendation(
        "min_trades", min_trades_rec, 0.65,
        f"Min trades={min_trades_rec} (30% of median {tc_med:.0f}). "
        "Removes ultra-low-trade assets that are nearly pure noise.",
        "filter",
    ))

    return recs


# ═══════════════════════════════════════════════════════════════════════
#  Thorough Mode — Walk-Forward Cross-Validation Sweep
# ═══════════════════════════════════════════════════════════════════════

def _build_settings_grid(
    algos: list[str] | None = None,
    objectives: list[str] | None = None,
    sweep_guards: bool = True,
    sweep_seed_prior: bool = True,
    sweep_filters: bool = True,
    profile: PoolProfile | None = None,
    user_min_pf: float = 0.0,
    user_min_trades: int = 0,
    user_min_wr: float = 0.0,
) -> list[dict]:
    """Build a grid of setting combinations to test.

    Designed to be smart — not full combinatorial, but targeted ranges.
    """
    algo_list = algos or DEFAULT_ALGOS_TO_SWEEP
    obj_list = objectives or DEFAULT_OBJECTIVES_TO_SWEEP

    # Seed prior weights to test
    spw_list = [0.0, 0.10, 0.20] if sweep_seed_prior else [0.10]

    # Overfitting guard presets (not full combinatorial — curated presets)
    # "off" excluded from thorough grid to prevent meta-optimizer from
    # discovering that disabling all guards scores best in-sample (D5-03)
    guard_presets = [{
        "name": "light",
        "overfit_guard_enabled": True,
        "of_log_enabled": True,
        "of_log_caps": {"pf": 4.0, "sortino": 3.0, "payoff": 3.0,
                        "wr": 70.0, "sharpe": 2.5, "calmar": 3.0},
        "of_z_score_enabled": False,
        "of_bayes_enabled": False,
        "of_sigmoid_enabled": False,
        "of_normal_range_enabled": False,
        "of_wf_rerank_enabled": True,
        "of_wf_split_pct": 75.0,
        "of_wf_validation_metric": "stability",
    }]
    if sweep_guards:
        guard_presets.extend([
            {
                "name": "moderate",
                "overfit_guard_enabled": True,
                "of_log_enabled": True,
                "of_log_caps": {"pf": 4.0, "sortino": 2.5, "payoff": 3.0,
                                "wr": 65.0, "sharpe": 2.5, "calmar": 3.0},
                "of_z_score_enabled": True,
                "of_z_threshold": 2.0,
                "of_z_penalty_strength": 0.15,
                "of_bayes_enabled": False,
                "of_sigmoid_enabled": False,
                "of_normal_range_enabled": True,
                "of_nr_pct_low": 20.0,
                "of_nr_pct_high": 60.0,
                "of_nr_penalty_scale": 0.3,
                "of_nr_bonus": 0.1,
                "of_wf_rerank_enabled": True,
                "of_wf_split_pct": 75.0,
                "of_wf_validation_metric": "stability",
            },
            {
                "name": "aggressive",
                "overfit_guard_enabled": True,
                "of_log_enabled": True,
                "of_log_caps": {"pf": 3.5, "sortino": 2.5, "payoff": 2.5,
                                "wr": 65.0, "sharpe": 2.0, "calmar": 2.5},
                "of_z_score_enabled": True,
                "of_z_threshold": 1.5,
                "of_z_penalty_strength": 0.20,
                "of_bayes_enabled": True,
                "of_bayes_k": 5.0,
                "of_sigmoid_enabled": False,
                "of_normal_range_enabled": True,
                "of_nr_pct_low": 15.0,
                "of_nr_pct_high": 65.0,
                "of_nr_penalty_scale": 0.35,
                "of_nr_bonus": 0.10,
                "of_wf_rerank_enabled": True,
                "of_wf_split_pct": 75.0,
                "of_wf_validation_metric": "stability",
            },
            {
                "name": "max",
                "overfit_guard_enabled": True,
                "of_log_enabled": True,
                "of_log_caps": {"pf": 3.0, "sortino": 2.0, "payoff": 2.5,
                                "wr": 60.0, "sharpe": 2.0, "calmar": 2.5},
                "of_z_score_enabled": True,
                "of_z_threshold": 1.5,
                "of_z_penalty_strength": 0.25,
                "of_bayes_enabled": True,
                "of_bayes_k": 7.0,
                "of_sigmoid_enabled": False,
                "of_normal_range_enabled": True,
                "of_nr_pct_low": 15.0,
                "of_nr_pct_high": 60.0,
                "of_nr_penalty_scale": 0.40,
                "of_nr_bonus": 0.10,
                "of_wf_rerank_enabled": True,
                "of_wf_split_pct": 75.0,
                "of_wf_validation_metric": "stability",
            },
        ])

    # Independent filter values (not bundled presets)
    if sweep_filters:
        pf_vals = _pick_centered(_PF_POOL, user_min_pf, 4)
        tr_vals = _pick_floor(_TRADES_POOL, user_min_trades, 4)
        wr_vals = _pick_centered(_WR_POOL, user_min_wr, 4)
    else:
        pf_vals = [user_min_pf]
        tr_vals = [user_min_trades]
        wr_vals = [user_min_wr]

    # Build grid (filters are independent axes)
    grid: list[dict] = []
    for algo in algo_list:
        for obj in obj_list:
            for spw in spw_list:
                for guard in guard_presets:
                    for pf in pf_vals:
                        for tr in tr_vals:
                            for wr in wr_vals:
                                grid.append({
                                    "div_algo": algo,
                                    "objective": obj,
                                    "seed_prior_weight": spw,
                                    "guard_preset": guard["name"],
                                    **{k: v for k, v in guard.items() if k != "name"},
                                    "min_pf": pf,
                                    "min_trades": tr,
                                    "min_wr": wr,
                                })

    logger.info("Settings grid: %d combinations (%d algos × %d objectives × "
                "%d seed_prior × %d guards × %d pf × %d trades × %d wr filters)",
                len(grid), len(algo_list), len(obj_list), len(spw_list),
                len(guard_presets), len(pf_vals), len(tr_vals), len(wr_vals))
    return grid


# ─── Depth-scaled grid builder ────────────────────────────────────────

# Ordered by priority — lower depth adds from the top first
_ALGO_ORDER = ["greedy", "quality_first", "tournament", "hrp",
               "pca", "marginal_sharpe", "ensemble", "walk_forward"]
_OBJ_ORDER = ["stability", "hybrid", "max_sortino", "max_sharpe", "max_calmar"]

# Guard presets defined once (reused by both grid builders)
_GUARD_LIGHT = {
    "name": "light",
    "overfit_guard_enabled": True,
    "of_log_enabled": True,
    "of_log_caps": {"pf": 4.0, "sortino": 3.0, "payoff": 3.0,
                    "wr": 70.0, "sharpe": 2.5, "calmar": 3.0},
    "of_z_score_enabled": False,
    "of_bayes_enabled": False,
    "of_sigmoid_enabled": False,
    "of_normal_range_enabled": False,
    "of_wf_rerank_enabled": True,
    "of_wf_split_pct": 75.0,
    "of_wf_validation_metric": "stability",
}
_GUARD_MODERATE = {
    "name": "moderate",
    "overfit_guard_enabled": True,
    "of_log_enabled": True,
    "of_log_caps": {"pf": 4.0, "sortino": 2.5, "payoff": 3.0,
                    "wr": 65.0, "sharpe": 2.5, "calmar": 3.0},
    "of_z_score_enabled": True,
    "of_z_threshold": 2.0,
    "of_z_penalty_strength": 0.15,
    "of_bayes_enabled": False,
    "of_sigmoid_enabled": False,
    "of_normal_range_enabled": True,
    "of_nr_pct_low": 20.0,
    "of_nr_pct_high": 60.0,
    "of_nr_penalty_scale": 0.3,
    "of_nr_bonus": 0.1,
    "of_wf_rerank_enabled": True,
    "of_wf_split_pct": 75.0,
    "of_wf_validation_metric": "stability",
}
_GUARD_AGGRESSIVE = {
    "name": "aggressive",
    "overfit_guard_enabled": True,
    "of_log_enabled": True,
    "of_log_caps": {"pf": 3.5, "sortino": 2.5, "payoff": 2.5,
                    "wr": 65.0, "sharpe": 2.0, "calmar": 2.5},
    "of_z_score_enabled": True,
    "of_z_threshold": 1.5,
    "of_z_penalty_strength": 0.20,
    "of_bayes_enabled": True,
    "of_bayes_k": 5.0,
    "of_sigmoid_enabled": False,
    "of_normal_range_enabled": True,
    "of_nr_pct_low": 15.0,
    "of_nr_pct_high": 65.0,
    "of_nr_penalty_scale": 0.35,
    "of_nr_bonus": 0.10,
    "of_wf_rerank_enabled": True,
    "of_wf_split_pct": 75.0,
    "of_wf_validation_metric": "stability",
}
_GUARD_MAX = {
    "name": "max",
    "overfit_guard_enabled": True,
    "of_log_enabled": True,
    "of_log_caps": {"pf": 3.0, "sortino": 2.0, "payoff": 2.5,
                    "wr": 60.0, "sharpe": 2.0, "calmar": 2.5},
    "of_z_score_enabled": True,
    "of_z_threshold": 1.5,
    "of_z_penalty_strength": 0.25,
    "of_bayes_enabled": True,
    "of_bayes_k": 7.0,
    "of_sigmoid_enabled": False,
    "of_normal_range_enabled": True,
    "of_nr_pct_low": 15.0,
    "of_nr_pct_high": 60.0,
    "of_nr_penalty_scale": 0.40,
    "of_nr_bonus": 0.10,
    "of_wf_rerank_enabled": True,
    "of_wf_split_pct": 75.0,
    "of_wf_validation_metric": "stability",
}
_GUARD_ORDER = [_GUARD_LIGHT, _GUARD_MODERATE, _GUARD_AGGRESSIVE, _GUARD_MAX]

# Extreme-only guard presets (added when extreme_values=True)
_GUARD_MINIMAL = {
    "name": "minimal",
    "overfit_guard_enabled": True,
    "of_log_enabled": True,
    "of_log_caps": {"pf": 6.0, "sortino": 5.0, "payoff": 5.0,
                    "wr": 85.0, "sharpe": 4.0, "calmar": 5.0},
    "of_z_score_enabled": False,
    "of_bayes_enabled": False,
    "of_sigmoid_enabled": False,
    "of_normal_range_enabled": False,
    "of_wf_rerank_enabled": False,
    "of_wf_split_pct": 75.0,
    "of_wf_validation_metric": "stability",
}
_GUARD_EXTREME = {
    "name": "extreme",
    "overfit_guard_enabled": True,
    "of_log_enabled": True,
    "of_log_caps": {"pf": 2.5, "sortino": 1.5, "payoff": 2.0,
                    "wr": 55.0, "sharpe": 1.5, "calmar": 2.0},
    "of_z_score_enabled": True,
    "of_z_threshold": 1.2,
    "of_z_penalty_strength": 0.30,
    "of_bayes_enabled": True,
    "of_bayes_k": 10.0,
    "of_sigmoid_enabled": False,
    "of_normal_range_enabled": True,
    "of_nr_pct_low": 10.0,
    "of_nr_pct_high": 55.0,
    "of_nr_penalty_scale": 0.50,
    "of_nr_bonus": 0.10,
    "of_wf_rerank_enabled": True,
    "of_wf_split_pct": 75.0,
    "of_wf_validation_metric": "stability",
}

# Independent filter value pools (swept independently, not as bundles)
_PF_POOL = [1.0, 1.2, 1.3, 1.4, 1.5, 1.7, 2.0]
_TRADES_POOL = [3, 4, 5, 6, 8, 10]
_WR_POOL = [20.0, 30.0, 35.0, 40.0, 45.0, 50.0, 55.0]

# Extreme-only additions (deduped against base on merge)
_PF_POOL_EXTREME = [2.5, 3.5]
_TRADES_POOL_EXTREME = [1, 15]
_WR_POOL_EXTREME = [60.0, 65.0]


def _pick_centered(pool: list, user_val: float, n: int) -> list:
    """Pick n values from pool, centered on user_val (both directions)."""
    combined = sorted(set(list(pool) + [user_val]))
    if n >= len(combined):
        return combined
    idx = combined.index(user_val)
    selected = [user_val]
    lo, hi = idx - 1, idx + 1
    while len(selected) < n:
        if lo >= 0:
            selected.append(combined[lo])
            lo -= 1
        if len(selected) >= n:
            break
        if hi < len(combined):
            selected.append(combined[hi])
            hi += 1
        if lo < 0 and hi >= len(combined):
            break
    return sorted(selected[:n])


def _pick_floor(pool: list, user_val: float, n: int) -> list:
    """Pick n values from pool, all >= user_val (user's value is floor)."""
    combined = sorted(set(list(pool) + [user_val]))
    candidates = [v for v in combined if v >= user_val]
    return candidates[:n]

# Seed metric combo presets ordered by complexity
_SEED_METRIC_COMBOS = [
    ["sortino"],
    ["sortino", "stability"],
    ["sortino", "sharpe", "stability"],
    ["sortino", "sharpe", "stability", "calmar"],
]


def _build_depth_grid(
    depth_pct: int,
    extreme_values: bool = False,
    sweep_seed_prior: bool = False,
    algos_override: list[str] | None = None,
    objectives_override: list[str] | None = None,
    dim_guards: int | None = None,
    dim_filters: int | None = None,
    dim_seed_combos: int | None = None,
    dim_folds: int | None = None,
    user_min_pf: float = 0.0,
    user_min_trades: int = 0,
    user_min_wr: float = 0.0,
    # Per-axis filter overrides (take priority over dim_filters)
    dim_filter_pf: int | None = None,
    dim_filter_trades: int | None = None,
    dim_filter_wr: int | None = None,
    # Custom seed metric combos (overrides _SEED_METRIC_COMBOS entirely)
    seed_combos_override: list[list[str]] | None = None,
) -> tuple[list[dict], int]:
    """Build a depth-scaled settings grid with per-dimension overrides.

    The depth_pct (1-100) controls how many dimensions are explored.
    Individual dim_* overrides let the user pin a specific dimension count
    while the master slider controls the rest.

    Parameters
    ----------
    depth_pct : 1-100 slider value (master depth)
    extreme_values : add extra presets at the far ends of parameter ranges
    sweep_seed_prior : test multiple seed prior weights (0.0, 0.10, 0.20)
    algos_override : explicit list of algorithms to test (ignores depth)
    objectives_override : explicit list of objectives to test (user-selected)
    dim_guards : override # guard presets (None = auto from depth)
    dim_filters : override # filter values per axis (None = auto from depth)
    dim_seed_combos : override # seed metric combos (None = auto from depth)
    dim_folds : override # CV folds (None = auto from depth)
    dim_filter_pf : override # PF values (None = falls back to dim_filters)
    dim_filter_trades : override # min trades values (None = falls back to dim_filters)
    dim_filter_wr : override # win rate values (None = falls back to dim_filters)

    Returns (grid, n_folds) tuple.
    """
    d = max(1, min(100, depth_pct))

    # Algorithms: user-selected or depth-based
    if algos_override:
        algos = [a for a in algos_override if a in _ALGO_ORDER]
        if not algos:
            algos = _ALGO_ORDER[:1]
    else:
        n_algos = 1 if d <= 10 else 2 if d <= 25 else 3 if d <= 40 else 4 if d <= 55 else 5 if d <= 65 else 6 if d <= 75 else 7 if d <= 90 else 8
        algos = _ALGO_ORDER[:n_algos]

    # Objectives: user-selected or depth-based fallback
    if objectives_override:
        objectives = list(objectives_override)
    else:
        n_objs = 1 if d <= 10 else 2 if d <= 30 else 3 if d <= 50 else 4 if d <= 80 else 5
        n_objs = max(1, min(n_objs, len(_OBJ_ORDER)))
        objectives = _OBJ_ORDER[:n_objs]

    # Seed prior weights
    if not sweep_seed_prior:
        spw_list = [0.10]
    elif d < 60:
        spw_list = [0.10]
    elif d < 85:
        spw_list = [0.0, 0.10]
    else:
        spw_list = [0.0, 0.10, 0.20]

    # Guards: override or depth-based
    n_guards_val = dim_guards if dim_guards is not None else (
        1 if d <= 35 else 2 if d <= 65 else 3 if d <= 85 else 4)
    n_guards_val = max(1, min(n_guards_val, len(_GUARD_ORDER)))
    guards = list(_GUARD_ORDER[:n_guards_val])

    # Filters: independent per-axis values with individual overrides
    # Per-axis overrides take priority > dim_filters > auto from depth
    n_default = dim_filters if dim_filters is not None else (
        1 if d <= 45 else 2 if d <= 65 else 3 if d <= 85 else 4)
    n_default = max(1, n_default)
    n_pf = max(1, dim_filter_pf if dim_filter_pf is not None else n_default)
    n_tr = max(1, dim_filter_trades if dim_filter_trades is not None else n_default)
    n_wr = max(1, dim_filter_wr if dim_filter_wr is not None else n_default)

    pf_base = list(_PF_POOL)
    tr_base = list(_TRADES_POOL)
    wr_base = list(_WR_POOL)
    if extreme_values:
        pf_base = sorted(set(pf_base + _PF_POOL_EXTREME))
        tr_base = sorted(set(tr_base + _TRADES_POOL_EXTREME))
        wr_base = sorted(set(wr_base + _WR_POOL_EXTREME))

    pf_vals = _pick_centered(pf_base, user_min_pf, n_pf)
    tr_vals = _pick_floor(tr_base, user_min_trades, n_tr)
    wr_vals = _pick_centered(wr_base, user_min_wr, n_wr)

    # Seed metric combos: user-supplied override or depth-based defaults
    if seed_combos_override:
        seed_combos = seed_combos_override
    else:
        n_seed = dim_seed_combos if dim_seed_combos is not None else (
            1 if d <= 20 else 2 if d <= 50 else 3 if d <= 75 else 4)
        n_seed = max(1, min(n_seed, len(_SEED_METRIC_COMBOS)))
        seed_combos = _SEED_METRIC_COMBOS[:n_seed]

    # Add extreme guard presets when toggled
    if extreme_values:
        guards.extend([_GUARD_MINIMAL, _GUARD_EXTREME])

    # CV folds: override or depth-based
    n_folds = dim_folds if dim_folds is not None else (
        1 if d <= 25 else 2 if d <= 65 else 3)
    n_folds = max(1, min(n_folds, 3))

    # Build grid (filters are independent axes, not bundled presets)
    grid: list[dict] = []
    for algo in algos:
        for obj in objectives:
            for spw in spw_list:
                for guard in guards:
                    for pf in pf_vals:
                        for tr in tr_vals:
                            for wr in wr_vals:
                                for sm in seed_combos:
                                    grid.append({
                                        "div_algo": algo,
                                        "objective": obj,
                                        "seed_prior_weight": spw,
                                        "seed_metrics": sm,
                                        "guard_preset": guard["name"],
                                        **{k: v for k, v in guard.items() if k != "name"},
                                        "min_pf": pf,
                                        "min_trades": tr,
                                        "min_wr": wr,
                                    })

    logger.info("Depth grid (depth=%d%%): %d combos × %d folds = %d evals "
                "(%d algos × %d obj × %d spw × %d guards × "
                "%d pf × %d trades × %d wr filters × %d seeds%s)",
                d, len(grid), n_folds, len(grid) * n_folds,
                len(algos), len(objectives), len(spw_list),
                len(guards),
                len(pf_vals), len(tr_vals), len(wr_vals),
                len(seed_combos),
                " +extreme" if extreme_values else "")
    return grid, n_folds


def _run_single_cv_fold(
    settings: dict,
    daily_pnl: pd.DataFrame,
    trades: list,
    asset_batch_map: dict[str, int],
    train_idx: int,
    n_assets: int,
    n_longs: int,
    n_shorts: int,
    split_mode: bool,
    seed_metrics: list[str],
    trade_key_fn: Callable | None = None,
    # ── Cache parameters (optional, pre-computed per fold) ──
    _precomputed_am: dict | None = None,
    _precomputed_seeds: dict[str, float] | None = None,
    _precomputed_corr: pd.DataFrame | None = None,
) -> dict:
    """Run optimizer with given settings on train portion, score on validation.

    Returns {settings_hash, train_score, val_score, val_stability, val_pf, ...}

    When _precomputed_am, _precomputed_seeds, _precomputed_corr are provided,
    they are subsetted to the post-quality-filter columns instead of being
    recomputed from scratch. This avoids O(N^2) corr and O(N*trades) metrics
    being repeated for every combo in the grid.
    """
    from backend.analysis.diversification import diversify_portfolio, diversify_split_portfolio
    from backend.analysis.overfit_guard import apply_overfit_guard
    from backend.analysis.optimizer_seeds import compute_seed_scores

    train_pnl = daily_pnl.iloc[:train_idx]
    val_pnl = daily_pnl.iloc[train_idx:]

    if train_pnl.shape[1] < 2 or len(train_pnl) < 10 or len(val_pnl) < 5:
        return {"val_stability": 0.0, "val_pf": 0.0, "error": "insufficient_data"}

    # Apply quality filters on train data
    min_pf = settings.get("min_pf", 0.0)
    min_trades = settings.get("min_trades", 0)
    min_wr = settings.get("min_wr", 0.0)

    if min_pf > 0 or min_trades > 0 or min_wr > 0:
        keep_cols = []
        for col in train_pnl.columns:
            s = train_pnl[col]
            pos = s[s > 0]
            neg = s[s < 0]
            pf = float(pos.sum() / max(abs(neg.sum()), 1.0)) if len(neg) > 0 else 999.0
            wr = float(len(pos) / max(len(s[s != 0]), 1) * 100)
            n_tr = int((s != 0).sum())
            if pf >= min_pf and n_tr >= min_trades and wr >= min_wr:
                keep_cols.append(col)
        if len(keep_cols) < 2:
            return {"val_stability": 0.0, "val_pf": 0.0, "error": "all_filtered"}
        train_pnl = train_pnl[keep_cols]
        val_pnl = val_pnl[[c for c in keep_cols if c in val_pnl.columns]]

    # Use combo-specific seed_metrics if present, else function parameter
    _metrics = settings.get("seed_metrics", seed_metrics) or ["sortino"]

    # Compute seed scores on train — use cache if available
    if _precomputed_seeds is not None:
        pool_scores = {k: v for k, v in _precomputed_seeds.items()
                       if k in train_pnl.columns}
    else:
        pool_scores = compute_seed_scores(train_pnl, train_pnl.columns.tolist(), _metrics)

    # Apply overfitting guard to seed scores (if enabled)
    if settings.get("overfit_guard_enabled", False):
        # Use cached asset metrics if available
        if _precomputed_am is not None:
            am = {k: v for k, v in _precomputed_am.items()
                  if k in train_pnl.columns}
        else:
            am = _compute_asset_metrics(train_pnl, trades, asset_batch_map, trade_key_fn)
            am = {k: v for k, v in am.items() if k in train_pnl.columns}
        og_config = {
            "log_enabled": settings.get("of_log_enabled", False),
            "log_caps": settings.get("of_log_caps", {}),
            "z_score_enabled": settings.get("of_z_score_enabled", False),
            "z_threshold": settings.get("of_z_threshold", 2.0),
            "z_penalty_strength": settings.get("of_z_penalty_strength", 0.15),
            "bayes_enabled": settings.get("of_bayes_enabled", False),
            "bayes_k": settings.get("of_bayes_k", 5.0),
            "sigmoid_enabled": settings.get("of_sigmoid_enabled", False),
            "sig_caps": settings.get("of_sig_caps", {}),
            "normal_range_enabled": settings.get("of_normal_range_enabled", False),
            "nr_pct_low": settings.get("of_nr_pct_low", 20.0),
            "nr_pct_high": settings.get("of_nr_pct_high", 60.0),
            "nr_penalty_scale": settings.get("of_nr_penalty_scale", 0.3),
            "nr_bonus": settings.get("of_nr_bonus", 0.1),
        }
        pool_scores = apply_overfit_guard(pool_scores, am, og_config)

    # Compute train correlation — use cache if available
    if _precomputed_corr is not None:
        keep = [c for c in train_pnl.columns if c in _precomputed_corr.columns]
        train_corr = _precomputed_corr.loc[keep, keep]
    else:
        train_corr = train_pnl.corr()

    algo = settings.get("div_algo", "greedy")
    spw = settings.get("seed_prior_weight", 0.0)
    objective = settings.get("objective", "stability")

    try:
        if split_mode:
            n_l = min(n_longs, sum(1 for c in train_pnl.columns if c.endswith("_Long")))
            n_s = min(n_shorts, sum(1 for c in train_pnl.columns if c.endswith("_Short")))
            if n_l < 1 or n_s < 1:
                return {"val_stability": 0.0, "val_pf": 0.0, "error": "no_l_or_s"}
            candidates = diversify_split_portfolio(
                algo, train_pnl, train_corr,
                n_longs=n_l, n_shorts=n_s,
                within_objective=objective,
                combined_objective=objective,
                n_portfolios=1,
                seed_metrics=_metrics,
                max_seeds=20,
                expand_pool=True,
                seed_prior_weight=spw,
                precomputed_seeds=pool_scores,
                fast_mode=True,
            )
        else:
            n_a = min(n_assets, train_pnl.shape[1])
            candidates = diversify_portfolio(
                algo, train_pnl, train_corr, n_a, objective,
                n_portfolios=1,
                seed_metrics=_metrics,
                max_seeds=20,
                expand_pool=True,
                seed_prior_weight=spw,
                precomputed_seeds=pool_scores,
                fast_mode=True,
            )
    except Exception as e:
        logger.debug("CV fold failed for %s/%s: %s", algo, objective, e)
        return {"val_stability": 0.0, "val_pf": 0.0, "error": str(e)}

    if not candidates:
        return {"val_stability": 0.0, "val_pf": 0.0, "error": "no_candidates"}

    # Score best candidate on validation data
    best = candidates[0]
    assets = best.get("assets", [])
    val_cols = [c for c in assets if c in val_pnl.columns]
    if not val_cols:
        return {"val_stability": 0.0, "val_pf": 0.0, "error": "no_val_cols"}

    port_val = val_pnl[val_cols].sum(axis=1)
    val_stability = calculate_stability(port_val) if len(port_val) > 5 else 0.0

    pos_val = port_val[port_val > 0]
    neg_val = port_val[port_val < 0]
    val_pf = float(pos_val.sum() / max(abs(neg_val.sum()), 1.0)) if len(neg_val) > 0 else 0.0

    val_cum = port_val.cumsum()
    val_dd = val_cum - val_cum.cummax()
    val_max_dd = float(val_dd.min()) if len(val_dd) > 0 else 0.0
    val_net = float(port_val.sum())

    val_sortino = float(port_val.mean() / neg_val.std()) if len(neg_val) > 1 and neg_val.std() > 0 else 0.0
    val_sharpe = float(port_val.mean() / port_val.std() * (365 ** 0.5)) if port_val.std() > 0 else 0.0

    # Train metrics for overfit ratio
    train_cols = [c for c in assets if c in train_pnl.columns]
    port_train = train_pnl[train_cols].sum(axis=1) if train_cols else pd.Series(dtype=float)
    train_stability = calculate_stability(port_train) if len(port_train) > 5 else 0.0

    return {
        "val_stability": round(val_stability, 4),
        "val_pf": round(val_pf, 4),
        "val_max_dd": round(val_max_dd, 2),
        "val_net_profit": round(val_net, 2),
        "val_sortino": round(val_sortino, 4),
        "val_sharpe": round(val_sharpe, 4),
        "train_stability": round(train_stability, 4),
        "overfit_ratio": round(val_stability / max(train_stability, 0.001), 4),
        "n_assets_selected": len(assets),
    }


def recommend_thorough(
    daily_pnl: pd.DataFrame,
    trades: list,
    asset_batch_map: dict[str, int],
    corr_matrix: pd.DataFrame | None = None,
    n_assets: int = 10,
    n_longs: int = 5,
    n_shorts: int = 5,
    split_mode: bool = True,
    seed_metrics: list[str] | None = None,
    # Sweep config
    algos: list[str] | None = None,
    objectives: list[str] | None = None,
    sweep_guards: bool = True,
    sweep_seed_prior: bool = True,
    sweep_filters: bool = True,
    # CV config
    n_splits: int = 3,
    split_pcts: list[float] | None = None,
    trade_key_fn: Callable | None = None,
    max_combos: int = 500,
    progress_fn: Callable[[int, int], None] | None = None,
    # Depth controls
    depth_pct: int = 100,
    extreme_values: bool = False,
    # Per-dimension overrides
    dim_guards: int | None = None,
    dim_filters: int | None = None,
    dim_seed_combos: int | None = None,
    dim_folds: int | None = None,
    # User filter anchors for independent sweeps
    user_min_pf: float = 0.0,
    user_min_trades: int = 0,
    user_min_wr: float = 0.0,
    # Per-axis filter overrides
    dim_filter_pf: int | None = None,
    dim_filter_trades: int | None = None,
    dim_filter_wr: int | None = None,
    # Custom seed metric combos (overrides hardcoded defaults)
    seed_combos_override: list[list[str]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Run walk-forward cross-validation over a settings grid.

    Parameters
    ----------
    depth_pct : 1-100 exploration depth. <100 uses _build_depth_grid()
        which scales grid dimensions (algos, objectives, guards, filters,
        folds) proportionally. 100 uses the full _build_settings_grid().
    extreme_values : when True, adds extreme guard/filter presets to the grid.

    Returns (cv_results, top_presets) where:
    - cv_results: all tested combinations with average validation scores
    - top_presets: top 5 presets sorted by validation stability
    """
    from backend.analysis.optimizer_seeds import compute_seed_scores as _css

    t0 = time.perf_counter()

    # Build settings grid — depth-scaled or full
    if depth_pct < 100:
        grid, n_folds_from_depth = _build_depth_grid(
            depth_pct, extreme_values, sweep_seed_prior,
            algos_override=algos,
            objectives_override=objectives,
            dim_guards=dim_guards,
            dim_filters=dim_filters,
            dim_seed_combos=dim_seed_combos,
            dim_folds=dim_folds,
            user_min_pf=user_min_pf,
            user_min_trades=user_min_trades,
            user_min_wr=user_min_wr,
            dim_filter_pf=dim_filter_pf,
            dim_filter_trades=dim_filter_trades,
            dim_filter_wr=dim_filter_wr,
            seed_combos_override=seed_combos_override,
        )
        if split_pcts is None:
            split_pcts = [0.65, 0.70, 0.75][:n_folds_from_depth]
    else:
        grid = _build_settings_grid(
            algos=algos, objectives=objectives,
            sweep_guards=sweep_guards, sweep_seed_prior=sweep_seed_prior,
            sweep_filters=sweep_filters,
            user_min_pf=user_min_pf,
            user_min_trades=user_min_trades,
            user_min_wr=user_min_wr,
        )
        # Cap grid size
        if len(grid) > max_combos:
            logger.warning("Grid has %d combos, capping to %d", len(grid), max_combos)
            grid = grid[:max_combos]

    # Define CV splits
    n_rows = len(daily_pnl)
    if split_pcts is None:
        split_pcts = [0.65, 0.70, 0.75]
    splits = [int(n_rows * p) for p in split_pcts if int(n_rows * p) >= 10]
    if not splits:
        splits = [int(n_rows * 0.70)]

    _seed_metrics = seed_metrics or ["sortino"]

    # Collect unique seed metric combos from the grid
    _unique_seed_combos: list[tuple[str, ...]] = []
    _seen_sm: set[tuple[str, ...]] = set()
    for _g in grid:
        _sm_key = tuple(_g.get("seed_metrics", _seed_metrics))
        if _sm_key not in _seen_sm:
            _seen_sm.add(_sm_key)
            _unique_seed_combos.append(_sm_key)

    # ── Pre-compute expensive fold data keyed by train_idx ────────────
    # These operations depend only on (train_idx, full daily_pnl) — not
    # on individual settings combos — so we compute once per split and
    # reuse across all combos. With 1400 assets this avoids re-computing
    # the 1400×1400 corr matrix and per-asset metrics 500+ times.
    _fold_cache: dict[int, dict] = {}
    # Seed scores keyed by (split_idx, seed_metric_combo)
    _seed_score_cache: dict[tuple[int, tuple[str, ...]], dict[str, float]] = {}
    for _split_idx in splits:
        _train_pnl_full = daily_pnl.iloc[:_split_idx]
        if _train_pnl_full.shape[1] < 2 or len(_train_pnl_full) < 10:
            continue
        t_cache = time.perf_counter()
        _full_am = _compute_asset_metrics(
            _train_pnl_full, trades, asset_batch_map, trade_key_fn,
        )
        _full_corr = _train_pnl_full.corr()
        _fold_cache[_split_idx] = {
            "full_am": _full_am,
            "full_corr": _full_corr,
        }
        # Pre-compute seed scores for each unique seed metric combo
        _cols = _train_pnl_full.columns.tolist()
        for _sm_combo in _unique_seed_combos:
            _full_seed_scores = _css(_train_pnl_full, _cols, list(_sm_combo))
            _seed_score_cache[(_split_idx, _sm_combo)] = _full_seed_scores
        logger.info("  Fold cache split=%d: %d assets, corr %dx%d, "
                     "%d seed combos (%.1fs)",
                     _split_idx, _train_pnl_full.shape[1],
                     _full_corr.shape[0], _full_corr.shape[1],
                     len(_unique_seed_combos),
                     time.perf_counter() - t_cache)

    logger.info("Fold cache pre-computed for %d splits × %d seed combos",
                len(_fold_cache), len(_unique_seed_combos))

    # Run all combinations across all splits
    results: list[dict] = []
    total = len(grid) * len(splits)
    logger.info("Thorough mode: %d grid × %d splits = %d evaluations "
                "(depth=%d%%, extreme=%s)",
                len(grid), len(splits), total, depth_pct, extreme_values)

    for i, settings in enumerate(grid):
        fold_scores: list[dict] = []
        _combo_sm = tuple(settings.get("seed_metrics", _seed_metrics))
        for split_idx in splits:
            cached = _fold_cache.get(split_idx, {})
            _cached_seeds = _seed_score_cache.get((split_idx, _combo_sm))
            fold = _run_single_cv_fold(
                settings, daily_pnl, trades, asset_batch_map,
                train_idx=split_idx,
                n_assets=n_assets, n_longs=n_longs, n_shorts=n_shorts,
                split_mode=split_mode, seed_metrics=_seed_metrics,
                trade_key_fn=trade_key_fn,
                _precomputed_am=cached.get("full_am"),
                _precomputed_seeds=_cached_seeds,
                _precomputed_corr=cached.get("full_corr"),
            )
            fold_scores.append(fold)

        # Average across folds
        valid_folds = [f for f in fold_scores if "error" not in f]
        if not valid_folds:
            logger.warning("All %d folds failed for combo — skipping", len(fold_scores))
            continue

        avg_val_stability = np.mean([f["val_stability"] for f in valid_folds])
        avg_val_pf = np.mean([f["val_pf"] for f in valid_folds])
        avg_overfit_ratio = np.mean([f.get("overfit_ratio", 0) for f in valid_folds])
        std_val_stability = np.std([f["val_stability"] for f in valid_folds])
        avg_val_sortino = np.mean([f.get("val_sortino", 0) for f in valid_folds])
        avg_val_sharpe = np.mean([f.get("val_sharpe", 0) for f in valid_folds])
        avg_val_max_dd = np.mean([f.get("val_max_dd", 0) for f in valid_folds])

        # Composite score: stability + consistency + anti-overfit
        overfit_penalty = max(0.0, 1.0 - avg_overfit_ratio) * 0.5
        consistency_bonus = max(0.0, 0.1 - std_val_stability) * 2.0
        composite = avg_val_stability - overfit_penalty + consistency_bonus

        results.append({
            "settings": {
                "div_algo": settings["div_algo"],
                "objective": settings["objective"],
                "seed_prior_weight": settings["seed_prior_weight"],
                "seed_metrics": settings.get("seed_metrics", _seed_metrics),
                "guard_preset": settings.get("guard_preset", "off"),
                "min_pf": settings.get("min_pf", 0.0),
                "min_trades": settings.get("min_trades", 0),
                "min_wr": settings.get("min_wr", 0.0),
                "overfit_guard_enabled": settings.get("overfit_guard_enabled", False),
            },
            "full_settings": settings,
            "avg_val_stability": round(float(avg_val_stability), 4),
            "avg_val_pf": round(float(avg_val_pf), 4),
            "avg_val_sortino": round(float(avg_val_sortino), 4),
            "avg_val_sharpe": round(float(avg_val_sharpe), 4),
            "avg_val_max_dd": round(float(avg_val_max_dd), 2),
            "avg_overfit_ratio": round(float(avg_overfit_ratio), 4),
            "std_val_stability": round(float(std_val_stability), 4),
            "composite_score": round(float(composite), 4),
            "n_valid_folds": len(valid_folds),
        })

        if progress_fn:
            progress_fn(i + 1, len(grid))
        if (i + 1) % 50 == 0 or (len(grid) <= 50 and (i + 1) % 5 == 0):
            elapsed = time.perf_counter() - t0
            logger.info("  Progress: %d/%d combos (%.1fs)", i + 1, len(grid), elapsed)

    # Sort by composite score (best first)
    results.sort(key=lambda r: r["composite_score"], reverse=True)

    # Top 5 presets
    top_presets = results[:5]

    elapsed = time.perf_counter() - t0
    logger.info("Thorough mode complete: %d valid results, top composite=%.4f (%.1fs)",
                len(results), top_presets[0]["composite_score"] if top_presets else 0, elapsed)

    return results, top_presets


# ═══════════════════════════════════════════════════════════════════════
#  Main Entry Points
# ═══════════════════════════════════════════════════════════════════════

def recommend_settings(
    daily_pnl: pd.DataFrame,
    trades: list,
    asset_batch_map: dict[str, int],
    corr_matrix: pd.DataFrame | None = None,
    mode: str = "quick",
    split_mode: bool = True,
    n_assets: int = 10,
    n_longs: int = 5,
    n_shorts: int = 5,
    seed_metrics: list[str] | None = None,
    # Thorough mode options
    algos: list[str] | None = None,
    objectives: list[str] | None = None,
    sweep_guards: bool = True,
    sweep_seed_prior: bool = True,
    sweep_filters: bool = True,
    n_splits: int = 3,
    trade_key_fn: Callable | None = None,
    max_combos: int = 500,
    progress_fn: Callable[[int, int], None] | None = None,
    # Depth controls
    depth_pct: int = 100,
    extreme_values: bool = False,
    # Per-dimension overrides
    dim_guards: int | None = None,
    dim_filters: int | None = None,
    dim_seed_combos: int | None = None,
    dim_folds: int | None = None,
    # User filter anchors for independent sweeps
    user_min_pf: float = 0.0,
    user_min_trades: int = 0,
    user_min_wr: float = 0.0,
    # Per-axis filter overrides
    dim_filter_pf: int | None = None,
    dim_filter_trades: int | None = None,
    dim_filter_wr: int | None = None,
    # Custom seed metric combos (overrides hardcoded defaults)
    seed_combos_override: list[list[str]] | None = None,
) -> RecommenderResult:
    """Main entry point: analyse IS data and recommend optimizer settings.

    Parameters
    ----------
    daily_pnl : IS-period daily PnL matrix (tagged columns)
    trades : list of trade objects
    asset_batch_map : {tagged_asset: batch_id}
    corr_matrix : pre-computed correlation matrix (optional, computed if None)
    mode : "quick" (heuristic) or "thorough" (cross-validation)
    split_mode : whether the optimizer uses L/S split
    n_assets, n_longs, n_shorts : target portfolio sizes
    seed_metrics : seed ranking metrics
    algos : algorithms to sweep in thorough mode
    objectives : user-selected objectives to sweep in thorough mode
    sweep_guards, sweep_seed_prior, sweep_filters : dimensions to sweep
    n_splits : number of CV splits in thorough mode
    trade_key_fn : function to get tagged key from trade object
    max_combos : max grid size in thorough mode
    depth_pct : 1-100 exploration depth slider value (thorough mode)
    extreme_values : add extreme guard/filter presets (thorough mode)
    user_min_pf, user_min_trades, user_min_wr : user's current filter
        settings — used as anchors for independent filter sweeps
    dim_filter_pf, dim_filter_trades, dim_filter_wr : per-axis overrides
        for how many filter values to test per axis
    """
    t0 = time.perf_counter()

    # Compute correlation if not provided
    if corr_matrix is None or corr_matrix.empty:
        corr_matrix = daily_pnl.corr()

    # Profile the pool (always run — feeds both modes)
    profile, asset_metrics = profile_pool(
        daily_pnl, trades, asset_batch_map, corr_matrix, trade_key_fn,
    )

    logger.info("Pool profile: %d assets (%dL/%dS), %d batches, "
                "median trades=%.0f, suspicious=%d, corr_mean=%.2f",
                profile.n_assets, profile.n_longs, profile.n_shorts,
                profile.n_batches, profile.trade_count_median,
                profile.n_suspicious, profile.corr_mean_abs)

    # Quick mode recommendations
    quick_recs = recommend_quick(profile, asset_metrics, split_mode,
                                 allowed_algos=algos)

    result = RecommenderResult(
        mode=mode,
        profile=profile,
        recommendations=quick_recs,
    )

    # Thorough mode: add cross-validation
    if mode == "thorough":
        cv_results, top_presets = recommend_thorough(
            daily_pnl, trades, asset_batch_map, corr_matrix,
            n_assets=n_assets, n_longs=n_longs, n_shorts=n_shorts,
            split_mode=split_mode, seed_metrics=seed_metrics,
            algos=algos, objectives=objectives,
            sweep_guards=sweep_guards, sweep_seed_prior=sweep_seed_prior,
            sweep_filters=sweep_filters,
            n_splits=n_splits, trade_key_fn=trade_key_fn,
            max_combos=max_combos, progress_fn=progress_fn,
            depth_pct=depth_pct, extreme_values=extreme_values,
            dim_guards=dim_guards,
            dim_filters=dim_filters, dim_seed_combos=dim_seed_combos,
            dim_folds=dim_folds,
            user_min_pf=user_min_pf,
            user_min_trades=user_min_trades,
            user_min_wr=user_min_wr,
            dim_filter_pf=dim_filter_pf,
            dim_filter_trades=dim_filter_trades,
            dim_filter_wr=dim_filter_wr,
            seed_combos_override=seed_combos_override,
        )
        result.cv_results = cv_results
        result.top_presets = top_presets

    result.elapsed_seconds = round(time.perf_counter() - t0, 2)
    return result


def result_to_dict(result: RecommenderResult) -> dict:
    """Serialize a RecommenderResult to JSON-safe dict."""
    return {
        "mode": result.mode,
        "elapsed_seconds": result.elapsed_seconds,
        "profile": {
            "n_assets": result.profile.n_assets,
            "n_batches": result.profile.n_batches,
            "n_longs": result.profile.n_longs,
            "n_shorts": result.profile.n_shorts,
            "trade_count_median": result.profile.trade_count_median,
            "trade_count_mean": result.profile.trade_count_mean,
            "trade_count_min": result.profile.trade_count_min,
            "trade_count_max": result.profile.trade_count_max,
            "trade_count_p25": result.profile.trade_count_p25,
            "trade_count_p75": result.profile.trade_count_p75,
            "pct_below_10_trades": result.profile.pct_below_10_trades,
            "pct_below_20_trades": result.profile.pct_below_20_trades,
            "metric_stats": result.profile.metric_stats,
            "corr_mean_abs": result.profile.corr_mean_abs,
            "corr_median_abs": result.profile.corr_median_abs,
            "corr_p90_abs": result.profile.corr_p90_abs,
            "corr_density_above_50": result.profile.corr_density_above_50,
            "corr_density_above_30": result.profile.corr_density_above_30,
            "pf_spread": result.profile.pf_spread,
            "quality_gini": result.profile.quality_gini,
            "batch_sizes": {str(k): v for k, v in result.profile.batch_sizes.items()},
            "batch_balance_ratio": result.profile.batch_balance_ratio,
            "n_suspicious": result.profile.n_suspicious,
            "suspicious_tags": result.profile.suspicious_tags,
        },
        "recommendations": [
            {
                "setting": r.setting,
                "value": r.value,
                "confidence": r.confidence,
                "reason": r.reason,
                "category": r.category,
            }
            for r in result.recommendations
        ],
        "cv_results": result.cv_results[:20],  # Top 20 for display
        "top_presets": result.top_presets,
    }
