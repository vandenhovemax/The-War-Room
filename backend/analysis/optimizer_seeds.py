"""Seed scoring — pre-optimizer asset ranking for greedy search.

Ranks assets by composite metrics before feeding them as seeds
to the portfolio optimizer. Reduces O(N^2) search space.
"""
from __future__ import annotations

import pandas as pd

from backend.analysis.scoring import (
    calculate_stability,
    portfolio_calmar,
    portfolio_cde,
    portfolio_est_efficiency,
    portfolio_expectancy,
    portfolio_gain_pain,
    portfolio_lake_ratio,
    portfolio_payoff,
    portfolio_profit_factor,
    portfolio_rapid_recovery,
    portfolio_recovery,
    portfolio_robust_sortino,
    portfolio_sharpe,
    portfolio_sortino,
    portfolio_tail_ratio,
    portfolio_ulcer,
)


def compute_benchmark_seed_scores(
    benchmark_stats: dict,
    min_reliable_trades: int = 10,
    consistency_blend: float = 0.0,
    fragility_blend: float = 0.0,
    wr_harmony_blend: float = 0.0,
    dsr_gate: bool = False,
    daily_pnl: "pd.DataFrame | None" = None,
) -> dict[str, float]:
    """Derive per-asset quality scores from Benchmark Robustness stats.

    Applies a trade-count confidence penalty so small samples
    don't outrank reliable ones.

    Scoring:
      base        = avg(H1_PF, H2_PF), each capped at 10
      confidence  = min(weaker_half_trades / min_reliable_trades, 1.0)
      status_mult = 1.2 Elite / 1.1 Strong / 1.0 Stable / 0.7 Weak
      final_score = base * confidence * status_mult  (higher = better)

    Optional multipliers (all disabled when blend=0.0):
      consistency  — rewards balanced H1/H2 PFs (penalizes lopsided splits)
      fragility    — rewards PF that survives top-5% winner removal
      wr_harmony   — rewards consistent win rates across H1/H2

    Parameters
    ----------
    benchmark_stats : {asset: {H1_PF, H2_PF, h1_trades, h2_trades, status,
                               h1_wr, h2_wr, pnl_values}}
    min_reliable_trades : minimum trades for full confidence
    consistency_blend : 0.0-0.5; weight for H1/H2 PF consistency (0=off)
    fragility_blend   : 0.0-0.5; weight for tail-risk fragility test (0=off)
    wr_harmony_blend  : 0.0-0.5; weight for win-rate consistency (0=off)

    Returns {asset: float} — higher = better seed candidate.
    """
    STATUS_MULT = {
        "Elite": 1.2,
        "Strong": 1.1,
        "Stable": 1.0,
        "Weak": 0.7,
        "Fragile": 0.5,
    }

    scores: dict[str, float] = {}
    for asset, s in benchmark_stats.items():
        h1_t = s.get("h1_trades", 0)
        h2_t = s.get("h2_trades", 0)
        confidence = min(min(h1_t, h2_t) / max(min_reliable_trades, 1), 1.0)

        h1_pf = min(s.get("H1_PF", 1.0), 10.0)
        h2_pf = min(s.get("H2_PF", 1.0), 10.0)
        base = (h1_pf + h2_pf) / 2.0

        status = s.get("status", "Weak")
        mult = STATUS_MULT.get(status, 0.7)

        score = base * confidence * mult

        # --- Optional multipliers (each defaults to 1.0 when blend=0) ---

        # A. H1/H2 Consistency: penalizes lopsided PF splits
        if consistency_blend > 0 and max(h1_pf, h2_pf) > 0:
            ratio = min(h1_pf, h2_pf) / max(h1_pf, h2_pf)
            score *= consistency_blend * ratio + (1 - consistency_blend)

        # B. Tail-Risk Fragility: checks if PF survives removing top 5% winners
        if fragility_blend > 0:
            pnl_vals = s.get("pnl_values")
            if pnl_vals and len(pnl_vals) >= 5:
                score *= _fragility_multiplier(pnl_vals, fragility_blend)

        # C. Win Rate Harmony: penalizes divergent win rates across halves
        if wr_harmony_blend > 0:
            h1_wr = s.get("h1_wr", 0.0)
            h2_wr = s.get("h2_wr", 0.0)
            wr_harmony = 1.0 - abs(h1_wr - h2_wr)
            score *= wr_harmony_blend * wr_harmony + (1 - wr_harmony_blend)

        # D. DSR Benchmark Gate: penalizes statistically insignificant Sharpes
        if dsr_gate and daily_pnl is not None and asset in daily_pnl.columns:
            from backend.analysis.seed_ranker import dsr_benchmark_multiplier
            n_pool = len(daily_pnl.columns)
            score *= dsr_benchmark_multiplier(daily_pnl[asset].values, n_pool)

        scores[asset] = score
    return scores


def _fragility_multiplier(pnl_values: list[float], blend: float) -> float:
    """Compute tail-risk fragility multiplier.

    Removes top 5% of winning trades and recomputes PF.
    Returns a blended multiplier: blend * (trimmed_pf/full_pf) + (1-blend).
    """
    from math import ceil

    wins_sum = sum(p for p in pnl_values if p > 0)
    loss_sum = sum(abs(p) for p in pnl_values if p < 0)
    if loss_sum == 0:
        return 1.0  # All winners, no fragility to measure
    full_pf = min(wins_sum / loss_sum, 10.0)
    if full_pf <= 0:
        return 1.0

    # Sort winners descending, trim top 5%
    winners = sorted((p for p in pnl_values if p > 0), reverse=True)
    trim_n = max(1, ceil(len(winners) * 0.05))
    trimmed_wins_sum = wins_sum - sum(winners[:trim_n])

    if trimmed_wins_sum <= 0:
        trimmed_pf = 0.0
    else:
        trimmed_pf = min(trimmed_wins_sum / loss_sum, 10.0)

    ratio = trimmed_pf / full_pf  # 0.0-1.0
    return blend * ratio + (1 - blend)


def compute_recency_seed_scores(
    df_trades: pd.DataFrame,
    mode: str = "gradual",
    batch_size: int = 10,
    boost_factor: float = 3.0,
    min_trades: int = 5,
    min_reliable_trades: int = 20,
) -> dict[str, float]:
    """Compute per-asset recency-weighted quality scores for seed ranking.

    For each asset:
      1. Sort trades by Exit Time (newest first)
      2. Assign weights based on mode:
         - gradual: weight[i] = (N - i) / N  (newest=1.0, oldest=1/N)
         - batch:   last batch_size trades get boost_factor, rest get 1.0
      3. Compute weighted Win Rate and weighted Profit Factor
      4. Score = weighted_WR * min(weighted_PF, 10.0) * confidence
         where confidence = min(n_trades / min_reliable_trades, 1.0)

    Returns {asset: float} — higher = better (same convention as benchmark).
    """
    scores: dict[str, float] = {}
    for asset, group in df_trades.groupby("Asset"):
        trades = group.sort_values("Exit Time", ascending=False).reset_index(drop=True)
        n = len(trades)

        if n < min_trades:
            scores[asset] = 0.0
            continue

        # Assign weights
        if mode == "batch":
            weights = [boost_factor if i < batch_size else 1.0 for i in range(n)]
        else:  # gradual
            weights = [(n - i) / n for i in range(n)]

        profits = trades["Net Profit"].values

        # Weighted Win Rate
        total_weight = sum(weights)
        win_weight = sum(w for w, p in zip(weights, profits) if p > 0)
        weighted_wr = win_weight / total_weight if total_weight > 0 else 0.0

        # Weighted Profit Factor
        w_gross_profit = sum(w * p for w, p in zip(weights, profits) if p > 0)
        w_gross_loss = sum(w * abs(p) for w, p in zip(weights, profits) if p < 0)

        if w_gross_loss > 0:
            weighted_pf = w_gross_profit / w_gross_loss
        else:
            weighted_pf = 10.0  # all winners → cap

        # Cap PF at 10 to prevent outlier dominance (same as benchmark)
        weighted_pf = min(weighted_pf, 10.0)

        # Confidence penalty (same pattern as benchmark)
        confidence = min(n / max(min_reliable_trades, 1), 1.0)

        scores[asset] = weighted_wr * weighted_pf * confidence

    return scores


def compute_is_wfe_seed_scores(
    daily_pnl: pd.DataFrame,
    is_pct: int = 60,
    min_is_trades: int = 0,
    trades_by_asset: dict[str, int] | None = None,
) -> dict[str, float]:
    """Compute real Walk-Forward Efficiency per asset using IS:OOS split.

    Splits each asset's daily PnL series into IS (first is_pct%) and OOS
    (remaining), then computes WFE = (OOS daily profit rate) / (IS daily profit rate).

    Parameters
    ----------
    daily_pnl : Date x Asset PnL matrix
    is_pct : IS window as percentage of total (e.g. 60 = 60:40 split)
    min_is_trades : minimum trades in the IS window for asset to be scored
    trades_by_asset : {asset: trade_count_in_IS} — required when min_is_trades > 0

    Returns {asset: float} — higher = better (same convention as external_scores).
    """
    scores: dict[str, float] = {}
    for asset in daily_pnl.columns:
        pnl = daily_pnl[asset].dropna()
        total = len(pnl)
        if total < 20:
            continue  # not enough data for meaningful WFE

        is_len = int(total * is_pct / 100)
        oos_len = total - is_len
        if oos_len < 5:
            continue  # OOS window too small

        # Check minimum IS trades if required
        if min_is_trades > 0 and trades_by_asset:
            is_trades = trades_by_asset.get(asset, 0)
            if is_trades < min_is_trades:
                continue

        is_pnl_sum = pnl.iloc[:is_len].sum()
        oos_pnl_sum = pnl.iloc[is_len:].sum()

        is_daily_rate = is_pnl_sum / is_len
        oos_daily_rate = oos_pnl_sum / oos_len

        if is_daily_rate <= 0:
            scores[asset] = 0.0  # IS not profitable — WFE meaningless
            continue

        wfe = oos_daily_rate / is_daily_rate
        scores[asset] = wfe  # higher = OOS held up better

    return scores


def compute_seed_scores(
    daily_returns: pd.DataFrame,
    assets: list[str],
    metrics: list[str],
    external_scores: dict[str, dict[str, float]] | None = None,
    metric_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Compute composite seed scores for ranking assets before greedy search.

    Uses weighted rank-averaging across selected metrics so sign differences cancel.
    Lower returned score = better seed candidate.

    Parameters
    ----------
    daily_returns : Date x Asset PnL matrix
    assets : list of asset column names to rank
    metrics : list of metric codes to average:
        'sortino', 'pf', 'pnl', 'avg_dd', 'max_dd',
        'ulcer', 'wfe', 'is_wfe', 'wr', 'recovery', 'stability',
        'sharpe', 'calmar', 'expectancy', 'payoff',
        'lake_ratio', 'tail_ratio', 'gain_pain',
        'cde', 'robust_sortino', 'est_efficiency', 'rapid_recovery'
    external_scores : {metric_code: {asset: float}} for pre-computed values
        (e.g. real WFE scores). Higher = better when provided.
    metric_weights : {metric_code: float} user-defined weight per metric.
        Default weight = 1.0 for any metric not specified.

    Returns {asset: float} — lower = better seed.
    """
    if not assets:
        return {}
    if external_scores is None:
        external_scores = {}
    _weights = metric_weights or {}

    all_ranks: dict[str, float] = {a: 0.0 for a in assets}
    total_weight = 0.0

    for m in metrics:
        w = _weights.get(m, 1.0)
        scores: dict[str, float] = {}

        if m in external_scores:
            raw = external_scores[m]
            for a in assets:
                scores[a] = -(raw.get(a, 0.0))
        else:
            for a in assets:
                if a not in daily_returns.columns:
                    scores[a] = float("inf")
                    continue
                pnl = daily_returns[a]
                if pnl.empty or pnl.std() == 0:
                    scores[a] = float("inf")
                    continue
                scores[a] = _score_metric(pnl, m, n_pool=len(daily_returns.columns))

        ranked = sorted(assets, key=lambda a: (scores.get(a, float("inf")), a))
        for rank, a in enumerate(ranked):
            all_ranks[a] += rank * w
        total_weight += w

    if total_weight == 0:
        return {
            a: portfolio_sortino(daily_returns[a])
            if a in daily_returns.columns
            else float("inf")
            for a in assets
        }

    return {a: all_ranks[a] / total_weight for a in assets}


# ---- Event-based seed scoring -------------------------------------------

# Maps seed_metrics keys to event_metrics dict keys
_SEED_TO_EVENT_KEY: dict[str, str] = {
    "pnl": "net_profit",
    "wfe": "sortino",      # no WFE in trade-level data; fall back to sortino
    "is_wfe": "sortino",   # no IS-WFE in trade-level data; fall back to sortino
    "avg_dd": "max_dd",    # average DD not available; use max_dd
}

# Metrics where lower raw value = better (negate before returning so
# compute_seed_scores sees "higher = better" for all metrics)
_EVENT_LOWER_IS_BETTER = {"max_dd", "ulcer"}


def compute_event_seed_scores(
    all_trades,
    n_trades: int = 20,
    objective: str = "sortino",
    staleness_days: int = 180,
    include_side: bool = False,
    reference_time=None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, float]:
    """Score each asset by its last N closed trades (event-based L1 scoring).

    Returns {asset: float} where **higher value = better candidate**.
    This matches the external_scores convention expected by compute_seed_scores
    (which negates the values internally when building seed ranks).

    Assets with fewer than n_trades or stale data are *excluded* from the
    returned dict; they fall back to calendar-based scoring automatically.

    Parameters
    ----------
    all_trades : list[BacktestTrade]
    n_trades   : minimum & lookback window size
    objective  : metric key (sortino|pf|wr|calmar|recovery|max_dd|ulcer|net_profit)
    staleness_days : max days since last trade before asset is excluded
    include_side   : True for cross-strategy (SYMBOL_Long/Short keys)
    reference_time : override for staleness anchor (default: utcnow)
    date_from      : only use trades with exit_time >= this date (ISO string)
    date_to        : only use trades with exit_time <= this date (OOS safety net)
    """
    from collections import defaultdict
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    from backend.analysis.daily_pnl import _asset_key
    from backend.analysis.event_wfo import _compute_event_metrics

    metric_key = _SEED_TO_EVENT_KEY.get(objective, objective)
    reference = reference_time if reference_time is not None else _dt.now(_tz.utc)

    # Sort trades chronologically, skip nulls
    valid = [t for t in all_trades if t.exit_time is not None and t.net_profit is not None]
    sorted_trades = sorted(valid, key=lambda t: str(t.exit_time))

    # Apply date_to filter (OOS safety net — defensive, trades should already be IS-filtered)
    if date_to:
        try:
            upper = _dt.fromisoformat(date_to)
            sorted_trades = [
                t for t in sorted_trades
                if (_dt.fromisoformat(str(t.exit_time)) if isinstance(t.exit_time, str) else t.exit_time) <= upper
            ]
        except ValueError:
            pass

    # Apply date_from filter (hard cutoff for one-shot scoring)
    if date_from:
        try:
            cutoff = _dt.fromisoformat(date_from)
            sorted_trades = [
                t for t in sorted_trades
                if (_dt.fromisoformat(str(t.exit_time)) if isinstance(t.exit_time, str) else t.exit_time) >= cutoff
            ]
        except ValueError:
            pass  # Invalid date, ignore

    asset_pnls: dict[str, list[float]] = defaultdict(list)
    asset_last: dict[str, object] = {}
    for t in sorted_trades:
        key = _asset_key(t, include_side)
        asset_pnls[key].append(float(t.net_profit))
        asset_last[key] = t.exit_time

    scores: dict[str, float] = {}
    for asset, pnls in asset_pnls.items():
        # Staleness: skip asset if its last trade is older than threshold
        if staleness_days > 0:
            last = asset_last[asset]
            if isinstance(last, str):
                try:
                    last = _dt.fromisoformat(last)
                except ValueError:
                    continue
            try:
                delta_days = (reference - last).days  # type: ignore[operator]
            except TypeError:
                delta_days = 0
            if delta_days > staleness_days:
                continue

        # Need at least n_trades to produce reliable event metrics
        if len(pnls) < n_trades:
            continue

        metrics = _compute_event_metrics(pnls[-n_trades:])
        raw = float(metrics.get(metric_key, 0.0))

        # Negate "lower is better" metrics → caller sees "higher = better"
        if metric_key in _EVENT_LOWER_IS_BETTER:
            raw = -raw

        scores[asset] = raw

    return scores


_STRUCTURAL_METRICS = frozenset({
    "gpr", "ter", "hurst", "dsr", "ac",
    "mri", "vr", "half_life", "crossing_rate",
    "trend_score", "meanrev_score",
})


def _score_metric(pnl: pd.Series, metric: str, n_pool: int = 20) -> float:
    """Score a single asset on one metric (lower = better)."""
    # Structural quality metrics — delegate to seed_ranker
    if metric in _STRUCTURAL_METRICS:
        from backend.analysis.seed_ranker import score_metric as sr_score
        return sr_score(metric, pnl.values, n_pool=n_pool)
    if metric == "sortino":
        return portfolio_sortino(pnl)
    if metric == "pf":
        return portfolio_profit_factor(pnl)
    if metric == "pnl":
        return -(pnl.sum())
    if metric == "avg_dd":
        cum = pnl.cumsum()
        return -(cum - cum.cummax()).mean()
    if metric == "max_dd":
        cum = pnl.cumsum()
        return abs((cum - cum.cummax()).min())
    if metric == "ulcer":
        return portfolio_ulcer(pnl)
    if metric == "wfe":
        return 1 - calculate_stability(pnl)
    if metric == "is_wfe":
        return 1 - calculate_stability(pnl)  # fallback when no external scores
    if metric == "wr":
        nonzero = pnl[pnl != 0]
        return -(nonzero > 0).sum() / max(len(nonzero), 1)
    if metric == "recovery":
        return portfolio_recovery(pnl)
    if metric == "stability":
        return 1 - calculate_stability(pnl)
    if metric == "sharpe":
        return portfolio_sharpe(pnl)
    if metric == "calmar":
        return portfolio_calmar(pnl)
    if metric == "expectancy":
        return portfolio_expectancy(pnl)
    if metric == "payoff":
        return portfolio_payoff(pnl)
    if metric == "lake_ratio":
        return portfolio_lake_ratio(pnl)
    if metric == "tail_ratio":
        return portfolio_tail_ratio(pnl)
    if metric == "gain_pain":
        return portfolio_gain_pain(pnl)
    if metric == "cde":
        return portfolio_cde(pnl)
    if metric == "robust_sortino":
        return portfolio_robust_sortino(pnl)
    if metric == "est_efficiency":
        return portfolio_est_efficiency(pnl)
    if metric == "rapid_recovery":
        return portfolio_rapid_recovery(pnl)
    return portfolio_sortino(pnl)
