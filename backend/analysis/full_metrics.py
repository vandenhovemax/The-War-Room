"""Full metrics suite — 50+ KPIs matching the Market Structure Dashboard.

Works on the standardized trade DataFrame produced by tv_data_loader.py.
Both TV Excel imports and internal backtester data flow through here.

Returns a dict with keys grouped by category:
  Core (8), Drawdown (8), Run-up (6), Win/Loss (6), Professional (9),
  Event details (4), Asset counts (3), MAE/MFE (6), Equity Curve (1),
  "When" timestamps (8).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.full_metrics_extras import (
    _asset_counts,
    _equity_curve_data,
    _event_details,
    _exit_reason_metrics,
    _mae_mfe_metrics,
)
from backend.analysis.full_metrics_helpers import (
    _professional_metrics,
    _runup_metrics,
    _win_loss_metrics,
)


def calculate_full_metrics(df: pd.DataFrame, *, leverage: int = 1,
                           strategy_name: str = "", config_name: str = "") -> dict:
    """Compute 50+ KPIs from a standardized trade DataFrame.

    Expects columns: Entry Time, Exit Time, Net Profit, Asset, Side.
    Optional: Drawdown, Run-up, Net Profit %, Commission.

    Parameters
    ----------
    leverage : int
        Leverage used for this backtest (default 1). Used to compute
        actual margin = notional / leverage.
    strategy_name, config_name : str
        Included in Max Open detail output for context.
    """
    if df.empty:
        return {}

    df = df.sort_values("Exit Time").copy()

    # Build equity curve
    df["Equity"] = df["Net Profit"].cumsum()

    # --- Core metrics ---
    core = _core_metrics(df)

    # --- Drawdown suite ---
    dd = _drawdown_metrics(df)

    # --- Run-up suite ---
    ru = _runup_metrics(df)

    # --- Win/Loss detail ---
    wl = _win_loss_metrics(df)

    # --- Professional KPIs ---
    pro = _professional_metrics(df, core, dd)

    # --- Event details + "When" timestamps ---
    events = _event_details(df)

    # --- Asset counts ---
    assets = _asset_counts(df)

    # --- MAE/MFE (per-trade drawdown/run-up from TV) ---
    mae_mfe = _mae_mfe_metrics(df)

    # --- Equity curve data ---
    eq_data = _equity_curve_data(df)

    # --- Exit reason breakdown (TSL WR, per-type avg win) ---
    exit_reasons = _exit_reason_metrics(df)

    # Max simultaneous open trades (with details for drill-down)
    max_sim = _max_simultaneous(df)
    ctx = {"strategy": strategy_name, "config": config_name}
    max_sim_details = _max_simultaneous_details(df, context=ctx)
    max_sim_post_tp1 = _max_simultaneous_post_tp1(df, context=ctx)

    # Peak margin usage (notional / leverage for concurrent trades)
    peak_margin = _peak_margin_details(df, leverage=leverage)

    # Merge all dicts
    result = {**core, **dd, **ru, **wl, **pro, **events, **assets, **mae_mfe,
              **exit_reasons, **eq_data,
              "Max Simultaneous Trades": max_sim,
              "Max Open Details": max_sim_details,
              "Max Open Post-TP1": max_sim_post_tp1.get("count", 0),
              "Max Open Post-TP1 Details": max_sim_post_tp1,
              "Peak Margin Used": peak_margin.get("peak_margin", 0),
              "Peak Margin Details": peak_margin,
              }
    return result


# ---------------------------------------------------------------------------
# Core (8 keys)
# ---------------------------------------------------------------------------

def _core_metrics(df: pd.DataFrame) -> dict:
    total_pnl = df["Net Profit"].sum()
    total_trades = len(df)
    from backend.analysis.full_metrics_helpers import _BE_THRESHOLD
    wins = df[df["Net Profit"] > 0]
    losses = df[df["Net Profit"] < _BE_THRESHOLD]
    win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0

    gross_profit = wins["Net Profit"].sum()
    gross_loss = abs(losses["Net Profit"].sum())
    pf = min(gross_profit / gross_loss, 10.0) if gross_loss > 0 else 10.0

    avg_trade = df["Net Profit"].mean()

    # Trade frequency
    if not df.empty and total_trades > 1:
        total_days = (df["Exit Time"].max() - df["Exit Time"].min()).days
        total_months = max(total_days / 30.44, 1)
        avg_per_month = total_trades / total_months
        monthly = df.set_index("Exit Time").resample("ME").size()
        monthly = monthly[monthly > 0]
        min_per_month = int(monthly.min()) if not monthly.empty else 0
        max_per_month = int(monthly.max()) if not monthly.empty else 0
    else:
        avg_per_month = 0
        min_per_month = 0
        max_per_month = 0

    return {
        "Total Net Profit": total_pnl,
        "Total Positions": total_trades,
        "Avg Positions/Month": avg_per_month,
        "Min Positions/Month": min_per_month,
        "Max Positions/Month": max_per_month,
        "Avg Trade Profit": avg_trade,
        "Win Rate": win_rate,
        "Profit Factor": pf,
        "Gross Profit": gross_profit,
        "Gross Loss": -gross_loss,
    }


# ---------------------------------------------------------------------------
# Drawdown suite (8 keys)
# ---------------------------------------------------------------------------

def _drawdown_metrics(df: pd.DataFrame) -> dict:
    # clip(lower=0) ensures the pre-trading baseline (equity=0) is the
    # implicit starting peak, so initial losses are counted as drawdown
    running_max = df["Equity"].cummax().clip(lower=0)
    dd = df["Equity"] - running_max
    max_dd = dd.min()
    avg_dd = dd[dd < 0].mean() if not dd[dd < 0].empty else 0

    # Drawdown periods — temporarily add column for _find_periods, then remove
    df_tmp = df.assign(_dd=dd)
    dd_periods = _find_periods(df_tmp, "_dd", lambda v: v < 0)
    avg_dd_dur = pd.Series([p["Duration"] for p in dd_periods]).mean() if dd_periods else pd.Timedelta(0)
    max_dd_dur = pd.Series([p["Duration"] for p in dd_periods]).max() if dd_periods else pd.Timedelta(0)

    # Find the period containing the worst drawdown point
    max_dd_start = None
    max_dd_end = None
    if dd_periods and max_dd < 0:
        worst_time = df_tmp.loc[dd.idxmin(), "Exit Time"]
        for p in dd_periods:
            if p["Start"] <= worst_time <= p["End"]:
                max_dd_start = p["Start"]
                max_dd_end = p["End"]
                break
        if max_dd_start is None:
            longest = max(dd_periods, key=lambda d: d["Duration"])
            max_dd_start = longest["Start"]
            max_dd_end = longest["End"]

    return {
        "Max Drawdown ($)": max_dd,
        "Avg Drawdown ($)": avg_dd,
        "Max DD Duration": max_dd_dur,
        "Avg DD Duration": avg_dd_dur,
        "Max DD Start": max_dd_start,
        "Max DD End": max_dd_end,
        # Sortino computed in professional metrics
    }


def _find_periods(df: pd.DataFrame, col: str, condition) -> list[dict]:
    """Find contiguous periods where condition(value) is True."""
    periods = []
    in_period = False
    start = None

    for _idx, row in df.iterrows():
        if condition(row[col]):
            if not in_period:
                in_period = True
                start = row["Exit Time"]
        else:
            if in_period:
                periods.append({"Start": start, "End": row["Exit Time"],
                                "Duration": row["Exit Time"] - start})
                in_period = False

    if in_period:
        end = df.iloc[-1]["Exit Time"]
        periods.append({"Start": start, "End": end, "Duration": end - start})

    return periods


def _max_simultaneous(df: pd.DataFrame) -> int:
    """Peak number of concurrent open trades from Entry/Exit timestamps."""
    if df.empty or "Entry Time" not in df.columns or "Exit Time" not in df.columns:
        return 0
    events: list[tuple[pd.Timestamp, int]] = []
    for _, row in df.iterrows():
        events.append((row["Entry Time"], 1))   # +1 on entry
        events.append((row["Exit Time"], -1))    # -1 on exit
    # Sort by time; entries before exits at same timestamp (captures peak overlap)
    events.sort(key=lambda e: (e[0], -e[1]))
    current = 0
    peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def _max_simultaneous_details(df: pd.DataFrame, *,
                              context: dict | None = None) -> dict:
    """Peak concurrent trades with detail about WHEN and WHICH trades were open.

    Returns dict with:
      - count: peak concurrent count
      - peak_time: timestamp when peak was reached
      - strategy / config: context names (if provided)
      - trades: list of dicts with Asset, Side, Entry Time, Exit Time, Exit Reason, had_tp1
    """
    if df.empty or "Entry Time" not in df.columns or "Exit Time" not in df.columns:
        return {"count": 0, "peak_time": None, "trades": []}

    # Build events: (time, +1/-1, row_index)
    events: list[tuple[pd.Timestamp, int, int]] = []
    for idx, row in df.iterrows():
        events.append((row["Entry Time"], 1, idx))
        events.append((row["Exit Time"], -1, idx))
    events.sort(key=lambda e: (e[0], -e[1]))

    current = 0
    peak = 0
    peak_time = None
    open_set: set[int] = set()  # row indices of currently-open trades
    peak_set: set[int] = set()

    for ts, delta, ridx in events:
        if delta == 1:
            open_set.add(ridx)
        else:
            open_set.discard(ridx)
        current += delta
        if current > peak:
            peak = current
            peak_time = ts
            peak_set = set(open_set)

    # Build detail rows for the peak set
    has_exit_reason = "Exit Reason" in df.columns
    trades = []
    for ridx in sorted(peak_set):
        row = df.loc[ridx]
        exit_reason = str(row.get("Exit Reason", "")) if has_exit_reason else ""
        trades.append({
            "asset": str(row.get("Asset", "")),
            "side": str(row.get("Side", "")),
            "entry_time": str(row["Entry Time"]),
            "exit_time": str(row["Exit Time"]),
            "exit_reason": exit_reason,
            "had_tp1": "TP1" in exit_reason.upper() if exit_reason else False,
        })

    ctx = context or {}
    result = {
        "count": peak,
        "peak_time": str(peak_time) if peak_time is not None else None,
        "trades": trades,
    }
    if ctx.get("strategy"):
        result["strategy"] = ctx["strategy"]
    if ctx.get("config"):
        result["config"] = ctx["config"]
    return result


def _max_simultaneous_post_tp1(df: pd.DataFrame, *,
                               context: dict | None = None) -> dict:
    """Peak concurrent trades EXCLUDING positions whose TP1 has already filled.

    Logic: if a trade's exit_reason contains "TP1", it means TP1 was hit —
    the remaining position (TP2/TSL) is now risk-free / funded by TP1 profit.
    We exclude those TP1-exited trades from the concurrency count.

    For trades without Exit Reason data, all trades are counted (conservative).
    Returns same structure as _max_simultaneous_details.
    """
    if df.empty or "Entry Time" not in df.columns or "Exit Time" not in df.columns:
        return {"count": 0, "peak_time": None, "trades": []}

    has_exit_reason = "Exit Reason" in df.columns

    # Filter: keep trades that are NOT TP1 exits (TP1 exits freed capital)
    if has_exit_reason:
        reasons = df["Exit Reason"].fillna("").str.upper()
        keep_mask = ~reasons.str.contains("TP1", na=False)
        filtered = df[keep_mask].copy()
    else:
        filtered = df.copy()

    if filtered.empty:
        return {"count": 0, "peak_time": None, "trades": []}

    return _max_simultaneous_details(filtered, context=context)


def _peak_margin_details(df: pd.DataFrame, *, leverage: int = 1) -> dict:
    """Estimate peak margin (capital) tied up concurrently.

    Uses (Entry Price * Quantity) / leverage as actual margin per trade.
    Tracks margin over time as trades open/close, reports the peak.
    Also computes a 'net' peak that credits back TP1 profit to reduce margin.
    """
    if df.empty or "Entry Time" not in df.columns or "Exit Time" not in df.columns:
        return {"peak_margin": 0, "peak_margin_net": 0, "peak_time": None,
                "leverage": leverage, "trades": []}

    lev = max(leverage, 1)
    has_entry_price = "Entry Price" in df.columns

    # Build events: (time, +/- margin_amount, row_index)
    events: list[tuple[pd.Timestamp, float, int]] = []
    margin_per_trade: dict[int, float] = {}  # idx → margin $

    for idx, row in df.iterrows():
        if has_entry_price and row.get("Entry Price"):
            qty = row.get("Quantity", 1) if "Quantity" in df.columns else 1
            notional = abs(float(row["Entry Price"]) * float(qty))
            margin = notional / lev
        else:
            # Fallback: use Net Profit as rough proxy (not ideal but something)
            margin = abs(float(row.get("Net Profit", 0)))
        margin_per_trade[idx] = margin
        events.append((row["Entry Time"], margin, idx))
        events.append((row["Exit Time"], -margin, idx))

    events.sort(key=lambda e: (e[0], -e[1]))

    current_margin = 0.0
    peak_margin = 0.0
    peak_time = None
    open_set: set[int] = set()
    peak_set: set[int] = set()

    for ts, delta, ridx in events:
        if delta > 0:
            open_set.add(ridx)
        else:
            open_set.discard(ridx)
        current_margin += delta
        if current_margin > peak_margin:
            peak_margin = current_margin
            peak_time = ts
            peak_set = set(open_set)

    # Net peak: exclude TP1 exits (TP1 freed that margin back)
    has_exit_reason = "Exit Reason" in df.columns
    if has_exit_reason:
        reasons = df["Exit Reason"].fillna("").str.upper()
        tp1_mask = reasons.str.contains("TP1", na=False)
        net_df = df[~tp1_mask].copy()
    else:
        net_df = df

    # Re-run sweep for net margin
    net_events: list[tuple[pd.Timestamp, float, int]] = []
    for idx, row in net_df.iterrows():
        m = margin_per_trade.get(idx, 0)
        net_events.append((row["Entry Time"], m, idx))
        net_events.append((row["Exit Time"], -m, idx))
    net_events.sort(key=lambda e: (e[0], -e[1]))

    net_current = 0.0
    net_peak = 0.0
    for ts, delta, _ in net_events:
        net_current += delta
        net_peak = max(net_peak, net_current)

    # Build detail for peak set
    trades = []
    for ridx in sorted(peak_set):
        row = df.loc[ridx]
        trades.append({
            "asset": str(row.get("Asset", "")),
            "margin": round(margin_per_trade.get(ridx, 0), 2),
            "entry_time": str(row["Entry Time"]),
        })

    return {
        "peak_margin": round(peak_margin, 2),
        "peak_margin_net": round(net_peak, 2),
        "peak_time": str(peak_time) if peak_time is not None else None,
        "leverage": lev,
        "trades": trades,
    }
