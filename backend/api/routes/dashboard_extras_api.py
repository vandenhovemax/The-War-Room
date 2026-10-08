"""Dashboard extras — WFE, benchmark, BTC context, significance."""
from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from scipy import stats
from sqlalchemy.orm import Session

from backend.analysis.benchmark import compute_benchmark_robustness
from backend.analysis.btc_context import btc_change_between, fetch_btc_daily
from backend.analysis.wfe import calculate_wfe_metrics
from backend.api.routes.dashboard_api import DashboardRequest, _load_data
from backend.db.database import get_db

router = APIRouter()


def _sanitize(d: dict) -> dict:
    """Convert numpy types to native Python and replace inf/NaN with None."""
    import math
    out = {}
    for k, v in d.items():
        if hasattr(v, "item"):  # numpy scalar → native Python
            v = v.item()
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            v = None
        out[k] = v
    return out


class WFERequest(DashboardRequest):
    wfe_tf: str = "30m"
    unseen_candles: int = 4000
    total_candles: int = 20000


class BenchmarkRequest(DashboardRequest):
    split_method: str = "half"
    validation_threshold: float = 1.0
    elite_target: float = 1.5


@router.post("/wfe")
def get_wfe(req: WFERequest, db: Session = Depends(get_db)):
    """Per-asset WFE scores."""
    df = _load_data(req, db)
    if df.empty:
        return {"assets": []}

    results = []
    for asset in sorted(df["Asset"].unique()):
        adf = df[df["Asset"] == asset]
        m = calculate_wfe_metrics(adf, req.unseen_candles, req.total_candles, req.wfe_tf)
        if m:
            results.append({"asset": asset, **_sanitize(m)})
    return {"assets": results}


@router.post("/benchmark")
def get_benchmark(req: BenchmarkRequest, db: Session = Depends(get_db)):
    """H1/H2 benchmark robustness per asset."""
    df = _load_data(req, db)
    if df.empty:
        return {"assets": {}, "source_pf": 0}

    stats_dict, source_pf = compute_benchmark_robustness(
        df,
        source_asset=None,
        split_method=req.split_method,
        validation_threshold=req.validation_threshold,
        elite_target=req.elite_target,
    )

    serializable = {}
    for asset, s in stats_dict.items():
        serializable[asset] = _sanitize(s)

    return {"assets": serializable, "source_pf": round(float(source_pf), 4)}


@router.get("/btc-change")
def get_btc_change(
    start: str = Query(..., description="Start date ISO string"),
    end: str = Query(..., description="End date ISO string"),
):
    """BTC % change between two dates."""
    change = btc_change_between(start, end)
    return {"start": start, "end": end, "btc_change_pct": change}


@router.get("/btc-daily")
def get_btc_daily(
    start: str = Query(..., description="Start date ISO string"),
    end: str = Query(..., description="End date ISO string"),
):
    """BTC daily close prices for overlay on equity curves."""
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)
    buffer = pd.Timedelta(days=2)
    start_ms = int((start_dt - buffer).timestamp() * 1000)
    end_ms = int((end_dt + buffer).timestamp() * 1000)
    df = fetch_btc_daily(start_ms, end_ms)
    if df.empty:
        return {"data": []}
    data = [
        {"date": row["timestamp"].isoformat(), "close": round(float(row["close"]), 2)}
        for _, row in df.iterrows()
    ]
    return {"data": data}


class HealthRequest(DashboardRequest):
    corr_method: str = "standard"


@router.post("/health")
def get_health(req: HealthRequest, db: Session = Depends(get_db)):
    """Portfolio health — diversification, concentration, key risk KPIs."""
    from backend.analysis.correlation import get_correlation_matrix

    # _load_data already handles exclude_top_n, pf_min, pf_max, min_trades
    df = _load_data(req, db)
    if df.empty:
        return {"correlation_pctiles": {}, "risk_kpis": {}, "n_assets": 0, "excluded_assets": []}

    # Build correlation matrix directly from the already-filtered df.
    # This avoids batch leakage for cross-strategy portfolios (previously
    # loaded trades from ALL batches) and fixes a name-mismatch bug where
    # build_daily_pnl_matrix columns didn't match surviving_assets names.
    corr_pctiles: dict = {}
    if "Asset" in df.columns and "Exit Time" in df.columns and "Net Profit" in df.columns:
        temp = df[["Exit Time", "Asset", "Net Profit"]].copy()
        temp["date"] = pd.to_datetime(temp["Exit Time"]).dt.floor("D")
        daily_pnl = temp.pivot_table(
            index="date", columns="Asset", values="Net Profit", aggfunc="sum",
        ).fillna(0)
        if not daily_pnl.empty:
            full_range = pd.date_range(
                start=daily_pnl.index.min(), end=daily_pnl.index.max(), freq="D",
            )
            daily_pnl = daily_pnl.reindex(full_range).fillna(0)
        if daily_pnl.shape[1] >= 2:
            corr = get_correlation_matrix(daily_pnl, req.corr_method)
            # Extract upper triangle (excluding diagonal)
            mask = np.triu(np.ones(corr.shape, dtype=bool), k=1)
            vals = corr.values[mask]
            if len(vals) > 0:
                non_zero = int(np.sum(vals != 0))
                corr_pctiles = {
                    "p95": round(float(np.percentile(vals, 95)), 3),
                    "p80": round(float(np.percentile(vals, 80)), 3),
                    "median": round(float(np.median(vals)), 3),
                    "p25": round(float(np.percentile(vals, 25)), 3),
                    "mean": round(float(np.mean(vals)), 3),
                    "pair_count": int(len(vals)),
                    "non_zero_pairs": non_zero,
                }

    # Risk KPIs from trade data
    wins = df[df["Net Profit"] > 0]
    losses = df[df["Net Profit"] <= 0]
    gp = float(wins["Net Profit"].sum()) if not wins.empty else 0
    gl = float(abs(losses["Net Profit"].sum())) if not losses.empty else 0
    equity = df.sort_values("Exit Time")["Net Profit"].cumsum()
    dd = equity - equity.cummax()
    max_dd = float(dd.min()) if not dd.empty else 0

    # Top-asset concentration
    asset_pnl = df.groupby("Asset")["Net Profit"].sum().sort_values(ascending=False)
    total_pnl = float(asset_pnl.sum())
    top_pct = round(float(asset_pnl.iloc[0]) / total_pnl * 100, 1) if total_pnl > 0 and len(asset_pnl) > 0 else 0

    # Per-asset Profit Factor distribution (mean + median)
    per_asset_pfs: list[float] = []
    for asset_name in df["Asset"].unique():
        adf = df[df["Asset"] == asset_name]
        a_gp = float(adf[adf["Net Profit"] > 0]["Net Profit"].sum())
        a_gl = float(abs(adf[adf["Net Profit"] <= 0]["Net Profit"].sum()))
        if a_gl > 0:
            per_asset_pfs.append(min(a_gp / a_gl, 10.0))
        elif a_gp > 0:
            per_asset_pfs.append(10.0)
    per_asset_pfs.sort()
    n_pf = len(per_asset_pfs)
    mean_pf = round(sum(per_asset_pfs) / n_pf, 2) if n_pf > 0 else None
    median_pf = round(per_asset_pfs[n_pf // 2], 2) if n_pf > 0 else None

    return {
        "correlation_pctiles": corr_pctiles,
        "risk_kpis": {
            "max_drawdown": round(max_dd, 2),
            "profit_factor": round(min(gp / gl, 10.0), 2) if gl > 0 else None,
            "mean_pf": mean_pf,
            "median_pf": median_pf,
            "win_rate": round(len(wins) / len(df) * 100, 1) if len(df) > 0 else 0,
            "top_asset_concentration": top_pct,
            "total_assets": int(df["Asset"].nunique()),
        },
        "n_assets": int(df["Asset"].nunique()),
        "excluded_assets": [],
    }


@router.post("/significance")
def get_significance(req: DashboardRequest, db: Session = Depends(get_db)):
    """T-statistic + Bonferroni significance for trade PnL."""
    df = _load_data(req, db)
    if df.empty or len(df) < 2:
        return {"t_stat": None, "p_value": None, "significant": False}

    pnl = df["Net Profit"].values
    t_stat, p_value = stats.ttest_1samp(pnl, 0)
    n_assets = df["Asset"].nunique()
    bonferroni_p = min(p_value * n_assets, 1.0) if not np.isnan(p_value) else None

    return {
        "t_stat": round(float(t_stat), 4) if not np.isnan(t_stat) else None,
        "p_value": round(float(p_value), 6) if not np.isnan(p_value) else None,
        "bonferroni_p": round(float(bonferroni_p), 6) if bonferroni_p else None,
        "significant": bool(bonferroni_p is not None and bonferroni_p < 0.05),
        "n_trades": len(df),
        "n_assets": n_assets,
    }


class OutlierRequest(DashboardRequest):
    loss_threshold: float | None = None  # absolute $ threshold
    win_threshold: float | None = None
    loss_multiplier: float = 2.0  # flag trades exceeding avg * multiplier
    win_multiplier: float = 2.0


@router.post("/health/outliers")
def get_outliers(req: OutlierRequest, db: Session = Depends(get_db)):
    """Flag trades with disproportionate wins or losses."""
    df = _load_data(req, db)
    if df.empty:
        return {"avg_loss": 0, "avg_win": 0, "flagged_losses": [], "flagged_wins": []}

    losses_df = df[df["Net Profit"] < 0].copy()
    wins_df = df[df["Net Profit"] > 0].copy()

    avg_loss = float(losses_df["Net Profit"].abs().mean()) if not losses_df.empty else 0
    avg_win = float(wins_df["Net Profit"].mean()) if not wins_df.empty else 0

    def _flag_and_group(sub_df: pd.DataFrame, abs_cut: float | None, mul_cut: float | None) -> list[dict]:
        if sub_df.empty:
            return []
        vals = sub_df["Net Profit"].abs()
        mask = pd.Series(False, index=sub_df.index)
        if abs_cut is not None and abs_cut > 0:
            mask |= (vals >= abs_cut)
        if mul_cut is not None and mul_cut > 0:
            mask |= (vals >= mul_cut)
        flagged = sub_df[mask]
        if flagged.empty:
            return []
        result = []
        for asset in sorted(flagged["Asset"].unique()):
            adf = flagged[flagged["Asset"] == asset].sort_values("Exit Time")
            trades = []
            for _, row in adf.iterrows():
                trades.append({
                    "entry_time": str(row.get("Entry Time", "")),
                    "exit_time": str(row.get("Exit Time", "")),
                    "side": str(row.get("Side", "")),
                    "net_profit": round(float(row["Net Profit"]), 2),
                    "entry_price": round(float(row.get("Entry Price", 0)), 6),
                    "exit_price": round(float(row.get("Exit Price", 0)), 6),
                })
            result.append({
                "asset": asset,
                "trade_count": len(trades),
                "total_pnl": round(float(adf["Net Profit"].sum()), 2),
                "trades": trades,
            })
        return result

    loss_abs = abs(req.loss_threshold) if req.loss_threshold else None
    loss_mul = avg_loss * req.loss_multiplier if avg_loss > 0 else None
    win_abs = abs(req.win_threshold) if req.win_threshold else None
    win_mul = avg_win * req.win_multiplier if avg_win > 0 else None

    return {
        "avg_loss": round(avg_loss, 2),
        "avg_win": round(avg_win, 2),
        "flagged_losses": _flag_and_group(losses_df, loss_abs, loss_mul),
        "flagged_wins": _flag_and_group(wins_df, win_abs, win_mul),
    }


class AggregatedPnLRequest(DashboardRequest):
    period: str = "W"  # "W" | "2W" | "SMS" | "ME"


@router.post("/aggregated-pnl")
def get_aggregated_pnl(req: AggregatedPnLRequest, db: Session = Depends(get_db)):
    """Gross profit vs gross loss aggregated by period."""
    df = _load_data(req, db)
    if df.empty:
        return {"bars": []}

    period_map = {"W": "W-MON", "2W": "2W-MON", "SMS": "SMS", "ME": "ME"}
    rule = period_map.get(req.period, "W-MON")

    df = df.copy()
    df["Exit Time"] = pd.to_datetime(df["Exit Time"])
    df.set_index("Exit Time", inplace=True)
    df.sort_index(inplace=True)

    gp = df["Net Profit"].clip(lower=0).resample(rule).sum()
    gl = df["Net Profit"].clip(upper=0).resample(rule).sum()

    bars = []
    for ts in gp.index:
        bars.append({
            "period_start": ts.isoformat(),
            "period_label": ts.strftime("%b %d"),
            "gross_profit": round(float(gp.loc[ts]), 2),
            "gross_loss": round(float(gl.loc[ts]), 2),
            "net": round(float(gp.loc[ts] + gl.loc[ts]), 2),
        })
    return {"bars": bars}
