"""Benchmark Robustness Validation — H1/H2 split analysis.

Splits each asset's trade history into two halves and validates
that performance is stable across both periods.

Ported from market_structure_dashboard/core/data_loader.py.
"""
from __future__ import annotations

import pandas as pd


def compute_benchmark_robustness(
    df: pd.DataFrame,
    source_asset: str = "",
    split_method: str = "time",
    validation_threshold: float = 1.2,
    elite_target: float = 1.0,
) -> tuple[dict, float]:
    """Compute per-asset robustness stats via H1/H2 split.

    Returns (stats_dict, source_pf) where:
      stats_dict[asset] = {H1_PF, H2_PF, Total_PF, is_stable, pct_of_source,
                           status, h1_trades, h2_trades}
    """
    # Source asset PF
    source_pf = 1.0
    if source_asset and source_asset.strip():
        src_mask = df["Asset"].str.contains(source_asset.strip(), case=False, na=False)
        src_df = df[src_mask]
        source_pf = _calc_pf(src_df["Net Profit"]) if not src_df.empty else 1.0

    stats: dict = {}
    for asset in df["Asset"].unique():
        asset_df = df[df["Asset"] == asset].sort_values("Exit Time").copy()
        pnl = asset_df["Net Profit"]
        n = len(asset_df)

        h1_df, h2_df = _split_data(asset_df, split_method, n)

        h1_pf = _calc_pf(h1_df["Net Profit"]) if not h1_df.empty else 0.0
        h2_pf = _calc_pf(h2_df["Net Profit"]) if not h2_df.empty else 0.0
        total_pf = _calc_pf(pnl)

        # Cap inf for display
        h1_disp = min(h1_pf, 10.0)
        h2_disp = min(h2_pf, 10.0)
        total_disp = min(total_pf, 10.0)

        is_stable = (
            h1_pf >= validation_threshold
            and h2_pf >= validation_threshold
            and total_pf >= source_pf * 0.75
        )

        pct_of_source = (total_pf / source_pf * 100) if source_pf > 0 and source_pf < 10.0 else 0.0

        if is_stable and total_pf >= source_pf * elite_target:
            status = "Elite"
        elif is_stable and total_pf >= source_pf * 0.9:
            status = "Strong"
        elif is_stable:
            status = "Stable"
        else:
            status = "Weak"

        # Win rates per half (for WR harmony multiplier)
        h1_wins = (h1_df["Net Profit"] > 0).sum() if not h1_df.empty else 0
        h2_wins = (h2_df["Net Profit"] > 0).sum() if not h2_df.empty else 0
        h1_wr = h1_wins / len(h1_df) if len(h1_df) > 0 else 0.0
        h2_wr = h2_wins / len(h2_df) if len(h2_df) > 0 else 0.0

        stats[asset] = {
            "H1_PF": h1_disp,
            "H2_PF": h2_disp,
            "Total_PF": total_disp,
            "is_stable": is_stable,
            "pct_of_source": pct_of_source,
            "status": status,
            "h1_trades": len(h1_df),
            "h2_trades": len(h2_df),
            "h1_wr": h1_wr,
            "h2_wr": h2_wr,
            "pnl_values": pnl.tolist(),
        }

    return stats, source_pf


def calculate_stability_metrics(
    df_trades: pd.DataFrame,
    source_pf: float = 1.0,
    validation_threshold: float = 1.2,
    validation_cutoff: float = 0.75,
    split_method: str = "time",
    elite_target: float = 1.0,
) -> dict:
    """Single-asset stability metrics for benchmark filtering."""
    if df_trades.empty:
        return {"status": "Weak", "is_valid": False}

    df = df_trades.sort_values("Exit Time")
    n = len(df)
    h1, h2 = _split_data(df, split_method, n)

    h1_pf = _calc_pf(h1["Net Profit"]) if not h1.empty else 0.0
    h2_pf = _calc_pf(h2["Net Profit"]) if not h2.empty else 0.0
    total_pf = _calc_pf(df["Net Profit"])

    min_total = source_pf * validation_cutoff
    is_valid = h1_pf >= validation_threshold and h2_pf >= validation_threshold and total_pf >= min_total

    if is_valid:
        if total_pf >= source_pf * elite_target:
            status = "Elite"
        elif total_pf >= source_pf:
            status = "Strong"
        else:
            status = "Stable"
    elif total_pf < min_total:
        status = "Weak"
    else:
        status = "Fragile"

    # Stability score (similarity of H1/H2)
    if h1_pf > 0 and h2_pf > 0:
        _h1 = min(h1_pf, 100.0)
        _h2 = min(h2_pf, 100.0)
        diff = abs(_h1 - _h2) / ((_h1 + _h2) / 2)
        stability_score = max(0, 100 * (1 - diff))
    else:
        stability_score = 0

    return {
        "h1_pf": h1_pf, "h2_pf": h2_pf, "total_pf": total_pf,
        "is_valid": is_valid, "status": status,
        "stability_score": stability_score,
        "h1_trades": len(h1), "h2_trades": len(h2),
    }


def compute_benchmark_robustness_filter(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Apply benchmark robustness filter to a trade DataFrame.

    Used by load_strategy_data() in tv_data_loader.py.
    Returns filtered DataFrame with only valid (stable) assets.
    """
    source_asset = config.get("benchmark_source", "")
    threshold = float(config.get("benchmark_threshold", 1.2))
    elite_target = float(config.get("benchmark_elite_target", 1.0))
    split_method = config.get("benchmark_split", "time").lower()
    split_method = "trades" if "trade" in split_method else "time"

    # Compute source PF
    source_pf = 1.0
    if source_asset:
        src_df = df[df["Asset"].str.contains(source_asset, case=False, na=False)]
        if not src_df.empty:
            source_pf = _calc_pf(src_df["Net Profit"])

    valid_assets = []
    for asset, asset_df in df.groupby("Asset"):
        m = calculate_stability_metrics(
            asset_df, source_pf=source_pf,
            validation_threshold=threshold, validation_cutoff=0.75,
            split_method=split_method, elite_target=elite_target,
        )
        if m["is_valid"]:
            valid_assets.append(asset)

    return df[df["Asset"].isin(valid_assets)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_data(df: pd.DataFrame, method: str, n: int):
    """Split DataFrame into H1/H2 by time or trade count."""
    if method == "trades":
        mid = max(n // 2, 1)
        return df.iloc[:mid], df.iloc[mid:]

    # Time-based split
    t_min = df["Exit Time"].min()
    t_max = df["Exit Time"].max()
    mid_time = t_min + (t_max - t_min) / 2
    h1 = df[df["Exit Time"] <= mid_time]
    h2 = df[df["Exit Time"] > mid_time]

    # Fallback if one half is empty
    if h1.empty or h2.empty:
        mid = max(n // 2, 1)
        return df.iloc[:mid], df.iloc[mid:]
    return h1, h2


def _calc_pf(pnl: pd.Series) -> float:
    """Calculate profit factor from a PnL series."""
    wins = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl < 0].sum())
    if losses == 0:
        return 10.0 if wins > 0 else 1.0
    return min(wins / losses, 10.0)
