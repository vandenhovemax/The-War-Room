"""Portfolio Health Monitor routes — rolling health, per-asset decay, multi-portfolio overview."""
from __future__ import annotations

import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.analysis.portfolio_health import (
    compute_multi_portfolio_health,
    compute_portfolio_health,
    health_result_to_dict,
)
from backend.api.routes.portfolio_analysis import _load_portfolio_trades
from backend.db.database import get_db
from backend.db.models import Portfolio, Trade
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class HealthRequest(BaseModel):
    portfolio_id: int
    windows: list[int] = [30, 60, 90]
    recent_days: int = 90
    include_is: bool = False  # Include IS trades in analysis (default: OOS only)


class MultiHealthRequest(BaseModel):
    portfolio_ids: list[int] = []  # empty = all active portfolios
    include_saved: bool = True
    include_paper: bool = True
    include_real: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_health_trades_df(
    p: Portfolio,
    db: Session,
    include_is: bool = False,
) -> pd.DataFrame:
    """Build a standardized trades DataFrame for health analysis.

    For saved portfolios: uses BacktestTrade data via _load_portfolio_trades.
    For paper/real portfolios: uses Trade table (live trades) if available,
    falls back to BacktestTrade data.

    Returns DataFrame with columns: exit_time, net_profit, asset, side.
    """
    frames: list[pd.DataFrame] = []

    # 1. Backtest trades (available for all portfolio types)
    bt_df = _load_portfolio_trades(p, db, clip_is=True)
    if not bt_df.empty:
        # _load_portfolio_trades returns columns like: Asset, Net Profit, Exit Time, Side, etc.
        bt_health = pd.DataFrame({
            "exit_time": pd.to_datetime(bt_df["Exit Time"]),
            "net_profit": bt_df["Net Profit"].astype(float),
            "asset": bt_df["Asset"],
            "side": bt_df["Side"] if "Side" in bt_df.columns else "unknown",
            "source": "backtest",
        })
        frames.append(bt_health)

    # 2. Live trades (paper/real portfolios only)
    if p.portfolio_type in ("paper", "real"):
        live_trades = (
            db.query(Trade)
            .filter(Trade.portfolio_id == p.id)
            .all()
        )
        if live_trades:
            live_rows = []
            for t in live_trades:
                # Build asset name to match portfolio asset naming
                asset_name = f"{t.symbol}_{t.side}" if t.side in ("Long", "Short") else t.symbol
                live_rows.append({
                    "exit_time": t.closed_at,
                    "net_profit": float(t.realized_pnl),
                    "asset": asset_name,
                    "side": t.side,
                    "source": "live",
                })
            if live_rows:
                live_df = pd.DataFrame(live_rows)
                live_df["exit_time"] = pd.to_datetime(live_df["exit_time"])
                frames.append(live_df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)

    # Deduplicate: if a trade exists in both backtest and live sources
    # (from a batch refresh after going live), prefer the live version.
    # Simple dedup: if live trades exist for this portfolio, drop backtest
    # trades that overlap in time with live data range.
    if len(frames) > 1 and any(f["source"].iloc[0] == "live" for f in frames if not f.empty):
        live_portion = combined[combined["source"] == "live"]
        if not live_portion.empty:
            live_start = live_portion["exit_time"].min()
            # Keep: all live trades + backtest trades BEFORE live start
            combined = pd.concat([
                combined[(combined["source"] == "backtest") & (combined["exit_time"] < live_start)],
                combined[combined["source"] == "live"],
            ], ignore_index=True)

    return combined.sort_values("exit_time").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/portfolio-health")
def portfolio_health(req: HealthRequest, db: Session = Depends(get_db)):
    """Compute health analysis for a single portfolio.

    Returns rolling metrics, per-asset health, decay detection, and trend.
    """
    p = db.query(Portfolio).filter(Portfolio.id == req.portfolio_id).first()
    if not p:
        raise HTTPException(404, "Portfolio not found")

    trades_df = _build_health_trades_df(p, db, include_is=req.include_is)
    if trades_df.empty:
        raise HTTPException(404, "No trades found for this portfolio")

    opt_end = None
    if p.optimization_end_date:
        opt_end = str(p.optimization_end_date)[:10]

    result = compute_portfolio_health(
        trades_df=trades_df,
        portfolio_id=p.id,
        portfolio_type=p.portfolio_type or "saved",
        optimization_end_date=opt_end if not req.include_is else None,
        windows=req.windows,
        recent_days=req.recent_days,
    )
    return health_result_to_dict(result)


@router.post("/portfolio-health-overview")
def portfolio_health_overview(req: MultiHealthRequest, db: Session = Depends(get_db)):
    """Multi-portfolio health overview for the standalone dashboard.

    Returns a summary row per portfolio with key health indicators.
    """
    query = db.query(Portfolio).filter(
        Portfolio.is_deleted == False,
        Portfolio.is_hidden == False,
    )

    # Filter by type
    types = []
    if req.include_saved:
        types.append("saved")
    if req.include_paper:
        types.append("paper")
    if req.include_real:
        types.append("real")
    if types:
        query = query.filter(Portfolio.portfolio_type.in_(types))

    # Filter by specific IDs if provided
    if req.portfolio_ids:
        query = query.filter(Portfolio.id.in_(req.portfolio_ids))

    portfolios = query.order_by(Portfolio.created_at.desc()).all()

    portfolio_data = []
    for p in portfolios:
        try:
            trades_df = _build_health_trades_df(p, db)
            if trades_df.empty:
                continue

            opt_end = None
            if p.optimization_end_date:
                opt_end = str(p.optimization_end_date)[:10]

            portfolio_data.append({
                "portfolio_id": p.id,
                "name": p.name,
                "portfolio_type": p.portfolio_type or "saved",
                "trades_df": trades_df,
                "optimization_end_date": opt_end,
            })
        except Exception as e:
            logger.warning("Failed to load trades for portfolio %d: %s", p.id, e)
            continue

    if not portfolio_data:
        return {"portfolios": [], "total": 0}

    summaries = compute_multi_portfolio_health(portfolio_data)
    return {
        "portfolios": summaries,
        "total": len(summaries),
    }
