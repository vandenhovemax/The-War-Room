"""Robustness analysis routes — fat-tail bootstrap and random exit stress test."""
from __future__ import annotations

import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.analysis.fat_tail_bootstrap import fat_tail_bootstrap
from backend.analysis.random_exit import random_exit_stress_test
from backend.db.database import get_db
from backend.db.models import BatchBacktestRun, Portfolio
from backend.utils.candle_loader import load_candles_df, to_nyt
from backend.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ─── Helpers ────────────────────────────────────────────────────────

def _load_portfolio_and_trades(portfolio_id: int, db: Session):
    """Load portfolio + trades DataFrame, get initial capital."""
    p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not p:
        raise HTTPException(404, "Portfolio not found")

    from backend.api.routes.portfolio_asset_routes import _load_trades
    df = _load_trades(p, db)
    if df.empty:
        raise HTTPException(400, "No trades in portfolio")

    # Initial capital from source batch
    ss = json.loads(p.source_strategies) if p.source_strategies else {}
    bid = (ss.get("batch_ids") or [ss.get("batch_id")])[0] if (
        ss.get("batch_ids") or ss.get("batch_id")
    ) else None
    capital = 10_000.0
    if bid:
        batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
        if batch and batch.parameters:
            capital = json.loads(batch.parameters).get("initial_capital", 10_000.0)

    return p, df, capital


def _apply_date_filters(
    df: pd.DataFrame, date_from: str | None, date_to: str | None,
) -> pd.DataFrame:
    """Filter trades by Entry Time (period attribution by entry)."""
    if date_from and "Entry Time" in df.columns:
        df = df[df["Entry Time"] >= pd.Timestamp(date_from)]
    if date_to and "Entry Time" in df.columns:
        df = df[df["Entry Time"] <= pd.Timestamp(date_to)]
    return df


# ─── Fat-Tail Bootstrap ────────────────────────────────────────────

class FatTailRequest(BaseModel):
    portfolio_id: int
    n_simulations: int = 1000
    outlier_pct: float = 5.0
    with_replacement: bool = True
    date_from: str | None = None
    date_to: str | None = None


@router.post("/fat-tail-bootstrap")
def fat_tail_bootstrap_route(req: FatTailRequest, db: Session = Depends(get_db)):
    """Run outlier-preserved bootstrap stress test."""
    _, df, capital = _load_portfolio_and_trades(req.portfolio_id, db)
    df = _apply_date_filters(df, req.date_from, req.date_to)

    pnl = df["Net Profit"].dropna().tolist()
    if not pnl:
        raise HTTPException(400, "No trades after filtering")

    result = fat_tail_bootstrap(
        pnl,
        n_simulations=req.n_simulations,
        outlier_pct=req.outlier_pct,
        with_replacement=req.with_replacement,
        initial_capital=capital,
    )
    if result is None:
        raise HTTPException(400, "No trades to simulate")
    return result


# ─── Random Exit Stress Test ───────────────────────────────────────

class RandomExitRequest(BaseModel):
    portfolio_id: int
    n_iterations: int = 500
    exit_mode: str = "random_bars"
    max_hold_bars: int = 50
    date_from: str | None = None
    date_to: str | None = None


@router.post("/random-exit")
def random_exit_route(req: RandomExitRequest, db: Session = Depends(get_db)):
    """Run randomized exit stress test to prove exit edge."""
    p, df, _ = _load_portfolio_and_trades(req.portfolio_id, db)
    df = _apply_date_filters(df, req.date_from, req.date_to)

    if df.empty:
        raise HTTPException(400, "No trades after filtering")

    # Build asset → batch_id → timeframe lookup
    asset_tf: dict[str, str] = {}
    batch_tf_cache: dict[int, str] = {}
    for a in p.assets:
        if not a.is_active:
            continue
        bid = a.batch_id
        if bid and bid not in batch_tf_cache:
            batch = db.query(BatchBacktestRun).filter(
                BatchBacktestRun.id == bid,
            ).first()
            if batch:
                batch_tf_cache[bid] = batch.timeframe

        tf = batch_tf_cache.get(bid) if bid else None
        if not tf:
            # Fallback: use first batch's timeframe
            ss = json.loads(p.source_strategies) if p.source_strategies else {}
            first_bid = (ss.get("batch_ids") or [ss.get("batch_id")])[0] if (
                ss.get("batch_ids") or ss.get("batch_id")
            ) else None
            if first_bid and first_bid not in batch_tf_cache:
                fb = db.query(BatchBacktestRun).filter(
                    BatchBacktestRun.id == first_bid,
                ).first()
                if fb:
                    batch_tf_cache[first_bid] = fb.timeframe
            tf = batch_tf_cache.get(first_bid, "30")

        # Map asset names (BTCUSDT_Long → BTCUSDT)
        sym = a.symbol
        if a.trade_side in ("Long", "Short"):
            asset_tf[f"{sym}_{a.trade_side}"] = tf
        else:
            asset_tf[f"{sym}_Long"] = tf
            asset_tf[f"{sym}_Short"] = tf

    # Pre-load candle data (one load per unique symbol+timeframe)
    candle_cache: dict[str, pd.DataFrame] = {}
    loaded_pairs: set[tuple[str, str]] = set()
    for asset_name in df["Asset"].unique():
        # Extract raw symbol from asset name (e.g., "BTCUSDT_Long" → "BTCUSDT")
        raw_sym = asset_name.rsplit("_", 1)[0] if (
            asset_name.endswith("_Long") or asset_name.endswith("_Short")
        ) else asset_name
        tf = asset_tf.get(asset_name, "30")

        if (raw_sym, tf) not in loaded_pairs:
            loaded_pairs.add((raw_sym, tf))
            candle_df = to_nyt(load_candles_df(db, raw_sym, tf))
            candle_cache[raw_sym] = candle_df

    # Build trade dicts from DataFrame rows
    trades_list: list[dict] = []
    for _, row in df.iterrows():
        asset = row.get("Asset", "")
        raw_sym = asset.rsplit("_", 1)[0] if (
            asset.endswith("_Long") or asset.endswith("_Short")
        ) else asset

        trades_list.append({
            "entry_time": row["Entry Time"],
            "symbol": raw_sym,
            "side": row.get("Side", "Long"),
            "entry_price": row.get("Entry Price", 0),
            "exit_price": row.get("Exit Price", 0),
            "net_profit": row.get("Net Profit", 0),
            "commission": row.get("Commission", 0),
        })

    # Extract slippage settings from source batch so random exits
    # face the same friction as the real backtest.
    slip_mode, slip_value = "pct", 0.0
    ss = json.loads(p.source_strategies) if p.source_strategies else {}
    first_bid = (ss.get("batch_ids") or [ss.get("batch_id")])[0] if (
        ss.get("batch_ids") or ss.get("batch_id")
    ) else None
    if first_bid:
        fb = db.query(BatchBacktestRun).filter(
            BatchBacktestRun.id == first_bid,
        ).first()
        if fb and fb.parameters:
            bp = json.loads(fb.parameters)
            slip_mode = bp.get("slippage_mode", "pct")
            slip_value = float(bp.get("slippage_value", 0.0))
            # Legacy fallback: slippage_value 0 → use slippage_pct (default 0.01)
            if slip_value == 0:
                slip_value = float(bp.get("slippage_pct", 0.01))

    result = random_exit_stress_test(
        trades_list,
        candle_cache,
        n_iterations=req.n_iterations,
        exit_mode=req.exit_mode,
        max_hold_bars=req.max_hold_bars,
        slippage_mode=slip_mode,
        slippage_value=slip_value,
    )
    if result is None:
        raise HTTPException(400, "No valid trades for simulation")
    return result
