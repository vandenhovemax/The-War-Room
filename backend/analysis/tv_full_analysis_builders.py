"""Builders and helpers for tv_full_analysis — formats metrics into TV sections."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _period_durations(df: pd.DataFrame, equity: pd.Series) -> tuple[dict, dict]:
    """Compute avg/max duration of drawdown and run-up periods."""
    peak = equity.cummax()
    dd = equity - peak
    trough = equity.cummin()
    ru = equity - trough

    dd_durs = _contiguous_durations(df, dd, lambda v: v < 0)
    ru_durs = _contiguous_durations(df, ru, lambda v: v > 0)

    return (
        {"avg": _fmt_dur(dd_durs, "mean"), "max": _fmt_dur(dd_durs, "max")},
        {"avg": _fmt_dur(ru_durs, "mean"), "max": _fmt_dur(ru_durs, "max")},
    )


def _contiguous_durations(
    df: pd.DataFrame, series: pd.Series, cond,
) -> list[pd.Timedelta]:
    """Find durations of contiguous periods where cond(value) is True."""
    durs: list[pd.Timedelta] = []
    in_period = False
    start = pd.NaT
    times = df["Exit Time"].values

    for i, val in enumerate(series.values):
        if cond(val):
            if not in_period:
                in_period = True
                start = times[i]
        else:
            if in_period:
                durs.append(pd.Timestamp(times[i]) - pd.Timestamp(start))
                in_period = False

    if in_period and len(times) > 0:
        durs.append(pd.Timestamp(times[-1]) - pd.Timestamp(start))
    return durs


def _fmt_dur(durs: list[pd.Timedelta], agg: str) -> str:
    if not durs:
        return "0 days"
    s = pd.Series(durs)
    val = s.mean() if agg == "mean" else s.max()
    days = val.days
    return f"{days} days"


def _cagr(capital: float, final: float, span_days: int) -> float:
    if span_days <= 0 or capital <= 0 or final <= 0:
        return 0.0
    years = span_days / 365.25
    return (((final / capital) ** (1 / years)) - 1) * 100


def _sharpe(ret_pct: np.ndarray) -> float:
    if len(ret_pct) < 2 or np.std(ret_pct) == 0:
        return 0.0
    return float(np.mean(ret_pct) / np.std(ret_pct) * np.sqrt(365))


def _sortino(ret_pct: np.ndarray) -> float:
    if len(ret_pct) < 2:
        return 0.0
    down = ret_pct[ret_pct < 0]
    ds = float(np.sqrt(np.mean(down ** 2))) if len(down) > 0 else 0.0
    return float(np.mean(ret_pct) / ds * np.sqrt(365)) if ds > 0 else 0.0


def _r(v: float, dp: int = 2) -> float | None:
    """Round, return None for inf/nan."""
    if v is None or np.isnan(v) or np.isinf(v):
        return None
    return round(v, dp)


def _g(m: dict, k: str) -> float | None:
    """Get metric value, return None if missing."""
    v = m.get(k)
    return _r(v) if v is not None else None


def _row(label: str, a: dict, l: dict, s: dict, k: str, kp: str = "") -> dict:
    """Build one row with All USDT / All % / Long USDT / Long % / Short USDT / Short %."""
    return {
        "label": label,
        "all_val": _g(a, k), "all_pct": _g(a, kp) if kp else None,
        "long_val": _g(l, k), "long_pct": _g(l, kp) if kp else None,
        "short_val": _g(s, k), "short_pct": _g(s, kp) if kp else None,
    }


def _build_performance(a: dict, l: dict, s: dict, cap: float) -> list[dict]:
    """Build Performance sheet rows matching TV format."""
    return [
        {"label": "Initial capital", "all_val": cap},
        _row("Net profit", a, l, s, "net", "net_pct"),
        _row("Gross profit", a, l, s, "gp", "gp_pct"),
        _row("Gross loss", a, l, s, "gl", "gl_pct"),
        _row("Expected payoff", a, l, s, "avg_pnl"),
        _row("Commission paid", a, l, s, "comm"),
        {"label": "Annualized return (CAGR)", "all_pct": _g(a, "cagr"),
         "long_pct": _g(l, "cagr"), "short_pct": _g(s, "cagr")},
        {"label": "Return on initial capital", "all_pct": _g(a, "roi"),
         "long_pct": _g(l, "roi"), "short_pct": _g(s, "roi")},
        {"label": "Account size required", "all_val": _g(a, "acct_required")},
        {"label": "Return on acct size required", "all_pct": _g(a, "roi_acct"),
         "long_pct": _g(l, "roi_acct"), "short_pct": _g(s, "roi_acct")},
        {"label": "Avg equity run-up duration", "all_val": a.get("avg_ru_dur", "—")},
        _row("Avg equity run-up", a, l, s, "avg_ru", "avg_ru_pct"),
        _row("Max equity run-up", a, l, s, "max_ru", "max_ru_pct"),
        {"label": "Avg equity DD duration", "all_val": a.get("avg_dd_dur", "—")},
        _row("Avg equity drawdown", a, l, s, "avg_dd"),
        {"label": "Return of max equity DD", "all_val": _g(a, "recovery"),
         "long_val": _g(l, "recovery"), "short_val": _g(s, "recovery")},
        _row("Max equity drawdown", a, l, s, "max_dd", "max_dd_pct"),
        {"label": "Net profit as % of largest loss", "all_pct": _g(a, "net_pct_largest_loss"),
         "long_pct": _g(l, "net_pct_largest_loss"), "short_pct": _g(s, "net_pct_largest_loss")},
        {"label": "Largest winner as % of gross profit", "all_pct": _g(a, "max_win_pct_gp"),
         "long_pct": _g(l, "max_win_pct_gp"), "short_pct": _g(s, "max_win_pct_gp")},
        {"label": "Largest loser as % of gross loss", "all_pct": _g(a, "max_loss_pct_gl"),
         "long_pct": _g(l, "max_loss_pct_gl"), "short_pct": _g(s, "max_loss_pct_gl")},
    ]


def _build_trades_analysis(a: dict, l: dict, s: dict) -> list[dict]:
    """Build Trades Analysis sheet rows."""
    return [
        _row("Total trades", a, l, s, "total"),
        _row("Winning trades", a, l, s, "n_wins"),
        _row("Losing trades", a, l, s, "n_losses"),
        _row("Even trades", a, l, s, "n_even"),
        {"label": "Percent profitable", "all_pct": _g(a, "wr"),
         "long_pct": _g(l, "wr"), "short_pct": _g(s, "wr")},
        _row("Avg P&L", a, l, s, "avg_pnl", "avg_pnl_pct"),
        _row("Avg winning trade", a, l, s, "avg_win", "avg_win_pct"),
        _row("Avg losing trade", a, l, s, "avg_loss", "avg_loss_pct"),
        _row("Ratio avg win / avg loss", a, l, s, "ratio_wl"),
        _row("Largest winning trade", a, l, s, "max_win"),
        {"label": "Largest winning trade %", "all_pct": _g(a, "max_win_pct"),
         "long_pct": _g(l, "max_win_pct"), "short_pct": _g(s, "max_win_pct")},
        _row("Largest losing trade", a, l, s, "max_loss"),
        {"label": "Largest losing trade %", "all_pct": _g(a, "max_loss_pct"),
         "long_pct": _g(l, "max_loss_pct"), "short_pct": _g(s, "max_loss_pct")},
        _row("Avg # bars in trades", a, l, s, "avg_bars"),
        _row("Avg # bars in winning trades", a, l, s, "avg_bars_win"),
        _row("Avg # bars in losing trades", a, l, s, "avg_bars_loss"),
    ]


def _build_risk_adjusted(a: dict, l: dict, s: dict) -> list[dict]:
    """Build Risk-adjusted Performance sheet rows."""
    return [
        {"label": "Sharpe ratio", "all_val": _g(a, "sharpe")},
        {"label": "Sortino ratio", "all_val": _g(a, "sortino")},
        _row("Profit factor", a, l, s, "pf"),
    ]
