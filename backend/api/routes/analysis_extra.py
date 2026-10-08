"""Extended analysis routes — split optimization + seed ranking."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.analysis.correlation import get_correlation_matrix
from backend.analysis.optimizer_seeds import compute_seed_scores
from backend.analysis.optimizer_split import optimize_split_portfolio
from backend.analysis.strategy_config import load_strategy
from backend.analysis.tv_data_loader import (
    get_split_pnl_matrix,
    load_strategy_data,
    trades_from_db,
)
from backend.db.database import get_db
from backend.db.models import BacktestRun, BacktestTrade

router = APIRouter()


class SplitOptimizeRequest(BaseModel):
    strategy_filename: str | None = None
    batch_id: int | None = None
    n_longs: int = 5
    n_shorts: int = 5
    within_objective: str = "uncorrelated"
    combined_objective: str = "stability"
    corr_method: str = "standard"
    n_portfolios: int = 3
    prioritize_stability: bool = False
    seed_metrics: list[str] = ["sortino"]
    max_seeds: int = 20


class SeedScoresRequest(BaseModel):
    strategy_filename: str | None = None
    batch_id: int | None = None
    metrics: list[str] = ["sortino", "pf", "pnl"]
    side_filter: str = "All"


def _load_trades(req, db: Session):
    """Load standardized DataFrame from TV config or batch."""
    if req.batch_id:
        runs = (
            db.query(BacktestRun)
            .filter(BacktestRun.batch_id == req.batch_id, BacktestRun.status == "completed")
            .all()
        )
        run_ids = [r.id for r in runs]
        trades = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).all()
        return trades_from_db(trades)
    elif req.strategy_filename:
        config = load_strategy(req.strategy_filename)
        if not config:
            raise HTTPException(404, "Strategy config not found")
        return load_strategy_data(config)
    raise HTTPException(400, "Provide strategy_filename or batch_id")


@router.post("/optimize-split")
def optimize_split(req: SplitOptimizeRequest, db: Session = Depends(get_db)):
    """Run split Long/Short portfolio optimization."""
    df = _load_trades(req, db)
    if df.empty:
        raise HTTPException(400, "No trades found")

    daily = get_split_pnl_matrix(df, "D")
    if daily.shape[1] < 2:
        raise HTTPException(400, "Need at least 2 assets")

    corr = get_correlation_matrix(daily, req.corr_method)
    candidates = optimize_split_portfolio(
        corr, req.n_longs, req.n_shorts,
        within_objective=req.within_objective,
        combined_objective=req.combined_objective,
        n_portfolios=req.n_portfolios,
        daily_returns=daily,
        prioritize_stability=req.prioritize_stability,
        seed_metrics=req.seed_metrics,
        max_seeds=req.max_seeds,
    )
    return {"candidates": candidates, "total_symbols": daily.shape[1]}


@router.post("/seed-scores")
def seed_scores(req: SeedScoresRequest, db: Session = Depends(get_db)):
    """Compute seed ranking scores for all assets."""
    df = _load_trades(req, db)
    if df.empty:
        raise HTTPException(400, "No trades found")

    daily = get_split_pnl_matrix(df, "D")
    assets = daily.columns.tolist()

    if req.side_filter != "All":
        suffix = f"_{req.side_filter}"
        assets = [a for a in assets if a.endswith(suffix)]

    scores = compute_seed_scores(daily, assets, req.metrics)
    ranked = sorted(scores.items(), key=lambda x: x[1])

    return {
        "scores": {a: round(s, 4) for a, s in ranked},
        "ranked": [a for a, _ in ranked],
    }
