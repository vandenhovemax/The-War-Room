"""Session Time Mask — trade filtering utility.

Used by optimizer and preview endpoint to filter BacktestTrade lists
by hour×day according to a saved mask.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict


def build_fixed_mask(
    exclude_days: list[int] | None = None,
    exclude_hours: list[int] | None = None,
) -> dict:
    """Build a mask_data dict from inline fixed exclusions.

    Parameters
    ----------
    exclude_days : weekday indices to exclude (0=Mon..6=Sun)
    exclude_hours : hours to exclude (0-23)

    Returns mask_data dict compatible with apply_session_mask().
    Uses "include" mode: builds cells for all non-excluded hour×day combos.
    """
    excl_days = set(exclude_days or [])
    excl_hours = set(exclude_hours or [])

    kept_hours = [h for h in range(24) if h not in excl_hours]
    kept_days = [d for d in range(7) if d not in excl_days]

    cells: list[dict] = []
    for h in kept_hours:
        if kept_days:
            cells.append({"hour": h, "days": kept_days})

    return {"mode": "include", "cells": cells}


def _build_active_set(mask_data: dict) -> set[tuple[int, int]]:
    """Build set of (hour, weekday) tuples that are active in the mask."""
    mode = mask_data.get("mode", "include")
    cells = mask_data.get("cells", [])

    if mode == "include":
        active = set()
        for cell in cells:
            hour = cell["hour"]
            for day in cell.get("days", []):
                active.add((hour, day))
        return active
    else:
        # Exclude mode: start with all 24×7 and remove specified cells
        all_cells = {(h, d) for h in range(24) for d in range(7)}
        for cell in cells:
            hour = cell["hour"]
            for day in cell.get("days", []):
                all_cells.discard((hour, day))
        return all_cells


def apply_session_mask(trades: list, mask_data: dict) -> list:
    """Filter BacktestTrade ORM objects by hour×day mask.

    Parameters
    ----------
    trades : list of BacktestTrade ORM objects
    mask_data : dict with "mode" and "cells" keys

    Returns filtered list (same ORM objects, not copies).
    """
    active = _build_active_set(mask_data)
    if not active:
        return []

    filtered = []
    for t in trades:
        entry = t.entry_time
        if entry is None:
            continue
        key = (entry.hour, entry.weekday())
        if key in active:
            filtered.append(t)
    return filtered


Z_SCORES = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
FAT_TAIL_K = 1.5  # crypto trend-following fat-tail correction
CLT_FLOOR = 30    # absolute minimum — no statistics meaningful below this


def _merge_positions(trades: list) -> tuple[list[float], dict[str, list[float]]]:
    """Merge trade fills into positions by position_id.

    Returns (all_position_pnls, {symbol: [position_pnls]}).
    Trades without position_id get a unique solo key.
    """
    # group: (position_id, symbol) → sum of net_profit
    pos_pnl: dict[tuple[str, str], float] = defaultdict(float)
    pos_sym: dict[str, str] = {}  # pid → symbol (first seen)

    for t in trades:
        pid = t.position_id or f"_solo_{t.id}"
        sym = t.symbol
        pos_pnl[(pid, sym)] += (t.net_profit or 0)
        pos_sym[pid] = sym

    # flatten into per-symbol lists
    by_symbol: dict[str, list[float]] = defaultdict(list)
    all_pnls: list[float] = []
    for (pid, sym), pnl in pos_pnl.items():
        by_symbol[sym].append(pnl)
        all_pnls.append(pnl)

    return all_pnls, dict(by_symbol)


def validate_mask_significance(
    original_trades: list,
    filtered_trades: list,
    n_free_params: int = 5,
    min_per_asset: int = 5,
    confidence: float = 0.95,
) -> dict:
    """Adaptive three-layer statistical significance gate.

    Counts POSITIONS (not fills). Uses adaptive n_min formula:
      n_min = ceil((z × σ/μ)² × k)   floored at CLT_FLOOR.

    Three gates:
      1. Primary — computed n_min from return distribution
      2. Secondary — 15 positions per free optimizer parameter
      3. Tertiary — per-asset minimum (auto-exclude thin assets)

    Returns enriched dict with all gate details for UI preview.
    """
    z = Z_SCORES.get(confidence, 1.96)

    # Merge fills → positions
    all_before, by_sym_before = _merge_positions(original_trades)
    all_after, by_sym_after = _merge_positions(filtered_trades)

    positions_before = len(all_before)
    positions_after = len(all_after)
    pct_retained = round(positions_after / positions_before * 100, 1) if positions_before > 0 else 0

    # --- Primary gate: adaptive n_min ---
    mean_pnl = 0.0
    std_pnl = 0.0
    sigma_over_mu = 0.0
    n_min_computed = CLT_FLOOR
    primary_pass = False
    gate_fail_reason = ""

    if positions_after < 2:
        gate_fail_reason = f"Only {positions_after} positions after mask — need at least {CLT_FLOOR}."
    elif all_after:
        mean_pnl = statistics.mean(all_after)
        std_pnl = statistics.stdev(all_after) if len(all_after) >= 2 else 0.0

        if mean_pnl <= 0:
            gate_fail_reason = (
                "Filtered position pool has negative expectancy "
                f"(mean PnL = ${mean_pnl:.2f}). The mask may be excluding profitable time slots."
            )
            n_min_computed = float("inf")
        else:
            sigma_over_mu = std_pnl / mean_pnl if mean_pnl != 0 else 0
            raw_n = math.ceil((z * sigma_over_mu) ** 2 * FAT_TAIL_K)
            n_min_computed = max(raw_n, CLT_FLOOR)

            if positions_after >= n_min_computed:
                primary_pass = True
            else:
                gate_fail_reason = (
                    f"Need {n_min_computed} positions for statistical significance "
                    f"(σ/μ = {sigma_over_mu:.1f}), but only {positions_after} remain after mask."
                )

    # --- Secondary gate: positions per free parameter ---
    required_for_params = n_free_params * 15
    secondary_pass = positions_after >= required_for_params
    if primary_pass and not secondary_pass:
        gate_fail_reason = (
            f"Need {required_for_params} positions (15 × {n_free_params} params), "
            f"but only {positions_after} remain after mask."
        )

    # --- Tertiary gate: per-asset minimum ---
    original_symbols = set(t.symbol for t in original_trades)
    per_asset_counts: dict[str, int] = {}
    dropped_assets: list[str] = []
    for sym in sorted(original_symbols):
        count = len(by_sym_after.get(sym, []))
        per_asset_counts[sym] = count
        if count < min_per_asset:
            dropped_assets.append(sym)

    surviving_count = len(original_symbols) - len(dropped_assets)
    dropout_pct = round(len(dropped_assets) / len(original_symbols) * 100, 1) if original_symbols else 0
    dropout_warning = dropout_pct > 30

    if primary_pass and secondary_pass and dropout_warning:
        gate_fail_reason = (
            f"Mask drops {dropout_pct:.0f}% of symbols ({len(dropped_assets)}/{len(original_symbols)}) "
            f"below {min_per_asset}-position minimum. Batch may no longer be representative."
        )

    # Overall
    ok = primary_pass and secondary_pass and surviving_count >= 2
    if ok and dropout_warning:
        ok = True  # warning only, not a hard block

    return {
        "ok": ok,
        "positions_before": positions_before,
        "positions_after": positions_after,
        "pct_retained": pct_retained,
        "n_min_computed": n_min_computed if n_min_computed != float("inf") else 999999,
        "sigma_over_mu": round(sigma_over_mu, 2),
        "mean_pnl": round(mean_pnl, 2),
        "primary_gate_pass": primary_pass,
        "secondary_gate_pass": secondary_pass,
        "secondary_gate_required": required_for_params,
        "secondary_gate_actual": positions_after,
        "dropped_assets": sorted(dropped_assets),
        "surviving_asset_count": surviving_count,
        "symbol_dropout_pct": dropout_pct,
        "symbol_dropout_warning": dropout_warning,
        "gate_fail_reason": gate_fail_reason,
        "per_asset_position_counts": per_asset_counts,
    }
