"""Portfolio Health Monitor — rolling metrics, per-asset decay detection, health grading.

Computes time-evolving health metrics for any portfolio (saved/paper/real) by
analysing trade data in rolling windows.  Works with both BacktestTrade
(saved/refreshed portfolios) and Trade (live/paper portfolios).

Key outputs:
  - Portfolio-level rolling metrics (PF, stability, sortino, drawdown)
  - Per-asset contribution scores that evolve over time
  - Decay detection: flags assets whose contribution has degraded
  - Overall health grade + trend (improving / stable / declining)

Usage:
  from backend.analysis.portfolio_health import compute_portfolio_health
  result = compute_portfolio_health(trades_df, optimization_end_date, windows=[30, 60, 90])
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backend.analysis.scoring import calculate_stability
from backend.analysis.portfolio_grading import (
    compute_portfolio_grade,
    compute_return_to_dd_ratio,
    detect_strategy_type,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AssetHealth:
    """Health metrics for a single asset within the portfolio."""
    asset: str
    # Current window metrics
    trades: int = 0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    avg_trade: float = 0.0
    # Contribution to portfolio
    contribution_pct: float = 0.0       # % of total portfolio profit
    contribution_score: float = 0.0     # normalized score (-1 to +1)
    # Decay metrics (compare recent vs historical)
    pf_trend: float = 0.0              # recent PF - historical PF
    wr_trend: float = 0.0              # recent WR - historical WR
    profit_trend: float = 0.0          # recent avg_trade - historical avg_trade
    # Status
    status: str = "healthy"             # healthy | warning | critical | inactive
    decay_reason: str = ""


@dataclass
class WindowMetrics:
    """Portfolio-level metrics for a specific rolling window."""
    window_days: int
    start_date: str = ""
    end_date: str = ""
    trades: int = 0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    stability: float = 0.0
    sortino: float = 0.0
    avg_trade: float = 0.0
    grade: str = ""
    grade_score: float = 0.0


@dataclass
class HealthResult:
    """Complete health analysis result for a portfolio."""
    portfolio_id: int
    portfolio_type: str = "saved"       # saved | paper | real
    analysis_date: str = ""
    optimization_end_date: str | None = None
    # Overall health
    overall_status: str = "healthy"     # healthy | warning | critical
    overall_grade: str = ""
    overall_grade_score: float = 0.0
    trend: str = "stable"               # improving | stable | declining
    trend_detail: str = ""
    # Rolling window metrics
    windows: list[WindowMetrics] = field(default_factory=list)
    # Per-asset health
    assets: list[AssetHealth] = field(default_factory=list)
    # Summary stats
    total_trades: int = 0
    total_assets: int = 0
    healthy_assets: int = 0
    warning_assets: int = 0
    critical_assets: int = 0
    inactive_assets: int = 0
    # Data info
    data_start: str = ""
    data_end: str = ""
    oos_trades: int = 0
    is_trades: int = 0


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def _compute_metrics_for_window(
    df: pd.DataFrame,
    window_days: int | None = None,
) -> dict:
    """Compute portfolio metrics from a trade DataFrame for a given window.

    Parameters
    ----------
    df : DataFrame
        Must have columns: exit_time, net_profit, asset, side.
    window_days : int, optional
        If provided, only use trades from the last N days.

    Returns
    -------
    dict with keys: trades, net_profit, profit_factor, win_rate, max_drawdown,
    stability, sortino, avg_trade, start_date, end_date
    """
    if df.empty:
        return {
            "trades": 0, "net_profit": 0.0, "profit_factor": 0.0,
            "win_rate": 0.0, "max_drawdown": 0.0, "stability": 0.0,
            "sortino": 0.0, "avg_trade": 0.0, "start_date": "", "end_date": "",
        }

    if window_days is not None:
        cutoff = df["exit_time"].max() - timedelta(days=window_days)
        wdf = df[df["exit_time"] >= cutoff].copy()
    else:
        wdf = df

    if wdf.empty:
        return {
            "trades": 0, "net_profit": 0.0, "profit_factor": 0.0,
            "win_rate": 0.0, "max_drawdown": 0.0, "stability": 0.0,
            "sortino": 0.0, "avg_trade": 0.0, "start_date": "", "end_date": "",
        }

    net = float(wdf["net_profit"].sum())
    wins = wdf[wdf["net_profit"] > 0]
    losses = wdf[wdf["net_profit"] <= 0]
    gp = float(wins["net_profit"].sum()) if not wins.empty else 0.0
    gl = abs(float(losses["net_profit"].sum())) if not losses.empty else 0.0
    pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)
    wr = round(len(wins) / len(wdf) * 100, 1) if len(wdf) > 0 else 0.0
    avg = round(net / len(wdf), 2) if len(wdf) > 0 else 0.0

    # Build daily PnL for time-series metrics
    wdf_sorted = wdf.sort_values("exit_time")
    daily = wdf_sorted.set_index("exit_time")["net_profit"].resample("D").sum()
    daily = daily.fillna(0.0)

    equity = daily.cumsum()
    dd = float((equity - equity.cummax()).min()) if len(equity) > 1 else 0.0
    stab = float(calculate_stability(daily)) if len(daily) >= 3 else 0.0

    # Sortino
    sortino = 0.0
    if len(daily) >= 5:
        mean_ret = daily.mean()
        downside = daily[daily < 0]
        if len(downside) > 0:
            down_std = float(np.sqrt((downside ** 2).mean()))
            if down_std > 0:
                sortino = round(mean_ret / down_std * math.sqrt(365), 2)

    start_d = str(wdf_sorted["exit_time"].min())[:10]
    end_d = str(wdf_sorted["exit_time"].max())[:10]

    return {
        "trades": len(wdf),
        "net_profit": round(net, 2),
        "profit_factor": pf,
        "win_rate": wr,
        "max_drawdown": round(dd, 2),
        "stability": round(stab, 4),
        "sortino": sortino,
        "avg_trade": avg,
        "start_date": start_d,
        "end_date": end_d,
    }


def _compute_asset_health(
    df: pd.DataFrame,
    asset: str,
    recent_days: int = 90,
    historical_days: int | None = None,
) -> AssetHealth:
    """Compute health metrics for a single asset, comparing recent vs historical."""
    adf = df[df["asset"] == asset].sort_values("exit_time")
    if adf.empty:
        return AssetHealth(asset=asset, status="inactive")

    total_profit = float(df["net_profit"].sum()) if not df.empty else 0.0

    # Current window (recent N days)
    cutoff = adf["exit_time"].max() - timedelta(days=recent_days)
    recent = adf[adf["exit_time"] >= cutoff]
    historical = adf[adf["exit_time"] < cutoff] if historical_days is None else adf

    # Recent metrics
    r_trades = len(recent)
    r_net = float(recent["net_profit"].sum()) if not recent.empty else 0.0
    r_wins = recent[recent["net_profit"] > 0] if not recent.empty else pd.DataFrame()
    r_losses = recent[recent["net_profit"] <= 0] if not recent.empty else pd.DataFrame()
    r_gp = float(r_wins["net_profit"].sum()) if not r_wins.empty else 0.0
    r_gl = abs(float(r_losses["net_profit"].sum())) if not r_losses.empty else 0.0
    r_pf = round(min(r_gp / r_gl, 10.0), 4) if r_gl > 0 else (10.0 if r_gp > 0 else 0.0)
    r_wr = round(len(r_wins) / r_trades * 100, 1) if r_trades > 0 else 0.0
    r_avg = round(r_net / r_trades, 2) if r_trades > 0 else 0.0

    # Historical metrics
    h_trades = len(historical)
    if h_trades > 0:
        h_net = float(historical["net_profit"].sum())
        h_wins = historical[historical["net_profit"] > 0]
        h_losses = historical[historical["net_profit"] <= 0]
        h_gp = float(h_wins["net_profit"].sum()) if not h_wins.empty else 0.0
        h_gl = abs(float(h_losses["net_profit"].sum())) if not h_losses.empty else 0.0
        h_pf = round(min(h_gp / h_gl, 10.0), 4) if h_gl > 0 else (10.0 if h_gp > 0 else 0.0)
        h_wr = round(len(h_wins) / h_trades * 100, 1)
        h_avg = round(h_net / h_trades, 2)
    else:
        h_pf = r_pf
        h_wr = r_wr
        h_avg = r_avg

    # Contribution
    all_net = float(adf["net_profit"].sum())
    contribution_pct = round(all_net / total_profit * 100, 1) if total_profit != 0 else 0.0

    # Contribution score: normalize to -1..+1 based on avg trade vs portfolio avg
    port_avg = total_profit / len(df) if len(df) > 0 else 0.0
    if port_avg != 0 and r_trades > 0:
        raw_score = r_avg / abs(port_avg)
        contribution_score = round(max(-1.0, min(1.0, raw_score)), 3)
    else:
        contribution_score = 0.0

    # Trends (recent - historical)
    pf_trend = round(r_pf - h_pf, 3)
    wr_trend = round(r_wr - h_wr, 1)
    profit_trend = round(r_avg - h_avg, 2)

    # Status determination
    # NOTE: With low-frequency strategies (~5-8 trades per asset in 5 months),
    # per-asset metrics are inherently noisy. We require a minimum trade count
    # before flagging decay — below that threshold, treat as "low data" info
    # rather than an actionable alert.
    MIN_TRADES_FOR_STATUS = 10  # Need 10+ trades for PF/WR to be meaningful
    status = "healthy"
    decay_reason = ""

    if r_trades == 0:
        status = "inactive"
        decay_reason = f"No trades in last {recent_days} days"
    elif r_trades < 3:
        status = "warning"
        decay_reason = f"Only {r_trades} trades in last {recent_days}d — insufficient data"
    elif r_trades < MIN_TRADES_FOR_STATUS:
        # Not enough trades to diagnose decay vs variance — show info only
        if r_pf < 1.0:
            status = "warning"
            decay_reason = f"PF {r_pf:.2f} on only {r_trades} trades — may be variance"
        else:
            status = "healthy"
            decay_reason = f"Low sample ({r_trades} trades) — metrics may be noisy"
    elif r_pf < 0.8:
        status = "critical"
        decay_reason = f"PF {r_pf:.2f} below 0.8 in last {recent_days}d ({r_trades} trades)"
    elif r_pf < 1.0:
        status = "critical"
        decay_reason = f"PF {r_pf:.2f} — losing money in last {recent_days}d ({r_trades} trades)"
    elif pf_trend < -0.5 and h_pf >= 1.2 and h_trades >= MIN_TRADES_FOR_STATUS:
        status = "warning"
        decay_reason = f"PF dropped {abs(pf_trend):.2f} from historical {h_pf:.2f}"
    elif wr_trend < -15 and h_wr >= 50 and h_trades >= MIN_TRADES_FOR_STATUS:
        status = "warning"
        decay_reason = f"Win rate dropped {abs(wr_trend):.0f}% from {h_wr:.0f}%"

    return AssetHealth(
        asset=asset,
        trades=r_trades,
        net_profit=round(r_net, 2),
        profit_factor=r_pf,
        win_rate=r_wr,
        avg_trade=r_avg,
        contribution_pct=contribution_pct,
        contribution_score=contribution_score,
        pf_trend=pf_trend,
        wr_trend=wr_trend,
        profit_trend=profit_trend,
        status=status,
        decay_reason=decay_reason,
    )


def _determine_trend(windows: list[WindowMetrics]) -> tuple[str, str]:
    """Determine portfolio trend from rolling window metrics.

    Compares the shortest window (most recent) to the longest (full history)
    to detect improvement or decay.

    Returns (trend, detail) tuple.
    """
    if len(windows) < 2:
        return "stable", "Insufficient windows for trend analysis"

    # Sort by window size: smallest = most recent, largest = full history
    sorted_w = sorted(windows, key=lambda w: w.window_days)
    recent = sorted_w[0]
    full = sorted_w[-1]

    if recent.trades < 5 or full.trades < 10:
        return "stable", "Too few trades for reliable trend"

    pf_delta = recent.profit_factor - full.profit_factor
    stab_delta = recent.stability - full.stability

    signals = []
    if pf_delta > 0.3:
        signals.append(f"PF improving (+{pf_delta:.2f})")
    elif pf_delta < -0.3:
        signals.append(f"PF declining ({pf_delta:.2f})")

    if stab_delta > 0.1:
        signals.append(f"Stability improving (+{stab_delta:.3f})")
    elif stab_delta < -0.1:
        signals.append(f"Stability declining ({stab_delta:.3f})")

    improving = sum(1 for s in signals if "improving" in s)
    declining = sum(1 for s in signals if "declining" in s)

    if declining > improving:
        return "declining", "; ".join(signals) or f"PF {pf_delta:+.2f}, Stability {stab_delta:+.3f}"
    elif improving > declining:
        return "improving", "; ".join(signals) or f"PF {pf_delta:+.2f}, Stability {stab_delta:+.3f}"
    else:
        return "stable", "; ".join(signals) or "Metrics stable across windows"


def compute_portfolio_health(
    trades_df: pd.DataFrame,
    portfolio_id: int = 0,
    portfolio_type: str = "saved",
    optimization_end_date: str | None = None,
    windows: list[int] | None = None,
    recent_days: int = 90,
) -> HealthResult:
    """Compute comprehensive health analysis for a portfolio.

    Parameters
    ----------
    trades_df : DataFrame
        All trades for this portfolio.  Required columns:
          exit_time (datetime), net_profit (float), asset (str), side (str).
        Trades after optimization_end_date are considered OOS.
    portfolio_id : int
        Portfolio ID for reference.
    portfolio_type : str
        "saved", "paper", or "real".
    optimization_end_date : str, optional
        ISO date string.  Trades after this date are OOS.
    windows : list[int], optional
        Rolling window sizes in days.  Default: [30, 60, 90].
    recent_days : int
        Window for per-asset decay comparison.  Default: 90.

    Returns
    -------
    HealthResult with all computed metrics.
    """
    if windows is None:
        windows = [60, 90, 180]

    result = HealthResult(
        portfolio_id=portfolio_id,
        portfolio_type=portfolio_type,
        analysis_date=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        optimization_end_date=optimization_end_date,
    )

    if trades_df.empty or "exit_time" not in trades_df.columns:
        result.overall_status = "critical"
        result.overall_grade = "F"
        result.trend = "stable"
        result.trend_detail = "No trade data available"
        return result

    # Ensure datetime
    df = trades_df.copy()
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df = df.sort_values("exit_time")

    result.data_start = str(df["exit_time"].min())[:10]
    result.data_end = str(df["exit_time"].max())[:10]
    result.total_trades = len(df)

    # Split IS / OOS
    if optimization_end_date:
        oos_cutoff = pd.Timestamp(optimization_end_date)
        oos_df = df[df["exit_time"] > oos_cutoff]
        is_df = df[df["exit_time"] <= oos_cutoff]
        result.oos_trades = len(oos_df)
        result.is_trades = len(is_df)
        # For health monitoring, focus on OOS trades (the real performance)
        analysis_df = oos_df if not oos_df.empty else df
    else:
        analysis_df = df
        result.oos_trades = len(df)
        result.is_trades = 0

    # Rolling window metrics
    window_results = []
    for w in sorted(windows):
        m = _compute_metrics_for_window(analysis_df, window_days=w)
        wm = WindowMetrics(
            window_days=w,
            start_date=m["start_date"],
            end_date=m["end_date"],
            trades=m["trades"],
            net_profit=m["net_profit"],
            profit_factor=m["profit_factor"],
            win_rate=m["win_rate"],
            max_drawdown=m["max_drawdown"],
            stability=m["stability"],
            sortino=m["sortino"],
            avg_trade=m["avg_trade"],
        )
        # Compute grade for this window
        if m["trades"] >= 5:
            ret_dd = compute_return_to_dd_ratio(m["net_profit"], m["max_drawdown"])
            grade, score = compute_portfolio_grade(
                stability_r2=m["stability"],
                wfe=1.0,  # Within-window, WFE not applicable
                return_to_dd=ret_dd,
                sortino=m["sortino"],
                profit_factor=m["profit_factor"],
                strategy_type="mixed",
            )
            wm.grade = grade
            wm.grade_score = round(score, 4)
        window_results.append(wm)

    # Full-period metrics (no window)
    full_m = _compute_metrics_for_window(analysis_df, window_days=None)
    full_wm = WindowMetrics(
        window_days=0,  # 0 = full period
        start_date=full_m["start_date"],
        end_date=full_m["end_date"],
        trades=full_m["trades"],
        net_profit=full_m["net_profit"],
        profit_factor=full_m["profit_factor"],
        win_rate=full_m["win_rate"],
        max_drawdown=full_m["max_drawdown"],
        stability=full_m["stability"],
        sortino=full_m["sortino"],
        avg_trade=full_m["avg_trade"],
    )
    if full_m["trades"] >= 5:
        ret_dd = compute_return_to_dd_ratio(full_m["net_profit"], full_m["max_drawdown"])
        grade, score = compute_portfolio_grade(
            stability_r2=full_m["stability"],
            wfe=1.0,
            return_to_dd=ret_dd,
            sortino=full_m["sortino"],
            profit_factor=full_m["profit_factor"],
            strategy_type="mixed",
        )
        full_wm.grade = grade
        full_wm.grade_score = round(score, 4)
        result.overall_grade = grade
        result.overall_grade_score = round(score, 4)

    result.windows = [full_wm] + window_results

    # Per-asset health
    assets = sorted(analysis_df["asset"].unique())
    result.total_assets = len(assets)
    asset_results = []
    for asset_name in assets:
        ah = _compute_asset_health(
            analysis_df, asset_name,
            recent_days=recent_days,
        )
        asset_results.append(ah)

    # Sort: critical first, then warning, then by contribution descending
    status_order = {"critical": 0, "warning": 1, "inactive": 2, "healthy": 3}
    asset_results.sort(key=lambda a: (status_order.get(a.status, 9), -a.contribution_pct))
    result.assets = asset_results

    # Count statuses
    result.healthy_assets = sum(1 for a in asset_results if a.status == "healthy")
    result.warning_assets = sum(1 for a in asset_results if a.status == "warning")
    result.critical_assets = sum(1 for a in asset_results if a.status == "critical")
    result.inactive_assets = sum(1 for a in asset_results if a.status == "inactive")

    # Overall status
    if result.critical_assets >= result.total_assets * 0.3 or full_m["profit_factor"] < 1.0:
        result.overall_status = "critical"
    elif result.critical_assets > 0 or result.warning_assets >= result.total_assets * 0.3:
        result.overall_status = "warning"
    else:
        result.overall_status = "healthy"

    # Trend
    result.trend, result.trend_detail = _determine_trend(window_results)

    return result


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def health_result_to_dict(r: HealthResult) -> dict:
    """Convert HealthResult to JSON-serializable dict."""
    return {
        "portfolio_id": r.portfolio_id,
        "portfolio_type": r.portfolio_type,
        "analysis_date": r.analysis_date,
        "optimization_end_date": r.optimization_end_date,
        "overall_status": r.overall_status,
        "overall_grade": r.overall_grade,
        "overall_grade_score": r.overall_grade_score,
        "trend": r.trend,
        "trend_detail": r.trend_detail,
        "windows": [
            {
                "window_days": w.window_days,
                "label": "Full Period" if w.window_days == 0 else f"Last {w.window_days}d",
                "start_date": w.start_date,
                "end_date": w.end_date,
                "trades": w.trades,
                "net_profit": w.net_profit,
                "profit_factor": w.profit_factor,
                "win_rate": w.win_rate,
                "max_drawdown": w.max_drawdown,
                "stability": w.stability,
                "sortino": w.sortino,
                "avg_trade": w.avg_trade,
                "grade": w.grade,
                "grade_score": w.grade_score,
            }
            for w in r.windows
        ],
        "assets": [
            {
                "asset": a.asset,
                "trades": a.trades,
                "net_profit": a.net_profit,
                "profit_factor": a.profit_factor,
                "win_rate": a.win_rate,
                "avg_trade": a.avg_trade,
                "contribution_pct": a.contribution_pct,
                "contribution_score": a.contribution_score,
                "pf_trend": a.pf_trend,
                "wr_trend": a.wr_trend,
                "profit_trend": a.profit_trend,
                "status": a.status,
                "decay_reason": a.decay_reason,
            }
            for a in r.assets
        ],
        "summary": {
            "total_trades": r.total_trades,
            "total_assets": r.total_assets,
            "healthy_assets": r.healthy_assets,
            "warning_assets": r.warning_assets,
            "critical_assets": r.critical_assets,
            "inactive_assets": r.inactive_assets,
            "data_start": r.data_start,
            "data_end": r.data_end,
            "oos_trades": r.oos_trades,
            "is_trades": r.is_trades,
        },
    }


# ---------------------------------------------------------------------------
# Multi-portfolio overview (for standalone dashboard)
# ---------------------------------------------------------------------------

def compute_multi_portfolio_health(
    portfolios: list[dict],
) -> list[dict]:
    """Compute health summary for multiple portfolios.

    Parameters
    ----------
    portfolios : list[dict]
        Each dict must have keys: portfolio_id, portfolio_type, name,
        trades_df, optimization_end_date.

    Returns
    -------
    list of summary dicts suitable for the dashboard overview.
    """
    summaries = []
    for p in portfolios:
        result = compute_portfolio_health(
            trades_df=p["trades_df"],
            portfolio_id=p["portfolio_id"],
            portfolio_type=p.get("portfolio_type", "saved"),
            optimization_end_date=p.get("optimization_end_date"),
            windows=[7, 30, 90],
        )
        # Build lookup: window_days -> WindowMetrics
        wmap = {w.window_days: w for w in result.windows}
        w7 = wmap.get(7)
        w30 = wmap.get(30)
        w90 = wmap.get(90)
        wfull = wmap.get(0)

        summaries.append({
            "portfolio_id": p["portfolio_id"],
            "name": p.get("name", f"Portfolio #{p['portfolio_id']}"),
            "portfolio_type": p.get("portfolio_type", "saved"),
            "overall_status": result.overall_status,
            "overall_grade": result.overall_grade,
            "overall_grade_score": result.overall_grade_score,
            "trend": result.trend,
            "trend_detail": result.trend_detail,
            "total_assets": result.total_assets,
            "healthy_assets": result.healthy_assets,
            "warning_assets": result.warning_assets,
            "critical_assets": result.critical_assets,
            "total_trades": result.total_trades,
            "oos_trades": result.oos_trades,
            # Per-period PF and WR
            "pf_7d": round(w7.profit_factor, 2) if w7 else 0.0,
            "wr_7d": round(w7.win_rate, 1) if w7 else 0.0,
            "pf_30d": round(w30.profit_factor, 2) if w30 else 0.0,
            "wr_30d": round(w30.win_rate, 1) if w30 else 0.0,
            "pf_90d": round(w90.profit_factor, 2) if w90 else 0.0,
            "wr_90d": round(w90.win_rate, 1) if w90 else 0.0,
            # Keep backward compat
            "latest_pf": round(w30.profit_factor, 2) if w30 else 0.0,
            "latest_stability": round(w30.stability, 4) if w30 else 0.0,
            "latest_net_profit": round(w30.net_profit, 2) if w30 else 0.0,
            "full_pf": round(wfull.profit_factor, 2) if wfull else 0.0,
            "full_net_profit": round(wfull.net_profit, 2) if wfull else 0.0,
        })
    return summaries
