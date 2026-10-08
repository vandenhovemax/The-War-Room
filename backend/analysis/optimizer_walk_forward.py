"""Walk-Forward Validated optimizer (Method 6 / 8th dropdown algorithm).

Splits the IS period into train (default 70%) and validation (30%).
Runs an inner algorithm on the train portion, then scores and ranks
each candidate portfolio by its performance on the validation window.

This is a *meta-algorithm* — it wraps another algorithm with validation.
"""
from __future__ import annotations

import logging

import pandas as pd

from backend.analysis.overfit_guard import _portfolio_daily_pnl, _score_validation

logger = logging.getLogger(__name__)


def walk_forward_select(
    daily_pnl: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    n_assets: int,
    objective: str = "stability",
    n_portfolios: int = 3,
    split_pct: float = 0.70,
    validation_metric: str = "stability",
    inner_algo: str = "greedy",
    seed_metrics: list[str] | None = None,
    max_seeds: int = 20,
    prioritize_stability: bool = False,
    external_scores: dict | None = None,
    streamlit_logic: bool = False,
    max_overlap: float | None = None,
    expand_pool: bool = True,
    seed_prior_weight: float = 0.0,
    seed_weights: dict[str, float] | None = None,
    max_dd_pct: float = 0.0,
    engine: str = "current",
    **kwargs,
) -> list[dict]:
    """Build portfolios on train data, rank by validation performance.

    1. Split daily_pnl at split_pct date boundary
    2. Compute correlation matrix on train portion only
    3. Run inner_algo on train portion (produces N candidates)
    4. Score each candidate on validation portion
    5. Return candidates sorted by validation score
    """
    if daily_pnl.empty:
        return []

    n_rows = len(daily_pnl)
    split_idx = int(n_rows * split_pct)
    if split_idx < 10 or (n_rows - split_idx) < 5:
        logger.warning("WF select: insufficient data (n=%d, split=%d)", n_rows, split_idx)
        return []

    train_pnl = daily_pnl.iloc[:split_idx]
    val_pnl = daily_pnl.iloc[split_idx:]

    # Recompute correlation on train data only
    train_corr = train_pnl.corr()

    # Run inner algorithm on train portion
    from backend.analysis.diversification import diversify_portfolio
    inner_candidates = diversify_portfolio(
        div_algo=inner_algo,
        daily_pnl=train_pnl,
        corr_matrix=train_corr,
        n_assets=n_assets,
        objective=objective,
        n_portfolios=max(n_portfolios * 2, 6),  # generate extra, filter later
        seed_metrics=seed_metrics,
        max_seeds=max_seeds,
        prioritize_stability=prioritize_stability,
        external_scores=external_scores,
        streamlit_logic=streamlit_logic,
        max_overlap=max_overlap,
        expand_pool=expand_pool,
        seed_prior_weight=seed_prior_weight,
        seed_weights=seed_weights,
        max_dd_pct=max_dd_pct,
        engine=engine,
    )

    if not inner_candidates:
        return []

    # Score each candidate on the validation window
    scored: list[tuple[float, dict]] = []
    for cand in inner_candidates:
        assets = cand.get("assets", [])
        port_pnl = _portfolio_daily_pnl(val_pnl, assets)
        vs = _score_validation(port_pnl, validation_metric)
        cand["validation_score"] = round(vs, 4)
        scored.append((vs, cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = [c for _, c in scored[:n_portfolios]]
    logger.info("WF select: %d candidates built on train, top %d by validation %s",
                len(inner_candidates), len(result), validation_metric)
    return result


def walk_forward_select_split(
    daily_pnl: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    n_longs: int,
    n_shorts: int,
    within_objective: str = "uncorrelated",
    combined_objective: str = "stability",
    n_portfolios: int = 3,
    split_pct: float = 0.70,
    validation_metric: str = "stability",
    inner_algo: str = "greedy",
    seed_metrics: list[str] | None = None,
    max_seeds: int = 20,
    prioritize_stability: bool = False,
    external_scores: dict | None = None,
    streamlit_logic: bool = False,
    max_overlap: float | None = None,
    expand_pool: bool = True,
    seed_prior_weight: float = 0.0,
    seed_weights: dict[str, float] | None = None,
    max_dd_pct: float = 0.0,
    engine: str = "current",
    **kwargs,
) -> list[dict]:
    """Split-mode walk-forward: builds L/S on train, validates on holdout."""
    if daily_pnl.empty:
        return []

    n_rows = len(daily_pnl)
    split_idx = int(n_rows * split_pct)
    if split_idx < 10 or (n_rows - split_idx) < 5:
        logger.warning("WF select split: insufficient data (n=%d, split=%d)", n_rows, split_idx)
        return []

    train_pnl = daily_pnl.iloc[:split_idx]
    val_pnl = daily_pnl.iloc[split_idx:]
    train_corr = train_pnl.corr()

    from backend.analysis.diversification import diversify_split_portfolio
    inner_candidates = diversify_split_portfolio(
        div_algo=inner_algo,
        daily_pnl=train_pnl,
        corr_matrix=train_corr,
        n_longs=n_longs,
        n_shorts=n_shorts,
        within_objective=within_objective,
        combined_objective=combined_objective,
        n_portfolios=max(n_portfolios * 2, 6),
        seed_metrics=seed_metrics,
        max_seeds=max_seeds,
        prioritize_stability=prioritize_stability,
        external_scores=external_scores,
        streamlit_logic=streamlit_logic,
        max_overlap=max_overlap,
        expand_pool=expand_pool,
        seed_prior_weight=seed_prior_weight,
        seed_weights=seed_weights,
        max_dd_pct=max_dd_pct,
        engine=engine,
    )

    if not inner_candidates:
        return []

    scored: list[tuple[float, dict]] = []
    for cand in inner_candidates:
        assets = cand.get("assets", [])
        port_pnl = _portfolio_daily_pnl(val_pnl, assets)
        vs = _score_validation(port_pnl, validation_metric)
        cand["validation_score"] = round(vs, 4)
        scored.append((vs, cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = [c for _, c in scored[:n_portfolios]]
    logger.info("WF select split: %d candidates, top %d by validation %s",
                len(inner_candidates), len(result), validation_metric)
    return result
