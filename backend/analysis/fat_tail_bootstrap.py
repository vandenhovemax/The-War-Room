"""Outlier-preserved bootstrap — fat-tail stress test.

Preserves top X% "power trades" in every simulation while
randomizing the rest.  Tests whether strategy collapses
without its big winners (critical for trend-following).
"""
from __future__ import annotations

import math

import numpy as np


def fat_tail_bootstrap(
    trades_pnl: np.ndarray | list[float],
    n_simulations: int = 1000,
    outlier_pct: float = 5.0,
    with_replacement: bool = True,
    initial_capital: float = 10_000.0,
) -> dict | None:
    """Run outlier-preserved bootstrap simulation.

    Args:
        trades_pnl: Array of trade PnL values (dollars).
        n_simulations: Number of bootstrap iterations.
        outlier_pct: Top X% of trades by profit to force-include.
        with_replacement: Whether to sample remaining trades with replacement.
        initial_capital: Starting equity for context.

    Returns dict with stats, cone, original_equity, power_trades info.
    Returns None if no trades.
    """
    pnl = np.asarray(trades_pnl, dtype=float)
    n_trades = len(pnl)
    if n_trades == 0:
        return None

    # Identify power trades (top X% by profit)
    top_n = max(1, math.ceil(n_trades * outlier_pct / 100.0))
    sorted_indices = np.argsort(pnl)[::-1]  # descending by profit
    power_indices = sorted_indices[:top_n]
    power_pnl = pnl[power_indices]
    fill_count = n_trades - top_n

    # Original equity curve
    original_equity = np.cumsum(pnl)
    original_profit = float(original_equity[-1])

    # Pre-allocate
    rng = np.random.default_rng()
    sim_paths = np.zeros((n_simulations, n_trades))
    end_equities = np.zeros(n_simulations)
    max_drawdowns = np.zeros(n_simulations)

    for i in range(n_simulations):
        if fill_count > 0:
            fill = rng.choice(pnl, size=fill_count, replace=with_replacement)
            combined = np.concatenate([power_pnl, fill])
        else:
            combined = power_pnl.copy()

        # Shuffle to randomize sequence order
        rng.shuffle(combined)

        sim_equity = np.cumsum(combined)
        sim_paths[i] = sim_equity
        end_equities[i] = sim_equity[-1]

        # Max drawdown
        peak = np.maximum.accumulate(sim_equity)
        dd = sim_equity - peak
        max_drawdowns[i] = float(np.min(dd))

    # Cone percentiles (same format as existing MC)
    cone: dict[str, list[float]] = {}
    for p in (5, 25, 50, 75, 95):
        cone[str(p)] = np.percentile(sim_paths, p, axis=0).tolist()

    # Survival probability
    survival_prob = float(np.sum(end_equities > 0) / n_simulations * 100)

    # Fragility ratio
    median_profit = float(np.median(end_equities))
    fragility_ratio = (
        median_profit / original_profit if original_profit != 0 else 0.0
    )

    # Power trade stats
    power_total_pnl = float(np.sum(power_pnl))
    total_positive = float(np.sum(pnl[pnl > 0]))
    power_pct_of_profit = (
        (power_total_pnl / total_positive * 100) if total_positive > 0 else 0.0
    )

    # Original max DD
    orig_peak = np.maximum.accumulate(original_equity)
    orig_max_dd = float(np.min(original_equity - orig_peak))

    return {
        "stats": {
            "survival_prob": round(survival_prob, 1),
            "fragility_ratio": round(fragility_ratio, 3),
            "median_return": round(median_profit, 2),
            "worst_case_return": round(float(np.percentile(end_equities, 5)), 2),
            "best_case_return": round(float(np.percentile(end_equities, 95)), 2),
            "original_return": round(original_profit, 2),
            "original_max_dd": round(orig_max_dd, 2),
            "median_max_dd": round(float(np.median(max_drawdowns)), 2),
            "n_trades": n_trades,
            "n_simulations": n_simulations,
            "outlier_pct": outlier_pct,
        },
        "cone": cone,
        "original_equity": original_equity.tolist(),
        "power_trades": {
            "count": top_n,
            "total_pnl": round(power_total_pnl, 2),
            "pct_of_profit": round(power_pct_of_profit, 1),
            "threshold": round(float(power_pnl[-1]), 2) if top_n > 0 else 0.0,
        },
    }
