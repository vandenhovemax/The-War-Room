"""TV-format full analysis — reproduces TradingView XLSX export sheets.

Returns structured metrics matching Performance, Trades Analysis, and
Risk-adjusted Performance sheets, each with All/Long/Short breakdowns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.tv_full_analysis_builders import (
    _build_performance,
    _build_risk_adjusted,
    _build_trades_analysis,
    _cagr,
    _period_durations,
    _sharpe,
    _sortino,
)


def compute_tv_analysis(
    df: pd.DataFrame, initial_capital: float = 10000.0,
) -> dict:
    """Compute all TV-format metrics from standardized trade DataFrame."""
    if df.empty:
        return {"performance": [], "trades_analysis": [], "risk_adjusted": []}

    df = df.sort_values("Exit Time").copy()

    all_m = _section_metrics(df, initial_capital)
    long_df = df[df["Side"] == "Long"] if "Side" in df.columns else df
    short_df = df[df["Side"] == "Short"] if "Side" in df.columns else pd.DataFrame()
    long_m = _section_metrics(long_df, initial_capital) if not long_df.empty else {}
    short_m = _section_metrics(short_df, initial_capital) if not short_df.empty else {}

    return {
        "performance": _build_performance(all_m, long_m, short_m, initial_capital),
        "trades_analysis": _build_trades_analysis(all_m, long_m, short_m),
        "risk_adjusted": _build_risk_adjusted(all_m, long_m, short_m),
    }


def _section_metrics(df: pd.DataFrame, capital: float) -> dict:
    """Compute raw metrics for a subset (All, Long, or Short)."""
    if df.empty:
        return {}

    pnl = df["Net Profit"]
    equity = pnl.cumsum()
    peak = equity.cummax()
    dd = equity - peak

    wins = df[pnl > 0]
    losses = df[pnl < 0]
    evens = df[pnl == 0]
    gp = float(wins["Net Profit"].sum()) if not wins.empty else 0.0
    gl = float(abs(losses["Net Profit"].sum())) if not losses.empty else 0.0
    net = float(pnl.sum())

    total = len(df)
    n_wins = len(wins)
    n_losses = len(losses)
    n_even = len(evens)
    wr = (n_wins / total * 100) if total > 0 else 0.0
    pf = min(gp / gl, 10.0) if gl > 0 else 10.0 if gp > 0 else 0.0
    avg_pnl = float(pnl.mean()) if total > 0 else 0.0

    avg_win = float(wins["Net Profit"].mean()) if not wins.empty else 0.0
    avg_loss = float(losses["Net Profit"].mean()) if not losses.empty else 0.0
    max_win = float(wins["Net Profit"].max()) if not wins.empty else 0.0
    max_loss = float(losses["Net Profit"].min()) if not losses.empty else 0.0

    ratio_wl = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

    # Pct versions
    pct_col = "Net Profit %" if "Net Profit %" in df.columns else None
    avg_pnl_pct = float(df[pct_col].mean()) if pct_col and total > 0 else 0.0
    avg_win_pct = float(wins[pct_col].mean()) if pct_col and not wins.empty else 0.0
    avg_loss_pct = float(losses[pct_col].mean()) if pct_col and not losses.empty else 0.0
    max_win_pct = float(wins[pct_col].max()) if pct_col and not wins.empty else 0.0
    max_loss_pct = float(losses[pct_col].min()) if pct_col and not losses.empty else 0.0

    # Drawdowns
    max_dd = float(dd.min())
    max_dd_pct = float((dd / (capital + peak)).min() * 100) if peak.max() > 0 else 0.0
    avg_dd = float(dd[dd < 0].mean()) if not dd[dd < 0].empty else 0.0

    # DD / run-up durations
    dd_dur, ru_dur = _period_durations(df, equity)
    avg_dd_dur = dd_dur["avg"]
    max_dd_dur = dd_dur["max"]
    avg_ru_dur = ru_dur["avg"]

    # Run-ups
    trough = equity.cummin()
    runup = equity - trough
    max_ru = float(runup.max())
    avg_ru = float(runup[runup > 0].mean()) if not runup[runup > 0].empty else 0.0

    # Bars in trades
    if "Entry Time" in df.columns and "Exit Time" in df.columns:
        dur = (df["Exit Time"] - df["Entry Time"])
        avg_bars = float(dur.mean().total_seconds() / 1800) if not dur.empty else 0
        w_dur = dur[pnl > 0]
        l_dur = dur[pnl < 0]
        avg_bars_win = float(w_dur.mean().total_seconds() / 1800) if not w_dur.empty else 0
        avg_bars_loss = float(l_dur.mean().total_seconds() / 1800) if not l_dur.empty else 0
    else:
        avg_bars = avg_bars_win = avg_bars_loss = 0

    # Time span for CAGR
    if not df.empty and "Exit Time" in df.columns:
        span_days = (df["Exit Time"].max() - df["Exit Time"].min()).days
    else:
        span_days = 0

    final_eq = capital + net
    cagr = _cagr(capital, final_eq, span_days)

    # Commission
    comm = float(df["Commission"].sum()) if "Commission" in df.columns else 0.0

    # Sharpe / Sortino
    returns = np.diff(np.concatenate([[capital], (capital + equity).values]))
    ret_pct = returns[1:] / (capital + equity.values[:-1]) if len(equity) > 1 else np.array([])
    sharpe = _sharpe(ret_pct)
    sortino = _sortino(ret_pct)

    return {
        "net": net, "net_pct": net / capital * 100, "gp": gp, "gl": gl,
        "gp_pct": gp / capital * 100, "gl_pct": gl / capital * 100,
        "avg_pnl": avg_pnl, "avg_pnl_pct": avg_pnl_pct, "comm": comm,
        "total": total, "n_wins": n_wins, "n_losses": n_losses, "n_even": n_even,
        "wr": wr, "pf": pf,
        "avg_win": avg_win, "avg_loss": avg_loss, "max_win": max_win, "max_loss": max_loss,
        "avg_win_pct": avg_win_pct, "avg_loss_pct": avg_loss_pct,
        "max_win_pct": max_win_pct, "max_loss_pct": max_loss_pct,
        "ratio_wl": ratio_wl,
        "max_dd": max_dd, "max_dd_pct": max_dd_pct, "avg_dd": avg_dd,
        "avg_dd_dur": avg_dd_dur, "max_dd_dur": max_dd_dur,
        "max_ru": max_ru, "avg_ru": avg_ru, "avg_ru_dur": avg_ru_dur,
        "max_ru_pct": max_ru / capital * 100, "avg_ru_pct": avg_ru / capital * 100,
        "cagr": cagr, "roi": net / capital * 100,
        "acct_required": abs(max_dd), "roi_acct": (net / abs(max_dd) * 100) if max_dd < 0 else 0,
        "avg_bars": round(avg_bars), "avg_bars_win": round(avg_bars_win),
        "avg_bars_loss": round(avg_bars_loss),
        "sharpe": sharpe, "sortino": sortino,
        "net_pct_largest_loss": (net / abs(max_loss) * 100) if max_loss != 0 else 0,
        "max_win_pct_gp": (max_win / gp * 100) if gp > 0 else 0,
        "max_loss_pct_gl": (abs(max_loss) / gl * 100) if gl > 0 else 0,
        "recovery": abs(net / max_dd) if max_dd < 0 else 0,
    }
