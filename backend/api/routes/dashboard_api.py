"""Dashboard API — full metrics, equity curve, monthly matrix, asset breakdown.

Works with both TV Excel imports (via strategy_filename) and
internal backtester data (via batch_id). Both produce the same
standardized trade DataFrame.
"""
from __future__ import annotations

import json
from collections import defaultdict

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.analysis.boundary_clip import clip_df_at_boundary
from backend.analysis.full_metrics import calculate_full_metrics
from backend.analysis.portfolio_grading import compute_portfolio_grade, compute_return_to_dd_ratio, detect_strategy_type
from backend.analysis.portfolio_metrics import compute_portfolio_metrics_from_trades
from backend.analysis.strategy_config import load_strategy
from backend.analysis.tv_data_loader import load_strategy_data, trades_from_db
from backend.analysis.tv_full_analysis import compute_tv_analysis
from backend.db.database import get_db
from backend.db.models import BacktestRun, BacktestTrade, BatchBacktestRun
from backend.utils.logger import get_logger
from backend.utils.ttl_cache import metrics_cache

logger = get_logger(__name__)
router = APIRouter()


class DashboardRequest(BaseModel):
    strategy_filename: str | None = None
    batch_id: int | None = None
    run_id: int | None = None
    portfolio_id: int | None = None
    side_filter: str = "All"
    date_from: str | None = None
    date_to: str | None = None
    min_trades: int = 0
    exclude_top_n: int = 0
    pf_min: float | None = None
    pf_max: float | None = None
    wr_min: float | None = None


def _load_data(req: DashboardRequest, db: Session) -> pd.DataFrame:
    """Load standardized trade data from TV config, batch backtest, portfolio, or single run."""
    if req.run_id:
        trades = db.query(BacktestTrade).filter(BacktestTrade.run_id == req.run_id).all()
        df = trades_from_db(trades)
    elif req.portfolio_id:
        from backend.db.models import Portfolio
        p = db.query(Portfolio).filter(Portfolio.id == req.portfolio_id).first()
        if not p:
            raise HTTPException(404, "Portfolio not found")
        from backend.api.routes.portfolio_asset_routes import _load_trades
        df = _load_trades(p, db)
    elif req.batch_id:
        runs = (
            db.query(BacktestRun)
            .filter(BacktestRun.batch_id == req.batch_id, BacktestRun.status == "completed")
            .all()
        )
        run_ids = [r.id for r in runs]
        trades = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).all()
        df = trades_from_db(trades)
    elif req.strategy_filename:
        config = load_strategy(req.strategy_filename)
        if not config:
            raise HTTPException(404, "Strategy config not found")
        df = load_strategy_data(config)
    else:
        raise HTTPException(400, "Provide strategy_filename, batch_id, run_id, or portfolio_id")

    if df.empty:
        return df

    if req.side_filter != "All" and "Side" in df.columns:
        df = df[df["Side"] == req.side_filter]
    if req.date_from and "Entry Time" in df.columns:
        df = df[df["Entry Time"] >= pd.Timestamp(req.date_from)]
    if req.date_to and "Entry Time" in df.columns:
        df = df[df["Entry Time"] <= pd.Timestamp(req.date_to)]
    if req.min_trades > 0 and "Asset" in df.columns:
        counts = df.groupby("Asset").size()
        df = df[df["Asset"].isin(counts[counts >= req.min_trades].index)]

    # Exclude top N assets by net profit
    if req.exclude_top_n > 0 and "Asset" in df.columns and not df.empty:
        asset_ranking = df.groupby("Asset")["Net Profit"].sum().sort_values(ascending=False)
        excluded = asset_ranking.head(req.exclude_top_n).index.tolist()
        df = df[~df["Asset"].isin(excluded)]

    # PF range filter — exclude assets outside [pf_min, pf_max]
    if (req.pf_min is not None or req.pf_max is not None) and "Asset" in df.columns and not df.empty:
        keep_assets = []
        for asset_name in df["Asset"].unique():
            adf = df[df["Asset"] == asset_name]
            wins = float(adf[adf["Net Profit"] > 0]["Net Profit"].sum())
            losses = float(abs(adf[adf["Net Profit"] <= 0]["Net Profit"].sum()))
            pf = min(wins / losses, 10.0) if losses > 0 else (10.0 if wins > 0 else 0.0)
            if req.pf_min is not None and pf < req.pf_min:
                continue
            if req.pf_max is not None and pf > req.pf_max:
                continue
            keep_assets.append(asset_name)
        df = df[df["Asset"].isin(keep_assets)]

    # Win Rate minimum filter — exclude assets below wr_min %
    if req.wr_min is not None and "Asset" in df.columns and not df.empty:
        wr_keep = []
        for asset_name in df["Asset"].unique():
            adf = df[df["Asset"] == asset_name]
            total = len(adf)
            wins = len(adf[adf["Net Profit"] > 0])
            wr = (wins / total * 100) if total > 0 else 0
            if wr >= req.wr_min:
                wr_keep.append(asset_name)
        df = df[df["Asset"].isin(wr_keep)]

    return df


def _serialize_metrics(metrics: dict) -> dict:
    """Convert non-JSON-serializable types in metrics dict."""
    result = {}
    for k, v in metrics.items():
        if isinstance(v, (pd.DataFrame, pd.Series)):
            continue
        elif isinstance(v, pd.Timestamp):
            result[k] = v.isoformat() if pd.notna(v) else None
        elif isinstance(v, pd.Timedelta):
            result[k] = str(v) if pd.notna(v) else None
        elif isinstance(v, np.datetime64):
            result[k] = pd.Timestamp(v).isoformat() if not pd.isna(v) else None
        elif isinstance(v, np.timedelta64):
            result[k] = str(pd.Timedelta(v)) if not pd.isna(v) else None
        elif isinstance(v, (np.floating, float)):
            result[k] = None if (pd.isna(v) or np.isinf(v)) else round(float(v), 4)
        elif isinstance(v, np.integer):
            result[k] = int(v)
        elif isinstance(v, np.bool_):
            result[k] = bool(v)
        elif v is None:
            result[k] = None
        else:
            result[k] = v
    return result


@router.post("/filter-summary")
def get_filter_summary(req: DashboardRequest, db: Session = Depends(get_db)):
    """Quick summary of filter impact: asset counts, PnL, PF, WR."""
    # Load unfiltered first to get total count
    unfiltered_req = DashboardRequest(
        batch_id=req.batch_id, portfolio_id=req.portfolio_id,
        run_id=req.run_id, strategy_filename=req.strategy_filename,
        side_filter=req.side_filter,
    )
    raw = _load_data(unfiltered_req, db)
    total_assets = int(raw["Asset"].nunique()) if not raw.empty and "Asset" in raw.columns else 0

    df = _load_data(req, db)
    if df.empty:
        return {"total_assets": total_assets, "filtered_assets": 0, "total_pnl": 0, "real_pf": None, "mean_pf": None, "avg_wr": None, "agg_wr": None, "total_trades": 0}

    assets = df["Asset"].unique() if "Asset" in df.columns else []
    filtered_count = len(assets)
    total_pnl = round(float(df["Net Profit"].sum()), 2)
    total_trades = len(df)

    # Real PF (aggregate gross profit / gross loss)
    gp = float(df[df["Net Profit"] > 0]["Net Profit"].sum())
    gl = float(abs(df[df["Net Profit"] <= 0]["Net Profit"].sum()))
    real_pf = round(min(gp / gl, 10.0), 2) if gl > 0 else (10.0 if gp > 0 else None)

    # Mean PF and avg WR across assets
    pfs, wrs = [], []
    for a in assets:
        adf = df[df["Asset"] == a]
        w = float(adf[adf["Net Profit"] > 0]["Net Profit"].sum())
        l = float(abs(adf[adf["Net Profit"] <= 0]["Net Profit"].sum()))
        if l > 0:
            pfs.append(min(w / l, 10.0))
        elif w > 0:
            pfs.append(10.0)
        wrs.append(len(adf[adf["Net Profit"] > 0]) / len(adf) * 100 if len(adf) > 0 else 0)

    mean_pf = round(sum(pfs) / len(pfs), 2) if pfs else None
    avg_wr = round(sum(wrs) / len(wrs), 1) if wrs else None
    agg_wr = round(len(df[df["Net Profit"] > 0]) / len(df) * 100, 1) if len(df) > 0 else None

    return {
        "total_assets": total_assets,
        "filtered_assets": filtered_count,
        "total_pnl": total_pnl,
        "real_pf": real_pf,
        "mean_pf": mean_pf,
        "avg_wr": avg_wr,
        "agg_wr": agg_wr,
        "total_trades": total_trades,
    }


@router.post("/metrics")
def get_metrics(req: DashboardRequest, db: Session = Depends(get_db)):
    """Full 50+ KPI metrics for a strategy or batch backtest."""
    cache_key = ("metrics", req.batch_id, req.run_id, req.strategy_filename,
                 req.side_filter, req.date_from, req.date_to)
    cached = metrics_cache.get(cache_key)
    if cached is not None:
        return cached

    df = _load_data(req, db)
    if df.empty:
        return {"metrics": {}, "trade_count": 0}
    metrics = calculate_full_metrics(df)
    result = {"metrics": _serialize_metrics(metrics), "trade_count": len(df)}
    metrics_cache.set(cache_key, result)
    return result


def _get_initial_capital(req: DashboardRequest, db: Session) -> float:
    """Look up initial_capital from run/batch parameters, default 10000."""
    if req.run_id:
        run = db.query(BacktestRun).filter(BacktestRun.id == req.run_id).first()
        if run and run.parameters:
            return json.loads(run.parameters).get("initial_capital", 10000.0)
    elif req.portfolio_id:
        from backend.db.models import Portfolio
        p = db.query(Portfolio).filter(Portfolio.id == req.portfolio_id).first()
        if p and p.source_strategies:
            ss = json.loads(p.source_strategies)
            bid = ss.get("batch_id") or (ss.get("batch_ids", [None])[0])
            if bid:
                batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
                if batch and batch.parameters:
                    return json.loads(batch.parameters).get("initial_capital", 10000.0)
    elif req.batch_id:
        batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == req.batch_id).first()
        if batch and batch.parameters:
            return json.loads(batch.parameters).get("initial_capital", 10000.0)
    return 10000.0


@router.post("/equity-curve")
def get_equity_curve(req: DashboardRequest, db: Session = Depends(get_db)):
    """Equity curve + drawdown series."""
    df = _load_data(req, db)
    if df.empty:
        return {"data": [], "oos_start_date": None}
    initial_capital = _get_initial_capital(req, db)
    df = df.sort_values("Exit Time").copy()
    df["Equity"] = initial_capital + df["Net Profit"].cumsum()
    peak = df["Equity"].cummax()
    df["DD"] = df["Equity"] - peak
    df["DD_pct"] = (df["DD"] / peak.replace(0, float("nan"))) * 100
    df["DD_pct"] = df["DD_pct"].fillna(0)
    data = [
        {
            "date": row["Exit Time"].isoformat(),
            "equity": round(float(row["Equity"]), 2),
            "drawdown": round(float(row["DD"]), 2),
            "drawdown_pct": round(float(row["DD_pct"]), 2),
            "pnl": round(float(row["Net Profit"]), 2),
            "asset": row.get("Asset", ""),
        }
        for _, row in df.iterrows()
    ]

    # Return OOS start date for portfolio equity curves
    oos_start_date: str | None = None
    if req.portfolio_id:
        from backend.db.models import Portfolio
        p = db.query(Portfolio).filter(Portfolio.id == req.portfolio_id).first()
        if p and p.optimization_end_date:
            oos_start_date = str(p.optimization_end_date)

    return {"data": data, "oos_start_date": oos_start_date}


@router.post("/monthly-matrix")
def get_monthly_matrix(req: DashboardRequest, db: Session = Depends(get_db)):
    """Month x Year PnL heatmap data."""
    df = _load_data(req, db)
    if df.empty:
        return {"matrix": {}, "years": [], "row_totals": {}, "col_totals": {}}
    df["Year"] = df["Exit Time"].dt.year
    df["Month"] = df["Exit Time"].dt.month
    pivot = df.pivot_table(
        index="Year", columns="Month", values="Net Profit",
        aggfunc="sum", fill_value=0,
    )
    matrix = {
        int(y): {int(m): round(float(pivot.loc[y, m]), 2) for m in pivot.columns}
        for y in pivot.index
    }
    row_totals = {int(y): round(float(pivot.loc[y].sum()), 2) for y in pivot.index}
    col_totals = {int(m): round(float(pivot[m].sum()), 2) for m in pivot.columns}
    return {
        "matrix": matrix,
        "years": sorted(matrix.keys()),
        "row_totals": row_totals,
        "col_totals": col_totals,
        "grand_total": round(float(pivot.values.sum()), 2),
    }


@router.post("/asset-breakdown")
def get_asset_breakdown(req: DashboardRequest, db: Session = Depends(get_db)):
    """Per-asset PnL, PF, win rate, trade count."""
    df = _load_data(req, db)
    if df.empty:
        return {"assets": []}
    breakdown = []
    for asset in sorted(df["Asset"].unique()):
        adf = df[df["Asset"] == asset]
        wins = adf[adf["Net Profit"] > 0]
        losses = adf[adf["Net Profit"] <= 0]
        gp = wins["Net Profit"].sum()
        gl = abs(losses["Net Profit"].sum())
        breakdown.append({
            "asset": asset,
            "side": adf["Side"].iloc[0] if not adf.empty else "Unknown",
            "trades": len(adf),
            "net_profit": round(float(adf["Net Profit"].sum()), 2),
            "profit_factor": round(min(float(gp / gl), 10.0), 2) if gl > 0 else None,
            "win_rate": round(len(wins) / len(adf) * 100, 1) if len(adf) > 0 else 0,
            "avg_trade": round(float(adf["Net Profit"].mean()), 2),
            "max_win": round(float(wins["Net Profit"].max()), 2) if not wins.empty else 0,
            "max_loss": round(float(losses["Net Profit"].min()), 2) if not losses.empty else 0,
        })
    return {"assets": breakdown}


@router.post("/full-analysis")
def get_full_analysis(req: DashboardRequest, db: Session = Depends(get_db)):
    """TV-format full analysis — Performance, Trades Analysis, Risk-adjusted."""
    df = _load_data(req, db)
    if df.empty:
        return {"performance": [], "trades_analysis": [], "risk_adjusted": []}
    initial_capital = _get_initial_capital(req, db)
    return compute_tv_analysis(df, initial_capital)


def _base_symbol(asset: str) -> str:
    """Strip _Long/_Short suffix to get the base symbol."""
    if asset.endswith("_Long"):
        return asset[:-5]
    if asset.endswith("_Short"):
        return asset[:-6]
    return asset


def _side_metrics(sdf: pd.DataFrame) -> dict | None:
    """Compute quick metrics for one side of one symbol."""
    if sdf.empty:
        return None
    wins = sdf[sdf["Net Profit"] > 0]
    losses = sdf[sdf["Net Profit"] <= 0]
    gp = float(wins["Net Profit"].sum()) if not wins.empty else 0.0
    gl = float(abs(losses["Net Profit"].sum())) if not losses.empty else 0.0
    # Max drawdown from cumulative equity
    equity = sdf["Net Profit"].cumsum()
    dd = equity - equity.cummax()
    max_dd = float(dd.min()) if not dd.empty else 0.0
    return {
        "trades": len(sdf),
        "net_profit": round(float(sdf["Net Profit"].sum()), 2),
        "profit_factor": round(min(gp / gl, 10.0), 2) if gl > 0 else None,
        "win_rate": round(len(wins) / len(sdf) * 100, 1),
        "avg_trade": round(float(sdf["Net Profit"].mean()), 2),
        "max_dd": round(max_dd, 2),
    }


@router.post("/batch-overview")
def get_batch_overview(req: DashboardRequest, db: Session = Depends(get_db)):
    """Batch-level summary: aggregate KPIs + per-symbol quick stats."""
    df = _load_data(req, db)
    if df.empty:
        return {"aggregate": {}, "trade_count": 0, "symbols": [], "parameters": {}}
    metrics = calculate_full_metrics(df)
    serialized = _serialize_metrics(metrics)
    # Per-symbol summary (quick: PnL, PF, WR, trades)
    symbols = []
    for asset in sorted(df["Asset"].unique()):
        adf = df[df["Asset"] == asset]
        wins = adf[adf["Net Profit"] > 0]
        losses = adf[adf["Net Profit"] <= 0]
        gp = float(wins["Net Profit"].sum()) if not wins.empty else 0.0
        gl = float(abs(losses["Net Profit"].sum())) if not losses.empty else 0.0
        symbols.append({
            "asset": asset,
            "side": adf["Side"].iloc[0] if not adf.empty else "Unknown",
            "trades": len(adf),
            "net_profit": round(float(adf["Net Profit"].sum()), 2),
            "profit_factor": round(min(gp / gl, 10.0), 2) if gl > 0 else None,
            "win_rate": round(len(wins) / len(adf) * 100, 1),
        })
    # Fetch stored batch parameters if available
    params = {}
    if req.batch_id:
        batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == req.batch_id).first()
        if batch and batch.parameters:
            try:
                params = json.loads(batch.parameters)
            except Exception as _exc:
                logger.debug("swallowed: %s", _exc)
    return {
        "aggregate": serialized,
        "trade_count": len(df),
        "symbols": symbols,
        "parameters": params,
    }


@router.post("/batch-ls-breakdown")
def get_batch_ls_breakdown(req: DashboardRequest, db: Session = Depends(get_db)):
    """Per-symbol metrics split by Long and Short sides."""
    df = _load_data(req, db)
    if df.empty:
        return {"symbols": []}
    df = df.sort_values("Exit Time").copy()
    df["BaseSymbol"] = df["Asset"].apply(_base_symbol)

    # Load structural metrics from BacktestRun.metrics per symbol
    structural_map: dict[str, dict] = {}
    _struct_keys = ("regime", "trend_grade", "meanrev_grade", "Hurst",
                    "avg_consec_bull", "avg_consec_bear", "avg_consec_all",
                    "trend_score", "meanrev_score")
    batch_ids: list[int] = []
    if req.batch_id:
        batch_ids = [req.batch_id]
    elif req.portfolio_id:
        from backend.db.models import Portfolio
        p = db.query(Portfolio).filter(Portfolio.id == req.portfolio_id).first()
        if p:
            from backend.api.routes.portfolio_asset_routes import _parse_batch_ids
            batch_ids = _parse_batch_ids(p)
    if batch_ids:
        runs = db.query(BacktestRun).filter(
            BacktestRun.batch_id.in_(batch_ids),
            BacktestRun.status == "completed",
        ).all()
        for r in runs:
            if r.metrics:
                try:
                    m = json.loads(r.metrics)
                    structural_map[r.symbol] = {k: m.get(k) for k in _struct_keys if m.get(k) is not None}
                except Exception as _exc:
                    logger.debug("swallowed: %s", _exc)

    # Load risk_mode per symbol from portfolio assets (if portfolio)
    risk_mode_map: dict[str, str] = {}
    if req.portfolio_id:
        from backend.db.models import PortfolioAsset
        pa_rows = db.query(PortfolioAsset).filter(
            PortfolioAsset.portfolio_id == req.portfolio_id,
            PortfolioAsset.risk_mode.isnot(None),
        ).all()
        for pa in pa_rows:
            risk_mode_map[pa.symbol] = pa.risk_mode

    result = []
    for sym in sorted(df["BaseSymbol"].unique()):
        sym_df = df[df["BaseSymbol"] == sym]
        long_df = sym_df[sym_df["Side"] == "Long"].copy()
        short_df = sym_df[sym_df["Side"] == "Short"].copy()
        row: dict = {
            "symbol": sym,
            "long": _side_metrics(long_df),
            "short": _side_metrics(short_df),
            "total_trades": len(sym_df),
            "total_pnl": round(float(sym_df["Net Profit"].sum()), 2),
        }
        # Merge structural metrics if available
        sm = structural_map.get(sym, {})
        if sm:
            row["structural"] = sm
        # Risk mode from auto-risk (if saved with portfolio)
        rm = risk_mode_map.get(sym)
        if rm:
            row["risk_mode"] = rm
        result.append(row)
    return {"symbols": result}


class BatchSymbolTradesRequest(BaseModel):
    batch_id: int | None = None
    portfolio_id: int | None = None
    symbol: str


@router.post("/batch-symbol-trades")
def get_batch_symbol_trades(req: BatchSymbolTradesRequest, db: Session = Depends(get_db)):
    """Return position-grouped trades for a specific symbol within a batch or portfolio.

    Used by L/S breakdown click-to-expand to show individual positions.
    """
    from collections import OrderedDict

    if req.portfolio_id:
        from backend.api.routes.portfolio_asset_routes import _parse_batch_ids
        from backend.db.models import Portfolio
        p = db.query(Portfolio).filter(Portfolio.id == req.portfolio_id).first()
        if not p:
            return []
        # Scope to asset's specific batch_id to prevent cross-batch leakage
        asset_bids = {
            a.batch_id for a in p.assets
            if a.symbol == req.symbol and a.batch_id is not None
        }
        if asset_bids:
            # Use asset-specific batch_id(s) — usually exactly one
            runs = (
                db.query(BacktestRun)
                .filter(
                    BacktestRun.batch_id.in_(list(asset_bids)),
                    BacktestRun.symbol == req.symbol,
                    BacktestRun.status == "completed",
                )
                .all()
            )
        else:
            # Legacy fallback: no batch_id on assets → use all portfolio batches
            batch_ids = _parse_batch_ids(p)
            if not batch_ids:
                return []
            runs = (
                db.query(BacktestRun)
                .filter(
                    BacktestRun.batch_id.in_(batch_ids),
                    BacktestRun.symbol == req.symbol,
                    BacktestRun.status == "completed",
                )
                .all()
            )
    elif req.batch_id:
        run = (
            db.query(BacktestRun)
            .filter(
                BacktestRun.batch_id == req.batch_id,
                BacktestRun.symbol == req.symbol,
                BacktestRun.status == "completed",
            )
            .first()
        )
        runs = [run] if run else []
    else:
        return []

    if not runs:
        return []

    # Build run_id → (strategy_name, config_name) lookup
    run_meta: dict[int, tuple[str, str]] = {}
    if req.portfolio_id and p:
        asset_by_batch = {a.batch_id: a for a in p.assets if a.symbol == req.symbol}
        for r in runs:
            asset = asset_by_batch.get(r.batch_id)
            if asset:
                run_meta[r.id] = (asset.strategy_name or "", asset.config_name or "")
            else:
                run_meta[r.id] = (r.strategy.name if r.strategy else "", "")
    else:
        for r in runs:
            run_meta[r.id] = (r.strategy.name if r.strategy else "", "")

    run_ids = [r.id for r in runs]
    raw_trades = (
        db.query(BacktestTrade)
        .filter(BacktestTrade.run_id.in_(run_ids))
        .order_by(BacktestTrade.trade_num)
        .all()
    )

    # Apply stored portfolio costs (slippage, commission, funding, spread)
    if req.portfolio_id and p:
        import json as _json
        settings = _json.loads(p.source_strategies) if p.source_strategies else {}
        _slip = float(settings.get("extra_slippage_pct", 0))
        _comm = float(settings.get("extra_commission_pct", 0))
        _fund = float(settings.get("funding_daily_pct", 0))
        _sprd = float(settings.get("spread_pct", 0))
        if _slip > 0 or _comm > 0 or _fund > 0 or _sprd > 0:
            from backend.api.routes.analysis import _apply_stress_slippage
            raw_trades = _apply_stress_slippage(
                raw_trades, _slip, db,
                extra_commission_pct=_comm,
                funding_daily_pct=_fund,
                spread_pct=_sprd,
            )

    # Apply stored risk profile scaling (if portfolio used a custom risk profile)
    if req.portfolio_id and p:
        rp = settings.get("risk_profile", {})
        if rp and rp.get("override_enabled", True):
            from types import SimpleNamespace
            from backend.api.routes.analysis import _apply_risk_scaling
            batch_ids_all = settings.get("batch_ids") or (
                [settings["batch_id"]] if settings.get("batch_id") else []
            )
            run_batch_map = {r.id: r.batch_id for r in runs}
            mock_req = SimpleNamespace(
                risk_profile_mode=rp.get("mode", "fixed"),
                risk_profile_value=float(rp.get("initial_risk_usd", 0)),
                risk_profile_initial_capital=float(rp.get("initial_capital", 10000)),
                risk_profile_multiplier=float(rp.get("multiplier", 2.0)),
                risk_profile_max_streak=int(rp.get("max_streak", 3)),
                risk_profile_kelly_n=int(rp.get("kelly_n", 2)),
                risk_profile_kelly_warmup=int(rp.get("kelly_warmup", 20)),
                risk_profile_martingale_compound=rp.get("martingale_compound", "off"),
                risk_profile_martingale_scope=rp.get("martingale_scope", "asset"),
                risk_profile_martingale_count_mode=rp.get("martingale_count_mode", "full_position"),
                risk_profile_martingale_oos_reset=rp.get("martingale_oos_reset", True),
                risk_profile_martingale_start_after=int(rp.get("martingale_start_after", 0)),
                risk_profile_anti_martingale_reset_after=int(rp.get("anti_martingale_reset_after", 0)),
                risk_profile_sizing_master=rp.get("sizing_master", "portfolio"),
                risk_profile_batch_configs=json.dumps(rp["batch_configs"]) if rp.get("batch_configs") else None,
                batch_ids=batch_ids_all,
                batch_id=batch_ids_all[0] if batch_ids_all else None,
                date_to=settings.get("date_to") or (
                    p.optimization_end_date.strftime("%Y-%m-%d")
                    if hasattr(p, 'optimization_end_date') and p.optimization_end_date
                    and hasattr(p.optimization_end_date, "strftime")
                    else None
                ),
            )
            _is_cross = settings.get("is_cross_strat", False)
            raw_trades = _apply_risk_scaling(
                raw_trades, mock_req, db, include_side=_is_cross,
                run_batch_map=run_batch_map,
            )

    # Group by position_id
    positions: OrderedDict[str, list] = OrderedDict()
    for t in raw_trades:
        key = t.position_id or f"_trade_{t.trade_num}"
        positions.setdefault(key, []).append(t)

    result = []
    pos_num = 0
    for pos_id, fills in positions.items():
        pos_num += 1
        first = fills[0]
        total_pnl = sum(f.net_profit for f in fills)
        total_qty = sum(f.quantity for f in fills)
        exits = [
            {
                "exit_time": f.exit_time.isoformat() if f.exit_time else None,
                "exit_price": f.exit_price,
                "quantity": f.quantity,
                "net_profit": f.net_profit,
                "exit_reason": f.exit_reason,
            }
            for f in fills
        ]
        strat_name, conf_name = run_meta.get(first.run_id, ("", ""))
        result.append({
            "trade_num": pos_num,
            "position_id": pos_id,
            "symbol": first.symbol,
            "side": first.side,
            "entry_time": first.entry_time.isoformat() if first.entry_time else None,
            "entry_price": first.entry_price or 0,
            "sl_price": first.sl_price,
            "tp_price": first.tp_price,
            "total_qty": total_qty,
            "net_profit": total_pnl,
            "net_profit_pct": sum(f.net_profit_pct for f in fills),
            "exit_count": len(fills),
            "exits": exits,
            "strategy_name": strat_name,
            "config_name": conf_name,
            "size_pct": getattr(first, '_size_pct', 100.0),
        })

    return result


class OOSCompareRequest(BaseModel):
    batch_id: int | None = None
    batch_ids: list[int] | None = None  # Cross-strategy: multiple batches
    run_id: int | None = None
    strategy_filename: str | None = None
    oos_date: str  # ISO date string, e.g. "2025-06-01"
    assets: list[str] | None = None  # Optional: filter to specific portfolio assets
    side_filter: str = "All"
    min_trades: int = 0  # Exclude assets with fewer than N trades
    # Boundary clipping toggles (independent)
    clip_is: bool = True   # ON = force-close IS trades at IS end close price
    clip_oos: bool = True  # ON = force-close OOS trades at OOS end close price; OFF = exclude them
    oos_end_date: str | None = None  # ISO date for OOS end boundary (optional)
    # Extra cost stress test (applied before IS/OOS split)
    extra_slippage_pct: float = 0.0
    extra_commission_pct: float = 0.0
    funding_daily_pct: float = 0.0
    spread_pct: float = 0.0
    # Risk profile scaling (position sizing override from optimizer)
    risk_profile_mode: str = ""
    risk_profile_value: float = 0.0
    risk_profile_initial_capital: float = 10000.0
    risk_profile_multiplier: float = 2.0
    risk_profile_max_streak: int = 3
    risk_profile_martingale_compound: str = "off"
    risk_profile_martingale_scope: str = "asset"
    risk_profile_martingale_count_mode: str = "full_position"
    risk_profile_martingale_oos_reset: bool = True
    risk_profile_martingale_start_after: int = 0
    risk_profile_anti_martingale_reset_after: int = 0
    risk_profile_kelly_n: int = 2
    risk_profile_kelly_warmup: int = 20
    # Per-batch position sizing (cross-strategy)
    risk_profile_sizing_master: str = "portfolio"
    risk_profile_batch_configs: str | None = None
    # Auto-risk per-asset configs (JSON string from auto-risk analysis)
    risk_profile_auto_asset_configs: str | None = None


def _calc_pf(df: pd.DataFrame) -> float:
    """Calculate profit factor from a trade DataFrame."""
    if df.empty:
        return 0.0
    wins = float(df[df["Net Profit"] > 0]["Net Profit"].sum())
    losses = float(abs(df[df["Net Profit"] <= 0]["Net Profit"].sum()))
    if losses == 0:
        return 10.0 if wins > 0 else 0.0
    return min(wins / losses, 10.0)


def _safe_pf(val: float) -> float | None:
    """Return None for infinite PF, else rounded float."""
    if val == 10.0 or val == float("-inf") or pd.isna(val):
        return None
    return round(val, 4)


def _compute_concurrent_stats(df: pd.DataFrame) -> dict:
    """Compute max and avg concurrent open trades from a trade DataFrame.

    Uses an event-sweep: +1 at each Entry Time, -1 at each Exit Time,
    then walks sorted events to find the peak and time-weighted average.
    """
    if df.empty or len(df) == 0:
        return {"max_concurrent": 0, "avg_concurrent": 0.0}

    entries = df["Entry Time"].dropna()
    exits = df["Exit Time"].dropna()

    # Build event list: (timestamp, delta) — -1 sorted before +1 at same time
    events: list[tuple] = []
    for t in entries:
        events.append((t, 1))
    for t in exits:
        events.append((t, -1))
    events.sort(key=lambda x: (x[0], x[1]))

    current = 0
    max_conc = 0
    weighted_sum = 0.0
    prev_time = events[0][0]

    for time, delta in events:
        if current > 0:
            dt = (time - prev_time).total_seconds()
            weighted_sum += current * dt
        current += delta
        max_conc = max(max_conc, current)
        prev_time = time

    total_secs = (events[-1][0] - events[0][0]).total_seconds() if len(events) > 1 else 1
    avg_conc = round(weighted_sum / max(total_secs, 1), 2)

    return {"max_concurrent": max_conc, "avg_concurrent": avg_conc}


@router.post("/oos-compare")
def oos_compare(req: OOSCompareRequest, db: Session = Depends(get_db)):
    """Compare In-Sample vs Out-of-Sample metrics, split at oos_date.

    Returns IS metrics, OOS metrics, aggregate WFE, and per-asset WFE breakdown.

    For unsaved candidates only — saved portfolios use portfolio_oos_date().
    Delegates trade loading to _load_portfolio_trades via a temporary Portfolio-
    like object so all trade loading goes through the canonical pipeline (batch-
    scoped loading, cost/risk application, IS clipping).
    """
    import re
    from types import SimpleNamespace
    from backend.api.routes.portfolio_analysis import _load_portfolio_trades

    _btag_re = re.compile(r"~B(-?\d+)")

    # --- Build a temporary Portfolio-like object for the canonical loader ---
    batch_ids = req.batch_ids or ([req.batch_id] if req.batch_id else [])

    # Parse tagged asset names into mock PortfolioAsset objects
    # e.g. "ATOMUSDT~B5_Short" → symbol=ATOMUSDT, side=Short, batch_id=5
    mock_assets = []
    if req.assets:
        for tagged in req.assets:
            m = _btag_re.search(tagged)
            clean = _btag_re.sub("", tagged)
            bid = int(m.group(1)) if m else (batch_ids[0] if batch_ids else None)
            # Split clean name into symbol + side
            if clean.endswith("_Long"):
                sym, side = clean[:-5], "Long"
            elif clean.endswith("_Short"):
                sym, side = clean[:-6], "Short"
            else:
                sym, side = clean, "both"
            mock_assets.append(SimpleNamespace(
                symbol=sym, trade_side=side, batch_id=bid, is_active=True,
            ))
    else:
        # No asset filter — query distinct symbols per batch so the loader
        # includes everything (trade_side="both" expands to _Long + _Short)
        for bid in batch_ids:
            syms = (
                db.query(BacktestTrade.symbol)
                .join(BacktestRun, BacktestTrade.run_id == BacktestRun.id)
                .filter(BacktestRun.batch_id == bid, BacktestRun.status == "completed")
                .distinct()
                .all()
            )
            for (sym,) in syms:
                mock_assets.append(SimpleNamespace(
                    symbol=sym, trade_side="both", batch_id=bid, is_active=True,
                ))

    # Build source_strategies JSON with costs and risk profile
    source_settings: dict = {"batch_ids": batch_ids}
    if req.extra_slippage_pct > 0:
        source_settings["extra_slippage_pct"] = req.extra_slippage_pct
    if req.extra_commission_pct > 0:
        source_settings["extra_commission_pct"] = req.extra_commission_pct
    if req.funding_daily_pct > 0:
        source_settings["funding_daily_pct"] = req.funding_daily_pct
    if req.spread_pct > 0:
        source_settings["spread_pct"] = req.spread_pct
    _has_risk_profile = (
        (req.risk_profile_mode and req.risk_profile_value > 0)
        or req.risk_profile_sizing_master == "per_batch"
        or req.risk_profile_auto_asset_configs
    )
    if _has_risk_profile:
        _rp: dict = {
            "override_enabled": True,
            "mode": req.risk_profile_mode or "fixed",
            "initial_risk_usd": req.risk_profile_value,
            "initial_capital": req.risk_profile_initial_capital,
            "multiplier": req.risk_profile_multiplier,
            "max_streak": req.risk_profile_max_streak,
            "martingale_compound": req.risk_profile_martingale_compound,
            "martingale_scope": req.risk_profile_martingale_scope,
            "martingale_count_mode": req.risk_profile_martingale_count_mode,
            "martingale_oos_reset": req.risk_profile_martingale_oos_reset,
            "martingale_start_after": req.risk_profile_martingale_start_after,
            "anti_martingale_reset_after": req.risk_profile_anti_martingale_reset_after,
            "kelly_n": req.risk_profile_kelly_n,
            "kelly_warmup": req.risk_profile_kelly_warmup,
            "sizing_master": req.risk_profile_sizing_master,
        }
        # Per-batch configs: parse inline and store for _load_portfolio_trades
        if req.risk_profile_sizing_master == "per_batch" and req.risk_profile_batch_configs:
            try:
                _rp["batch_configs"] = json.loads(req.risk_profile_batch_configs) if isinstance(req.risk_profile_batch_configs, str) else req.risk_profile_batch_configs
            except Exception as _exc:
                logger.debug("swallowed: %s", _exc)
        # Auto-risk per-asset configs: pass through for _apply_risk_scaling
        if req.risk_profile_auto_asset_configs:
            _rp["auto_asset_configs"] = req.risk_profile_auto_asset_configs
        source_settings["risk_profile"] = _rp
    # Determine timeframe from source batch(es) — used by clipping
    _clip_tf = "D"
    for _bid in batch_ids:
        _batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == _bid).first()
        if _batch and _batch.timeframe:
            _clip_tf = _batch.timeframe
            source_settings["timeframe"] = _clip_tf
            break

    _is_cross = len(batch_ids) > 1
    if _is_cross:
        source_settings["is_cross_strat"] = True
    # Store date_to so the risk scaler gets the correct oos_date
    source_settings["date_to"] = req.oos_date

    mock_portfolio = SimpleNamespace(
        id=-1,
        name="<unsaved candidate>",
        source_strategies=json.dumps(source_settings),
        optimization_end_date=req.oos_date,
        is_start_date=None,
        created_at=None,
        is_cross_strat=_is_cross,
        assets=mock_assets,
    )

    # --- Load trades through the canonical pipeline ---
    df = _load_portfolio_trades(mock_portfolio, db, clip_is=req.clip_is)

    if df.empty:
        return {
            "is_metrics": {}, "oos_metrics": {},
            "is_trades": 0, "oos_trades": 0,
            "aggregate_wfe": 0, "aggregate_is_pf": None, "aggregate_oos_pf": None,
            "per_asset": [], "cutoff": req.oos_date,
        }

    cutoff = pd.Timestamp(req.oos_date)
    df["Entry Time"] = pd.to_datetime(df["Entry Time"])
    df["Exit Time"] = pd.to_datetime(df["Exit Time"])

    is_df = df[df["Entry Time"].dt.date <= cutoff.date()].copy()
    oos_df = df[df["Entry Time"].dt.date > cutoff.date()].copy()

    # OOS boundary clipping at oos_end_date
    _oos_end = req.oos_end_date
    _oos_clip_details: list[dict] = []
    if _oos_end and not oos_df.empty:
        oos_end_ts = pd.Timestamp(_oos_end)
        # First: hard-remove trades that entered AFTER the OOS end date
        oos_df = oos_df[oos_df["Entry Time"].dt.date <= oos_end_ts.date()].copy()
        # Then: handle trades that straddle the boundary
        if not oos_df.empty:
            if req.clip_oos:
                oos_df, _oos_clip_details = clip_df_at_boundary(oos_df, _oos_end, _clip_tf, db, return_details=True)
            else:
                oos_df = oos_df[oos_df["Exit Time"].dt.date <= oos_end_ts.date()]

    # Drop rows with missing Exit Time before computing metrics —
    # prevents divergence between full_metrics (sums all rows) and
    # quick_metrics (skips NaT exits via daily PnL matrix).
    is_df = is_df.dropna(subset=["Exit Time"]) if not is_df.empty else is_df
    oos_df = oos_df.dropna(subset=["Exit Time"]) if not oos_df.empty else oos_df

    is_metrics = _serialize_metrics(calculate_full_metrics(is_df)) if not is_df.empty else {}
    oos_metrics = _serialize_metrics(calculate_full_metrics(oos_df)) if not oos_df.empty else {}

    # Concurrent trade stats for the OOS period
    oos_concurrent = _compute_concurrent_stats(oos_df)

    # Quick metrics — daily-PnL DD, position-merged, consistent with portfolio tabs
    oos_quick = compute_portfolio_metrics_from_trades(oos_df) if not oos_df.empty else {}
    is_quick = compute_portfolio_metrics_from_trades(is_df) if not is_df.empty else {}

    # Inject stability R² into oos_metrics (from quick metrics)
    if oos_quick.get("stability") is not None and oos_metrics:
        oos_metrics["Stability (R²)"] = oos_quick["stability"]

    # Portfolio-wide time spans (for time-normalized WFE, consistent with
    # portfolio_analysis.py — all assets use the same time basis to prevent
    # insane per-asset values when an asset has only 1-2 trades)
    ptf_is_span = (is_df["Entry Time"].max() - is_df["Entry Time"].min()).total_seconds() if not is_df.empty and len(is_df) > 1 else 1
    ptf_oos_span = (oos_df["Entry Time"].max() - oos_df["Entry Time"].min()).total_seconds() if not oos_df.empty and len(oos_df) > 1 else 1

    # Per-asset WFE breakdown
    per_asset = []
    # Use the already-filtered is_df / oos_df (which respect oos_end_date)
    _all_assets = sorted(set(is_df["Asset"].unique()) | set(oos_df["Asset"].unique())) if not oos_df.empty else sorted(is_df["Asset"].unique())
    for asset in _all_assets:
        is_adf = is_df[is_df["Asset"] == asset]
        oos_adf = oos_df[oos_df["Asset"] == asset]
        is_pf = _calc_pf(is_adf)
        oos_pf = _calc_pf(oos_adf)
        is_pnl = float(is_adf["Net Profit"].sum()) if not is_adf.empty else 0.0
        oos_pnl = float(oos_adf["Net Profit"].sum()) if not oos_adf.empty else 0.0
        # Time-normalized WFE (kept for backward compat)
        a_wfe = 0.0
        if ptf_is_span > 0 and is_pnl != 0 and ptf_oos_span > 0:
            a_wfe = (oos_pnl / ptf_oos_span) / (is_pnl / ptf_is_span)
            a_wfe = max(-10.0, min(10.0, a_wfe))
        # PF-based WFE
        is_edge = is_pf - 1.0
        a_wfe_pf = ((oos_pf - 1.0) / is_edge) if is_edge > 0 else 0.0
        # OOS gross profit / gross loss for per-batch PF aggregation
        oos_wins = oos_adf[oos_adf["Net Profit"] > 0] if not oos_adf.empty else oos_adf
        oos_gp = float(oos_wins["Net Profit"].sum()) if not oos_wins.empty else 0.0
        oos_gl = float(abs(oos_adf[oos_adf["Net Profit"] <= 0]["Net Profit"].sum())) if not oos_adf.empty else 0.0
        per_asset.append({
            "asset": asset,
            "WFE": round(a_wfe, 4),
            "WFE_PF": round(a_wfe_pf, 4),
            "IS_PF": _safe_pf(is_pf),
            "OOS_PF": _safe_pf(oos_pf),
            "IS_PnL": round(is_pnl, 2),
            "OOS_PnL": round(oos_pnl, 2),
            "OOS_GP": round(oos_gp, 2),
            "OOS_GL": round(oos_gl, 2),
            "IS_Trades": len(is_adf),
            "OOS_Trades": len(oos_adf),
        })

    # Aggregate WFE — time-normalized (kept for backward compat)
    agg_is_pf = _calc_pf(is_df)
    agg_oos_pf = _calc_pf(oos_df)
    is_pnl_total = float(is_df["Net Profit"].sum()) if not is_df.empty else 0
    oos_pnl_total = float(oos_df["Net Profit"].sum()) if not oos_df.empty else 0
    agg_wfe = 0.0
    if ptf_is_span > 0 and is_pnl_total != 0 and ptf_oos_span > 0:
        agg_wfe = (oos_pnl_total / ptf_oos_span) / (is_pnl_total / ptf_is_span)

    # PF-based WFE: (OOS_PF - 1) / (IS_PF - 1) — measures edge retention
    agg_is_edge = agg_is_pf - 1.0
    agg_wfe_pf = ((agg_oos_pf - 1.0) / agg_is_edge) if agg_is_edge > 0 else 0.0

    # Include trade date range for diagnostics
    trade_min = str(df["Exit Time"].min())[:10] if not df.empty else None
    trade_max = str(df["Exit Time"].max())[:10] if not df.empty else None

    # Robustness metrics + portfolio grade
    _oos_net = float(oos_quick.get("net_profit", 0) or 0)
    _oos_dd = float(oos_quick.get("max_drawdown", 0) or 0)
    _oos_stab = float(oos_quick.get("stability", 0) or 0)
    _oos_sort = float(oos_quick.get("sortino", 0) or 0)
    _oos_pf_v = float(oos_quick.get("profit_factor", 0) or 0)
    _oos_wr = float(oos_quick.get("win_rate", 0) or 0)
    _ret_dd = compute_return_to_dd_ratio(_oos_net, _oos_dd)

    # Auto-detect strategy type from OOS trade profile
    _oos_avg_win = float(oos_metrics.get("Avg Win", 0) or 0)
    _oos_avg_loss = float(oos_metrics.get("Avg Loss", 0) or 0)
    _strat_type = detect_strategy_type(
        win_rate=_oos_wr,
        profit_factor=_oos_pf_v,
        avg_win=_oos_avg_win,
        avg_loss=_oos_avg_loss,
    )

    _grade, _grade_score = compute_portfolio_grade(
        stability_r2=_oos_stab,
        wfe=agg_wfe_pf,
        return_to_dd=_ret_dd,
        sortino=_oos_sort,
        profit_factor=_oos_pf_v,
        strategy_type=_strat_type,
    )

    # Per-batch OOS breakdown for cross-strategy mode
    per_batch = []
    if _is_cross and per_asset:
        # Map asset name → batch_id using mock_assets
        _ab_map: dict[str, int] = {}
        for _ma in mock_assets:
            _aname = f"{_ma.symbol}_{_ma.trade_side}" if _ma.trade_side in ("Long", "Short") else _ma.symbol
            if _aname not in _ab_map:
                _ab_map[_aname] = _ma.batch_id
        # Group per_asset entries by batch_id
        _bg: dict[int, list] = defaultdict(list)
        for pa in per_asset:
            _bid = _ab_map.get(pa["asset"], batch_ids[0] if batch_ids else 0)
            _bg[_bid].append(pa)
        # Fetch batch names
        _batch_names: dict[int, str] = {}
        for _bid in _bg:
            _bo = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == _bid).first()
            _batch_names[_bid] = _bo.name if _bo and _bo.name else f"Batch #{_bid}"
        for _bid, _pas in sorted(_bg.items(), key=lambda x: _batch_names.get(x[0], "")):
            _oos_gp = sum(a.get("OOS_GP", 0) for a in _pas)
            _oos_gl = sum(a.get("OOS_GL", 0) for a in _pas)
            _b_pf = round(min(_oos_gp / _oos_gl, 10.0), 2) if _oos_gl > 0 else (10.0 if _oos_gp > 0 else 0)
            _tot_oos_t = sum(a.get("OOS_Trades", 0) for a in _pas)
            _wfe_vals = [a for a in _pas if a.get("WFE_PF", 0) != 0]
            _wfe_avg = (
                sum(a["WFE_PF"] * max(a.get("OOS_Trades", 1), 1) for a in _wfe_vals)
                / sum(max(a.get("OOS_Trades", 1), 1) for a in _wfe_vals)
            ) if _wfe_vals else 0.0
            per_batch.append({
                "batch_id": _bid,
                "batch_name": _batch_names.get(_bid, f"#{_bid}"),
                "assets": len(_pas),
                "OOS_PnL": round(sum(a.get("OOS_PnL", 0) for a in _pas), 2),
                "IS_PnL": round(sum(a.get("IS_PnL", 0) for a in _pas), 2),
                "OOS_PF": _b_pf,
                "WFE_PF": round(_wfe_avg, 4),
                "IS_Trades": sum(a.get("IS_Trades", 0) for a in _pas),
                "OOS_Trades": _tot_oos_t,
            })

    return {
        "is_metrics": is_metrics,
        "oos_metrics": oos_metrics,
        "is_trades": len(is_df),
        "oos_trades": len(oos_df),
        "aggregate_wfe": round(agg_wfe, 4),
        "aggregate_wfe_pf": round(agg_wfe_pf, 4),
        "aggregate_is_pf": _safe_pf(agg_is_pf),
        "aggregate_oos_pf": _safe_pf(agg_oos_pf),
        "per_asset": per_asset,
        "per_batch": per_batch,
        "cutoff": req.oos_date,
        "trade_date_range": {"from": trade_min, "to": trade_max},
        "return_to_dd_ratio": _ret_dd,
        "portfolio_grade": _grade,
        "portfolio_grade_score": round(_grade_score, 4),
        "strategy_type": _strat_type,
        # Unified quick metrics (daily-PnL DD, position-merged) — same formula as portfolio tabs
        "oos_quick_metrics": oos_quick,
        "is_quick_metrics": is_quick,
        "clipped_trades": _oos_clip_details,
        "oos_concurrent": oos_concurrent,
    }
