"""Auto Session Mask — compute profitable hours/days from trade data.

Analyzes IS trades to automatically identify profitable time slots,
then builds a mask_data dict compatible with apply_session_mask().
"""
from __future__ import annotations

from collections import defaultdict


def compute_auto_mask(
    trades: list,
    min_trades: int = 10,
    include_insufficient: bool = True,
) -> dict:
    """Analyze trades and generate a session mask based on profitability.

    Parameters
    ----------
    trades : list of BacktestTrade ORM objects (must have entry_time, net_profit)
    min_trades : minimum trades per hour/day to consider statistically valid
    include_insufficient : if True, keep positive hours/days even if < min_trades

    Returns
    -------
    dict with:
        mask_data : dict compatible with apply_session_mask()
        hour_stats : list of per-hour stats (0-23)
        day_stats : list of per-day stats (0-6, Mon=0)
        excluded_hours : list[int]
        excluded_days : list[int]
        kept_hours : list[int]
        kept_days : list[int]
    """
    # Group trades by hour and by day
    by_hour: dict[int, list[float]] = defaultdict(list)
    by_day: dict[int, list[float]] = defaultdict(list)

    for t in trades:
        entry = t.entry_time
        if entry is None:
            continue
        pnl = t.net_profit or 0.0
        by_hour[entry.hour].append(pnl)
        by_day[entry.weekday()].append(pnl)

    # Evaluate each hour (0-23)
    hour_stats = []
    kept_hours: list[int] = []
    excluded_hours: list[int] = []

    for h in range(24):
        pnls = by_hour.get(h, [])
        n = len(pnls)
        total_pnl = sum(pnls)
        is_positive = total_pnl > 0
        has_enough = n >= min_trades

        # Keep if: positive AND (enough trades OR include_insufficient)
        keep = is_positive and (has_enough or include_insufficient)
        # Also keep if no trades at all and include_insufficient (no data ≠ bad)
        if n == 0 and include_insufficient:
            keep = True

        if keep:
            kept_hours.append(h)
        else:
            excluded_hours.append(h)

        hour_stats.append({
            "hour": h,
            "trade_count": n,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / n, 2) if n > 0 else 0,
            "is_positive": is_positive,
            "has_enough_trades": has_enough,
            "kept": keep,
        })

    # Evaluate each day (0=Mon .. 6=Sun)
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_stats = []
    kept_days: list[int] = []
    excluded_days: list[int] = []

    for d in range(7):
        pnls = by_day.get(d, [])
        n = len(pnls)
        total_pnl = sum(pnls)
        is_positive = total_pnl > 0
        has_enough = n >= min_trades

        keep = is_positive and (has_enough or include_insufficient)
        if n == 0 and include_insufficient:
            keep = True

        if keep:
            kept_days.append(d)
        else:
            excluded_days.append(d)

        day_stats.append({
            "day": d,
            "day_name": day_names[d],
            "trade_count": n,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / n, 2) if n > 0 else 0,
            "is_positive": is_positive,
            "has_enough_trades": has_enough,
            "kept": keep,
        })

    # Build mask_data compatible with apply_session_mask()
    # Use "include" mode: only active (hour, day) combos pass
    cells: list[dict] = []
    for h in kept_hours:
        if kept_days:
            cells.append({"hour": h, "days": kept_days})

    mask_data = {
        "mode": "include",
        "cells": cells,
    }

    return {
        "mask_data": mask_data,
        "hour_stats": hour_stats,
        "day_stats": day_stats,
        "excluded_hours": excluded_hours,
        "excluded_days": excluded_days,
        "kept_hours": kept_hours,
        "kept_days": kept_days,
    }
