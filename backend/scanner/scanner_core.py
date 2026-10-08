"""Scanner core — iterates active portfolios and detects new bars.

Orchestrates the signal scan cycle: for each active portfolio, resolves
strategy + timeframe + parameters, checks for new candles since last scan,
and delegates to scanner_executor for strategy execution.
"""
import json
import traceback
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db.database import SessionLocal
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  BatchBacktestRun,
  LiveSignal,
  LiveStrategyState,
  OHLCVData,
  Portfolio,
  PortfolioAsset,
  Strategy,
)
from backend.scanner.scanner_executor import execute_strategy_on_new_bars
from backend.utils.logger import get_logger
from backend.utils.tf_resolver import resolve_extra_tfs, resolve_timeframe

logger = get_logger("scanner_core")

# Portfolio IDs managed by the live execution engine — scanner must skip these
_live_engine_portfolio_ids: set[int] = set()


def register_live_portfolio(portfolio_id: int) -> None:
  """Mark a portfolio as managed by the live engine (scanner will skip it)."""
  _live_engine_portfolio_ids.add(portfolio_id)


def unregister_live_portfolio(portfolio_id: int) -> None:
  """Unmark a portfolio from live engine management."""
  _live_engine_portfolio_ids.discard(portfolio_id)


def run_scan_cycle() -> int:
  """One full scan cycle across all active portfolios. Returns signal count."""
  db = SessionLocal()
  total_signals = 0
  try:
    active = db.query(Portfolio).filter(Portfolio.is_active == True).all()
    if not active:
      logger.info("No active portfolios — nothing to scan")
      return 0

    logger.info("Signal scan: %d active portfolios", len(active))
    for portfolio in active:
      try:
        count = _scan_portfolio(db, portfolio)
        total_signals += count
      except Exception as e:
        logger.error(
          "Portfolio %d scan failed: %s\n%s",
          portfolio.id, e, traceback.format_exc(),
        )

    return total_signals
  finally:
    db.close()


def _scan_portfolio(db: Session, portfolio: Portfolio) -> int:
  """Scan all assets in one portfolio. Returns signal count.

  For single-strategy portfolios: resolves one strategy for all assets.
  For cross-strategy portfolios: resolves strategy PER ASSET via batch_id
  (each batch can reference a different strategy with different params).
  """
  # Skip portfolios managed by the live execution engine (avoid duplicate signals)
  if portfolio.id in _live_engine_portfolio_ids:
    logger.debug("Portfolio %d managed by live engine — skipping scanner", portfolio.id)
    return 0

  source = json.loads(portfolio.source_strategies or "{}")
  is_cross = source.get("is_cross_strat", False)

  # Portfolio-level defaults (used for single-strategy portfolios)
  portfolio_tf = resolve_timeframe(db, source)
  portfolio_extra_tfs = resolve_extra_tfs(db, source)
  portfolio_strategy = _resolve_strategy(db, source)

  # For single-strategy portfolios, a strategy is required
  if not is_cross and (not portfolio_strategy or not portfolio_strategy.python_source):
    logger.debug("Portfolio %d: no strategy found, skipping", portfolio.id)
    return 0

  assets = db.query(PortfolioAsset).filter(
    PortfolioAsset.portfolio_id == portfolio.id,
    PortfolioAsset.is_active == True,
  ).all()
  if not assets:
    return 0

  default_params = (
    _resolve_default_params(portfolio_strategy, source, db)
    if portfolio_strategy else {}
  )

  signal_count = 0
  for asset in assets:
    base_symbol = asset.symbol.split("_")[0] if "_" in asset.symbol else asset.symbol

    # For cross-strategy portfolios, resolve strategy from the asset's batch
    if is_cross and asset.batch_id:
      batch = db.get(BatchBacktestRun, asset.batch_id)
      if batch and batch.strategy_id:
        asset_strategy = db.get(Strategy, batch.strategy_id)
      else:
        asset_strategy = portfolio_strategy
      asset_tf = batch.timeframe if batch and batch.timeframe else portfolio_tf
      asset_extra_tfs = (
        sorted(set(json.loads(batch.extra_timeframes or "[]")))
        if batch else portfolio_extra_tfs
      )
    else:
      asset_strategy = portfolio_strategy
      asset_tf = portfolio_tf
      asset_extra_tfs = portfolio_extra_tfs

    if not asset_strategy or not asset_strategy.python_source:
      logger.debug("Portfolio %d asset %s: no strategy, skipping", portfolio.id, base_symbol)
      continue

    # Per-asset parameter overrides
    if is_cross and asset.batch_id:
      asset_params = _resolve_default_params(asset_strategy, source, db)
      batch = db.get(BatchBacktestRun, asset.batch_id)
      if batch:
        bp = json.loads(batch.parameters or "{}")
        asset_params.update(bp.get("strategy_inputs", bp))
      inputs = asset_params
    else:
      inputs = _resolve_asset_inputs(db, default_params, source, asset_strategy, asset, is_cross)

    # Load or seed LiveStrategyState
    state = _get_or_create_state(
      db, portfolio.id, base_symbol, asset_strategy.id, asset.batch_id,
    )

    # Check for new bars
    from backend.db.database import ohlcv_router
    _ohlcv_db = ohlcv_router.get_session(asset_tf)
    try:
      latest_ts = _ohlcv_db.query(func.max(OHLCVData.timestamp)).filter(
        OHLCVData.symbol == base_symbol,
        OHLCVData.timeframe == asset_tf,
      ).scalar()
    finally:
      _ohlcv_db.close()

    if latest_ts is None:
      continue
    if state.last_bar_timestamp and latest_ts <= state.last_bar_timestamp:
      continue

    # New bars found — run strategy
    signals = execute_strategy_on_new_bars(
      db=db,
      portfolio_id=portfolio.id,
      symbol=base_symbol,
      strategy_source=asset_strategy.python_source,
      base_tf=asset_tf,
      extra_tfs=asset_extra_tfs,
      state=state,
      side_filter=asset.trade_side,
      inputs=inputs,
    )

    for sig in signals:
      db.add(sig)
    if signals:
      db.commit()
    signal_count += len(signals)

  return signal_count


# ── Resolution helpers ──────────────────────────────────────────
# resolve_timeframe() and resolve_extra_tfs() imported from
# backend.utils.tf_resolver (shared with strategy_executor.py)


def _resolve_strategy(db: Session, source: dict) -> Strategy | None:
  """Resolve strategy row via strategy_id, name, or batch fallback."""
  if source.get("strategy_id"):
    row = db.get(Strategy, source["strategy_id"])
    if row:
      return row
  if source.get("strategy_name"):
    row = db.query(Strategy).filter(
      Strategy.name == source["strategy_name"]
    ).first()
    if row:
      return row
  if source.get("batch_ids"):
    for bid in source["batch_ids"]:
      batch = db.get(BatchBacktestRun, bid)
      if batch and batch.strategy_id:
        row = db.get(Strategy, batch.strategy_id)
        if row:
          return row
  return None


def _resolve_default_params(
  strategy_row: Strategy, source: dict, db: Session,
) -> dict:
  """Build default parameters from strategy definition + config."""
  from backend.db.models import StrategyConfig
  raw = json.loads(strategy_row.parameters or "{}")
  if isinstance(raw, list):
    params = {p["name"]: p.get("default") for p in raw if "name" in p}
  else:
    params = dict(raw)

  config_name = source.get("config_name")
  if config_name and not source.get("is_cross_strat"):
    cfg = db.query(StrategyConfig).filter(
      StrategyConfig.strategy_id == strategy_row.id,
      StrategyConfig.name == config_name,
    ).first()
    if cfg:
      params.update(json.loads(cfg.parameter_values or "{}"))
  return params


def _resolve_asset_inputs(
  db: Session, default_params: dict, source: dict,
  strategy_row: Strategy, asset: PortfolioAsset, is_cross: bool,
) -> dict:
  """Resolve per-asset parameter overrides for cross-strategy portfolios."""
  inputs = dict(default_params)
  if is_cross and asset.batch_id:
    batch = db.get(BatchBacktestRun, asset.batch_id)
    if batch:
      bp = json.loads(batch.parameters or "{}")
      inputs.update(bp.get("strategy_inputs", bp))
  return inputs


def _get_or_create_state(
  db: Session, portfolio_id: int, symbol: str,
  strategy_id: int, batch_id: int | None,
) -> LiveStrategyState:
  """Load existing LiveStrategyState or seed from latest backtest."""
  state = db.query(LiveStrategyState).filter(
    LiveStrategyState.portfolio_id == portfolio_id,
    LiveStrategyState.symbol == symbol,
  ).first()

  if state:
    return state

  # Seed from BacktestRun.strategy_state
  saved_state = "{}"
  ongoing = "[]"
  end_date = None

  if batch_id:
    run = (db.query(BacktestRun)
      .filter(
        BacktestRun.batch_id == batch_id,
        BacktestRun.symbol == symbol,
        BacktestRun.status == "completed",
        BacktestRun.strategy_state.isnot(None),
      )
      .order_by(BacktestRun.end_date.desc())
      .first())

    if run and run.strategy_state:
      saved_state = run.strategy_state
      end_date = run.end_date

      # Ongoing positions (force-closed at batch end)
      ongoing_trades = db.query(BacktestTrade).filter(
        BacktestTrade.run_id == run.id,
        BacktestTrade.exit_reason == "backtest_end",
      ).all()
      if ongoing_trades:
        ongoing = json.dumps([{
          "position_id": t.position_id or f"pos_{t.id}",
          "side": t.side,
          "entry_price": t.entry_price,
          "qty": t.quantity,
          "sl_price": t.sl_price or 0,
          "tp_price": t.tp_price or 0,
          "entry_time": t.entry_time.isoformat() if t.entry_time else None,
        } for t in ongoing_trades])

  state = LiveStrategyState(
    portfolio_id=portfolio_id,
    symbol=symbol,
    strategy_id=strategy_id,
    batch_id=batch_id,
    strategy_state=saved_state,
    ongoing_positions=ongoing,
    last_bar_timestamp=end_date,
    last_bar_index=0,
    bars_processed=0,
  )
  db.add(state)
  db.commit()

  logger.info(
    "Seeded state for %s portfolio %d from batch %s (end=%s)",
    symbol, portfolio_id, batch_id, end_date,
  )
  return state
