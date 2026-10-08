"""Extra metric helpers — event details, asset counts, MAE/MFE, equity curve.

Split from full_metrics.py for the 150-line rule.
"""
from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Event details (4 keys + 2 "when")
# ---------------------------------------------------------------------------

def _event_details(df: pd.DataFrame) -> dict:
    wins = df[df["Net Profit"] > 0]
    losses = df[df["Net Profit"] <= 0]

    max_win_detail = "N/A"
    max_loss_detail = "N/A"
    max_win_when = None
    max_loss_when = None

    if not wins.empty:
        idx = wins["Net Profit"].idxmax()
        max_win_detail = f"{df.loc[idx, 'Asset']} ({df.loc[idx, 'Exit Time']})"
        max_win_when = df.loc[idx, "Exit Time"]
    if not losses.empty:
        idx = losses["Net Profit"].idxmin()
        max_loss_detail = f"{df.loc[idx, 'Asset']} ({df.loc[idx, 'Exit Time']})"
        max_loss_when = df.loc[idx, "Exit Time"]

    return {
        "Max Win Detail": max_win_detail,
        "Max Loss Detail": max_loss_detail,
        "Max Win When": max_win_when,
        "Max Loss When": max_loss_when,
    }


# ---------------------------------------------------------------------------
# Asset counts (3 keys)
# ---------------------------------------------------------------------------

def _asset_counts(df: pd.DataFrame) -> dict:
    n_assets = df["Asset"].nunique() if "Asset" in df.columns else 0
    n_long = df[df["Side"] == "Long"]["Asset"].nunique() if "Side" in df.columns else 0
    n_short = df[df["Side"] == "Short"]["Asset"].nunique() if "Side" in df.columns else 0

    return {
        "N Assets": n_assets,
        "N Long Assets": n_long,
        "N Short Assets": n_short,
    }


# ---------------------------------------------------------------------------
# MAE/MFE — per-trade drawdown/run-up from TradingView (6 keys)
# ---------------------------------------------------------------------------

def _mae_mfe_metrics(df: pd.DataFrame) -> dict:
    result: dict = {}

    # MAE (Max Adverse Excursion) — from TV's per-trade Drawdown column
    if "Drawdown" in df.columns:
        dd = pd.to_numeric(df["Drawdown"], errors="coerce").fillna(0)
        if not dd.empty and dd.abs().sum() > 0:
            result["MAE Avg ($)"] = dd.mean()
            result["MAE Max ($)"] = dd.min()

            mae_idx = dd.idxmin() if not dd.isna().all() else None
            if mae_idx is not None:
                result["MAE Detail"] = f"{df.loc[mae_idx, 'Asset']} ({df.loc[mae_idx, 'Exit Time']})"

    # MFE (Max Favorable Excursion) — from TV's per-trade Run-up column
    if "Run-up" in df.columns:
        ru = pd.to_numeric(df["Run-up"], errors="coerce").fillna(0)
        if not ru.empty and ru.abs().sum() > 0:
            result["MFE Avg ($)"] = ru.mean()
            result["MFE Max ($)"] = ru.max()

            mfe_idx = ru.idxmax() if not ru.isna().all() else None
            if mfe_idx is not None:
                result["MFE Detail"] = f"{df.loc[mfe_idx, 'Asset']} ({df.loc[mfe_idx, 'Exit Time']})"

    # Commission drag
    if "Commission" in df.columns:
        total_comm = pd.to_numeric(df["Commission"], errors="coerce").fillna(0).sum()
        total_pnl = df["Net Profit"].sum()
        if total_pnl != 0:
            result["Commission Drag (%)"] = abs(total_comm / total_pnl) * 100

    return result


# ---------------------------------------------------------------------------
# Exit reason breakdown (TSL WR, Avg Win per exit type)
# ---------------------------------------------------------------------------

def _exit_reason_metrics(df: pd.DataFrame) -> dict:
    """Group trades by Exit Reason and compute per-group stats."""
    if "Exit Reason" not in df.columns or df["Exit Reason"].isna().all():
        return {}

    result: dict = {}
    reasons = df["Exit Reason"].fillna("").str.upper()

    # Classify: TP (TP1, TP2, TP), TSL, SL, Signal, other
    tp_mask = reasons.str.contains("TP", na=False) & ~reasons.str.contains("TSL", na=False)
    tsl_mask = reasons.str.contains("TSL", na=False)
    sl_mask = reasons.str.contains(r"^SL$|STOP_LOSS|STOPLOSS", na=False, regex=True)

    tp_trades = df[tp_mask]
    tsl_trades = df[tsl_mask]

    # TSL Win Rate
    if not tsl_trades.empty:
        tsl_wins = tsl_trades[tsl_trades["Net Profit"] > 0]
        result["TSL Win Rate"] = (len(tsl_wins) / len(tsl_trades)) * 100

    # Avg Win per exit type (only winning trades)
    tp_wins = tp_trades[tp_trades["Net Profit"] > 0] if not tp_trades.empty else tp_trades
    tsl_wins_df = tsl_trades[tsl_trades["Net Profit"] > 0] if not tsl_trades.empty else tsl_trades
    if not tp_wins.empty:
        result["Avg Win TP"] = tp_wins["Net Profit"].mean()
    if not tsl_wins_df.empty:
        result["Avg Win TSL"] = tsl_wins_df["Net Profit"].mean()

    # Exit counts
    result["TP Exit Count"] = int(tp_mask.sum())
    result["TSL Exit Count"] = int(tsl_mask.sum())
    result["SL Exit Count"] = int(sl_mask.sum())

    return result


# ---------------------------------------------------------------------------
# Equity curve data (for charts)
# ---------------------------------------------------------------------------

def _equity_curve_data(df: pd.DataFrame) -> dict:
    """Return equity curve DataFrame for charting."""
    if "Equity" not in df.columns:
        return {}

    running_max = df["Equity"].cummax()
    df_out = df[["Exit Time", "Equity"]].copy()
    df_out["Drawdown"] = df["Equity"] - running_max
    df_out["RunUp_from_Bottom"] = df["Equity"] - df["Equity"].cummin()

    return {
        "Equity Curve": df_out,
    }
