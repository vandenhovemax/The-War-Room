"""Walk-Forward Efficiency (WFE) — validates out-of-sample performance.

Splits trade history into In-Sample (IS) and Out-of-Sample (OOS) windows
based on candle count and timeframe, then computes WFE ratio.

Ported from market_structure_dashboard/core/data_loader.py.
"""
from __future__ import annotations

import pandas as pd


def calculate_wfe_metrics(
    df_trades: pd.DataFrame,
    unseen_candles: int,
    total_candles: int,
    timeframe_str: str,
) -> dict:
    """Calculate Walk-Forward Efficiency and OOS Profit Factor.

    Parameters
    ----------
    df_trades : standardized trade DataFrame for a single asset or portfolio
    unseen_candles : number of candles for OOS period
    total_candles : total lookback candles (IS + OOS)
    timeframe_str : "30m", "1h", "4h", "1d", etc.

    Returns dict with: WFE, OOS_PF, IS_PF, OOS_PnL, IS_PnL, OOS_Trades, IS_Trades
    """
    if df_trades.empty:
        return {}

    minutes = _parse_timeframe(timeframe_str)
    if minutes == 0:
        return {}

    oos_duration = pd.Timedelta(minutes=minutes * unseen_candles)
    total_duration = pd.Timedelta(minutes=minutes * total_candles)
    is_duration = total_duration - oos_duration

    end_date = df_trades["Exit Time"].max()
    cutoff_date = end_date - oos_duration
    start_date = end_date - total_duration

    # Split into IS and OOS
    is_data = df_trades[
        (df_trades["Exit Time"] >= start_date) & (df_trades["Exit Time"] <= cutoff_date)
    ]
    oos_data = df_trades[df_trades["Exit Time"] > cutoff_date]

    is_pnl = is_data["Net Profit"].sum() if not is_data.empty else 0
    is_pf = _calc_pf(is_data) if not is_data.empty else 0

    oos_pnl = oos_data["Net Profit"].sum() if not oos_data.empty else 0
    oos_pf = _calc_pf(oos_data) if not oos_data.empty else 0

    # WFE = annualized OOS / annualized IS
    wfe = 0.0
    is_secs = is_duration.total_seconds()
    oos_secs = oos_duration.total_seconds()
    if is_secs > 0 and is_pnl > 0 and oos_secs > 0:
        ann_is = is_pnl / is_secs
        ann_oos = oos_pnl / oos_secs
        wfe = ann_oos / ann_is

    return {
        "WFE": wfe,
        "OOS_PF": oos_pf,
        "IS_PF": is_pf,
        "OOS_PnL": oos_pnl,
        "IS_PnL": is_pnl,
        "OOS_Trades": len(oos_data),
        "IS_Trades": len(is_data),
    }


def _parse_timeframe(tf_str: str) -> int:
    """Convert timeframe string to minutes. Returns 0 if unparseable.

    Handles both display formats ("30m","1h","4h","1d") and
    Bybit canonical formats ("1","3","5","15","30","60","240","D","W").
    """
    tf = tf_str.strip()
    tf_lower = tf.lower()

    # Bybit canonical: pure digits = minutes, "D"/"W" = daily/weekly
    if tf in ("D", "d"):
        return 1440
    if tf in ("W", "w"):
        return 1440 * 7
    try:
        if tf.isdigit():
            return int(tf)
        if "m" in tf_lower:
            return int(tf_lower.replace("m", ""))
        if "h" in tf_lower:
            return int(tf_lower.replace("h", "")) * 60
        if "d" in tf_lower:
            return int(tf_lower.replace("d", "")) * 1440
    except ValueError:
        pass
    return 0


def _calc_pf(df: pd.DataFrame) -> float:
    """Calculate profit factor from a trade DataFrame."""
    wins = df[df["Net Profit"] > 0]["Net Profit"].sum()
    losses = abs(df[df["Net Profit"] <= 0]["Net Profit"].sum())
    if losses == 0:
        return 10.0 if wins > 0 else 0.0
    return min(wins / losses, 10.0)
