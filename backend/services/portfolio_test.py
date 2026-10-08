"""
Portfolio test service — re-runs a saved portfolio in Portfolio Mode.
All symbols share one equity pool. Risk % uses total portfolio value.
Supports single-batch, cross-strategy (multi-batch), and WFO portfolios.
"""
from __future__ import annotations

import json
import re

import pandas as pd
from sqlalchemy.orm import Session

from backend.backtester.dual_pass import merge_portfolio_results
from backend.backtester.engine import BacktestConfig
from backend.backtester.metrics import calculate_metrics
from backend.backtester.portfolio_engine import PortfolioResult, run_portfolio_backtest
from backend.constants import COMMISSION_PCT
from backend.db.models import (
    BatchBacktestRun,
    Portfolio,
    PortfolioAsset,
    Strategy,
)
from backend.utils.candle_loader import load_candles_df, to_nyt
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def _detect_extra_timeframes(params: dict, python_source: str) -> list[str]:
    """Auto-detect extra timeframes from strategy inputs and source."""
    extra: set[str] = set()
    _TF_MAP = {"1h": "60", "4h": "240", "12h": "720", "1D": "D", "3D": "3D"}
    inputs = params.get("strategy_inputs", {})
    for param_key in ("RSI Timeframe", "MA Filter Timeframe"):
        tf_label = inputs.get(param_key)
        if tf_label and tf_label != "Current" and tf_label in _TF_MAP:
            extra.add(_TF_MAP[tf_label])
    for match in re.findall(r'request_security\(\s*["\'](\w+)["\']', python_source):
        extra.add(match)
    return list(extra)


def _load_symbol_data(
    db: Session, symbols: list[str], timeframe: str,
    extra_tfs: list[str],
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, pd.DataFrame]]]:
    """Load OHLCV data for symbols, convert to NYT."""
    symbol_dfs: dict[str, pd.DataFrame] = {}
    extra_tf_dfs: dict[str, dict[str, pd.DataFrame]] = {}

    for sym in symbols:
        try:
            symbol_dfs[sym] = to_nyt(load_candles_df(db, sym, timeframe))
        except ValueError:
            logger.warning("No data for %s %s — skipping", sym, timeframe)
            continue

        if extra_tfs:
            sym_tfs: dict[str, pd.DataFrame] = {}
            for tf in extra_tfs:
                try:
                    sym_tfs[tf] = to_nyt(load_candles_df(db, sym, tf))
                except ValueError:
                    pass
            if sym_tfs:
                extra_tf_dfs[sym] = sym_tfs

    return symbol_dfs, extra_tf_dfs


def _trim_to_oos(
    symbol_dfs: dict[str, pd.DataFrame],
    extra_tf_dfs: dict[str, dict[str, pd.DataFrame]],
    cutoff: pd.Timestamp,
    original_warmup: int,
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, pd.DataFrame]], int]:
    """Trim OHLCV data for OOS-only testing.

    Keeps warmup_bars + 200 bars before the cutoff for indicator convergence,
    then sets effective_warmup to cover pre-cutoff bars so the engine
    suppresses trading until OOS candles begin.

    Returns (trimmed_symbol_dfs, trimmed_extra_tf_dfs, effective_warmup).
    """
    convergence_buffer = 200
    keep_before = original_warmup + convergence_buffer

    trimmed: dict[str, pd.DataFrame] = {}
    trimmed_extra: dict[str, dict[str, pd.DataFrame]] = {}
    max_pre_cutoff_bars = 0

    for sym, df in symbol_dfs.items():
        # Find first bar at or after cutoff
        oos_mask = df.index >= cutoff
        if not oos_mask.any():
            # No OOS data for this symbol — skip it
            continue

        first_oos_idx = oos_mask.argmax()  # index of first True
        start_idx = max(0, first_oos_idx - keep_before)
        trimmed[sym] = df.iloc[start_idx:].copy()

        pre_cutoff_bars = first_oos_idx - start_idx
        max_pre_cutoff_bars = max(max_pre_cutoff_bars, pre_cutoff_bars)

        # Trim extra timeframes too (keep all data — HTF needs fewer bars)
        if sym in extra_tf_dfs:
            sym_extras: dict[str, pd.DataFrame] = {}
            for tf, tdf in extra_tf_dfs[sym].items():
                htf_mask = tdf.index >= cutoff
                if htf_mask.any():
                    htf_first = htf_mask.argmax()
                    htf_start = max(0, htf_first - keep_before)
                    sym_extras[tf] = tdf.iloc[htf_start:].copy()
                else:
                    sym_extras[tf] = tdf  # keep all for warmup
            trimmed_extra[sym] = sym_extras

    effective_warmup = max(original_warmup, max_pre_cutoff_bars)
    return trimmed, trimmed_extra, effective_warmup


def _run_group(
    python_source: str,
    symbol_dfs: dict[str, pd.DataFrame],
    extra_tf_dfs: dict[str, dict[str, pd.DataFrame]],
    config: BacktestConfig,
    inputs: dict | None,
    direction_filter: str,
    timeframe: str = "1d",
) -> PortfolioResult:
    """Run portfolio engine for a group of symbols (handles long_plus_short)."""
    if direction_filter == "long_plus_short":
        cfg_long = BacktestConfig(
            initial_capital=config.initial_capital,
            commission_pct=config.commission_pct,
            slippage_mode=config.slippage_mode,
            slippage_value=config.slippage_value,
            leverage=config.leverage,
            direction_filter="long",
            max_pyramiding=config.max_pyramiding,
            warmup_bars=config.warmup_bars,
            risk_pct=config.risk_pct,
            risk_mode=config.risk_mode,
            risk_value=config.risk_value,
            process_orders_on_close=config.process_orders_on_close,
        )
        cfg_short = BacktestConfig(
            initial_capital=config.initial_capital,
            commission_pct=config.commission_pct,
            slippage_mode=config.slippage_mode,
            slippage_value=config.slippage_value,
            leverage=config.leverage,
            direction_filter="short",
            max_pyramiding=config.max_pyramiding,
            warmup_bars=config.warmup_bars,
            risk_pct=config.risk_pct,
            risk_mode=config.risk_mode,
            risk_value=config.risk_value,
            process_orders_on_close=config.process_orders_on_close,
        )
        result_long = run_portfolio_backtest(
            python_source, symbol_dfs, cfg_long,
            inputs=inputs,
            extra_tf_dfs=extra_tf_dfs or None,
            timeframe=timeframe,
        )
        result_short = run_portfolio_backtest(
            python_source, symbol_dfs, cfg_short,
            inputs=inputs,
            extra_tf_dfs=extra_tf_dfs or None,
            timeframe=timeframe,
        )
        return merge_portfolio_results(result_long, result_short, config.initial_capital, timeframe=timeframe)
    else:
        effective = direction_filter if direction_filter != "long_plus_short" else "both"
        cfg = BacktestConfig(
            initial_capital=config.initial_capital,
            commission_pct=config.commission_pct,
            slippage_mode=config.slippage_mode,
            slippage_value=config.slippage_value,
            leverage=config.leverage,
            direction_filter=effective,
            max_pyramiding=config.max_pyramiding,
            warmup_bars=config.warmup_bars,
            risk_pct=config.risk_pct,
            risk_mode=config.risk_mode,
            risk_value=config.risk_value,
            process_orders_on_close=config.process_orders_on_close,
        )
        return run_portfolio_backtest(
            python_source, symbol_dfs, cfg,
            inputs=inputs,
            extra_tf_dfs=extra_tf_dfs or None,
            timeframe=timeframe,
        )


def _merge_multi_results(
    results: list[PortfolioResult], initial_capital: float,
    timeframe: str = "30",
) -> PortfolioResult:
    """Merge PortfolioResults from multiple strategy groups."""
    if len(results) == 1:
        return results[0]

    merged_trades: dict[str, list] = {}
    merged_metrics: dict[str, dict] = {}
    all_trades_combined: list = []

    for r in results:
        for sym, trades in r.per_symbol_trades.items():
            merged_trades[sym] = trades
            all_trades_combined.extend(trades)
        for sym, metrics in r.per_symbol_metrics.items():
            merged_metrics[sym] = metrics

    # Sum equity deltas across all groups
    max_len = max(len(r.combined_equity) for r in results)
    merged_equity: list[float] = []
    for i in range(max_len):
        total = 0.0
        for r in results:
            eq = r.combined_equity
            val = eq[i] if i < len(eq) else (eq[-1] if eq else initial_capital)
            total += val
        # Each group starts at a fraction of capital, sum gives total
        merged_equity.append(total)

    # Recalculate combined metrics from all merged trades + equity
    bars = max(r.bars_processed for r in results)
    combined = calculate_metrics(
        all_trades_combined, merged_equity, initial_capital,
        total_bars=bars,
        timeframe=timeframe,
    )

    return PortfolioResult(
        per_symbol_trades=merged_trades,
        per_symbol_metrics=merged_metrics,
        combined_equity=merged_equity,
        combined_metrics=combined,
        bars_processed=bars,
    )


def run_portfolio_test(
    portfolio_id: int,
    db: Session,
    initial_capital: float = 10_000.0,
    commission_pct: float = COMMISSION_PCT,
    leverage: int = 1,
    risk_mode: str = "pct",
    risk_value: float = 0.0,
    slippage_pct: float = 0.0,
    oos_only: bool = False,
    progress: dict | None = None,
    task_id: str | None = None,
    cancel_check: "Callable[[], bool] | None" = None,
) -> dict:
    """Run a portfolio test for a saved portfolio.

    Groups assets by their source batch, loads strategy code,
    runs the portfolio engine per-group, and merges results.
    """
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise ValueError(f"Portfolio {portfolio_id} not found")

    assets: list[PortfolioAsset] = [a for a in portfolio.assets if a.is_active]
    if not assets:
        raise ValueError("Portfolio has no active assets")

    settings = json.loads(portfolio.source_strategies) if portfolio.source_strategies else {}

    # Resolve batch IDs
    batch_ids: list[int] = []
    if "batch_ids" in settings:
        batch_ids = settings["batch_ids"]
    elif "batch_id" in settings:
        batch_ids = [settings["batch_id"]]
    else:
        raise ValueError("Portfolio has no batch reference — cannot test")

    # Load all batches
    batches = (
        db.query(BatchBacktestRun)
        .filter(BatchBacktestRun.id.in_(batch_ids))
        .all()
    )
    if not batches:
        raise ValueError("No batch records found for this portfolio")

    # Build a map: batch_id → strategy details
    batch_map: dict[int, dict] = {}
    for batch in batches:
        strategy = db.query(Strategy).filter(Strategy.id == batch.strategy_id).first()
        if not strategy:
            logger.warning("Strategy %d not found for batch %d", batch.strategy_id, batch.id)
            continue
        params = json.loads(batch.parameters) if batch.parameters else {}
        batch_map[batch.id] = {
            "python_source": strategy.python_source,
            "strategy_name": strategy.name,
            "timeframe": batch.timeframe,
            "direction_filter": batch.direction_filter or "both",
            "inputs": params.get("inputs") or params.get("strategy_inputs") or {},
            "parameters": params,
            "warmup_bars": params.get("warmup_bars", 0),
            "max_pyramiding": params.get("max_pyramiding", 1),
        }

    # Group portfolio assets by batch
    # For single-batch portfolios, all go to one group
    # For cross-strategy, match by strategy_name
    asset_groups: dict[int, list[str]] = {bid: [] for bid in batch_map}

    if len(batch_map) == 1:
        # Single batch — all assets go here
        bid = next(iter(batch_map))
        for a in assets:
            asset_groups[bid].append(a.symbol)
    else:
        # Multi-batch — prefer batch_id from asset, fall back to strategy_name
        name_to_batch: dict[str, int] = {}
        for bid, info in batch_map.items():
            name_to_batch[info["strategy_name"]] = bid

        for a in assets:
            # Direct batch_id takes priority (set during save)
            if a.batch_id and a.batch_id in asset_groups:
                asset_groups[a.batch_id].append(a.symbol)
            else:
                matched_bid = name_to_batch.get(a.strategy_name)
                if matched_bid and matched_bid in asset_groups:
                    asset_groups[matched_bid].append(a.symbol)
                else:
                    # Fallback: assign to first batch
                    first_bid = next(iter(asset_groups))
                    asset_groups[first_bid].append(a.symbol)

    # OOS-only: resolve cutoff date from portfolio
    oos_cutoff: pd.Timestamp | None = None
    if oos_only:
        if portfolio.optimization_end_date:
            oos_cutoff = pd.Timestamp(portfolio.optimization_end_date)
        else:
            raise ValueError("No optimization end date on this portfolio — cannot run OOS-only test")

    # Run portfolio engine per group
    total_symbols = sum(len(syms) for syms in asset_groups.values())
    group_results: list[PortfolioResult] = []
    processed = 0

    for bid, symbols in asset_groups.items():
        if not symbols:
            continue

        if cancel_check and cancel_check():
            logger.info("Portfolio test cancelled during group iteration")
            return {}

        info = batch_map[bid]
        extra_tfs = _detect_extra_timeframes(info["parameters"], info["python_source"])

        if progress and task_id:
            progress[task_id]["current_group"] = info["strategy_name"]

        # Load data
        symbol_dfs, extra_tf_dfs = _load_symbol_data(
            db, symbols, info["timeframe"], extra_tfs,
        )
        if not symbol_dfs:
            continue

        # Proportional capital for multi-group
        group_capital = (
            initial_capital * len(symbols) / total_symbols
            if len(asset_groups) > 1
            else initial_capital
        )

        # OOS-only: trim candle data to start near the cutoff
        effective_warmup = info["warmup_bars"]
        if oos_only and oos_cutoff is not None:
            symbol_dfs, extra_tf_dfs, effective_warmup = _trim_to_oos(
                symbol_dfs, extra_tf_dfs, oos_cutoff, info["warmup_bars"],
            )
            if not symbol_dfs:
                continue

        config = BacktestConfig(
            initial_capital=group_capital,
            commission_pct=commission_pct,
            slippage_mode="pct",
            slippage_value=slippage_pct,
            leverage=leverage,
            risk_mode=risk_mode,
            risk_value=risk_value,
            max_pyramiding=info["max_pyramiding"],
            warmup_bars=effective_warmup,
            process_orders_on_close=info["parameters"].get("process_orders_on_close", False),
        )

        result = _run_group(
            info["python_source"], symbol_dfs, extra_tf_dfs,
            config, info["inputs"], info["direction_filter"],
            timeframe=info["timeframe"],
        )
        group_results.append(result)

        processed += len(symbol_dfs)
        if progress and task_id:
            progress[task_id]["completed_symbols"] = processed

    if not group_results:
        raise ValueError("No symbols had data — test could not run")

    # Merge all group results (use first batch's timeframe for metrics annualization)
    first_tf = next(iter(batch_map.values()))["timeframe"] if batch_map else "30"
    final = _merge_multi_results(group_results, initial_capital, timeframe=first_tf)

    # Serialize for JSON response
    serialized_metrics: dict[str, dict] = {}
    for sym, m in final.per_symbol_metrics.items():
        serialized_metrics[sym] = _safe_serialize(m)

    return {
        "combined_equity": final.combined_equity,
        "combined_metrics": _safe_serialize(final.combined_metrics),
        "per_symbol_metrics": serialized_metrics,
        "bars_processed": final.bars_processed,
        "total_symbols": total_symbols,
        "oos_only": oos_only,
    }


def _safe_serialize(d: dict) -> dict:
    """Make a metrics dict JSON-safe (handle numpy/pandas types)."""
    import numpy as np
    result = {}
    for k, v in d.items():
        if isinstance(v, (pd.DataFrame, pd.Series)):
            continue
        elif isinstance(v, (float, np.floating)):
            result[k] = None if (pd.isna(v) or np.isinf(v)) else round(float(v), 4)
        elif isinstance(v, (int, np.integer)):
            result[k] = int(v)
        elif isinstance(v, np.bool_):
            result[k] = bool(v)
        else:
            result[k] = v
    return result
