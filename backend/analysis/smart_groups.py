"""Auto Smart Groups — pre-optimization batch family detection & L2 group assignment.

Detects strategy families via correlation clustering, picks the strongest
representative from each family, and suggests L2 groups.  Uses ONLY IS data —
never touches OOS.

The pipeline:
1. Apply IS asset filters (same min_pf / min_wr / min_trades the optimizer uses)
   to get the REALISTIC asset pool per batch — garbage assets are excluded.
2. Build batch-level equity curves from filtered assets only.
3. Split Long/Short batches — families detected WITHIN each side, never cross-side.
4. Compute pairwise ROLLING correlation matrix (75-day windows, not static).
   Optional: regime-weight windows by vol similarity to recent conditions.
5. Hierarchical clustering into families (high rolling corr = same family).
6. Rank batches within each family by DEFLATED Sharpe + quality.
7. Greedy Sharpe addition: iteratively build portfolio maximizing deflated Sharpe.
   Guardrails: concentration penalty, IS holdout validation, trend direction tilt.
8. Optional: trade-level bootstrap stability check.
9. IS cross-validation: run the pipeline on K time-folds, only trust STABLE results.
10. Assign L2 groups: each family → one group.
11. Return grouping + drop suggestions.

Key concepts:
- Deflated Sharpe: IS Sharpe decays ~73% OOS in crypto. We haircut IS metrics
  by 30-40% depending on trade count. Fewer trades → bigger haircut.
- Rolling correlation: static correlation over a 2-year window hides regime shifts.
  We use 75-day rolling windows and take the MEDIAN across windows.
- IS cross-validation: split IS into K time-based folds, run family detection +
  greedy Sharpe on each fold. Only trust results stable across ≥ K-1 folds.
- Direction-aware: Long and Short batches form families only within their own side.
- IS-filtered assets: same filters the optimizer applies (min_pf, min_wr, min_trades)
  so the batch curves reflect what L1 would actually work with.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Trend strength name → multiplier mapping
TREND_STRENGTH_MAP: dict[str, float] = {
    "none": 1.0,
    "small": 1.05,
    "medium": 1.10,
    "high": 1.20,
    "extreme": 1.35,
}


@dataclass
class BatchInfo:
    """Summary of a single batch for grouping decisions."""
    batch_id: int
    name: str = ""
    avg_profit_factor: float = 0.0
    avg_win_rate: float = 0.0
    total_net_profit: float = 0.0
    batch_grade: str = ""
    total_trades: int = 0
    avg_trades_per_symbol: float = 0.0
    quality_score: float = 0.0


@dataclass
class FamilyGroup:
    """A detected strategy family."""
    family_id: int
    batch_ids: list[int]           # all members
    representative_id: int         # best member (highest quality)
    avg_internal_corr: float       # how similar members are
    quality_scores: dict[int, float] = field(default_factory=dict)
    side: str = ""                 # "Long" | "Short" | "" (direction-aware)


@dataclass
class SmartGroupResult:
    """Complete result of auto smart grouping."""
    families: list[FamilyGroup]
    l2_groups: list[list[int]]         # suggested L2 groups (list of batch ID lists)
    recommended_ids: list[int]         # suggested batch IDs to use (best per family)
    drop_ids: list[int]                # batch IDs that are redundant
    drop_reasons: dict[int, str]       # why each was dropped
    correlation_matrix: dict           # for frontend visualization if needed

    greedy_order: list[dict] = field(default_factory=list)  # greedy Sharpe addition log
    cv_stability: dict = field(default_factory=dict)         # cross-validation results
    assets_filtered: dict = field(default_factory=dict)      # per-batch: total → filtered count
    bootstrap_stability: dict = field(default_factory=dict)  # {batch_id: float 0.0-1.0}
    trend_regime: dict = field(default_factory=dict)          # {regime, bull_pct, bear_pct, ...}

    def to_dict(self) -> dict:
        return {
            "families": [asdict(f) for f in self.families],
            "l2_groups": self.l2_groups,
            "recommended_ids": self.recommended_ids,
            "drop_ids": self.drop_ids,
            "drop_reasons": {str(k): v for k, v in self.drop_reasons.items()},
            "correlation_matrix": self.correlation_matrix,
            "greedy_order": self.greedy_order,
            "cv_stability": self.cv_stability,
            "assets_filtered": {str(k): v for k, v in self.assets_filtered.items()},
            "bootstrap_stability": {str(k): round(v, 3) for k, v in self.bootstrap_stability.items()},
            "trend_regime": self.trend_regime,
        }


# ---------------------------------------------------------------------------
# Deflated Sharpe
# ---------------------------------------------------------------------------

def _deflation_haircut(
    total_trades: int,
    avg_trades_per_symbol: float,
    n_params: int = 0,
    base_haircut: float = 0.35,
) -> float:
    """Compute a deflation multiplier for IS Sharpe → expected OOS Sharpe.

    Returns a multiplier in [0.30, 0.70] — multiply raw IS Sharpe by this.
    Higher = more trustworthy.  Lower = more likely overfit.
    """
    mult = 1.0 - base_haircut  # 0.65

    # Trade count penalty: scaled for low-freq crypto (3-30 trades/sym range)
    if avg_trades_per_symbol > 0:
        trade_penalty = max(0.0, 0.15 * (1 - min(avg_trades_per_symbol / 30, 1.0)))
    elif total_trades > 0:
        effective_per_sym = total_trades / 200
        trade_penalty = max(0.0, 0.15 * (1 - min(effective_per_sym / 30, 1.0)))
    else:
        trade_penalty = 0.15

    param_penalty = min(0.10, n_params * 0.01) if n_params > 0 else 0.0

    mult = mult - trade_penalty - param_penalty
    return max(0.30, min(0.70, mult))


def _deflated_sharpe(
    raw_sharpe: float,
    total_trades: int = 0,
    avg_trades_per_symbol: float = 0.0,
    n_params: int = 0,
    base_haircut: float = 0.35,
) -> float:
    """Apply deflation haircut to an IS Sharpe ratio."""
    mult = _deflation_haircut(total_trades, avg_trades_per_symbol, n_params, base_haircut)
    return raw_sharpe * mult


# ---------------------------------------------------------------------------
# Enhancement 1: Concentration penalty
# ---------------------------------------------------------------------------

def _concentration_penalty(
    pass_rate: float,
) -> float:
    """Piecewise-linear concentration penalty based on asset pass rate.

    pass_rate = surviving_assets / universe_count.
    Returns a multiplier in [0.2, 1.0] applied to marginal Sharpe.
    Lenient above 25%, punishing below 15%.
    """
    if pass_rate >= 0.25:
        return 1.0
    if pass_rate >= 0.15:
        # Linear interpolation: 0.15 → 0.7, 0.25 → 1.0
        return 0.7 + (pass_rate - 0.15) / 0.10 * 0.3
    if pass_rate >= 0.05:
        # Linear interpolation: 0.05 → 0.3, 0.15 → 0.7
        return 0.3 + (pass_rate - 0.05) / 0.10 * 0.4
    # Below 5%
    return 0.2


# ---------------------------------------------------------------------------
# Enhancement 5: Trend regime detection
# ---------------------------------------------------------------------------

def _compute_trend_regime(
    trend_data: dict[str, dict],
    lookback: int = 20,
    regime_pct: float = 0.65,
) -> dict:
    """Determine current market regime from close-to-close direction streaks.

    trend_data: per-symbol dict with keys:
      - recent_streaks: list of (direction, length) tuples, newest first
      - avg_bull_len: historical average bullish streak length
      - avg_bear_len: historical average bearish streak length
      - current_dir: "bull" or "bear" (most recent streak direction)
      - current_len: length of the current (ongoing) streak

    Returns dict: {regime, bull_pct, bear_pct, streak_ratio}
    """
    if not trend_data:
        return {"regime": "neutral", "bull_pct": 0.5, "bear_pct": 0.5, "streak_ratio": 1.0}

    # Aggregate the last N streaks across all symbols
    total_bull = 0
    total_bear = 0
    current_lens: list[float] = []
    avg_historical_lens: list[float] = []

    for sym_data in trend_data.values():
        recent = sym_data.get("recent_streaks", [])
        for direction, _length in recent[:lookback]:
            if direction == "bull":
                total_bull += 1
            else:
                total_bear += 1
        # Current streak info for streak_ratio
        c_dir = sym_data.get("current_dir", "")
        c_len = sym_data.get("current_len", 0)
        if c_len > 0:
            current_lens.append(c_len)
            if c_dir == "bull":
                avg_hist = sym_data.get("avg_bull_len", c_len)
            else:
                avg_hist = sym_data.get("avg_bear_len", c_len)
            if avg_hist > 0:
                avg_historical_lens.append(avg_hist)

    total = total_bull + total_bear
    if total == 0:
        return {"regime": "neutral", "bull_pct": 0.5, "bear_pct": 0.5, "streak_ratio": 1.0}

    bull_pct = total_bull / total
    bear_pct = total_bear / total

    if bull_pct >= regime_pct:
        regime = "bullish"
    elif bear_pct >= regime_pct:
        regime = "bearish"
    else:
        regime = "neutral"

    # Streak ratio: how extended the current streak is vs historical avg
    if current_lens and avg_historical_lens:
        avg_current = float(np.mean(current_lens))
        avg_hist = float(np.mean(avg_historical_lens))
        streak_ratio = avg_current / avg_hist if avg_hist > 0 else 1.0
    else:
        streak_ratio = 1.0

    return {
        "regime": regime,
        "bull_pct": round(bull_pct, 3),
        "bear_pct": round(bear_pct, 3),
        "streak_ratio": round(streak_ratio, 3),
        "total_ranges": total,
    }


def _trend_tilt_multiplier(
    batch_side: str,
    regime: str,
    base_tilt: float,
    streak_ratio: float,
) -> float:
    """Compute direction tilt multiplier for a batch.

    Aligned direction gets a boost, opposite gets 1.0 (no penalty).
    Streak decay: as streak exceeds avg length, tilt decays back to 1.0.
    """
    if base_tilt <= 1.0 or regime == "neutral":
        return 1.0

    # Check alignment
    aligned = False
    if regime == "bullish" and batch_side == "Long":
        aligned = True
    elif regime == "bearish" and batch_side == "Short":
        aligned = True

    if not aligned:
        return 1.0

    # Apply streak decay: full tilt at ratio <= 1.0, decays to 1.0 at ratio >= 2.0
    decay = max(0.0, 1.0 - (streak_ratio - 1.0))
    tilt = 1.0 + (base_tilt - 1.0) * decay
    return max(1.0, min(base_tilt, tilt))


# ---------------------------------------------------------------------------
# Rolling correlation (Enhancement 4: regime-weighted)
# ---------------------------------------------------------------------------

def _rolling_correlation_matrix(
    batch_curves: dict[int, pd.Series],
    window: int = 75,
    min_periods: int = 30,
    regime_ref_days: int = 0,
    regime_bandwidth: float = 0.0,
) -> pd.DataFrame:
    """Compute pairwise correlation using rolling windows.

    Default: take MEDIAN across windows (robust to regime shifts).
    With regime_ref_days > 0: weight windows by volatility similarity
    to the most recent regime, so families detected are stable under
    conditions similar to what you're about to trade live.
    """
    batch_ids = sorted(batch_curves.keys())
    n = len(batch_ids)

    if n < 2:
        return pd.DataFrame(1.0, index=batch_ids, columns=batch_ids)

    df = pd.DataFrame({bid: batch_curves[bid] for bid in batch_ids}).fillna(0)

    if len(df) < min_periods * 2:
        logger.warning("Series too short (%d days) for rolling correlation (window=%d), "
                       "falling back to static", len(df), window)
        return df.corr()

    # Pre-compute vol-similarity weights if regime weighting is enabled
    vol_weights: pd.Series | None = None
    if regime_ref_days > 0 and len(df) > regime_ref_days + window:
        # Equal-weight portfolio for vol computation
        eq_portfolio = df.mean(axis=1)
        rolling_vol = eq_portfolio.rolling(window=window, min_periods=min_periods).std()
        rolling_vol = rolling_vol.dropna()

        if len(rolling_vol) > 0:
            # Reference vol: last regime_ref_days of the series
            ref_vol = float(eq_portfolio.iloc[-regime_ref_days:].std())
            # Auto bandwidth: 1 std of vol distribution
            bw = regime_bandwidth if regime_bandwidth > 0 else float(rolling_vol.std())
            if bw > 0:
                vol_weights = np.exp(-0.5 * ((rolling_vol - ref_vol) / bw) ** 2)
                # Normalize to prevent numerical issues
                vol_weights = vol_weights / vol_weights.sum() * len(vol_weights)
                logger.info("Regime-weighted correlation: ref_vol=%.4f, bandwidth=%.4f, "
                            "%d weighted windows", ref_vol, bw, len(vol_weights))

    result = pd.DataFrame(0.0, index=batch_ids, columns=batch_ids)
    for i in range(n):
        result.iloc[i, i] = 1.0
        for j in range(i + 1, n):
            rolling_corr = df.iloc[:, i].rolling(window=window, min_periods=min_periods).corr(
                df.iloc[:, j]
            )
            valid = rolling_corr.dropna()

            if len(valid) == 0:
                agg = 0.0
            elif vol_weights is not None:
                # Regime-weighted average instead of median
                aligned_w = vol_weights.reindex(valid.index).dropna()
                common = valid.index.intersection(aligned_w.index)
                if len(common) > 0:
                    w = aligned_w.loc[common].values
                    v = valid.loc[common].values
                    w_sum = w.sum()
                    agg = float(np.dot(w, v) / w_sum) if w_sum > 0 else float(valid.median())
                else:
                    agg = float(valid.median())
            else:
                agg = float(valid.median())

            result.iloc[i, j] = agg
            result.iloc[j, i] = agg

    return result


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------

def _quality_score(info: BatchInfo) -> float:
    """Compute a quality score from batch metrics. Blends PF, WR, grade, trade count."""
    pf_s = max(0, min(1, (info.avg_profit_factor - 0.5) / 2.5)) if info.avg_profit_factor > 0 else 0
    wr_s = max(0, min(1, (info.avg_win_rate - 30) / 40)) if info.avg_win_rate > 0 else 0
    grade_map = {
        "A+": 1.0, "A": 0.92, "A-": 0.85,
        "B+": 0.77, "B": 0.69, "B-": 0.62,
        "C+": 0.54, "C": 0.46, "C-": 0.38,
        "D+": 0.31, "D": 0.23, "D-": 0.15,
        "F": 0.05,
    }
    grade_s = grade_map.get(info.batch_grade, 0.5)
    trade_s = max(0, min(1, info.avg_trades_per_symbol / 30)) if info.avg_trades_per_symbol > 0 else 0.3
    return pf_s * 0.30 + wr_s * 0.20 + grade_s * 0.30 + trade_s * 0.20


# ---------------------------------------------------------------------------
# Family detection (BFS clustering)
# ---------------------------------------------------------------------------

def detect_families(
    corr_matrix: pd.DataFrame,
    threshold: float = 0.65,
) -> list[list[int]]:
    """Cluster batches into families via BFS on correlation adjacency graph."""
    batch_ids = list(corr_matrix.columns)
    n = len(batch_ids)
    if n <= 1:
        return [batch_ids]

    adj: dict[int, set[int]] = {b: set() for b in batch_ids}
    for i in range(n):
        for j in range(i + 1, n):
            c = corr_matrix.iloc[i, j]
            if not np.isnan(c) and c > threshold:
                adj[batch_ids[i]].add(batch_ids[j])
                adj[batch_ids[j]].add(batch_ids[i])

    visited: set[int] = set()
    families: list[list[int]] = []
    for b in batch_ids:
        if b in visited:
            continue
        queue = [b]
        component: list[int] = []
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            for neighbor in adj[node]:
                if neighbor not in visited:
                    queue.append(neighbor)
        families.append(sorted(component))

    return families


# ---------------------------------------------------------------------------
# Direction detection from batch name
# ---------------------------------------------------------------------------

def _batch_side(info: BatchInfo) -> str:
    """Extract 'Long' or 'Short' from batch name.

    Batch names end with ' — Long' or ' — Short'.
    Returns 'Long', 'Short', or '' if unknown.
    """
    name = info.name.strip()
    if name.endswith("— Long") or name.endswith("- Long") or name.lower().endswith("long"):
        return "Long"
    if name.endswith("— Short") or name.endswith("- Short") or name.lower().endswith("short"):
        return "Short"
    return ""


# ---------------------------------------------------------------------------
# IS-filtered asset curves (replaces _build_top_asset_curves)
# ---------------------------------------------------------------------------

def _build_filtered_asset_curves(
    asset_pnl: pd.DataFrame,
    asset_batch: dict[str, int],
    asset_metrics: dict[str, dict] | None = None,
    min_pf: float = 0.0,
    min_wr: float = 0.0,
    min_trades: int = 0,
) -> tuple[dict[int, pd.Series], dict[int, tuple[int, int]]]:
    """Build batch-level curves from IS-FILTERED assets only.

    Instead of arbitrary top-N by Sharpe, applies the SAME filters the
    optimizer uses (min_pf, min_wr, min_trades) so curves reflect the
    realistic asset pool that L1 would work with.

    Returns:
        (batch_curves, filter_stats) where:
        - batch_curves: batch_id → daily PnL Series (sum of surviving assets)
        - filter_stats: batch_id → (total_assets, surviving_assets)
    """
    # Group assets by batch
    batch_assets: dict[int, list[str]] = {}
    for asset, bid in asset_batch.items():
        if asset in asset_pnl.columns:
            batch_assets.setdefault(bid, []).append(asset)

    result: dict[int, pd.Series] = {}
    stats: dict[int, tuple[int, int]] = {}

    for bid, assets in batch_assets.items():
        if not assets:
            continue

        total = len(assets)
        surviving: list[str] = []

        for a in assets:
            if asset_metrics and a in asset_metrics:
                m = asset_metrics[a]
                a_pf = m.get("pf", 0.0)
                a_wr = m.get("wr", 0.0)
                a_trades = m.get("trades", 0)

                if min_trades > 0 and a_trades < min_trades:
                    continue
                if min_pf > 0 and a_pf < min_pf:
                    continue
                if min_wr > 0 and a_wr < min_wr:
                    continue

            surviving.append(a)

        if surviving:
            result[bid] = asset_pnl[surviving].sum(axis=1).fillna(0)
        stats[bid] = (total, len(surviving))

    return result, stats


# Legacy fallback: top-N by Sharpe (used when no asset_metrics provided)
def _build_top_asset_curves(
    asset_pnl: pd.DataFrame,
    asset_batch: dict[str, int],
    top_n: int = 5,
) -> dict[int, pd.Series]:
    """Build batch curves using top N assets by Sharpe (fallback only)."""
    batch_assets: dict[int, list[str]] = {}
    for asset, bid in asset_batch.items():
        if asset in asset_pnl.columns:
            batch_assets.setdefault(bid, []).append(asset)

    result: dict[int, pd.Series] = {}
    for bid, assets in batch_assets.items():
        if not assets:
            continue
        asset_sharpes: list[tuple[str, float]] = []
        for a in assets:
            series = asset_pnl[a].fillna(0)
            if series.std() > 0 and len(series) > 10:
                sharpe = float(series.mean() / series.std() * np.sqrt(365))
            else:
                sharpe = 0.0
            asset_sharpes.append((a, sharpe))
        asset_sharpes.sort(key=lambda x: x[1], reverse=True)
        top_assets = [a for a, s in asset_sharpes[:top_n] if s > 0]
        if not top_assets:
            top_assets = [a for a, _ in asset_sharpes[:top_n]]
        if top_assets:
            result[bid] = asset_pnl[top_assets].sum(axis=1).fillna(0)

    return result


# ---------------------------------------------------------------------------
# Portfolio Sharpe
# ---------------------------------------------------------------------------

def _portfolio_sharpe(curves: list[pd.Series], deflation_mult: float = 1.0) -> float:
    """Compute annualized Sharpe of a combined portfolio (sum of curves)."""
    if not curves:
        return 0.0
    combined = pd.concat(curves, axis=1).fillna(0).sum(axis=1)
    if combined.std() == 0 or len(combined) < 10:
        return 0.0
    raw = float(combined.mean() / combined.std() * np.sqrt(365))
    return raw * deflation_mult


# ---------------------------------------------------------------------------
# Greedy Sharpe selection (with all V2 enhancements)
# ---------------------------------------------------------------------------

def greedy_sharpe_selection(
    top_curves: dict[int, pd.Series],
    family_reps: list[int],
    batch_infos: dict[int, BatchInfo] | None = None,
    min_improvement: float = 0.02,
    use_deflated: bool = True,
    base_haircut: float = 0.35,
    # Enhancement 1: concentration penalty
    filter_stats: dict[int, tuple[int, int]] | None = None,
    # Enhancement 2: IS holdout
    holdout_ratio: float = 0.0,
    holdout_weight: float = 0.4,
    # Enhancement 5: trend tilt
    side_map: dict[int, str] | None = None,
    trend_regime_info: dict | None = None,
    trend_tilt_base: float = 1.0,
) -> list[dict]:
    """Greedy Sharpe addition using DEFLATED Sharpe with V2 guardrails.

    Start with highest deflated-Sharpe batch, iteratively add the batch
    that most improves portfolio deflated Sharpe. Stop when marginal < threshold.

    V2 enhancements (all default to off for backward compat):
    - Concentration penalty: penalize batches with low asset pass rate
    - IS holdout: split curves into train/holdout, validate marginal on holdout
    - Trend tilt: mild boost to direction-aligned batches in trending regime
    """
    if not family_reps or not top_curves:
        return []

    available = sorted(b for b in family_reps if b in top_curves)
    if not available:
        return []

    # --- Enhancement 1: precompute concentration multipliers ---
    conc_mults: dict[int, float] = {}
    if filter_stats:
        universe = max(total for total, _surv in filter_stats.values()) if filter_stats else 1
        universe = max(universe, 1)
        for bid in available:
            if bid in filter_stats:
                _total, surviving = filter_stats[bid]
                pass_rate = surviving / universe
                conc_mults[bid] = _concentration_penalty(pass_rate)
            else:
                conc_mults[bid] = 1.0

    # --- Enhancement 2: split curves into train/holdout ---
    use_holdout = holdout_ratio > 0 and holdout_weight > 0
    train_curves: dict[int, pd.Series] = {}
    holdout_curves: dict[int, pd.Series] = {}

    if use_holdout:
        for bid, curve in top_curves.items():
            if bid not in available and bid not in family_reps:
                continue
            n_pts = len(curve)
            split_idx = int(n_pts * (1.0 - holdout_ratio))
            if split_idx < 30 or (n_pts - split_idx) < 10:
                # Not enough data for meaningful holdout — skip
                train_curves[bid] = curve
                holdout_curves[bid] = curve
            else:
                train_curves[bid] = curve.iloc[:split_idx]
                holdout_curves[bid] = curve.iloc[split_idx:]
    else:
        train_curves = top_curves

    # --- Enhancement 5: precompute trend tilts ---
    trend_tilts: dict[int, float] = {}
    regime_str = ""
    if trend_tilt_base > 1.0 and trend_regime_info and side_map:
        regime_str = trend_regime_info.get("regime", "neutral")
        streak_ratio = trend_regime_info.get("streak_ratio", 1.0)
        for bid in available:
            batch_side = side_map.get(bid, "")
            trend_tilts[bid] = _trend_tilt_multiplier(
                batch_side, regime_str, trend_tilt_base, streak_ratio,
            )

    # --- Per-batch deflation multipliers ---
    deflation_mults: dict[int, float] = {}
    for bid in available:
        if use_deflated and batch_infos and bid in batch_infos:
            info = batch_infos[bid]
            deflation_mults[bid] = _deflation_haircut(
                info.total_trades, info.avg_trades_per_symbol,
                base_haircut=base_haircut,
            )
        else:
            deflation_mults[bid] = 1.0 if not use_deflated else (1.0 - base_haircut)

    def _avg_mult(batch_ids: list[int]) -> float:
        if not batch_ids:
            return 1.0 - base_haircut
        return sum(deflation_mults.get(b, 0.6) for b in batch_ids) / len(batch_ids)

    def _eval_portfolio(batch_ids: list[int], curves_dict: dict[int, pd.Series]) -> tuple[float, float]:
        curves = [curves_dict[b] for b in batch_ids if b in curves_dict]
        raw = _portfolio_sharpe(curves, deflation_mult=1.0)
        deflated = raw * _avg_mult(batch_ids)
        return raw, deflated

    # Standalone scores (on train curves if holdout enabled)
    eval_curves = train_curves if use_holdout else top_curves
    standalone_raw: dict[int, float] = {}
    standalone_defl: dict[int, float] = {}
    for b in available:
        raw, defl = _eval_portfolio([b], eval_curves)
        standalone_raw[b] = raw
        standalone_defl[b] = defl

    selected: list[int] = []
    log: list[dict] = []

    # Seed: highest deflated Sharpe (tiebreak by batch_id)
    first = max(available, key=lambda b: (standalone_defl[b], b))
    selected.append(first)
    available.remove(first)
    current_raw, current_defl = standalone_raw[first], standalone_defl[first]

    seed_entry: dict = {
        "batch_id": first, "step": 1,
        "sharpe_before": 0.0, "sharpe_after": round(current_defl, 4),
        "sharpe_raw": round(current_raw, 4),
        "deflation_mult": round(deflation_mults.get(first, 0.65), 3),
        "marginal": round(current_defl, 4), "action": "seed",
    }
    if conc_mults:
        seed_entry["concentration_mult"] = round(conc_mults.get(first, 1.0), 3)
    if trend_tilts:
        seed_entry["trend_tilt"] = round(trend_tilts.get(first, 1.0), 3)
    log.append(seed_entry)

    # Holdout tracking
    if use_holdout:
        _, current_holdout_defl = _eval_portfolio(selected, holdout_curves)
    else:
        current_holdout_defl = 0.0

    # Greedy addition
    step = 2
    while available:
        best_candidate = None
        best_net_marginal = -float("inf")
        best_train_defl = current_defl
        best_train_raw = current_raw
        best_holdout_marginal = 0.0
        best_train_marginal = 0.0

        for candidate in available:
            test_ids = selected + [candidate]
            raw, defl = _eval_portfolio(test_ids, eval_curves)
            train_marginal = defl - current_defl

            # Enhancement 1: concentration penalty on marginal
            cm = conc_mults.get(candidate, 1.0) if conc_mults else 1.0
            adj_marginal = train_marginal * cm

            # Enhancement 5: trend tilt on marginal
            tt = trend_tilts.get(candidate, 1.0) if trend_tilts else 1.0
            adj_marginal = adj_marginal * tt

            # Enhancement 2: holdout validation
            holdout_marginal = 0.0
            if use_holdout:
                _, h_defl = _eval_portfolio(test_ids, holdout_curves)
                holdout_marginal = h_defl - current_holdout_defl
                net_marginal = adj_marginal + holdout_weight * holdout_marginal
            else:
                net_marginal = adj_marginal

            if net_marginal > best_net_marginal:
                best_net_marginal = net_marginal
                best_train_defl = defl
                best_train_raw = raw
                best_candidate = candidate
                best_holdout_marginal = holdout_marginal
                best_train_marginal = train_marginal

        if best_candidate is None or best_net_marginal < min_improvement:
            break

        selected.append(best_candidate)
        available.remove(best_candidate)

        entry: dict = {
            "batch_id": best_candidate, "step": step,
            "sharpe_before": round(current_defl, 4),
            "sharpe_after": round(best_train_defl, 4),
            "sharpe_raw": round(best_train_raw, 4),
            "deflation_mult": round(deflation_mults.get(best_candidate, 0.65), 3),
            "marginal": round(best_train_marginal, 4),
            "net_marginal": round(best_net_marginal, 4),
            "action": "add",
        }
        if conc_mults:
            entry["concentration_mult"] = round(conc_mults.get(best_candidate, 1.0), 3)
        if trend_tilts:
            entry["trend_tilt"] = round(trend_tilts.get(best_candidate, 1.0), 3)
        if use_holdout:
            entry["holdout_marginal"] = round(best_holdout_marginal, 4)
        log.append(entry)

        current_defl = best_train_defl
        current_raw = best_train_raw
        if use_holdout:
            _, current_holdout_defl = _eval_portfolio(selected, holdout_curves)
        step += 1

    # Log skipped
    for bid in available:
        test_ids = selected + [bid]
        raw, defl = _eval_portfolio(test_ids, eval_curves)
        train_marginal = defl - current_defl
        cm = conc_mults.get(bid, 1.0) if conc_mults else 1.0
        tt = trend_tilts.get(bid, 1.0) if trend_tilts else 1.0
        adj_marginal = train_marginal * cm * tt

        skip_entry: dict = {
            "batch_id": bid, "step": step,
            "sharpe_before": round(current_defl, 4),
            "sharpe_after": round(defl, 4),
            "sharpe_raw": round(raw, 4),
            "deflation_mult": round(deflation_mults.get(bid, 0.65), 3),
            "marginal": round(train_marginal, 4),
            "net_marginal": round(adj_marginal, 4),
            "action": "skip",
        }
        if conc_mults:
            skip_entry["concentration_mult"] = round(cm, 3)
        if trend_tilts:
            skip_entry["trend_tilt"] = round(tt, 3)
        if use_holdout:
            _, h_defl = _eval_portfolio(test_ids, holdout_curves)
            skip_entry["holdout_marginal"] = round(h_defl - current_holdout_defl, 4)
        log.append(skip_entry)

    return log


# ---------------------------------------------------------------------------
# Enhancement 3: Trade-level bootstrap stability
# ---------------------------------------------------------------------------

def _bootstrap_stability(
    trades_by_batch: dict[int, list[dict]],
    filtered_curves: dict[int, pd.Series],
    family_reps: list[int],
    batch_infos: dict[int, BatchInfo],
    use_deflated: bool = True,
    base_haircut: float = 0.35,
    n_iter: int = 100,
    drop_pct: float = 0.15,
    filter_stats: dict[int, tuple[int, int]] | None = None,
    holdout_ratio: float = 0.0,
    holdout_weight: float = 0.4,
    side_map: dict[int, str] | None = None,
    trend_regime_info: dict | None = None,
    trend_tilt_base: float = 1.0,
) -> dict[int, float]:
    """Run N bootstrap iterations, count how often each batch is selected.

    In each iteration: for every batch, randomly drop drop_pct of trades,
    rebuild daily PnL, re-run greedy selection, record selections.

    Returns {batch_id: stability_score} where score = selection_count / n_iter.
    """
    if not trades_by_batch or not filtered_curves or not family_reps:
        return {}

    rng = random.Random(42)  # deterministic bootstrap
    selection_counts: dict[int, int] = {bid: 0 for bid in family_reps}

    for _iteration in range(n_iter):
        # Build degraded curves: drop random trades from each batch
        degraded_curves: dict[int, pd.Series] = {}
        for bid in family_reps:
            if bid not in trades_by_batch or bid not in filtered_curves:
                if bid in filtered_curves:
                    degraded_curves[bid] = filtered_curves[bid]
                continue

            trades = trades_by_batch[bid]
            if len(trades) < 5:
                # Too few trades to drop — keep original
                degraded_curves[bid] = filtered_curves[bid]
                continue

            n_drop = max(1, int(len(trades) * drop_pct))
            drop_indices = set(rng.sample(range(len(trades)), n_drop))
            surviving_trades = [t for i, t in enumerate(trades) if i not in drop_indices]

            # Rebuild daily PnL from surviving trades
            if surviving_trades:
                daily: dict[str, float] = {}
                for t in surviving_trades:
                    d = t.get("date", "")
                    daily[d] = daily.get(d, 0.0) + t.get("pnl", 0.0)
                if daily:
                    s = pd.Series(daily).sort_index()
                    # Reindex to match original curve dates
                    orig = filtered_curves[bid]
                    s = s.reindex(orig.index, fill_value=0.0)
                    degraded_curves[bid] = s
                else:
                    degraded_curves[bid] = filtered_curves[bid]
            else:
                degraded_curves[bid] = filtered_curves[bid]

        if not degraded_curves:
            continue

        # Re-run greedy on degraded curves (with same params)
        greedy_log = greedy_sharpe_selection(
            degraded_curves, [b for b in family_reps if b in degraded_curves],
            batch_infos=batch_infos,
            use_deflated=use_deflated,
            base_haircut=base_haircut,
            filter_stats=filter_stats,
            holdout_ratio=holdout_ratio,
            holdout_weight=holdout_weight,
            side_map=side_map,
            trend_regime_info=trend_regime_info,
            trend_tilt_base=trend_tilt_base,
        )
        for entry in greedy_log:
            if entry["action"] in ("seed", "add"):
                bid = entry["batch_id"]
                selection_counts[bid] = selection_counts.get(bid, 0) + 1

    # Convert to stability scores
    return {bid: count / n_iter for bid, count in selection_counts.items()}


# ---------------------------------------------------------------------------
# IS cross-validation
# ---------------------------------------------------------------------------

def _cross_validate_families(
    batch_curves: dict[int, pd.Series],
    batch_infos: dict[int, BatchInfo],
    filtered_curves: dict[int, pd.Series],
    family_threshold: float = 0.65,
    rolling_window: int = 75,
    n_folds: int = 5,
    use_deflated: bool = True,
    base_haircut: float = 0.35,
) -> dict:
    """Run family detection + greedy Sharpe on K time-based folds of IS.

    Returns stability metrics:
    - family_stability: for each batch pair, in how many folds they were in the same family
    - selection_stability: for each batch, in how many folds it was selected by greedy Sharpe
    - stable_families: families that appear in ≥ (n_folds - 1) folds
    - stable_selections: batches selected in ≥ (n_folds - 1) folds

    Time-based folds (NOT random): split the date range into n_folds consecutive blocks.
    Each fold uses all data EXCEPT that block (leave-one-out on time).
    """
    batch_ids = sorted(batch_curves.keys())
    if len(batch_ids) < 2:
        return {"n_folds": 0, "stable_selections": batch_ids}

    # Build aligned date index
    all_dates = sorted(set().union(*(set(batch_curves[b].index) for b in batch_ids)))
    if len(all_dates) < n_folds * 30:
        # Too few dates for meaningful CV — skip
        logger.warning("Too few dates (%d) for %d-fold CV, skipping", len(all_dates), n_folds)
        return {"n_folds": 0, "skipped": True, "reason": "too_few_dates"}

    # Split dates into n_folds consecutive blocks
    fold_size = len(all_dates) // n_folds
    folds: list[list] = []
    for i in range(n_folds):
        start = i * fold_size
        end = start + fold_size if i < n_folds - 1 else len(all_dates)
        folds.append(all_dates[start:end])

    # Track: how often each pair is in the same family, how often each batch is selected
    pair_same_family: dict[tuple[int, int], int] = {}
    selection_count: dict[int, int] = {b: 0 for b in batch_ids}

    for fold_idx in range(n_folds):
        # Leave-one-out: exclude fold_idx dates
        excluded_dates = set(folds[fold_idx])
        included_dates = [d for d in all_dates if d not in excluded_dates]

        if len(included_dates) < 60:
            continue  # skip folds that leave too little data

        # Build fold-specific batch curves
        fold_batch_curves: dict[int, pd.Series] = {}
        for bid in batch_ids:
            s = batch_curves[bid]
            mask = s.index.isin(included_dates)
            fold_s = s[mask]
            if len(fold_s) > 10:
                fold_batch_curves[bid] = fold_s

        if len(fold_batch_curves) < 2:
            continue

        # Rolling correlation + family detection on this fold
        fold_corr = _rolling_correlation_matrix(fold_batch_curves, window=rolling_window)
        fold_families = detect_families(fold_corr, threshold=family_threshold)

        # Track pair membership
        for fam_members in fold_families:
            for i, a in enumerate(fam_members):
                for b in fam_members[i + 1:]:
                    pair = (min(a, b), max(a, b))
                    pair_same_family[pair] = pair_same_family.get(pair, 0) + 1

        # Greedy Sharpe on this fold
        # Pick best per family (tiebreak by batch_id)
        fold_reps: list[int] = []
        for fam_members in fold_families:
            best = max(fam_members, key=lambda b: (batch_infos.get(b, BatchInfo(b)).quality_score, b))
            fold_reps.append(best)

        # Use filtered curves for greedy Sharpe
        fold_filtered = {b: filtered_curves[b] for b in fold_reps
                         if b in filtered_curves}
        if fold_filtered:
            # Restrict to included dates
            for bid in list(fold_filtered.keys()):
                s = fold_filtered[bid]
                mask = s.index.isin(included_dates)
                fold_filtered[bid] = s[mask]

            fold_greedy = greedy_sharpe_selection(
                fold_filtered, list(fold_filtered.keys()),
                batch_infos=batch_infos,
                use_deflated=use_deflated,
                base_haircut=base_haircut,
            )
            for entry in fold_greedy:
                if entry["action"] in ("seed", "add"):
                    selection_count[entry["batch_id"]] = selection_count.get(entry["batch_id"], 0) + 1

    # Compute stability
    min_stable = max(1, n_folds - 1)  # ≥ K-1 folds = "stable"
    stable_selections = [b for b, c in selection_count.items() if c >= min_stable]
    stable_pairs = [(a, b) for (a, b), c in pair_same_family.items() if c >= min_stable]

    return {
        "n_folds": n_folds,
        "selection_counts": {str(b): c for b, c in selection_count.items() if c > 0},
        "stable_selections": stable_selections,
        "pair_stability": {f"{a}-{b}": c for (a, b), c in pair_same_family.items()},
        "stable_pairs": stable_pairs,
        "min_stable_threshold": min_stable,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_smart_groups(
    batch_curves: dict[int, pd.Series],
    batch_infos: dict[int, BatchInfo],
    asset_pnl: pd.DataFrame | None = None,
    asset_batch: dict[str, int] | None = None,
    asset_metrics: dict[str, dict] | None = None,
    min_pf: float = 0.0,
    min_wr: float = 0.0,
    min_trades: int = 0,
    top_n_assets: int = 5,
    family_threshold: float = 0.65,
    max_groups: int = 6,
    rolling_window: int = 75,
    use_deflated: bool = True,
    base_haircut: float = 0.35,
    cv_folds: int = 5,
    # V2 enhancements (all default to off for backward compat)
    holdout_ratio: float = 0.0,
    holdout_weight: float = 0.4,
    skip_bootstrap: bool = True,
    bootstrap_n: int = 100,
    bootstrap_drop_pct: float = 0.15,
    trades_by_batch: dict[int, list[dict]] | None = None,
    regime_ref_days: int = 0,
    regime_bandwidth: float = 0.0,
    trend_strength: str = "none",
    trend_regime_info: dict | None = None,
) -> SmartGroupResult:
    """Main entry point: detect families, pick best reps, build L2 groups.

    V2 enhancements (all optional, backward compatible):
    - holdout_ratio/holdout_weight: IS sacrificial holdout for greedy validation
    - skip_bootstrap/bootstrap_n/bootstrap_drop_pct: trade-level bootstrap stability
    - trades_by_batch: per-batch trade data for bootstrap
    - regime_ref_days/regime_bandwidth: vol-weighted rolling correlation
    - trend_strength/trend_regime_info: direction tilt in greedy selection
    """
    batch_ids = sorted(batch_curves.keys())
    if len(batch_ids) < 2:
        return SmartGroupResult(
            families=[], l2_groups=[batch_ids], recommended_ids=batch_ids,
            drop_ids=[], drop_reasons={}, correlation_matrix={},
        )

    # -------------------------------------------------------------------
    # 0. Direction-aware: split batches into Long / Short / Unknown groups
    # -------------------------------------------------------------------
    side_map: dict[int, str] = {}
    long_ids: list[int] = []
    short_ids: list[int] = []
    unknown_ids: list[int] = []

    for bid in batch_ids:
        info = batch_infos.get(bid)
        if info:
            side = _batch_side(info)
            side_map[bid] = side
            if side == "Long":
                long_ids.append(bid)
            elif side == "Short":
                short_ids.append(bid)
            else:
                unknown_ids.append(bid)
        else:
            side_map[bid] = ""
            unknown_ids.append(bid)

    logger.info("Smart groups: direction split — %d Long, %d Short, %d unknown",
                len(long_ids), len(short_ids), len(unknown_ids))

    # -------------------------------------------------------------------
    # 1. Build ROLLING correlation matrix (with optional regime weighting)
    # -------------------------------------------------------------------
    corr_mat = _rolling_correlation_matrix(
        batch_curves, window=rolling_window,
        regime_ref_days=regime_ref_days,
        regime_bandwidth=regime_bandwidth,
    )
    logger.info("Smart groups: rolling correlation (window=%d, regime_days=%d) for %d batches",
                rolling_window, regime_ref_days, len(batch_ids))

    # -------------------------------------------------------------------
    # 2. Detect families WITHIN each side (never cross Long/Short)
    # -------------------------------------------------------------------
    all_families: list[list[int]] = []

    for side_label, side_ids in [("Long", long_ids), ("Short", short_ids), ("", unknown_ids)]:
        if len(side_ids) < 2:
            if side_ids:
                all_families.append(side_ids)
            continue
        # Extract sub-matrix for this side
        sub_corr = corr_mat.loc[side_ids, side_ids]
        side_fams = detect_families(sub_corr, threshold=family_threshold)
        all_families.extend(side_fams)
        logger.info("Smart groups: %s side — %d families from %d batches",
                     side_label or "Unknown", len(side_fams), len(side_ids))

    logger.info("Smart groups: total %d families from %d batches (threshold=%.2f)",
                len(all_families), len(batch_ids), family_threshold)

    # -------------------------------------------------------------------
    # 3. Score batches, pick best representative per family
    # -------------------------------------------------------------------
    families: list[FamilyGroup] = []
    recommended: list[int] = []
    drop_ids: list[int] = []
    drop_reasons: dict[int, str] = {}

    for fam_idx, members in enumerate(all_families):
        quality_map: dict[int, float] = {}
        for bid in members:
            info = batch_infos.get(bid)
            if info:
                qs = _quality_score(info)
                info.quality_score = qs
                quality_map[bid] = qs
            else:
                quality_map[bid] = 0.3

        best_id = max(members, key=lambda b: (quality_map.get(b, 0), b))
        recommended.append(best_id)

        for bid in members:
            if bid != best_id:
                drop_ids.append(bid)
                best_info = batch_infos.get(best_id)
                best_name = best_info.name if best_info else f"#{best_id}"
                this_qs = quality_map.get(bid, 0)
                best_qs = quality_map.get(best_id, 0)
                drop_reasons[bid] = (
                    f"Family {fam_idx + 1}: quality {this_qs:.2f} < {best_name} ({best_qs:.2f})"
                )

        # Avg internal correlation
        internal_corrs: list[float] = []
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if a in corr_mat.columns and b in corr_mat.columns:
                    c = corr_mat.loc[a, b]
                    if not np.isnan(c):
                        internal_corrs.append(float(c))

        # Determine side from first member
        fam_side = side_map.get(members[0], "")

        families.append(FamilyGroup(
            family_id=fam_idx,
            batch_ids=members,
            representative_id=best_id,
            avg_internal_corr=float(np.mean(internal_corrs)) if internal_corrs else 0.0,
            quality_scores=quality_map,
            side=fam_side,
        ))

    # -------------------------------------------------------------------
    # 4. Build IS-filtered asset curves for greedy Sharpe
    # -------------------------------------------------------------------
    filter_stats: dict[int, tuple[int, int]] = {}
    greedy_log: list[dict] = []
    filtered_curves: dict[int, pd.Series] = {}

    # Resolve trend tilt base from strength name
    trend_tilt_base = TREND_STRENGTH_MAP.get(trend_strength, 1.0)

    if asset_pnl is not None and asset_batch is not None:
        has_filters = (min_pf > 0 or min_wr > 0 or min_trades > 0) and asset_metrics
        if has_filters:
            filtered_curves, filter_stats = _build_filtered_asset_curves(
                asset_pnl, asset_batch, asset_metrics,
                min_pf=min_pf, min_wr=min_wr, min_trades=min_trades,
            )
            logger.info("Smart groups: IS-filtered asset curves — %s",
                        {bid: f"{s[1]}/{s[0]}" for bid, s in filter_stats.items()})
        else:
            # Fallback: top-N by Sharpe (no IS filters provided)
            filtered_curves = _build_top_asset_curves(asset_pnl, asset_batch, top_n=top_n_assets)
            logger.info("Smart groups: fallback to top-%d assets (no IS filters)", top_n_assets)

        if filtered_curves:
            greedy_log = greedy_sharpe_selection(
                filtered_curves, recommended,
                batch_infos=batch_infos,
                use_deflated=use_deflated,
                base_haircut=base_haircut,
                filter_stats=filter_stats if filter_stats else None,
                holdout_ratio=holdout_ratio,
                holdout_weight=holdout_weight,
                side_map=side_map,
                trend_regime_info=trend_regime_info,
                trend_tilt_base=trend_tilt_base,
            )
            greedy_selected = [e["batch_id"] for e in greedy_log if e["action"] in ("seed", "add")]
            greedy_skipped = [e["batch_id"] for e in greedy_log if e["action"] == "skip"]
            if greedy_selected:
                for bid in greedy_skipped:
                    if bid in recommended:
                        recommended.remove(bid)
                        drop_ids.append(bid)
                        entry = next((e for e in greedy_log if e["batch_id"] == bid), None)
                        marginal = entry.get("net_marginal", entry.get("marginal", 0)) if entry else 0
                        defl_mult = entry.get("deflation_mult", 0.65) if entry else 0.65
                        drop_reasons[bid] = (
                            f"Greedy skip — net marginal {marginal:+.4f} "
                            f"(deflation ×{defl_mult:.2f})"
                        )
                recommended = greedy_selected
                logger.info("Smart groups: greedy selection — %d selected, %d skipped",
                            len(greedy_selected), len(greedy_skipped))

    # -------------------------------------------------------------------
    # 4b. Bootstrap stability (optional, compute-heavy)
    # -------------------------------------------------------------------
    bootstrap_result: dict[int, float] = {}
    if not skip_bootstrap and trades_by_batch and filtered_curves and recommended:
        logger.info("Smart groups: running bootstrap stability (%d iterations, %.0f%% drop)",
                    bootstrap_n, bootstrap_drop_pct * 100)
        bootstrap_result = _bootstrap_stability(
            trades_by_batch, filtered_curves, recommended,
            batch_infos=batch_infos,
            use_deflated=use_deflated,
            base_haircut=base_haircut,
            n_iter=bootstrap_n,
            drop_pct=bootstrap_drop_pct,
            filter_stats=filter_stats if filter_stats else None,
            holdout_ratio=holdout_ratio,
            holdout_weight=holdout_weight,
            side_map=side_map,
            trend_regime_info=trend_regime_info,
            trend_tilt_base=trend_tilt_base,
        )
        for bid, score in bootstrap_result.items():
            if score < 0.6:
                existing = drop_reasons.get(bid, "")
                flag = f"Bootstrap unstable: {score:.0%} ({int(score * bootstrap_n)}/{bootstrap_n})"
                drop_reasons[bid] = (existing + " | " + flag).strip(" |") if existing else flag
        logger.info("Smart groups: bootstrap stability — %s",
                    {bid: f"{s:.0%}" for bid, s in bootstrap_result.items()})

    # -------------------------------------------------------------------
    # 5. IS cross-validation (stability check)
    # -------------------------------------------------------------------
    cv_stability: dict = {}
    if cv_folds > 0 and len(batch_ids) >= 3:
        use_curves = filtered_curves if filtered_curves else batch_curves
        cv_stability = _cross_validate_families(
            batch_curves, batch_infos, use_curves,
            family_threshold=family_threshold,
            rolling_window=rolling_window,
            n_folds=cv_folds,
            use_deflated=use_deflated,
            base_haircut=base_haircut,
        )
        # Annotate: mark unstable selections in drop_reasons
        stable_set = set(cv_stability.get("stable_selections", []))
        if stable_set:
            for bid in recommended[:]:
                if bid not in stable_set and len(stable_set) > 0:
                    sel_counts = cv_stability.get("selection_counts", {})
                    fold_count = sel_counts.get(str(bid), 0)
                    n_folds_actual = cv_stability.get("n_folds", cv_folds)
                    if fold_count < n_folds_actual - 1:
                        drop_reasons[bid] = (
                            drop_reasons.get(bid, "") +
                            f" | CV unstable: selected in {fold_count}/{n_folds_actual} folds"
                        ).strip(" |")
            logger.info("Smart groups: CV stable selections: %s", stable_set)

    # -------------------------------------------------------------------
    # 6. Build L2 groups from families
    # -------------------------------------------------------------------
    l2_groups: list[list[int]] = []
    sorted_fams = sorted(families, key=lambda f: len(f.batch_ids), reverse=True)

    if len(sorted_fams) <= max_groups:
        for fam in sorted_fams:
            l2_groups.append(fam.batch_ids)
    else:
        for fam in sorted_fams[:max_groups - 1]:
            l2_groups.append(fam.batch_ids)
        overflow: list[int] = []
        for fam in sorted_fams[max_groups - 1:]:
            overflow.extend(fam.batch_ids)
        if overflow:
            l2_groups.append(overflow)

    # -------------------------------------------------------------------
    # 7. Serialize
    # -------------------------------------------------------------------
    corr_dict = {
        "batch_ids": batch_ids,
        "matrix": corr_mat.values.tolist(),
    }

    return SmartGroupResult(
        families=families,
        l2_groups=l2_groups,
        recommended_ids=recommended,
        drop_ids=drop_ids,
        drop_reasons=drop_reasons,
        greedy_order=greedy_log,
        correlation_matrix=corr_dict,
        cv_stability=cv_stability,
        assets_filtered=filter_stats,
        bootstrap_stability=bootstrap_result,
        trend_regime=trend_regime_info or {},
    )
