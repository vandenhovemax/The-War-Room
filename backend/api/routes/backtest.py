"""Backtest routes — run backtests and view results."""
import json
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api.rate_limit import rate_limit
from backend.backtester.trade_storage import store_trades
from backend.constants import COMMISSION_PCT
from backend.db.database import get_db
from backend.db.models import BacktestRun, BacktestTrade, OHLCVData, Strategy, StrategyConfig
from backend.utils.logger import get_logger
from backend.utils.sanitize import sanitize_for_json

logger = get_logger(__name__)
router = APIRouter()

# In-memory progress tracking
_backtest_progress: dict[int, dict] = {}


def _extract_position_key(exit_reason: str | None) -> str | None:
  """Extract position ID from exit_reason like 'TP1_T3485' → 'T3485'.

  Exit reasons follow the pattern '{exit_type}_{position_id}', e.g.:
    TP1_T3485, TSL_T3485, SL_T3485 → all return 'T3485'
  Returns None for plain labels like 'signal', 'reverse', etc.
  """
  if not exit_reason or "_" not in exit_reason:
    return None
  parts = exit_reason.split("_", 1)
  if len(parts) == 2 and parts[1]:
    return parts[1]
  return None


def _build_entry_price_cache(
  db: Session, trades: list[BacktestTrade], run: BacktestRun,
) -> dict:
  """Build {entry_time: open_price} for trades with missing entry_price.

  Old backtest data may have entry_price=0. We recover the price by
  looking up the OHLCV open at the trade's entry_time.
  """
  missing_times = set()
  for t in trades:
    if not t.entry_price and t.entry_time:
      missing_times.add(t.entry_time)
  if not missing_times:
    return {}

  from backend.db.database import ohlcv_router
  ohlcv_db = ohlcv_router.get_session(run.timeframe)
  try:
    rows = (
      ohlcv_db.query(OHLCVData.timestamp, OHLCVData.open)
      .filter(
        OHLCVData.symbol == run.symbol,
        OHLCVData.timeframe == run.timeframe,
        OHLCVData.timestamp.in_(list(missing_times)),
      )
      .all()
    )
    return {r.timestamp: r.open for r in rows}
  finally:
    ohlcv_db.close()


import re

# Params controlled at backtester level — strip from strategy inputs so strategy
# uses its baked-in defaults. Engine config controls these independently.
# $ anchor: match exact param names (not prefixes).
# Max Concurrent / Pyramiding are NOT stripped — they pass through and are used
# to sync engine max_pyramiding.
_BACKTESTER_LEVEL_RE = re.compile(
  r"^(Trade Direction|Risk Mode|Risk Per Trade|Risk %|Risk Amount"
  r"|Position Sizing|Martingale|Anti.?Martingale"
  r"|Volatility Filter|Volatility ATR|Volatility Multiplier"
  r"|ATR Ceiling|ATR Volatility|ATR Filter|Vol Filter)",
  re.IGNORECASE,
)


def _strip_backtester_params(inputs: dict) -> dict:
  """Remove backtester-level keys — they are controlled by engine config."""
  return {k: v for k, v in inputs.items() if not _BACKTESTER_LEVEL_RE.match(k)}


class BacktestRequest(BaseModel):
  strategy_id: int
  symbol: str
  timeframe: str
  direction_filter: str = "both"  # "long" | "short" | "both" | "long_plus_short"
  initial_capital: float = 10000.0
  commission_pct: float = COMMISSION_PCT
  slippage_mode: str = "pct"     # "pct" | "pips" | "risk_pct"
  slippage_value: float = 0.0    # 0 = use default
  leverage: int = 1
  min_avg_daily_volume: float = 0.0
  max_avg_daily_volume: float = 0.0  # upper ADV cap; 0 = no limit
  config_id: int | None = None
  strategy_inputs: dict | None = None
  max_pyramiding: int = 1
  warmup_bars: int = 0
  extra_timeframes: list[str] | None = None
  risk_pct: float = 0.0
  risk_mode: str = "pct"         # "pct" | "fixed"
  risk_value: float = 0.0        # 0 = disabled
  # Martingale
  martingale_enabled: bool = False
  martingale_type: str = "anti"   # "martingale" | "anti"
  martingale_multiplier: float = 2.0
  martingale_max_streak: int = 3
  martingale_scope: Literal["asset", "portfolio", "asset_batch", "batch"] = "asset"
  # Volatility filter
  vol_filter_enabled: bool = False
  vol_filter_atr_ceiling: float = 0.0
  vol_filter_atr_length: int = 14
  start_date: str | None = None  # ISO date string e.g. "2024-01-01"
  end_date: str | None = None    # ISO date string e.g. "2025-12-31"
  process_orders_on_close: bool = False  # True = fill entries at bar's close
  enforce_bybit_limits: bool = False     # True = fetch & enforce min/max qty + lot_size


@router.get("/runs")
def list_runs(db: Session = Depends(get_db), single_only: bool = False):
  _ensure_config_cache(db)
  q = db.query(BacktestRun).filter(BacktestRun.is_deleted != True)
  if single_only:
    q = q.filter(BacktestRun.batch_id == None)  # noqa: E711
  runs = q.order_by(BacktestRun.created_at.desc()).limit(500).all()
  return [_run_to_dict(r) for r in runs]


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
  r = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
  if not r:
    raise HTTPException(status_code=404, detail="Run not found")
  result = _run_to_dict(r)
  result["metrics"] = sanitize_for_json(json.loads(r.metrics)) if r.metrics else {}
  result["parameters"] = json.loads(r.parameters) if r.parameters else {}
  return result


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, db: Session = Depends(get_db)):
  r = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
  if not r:
    raise HTTPException(status_code=404, detail="Run not found")
  from datetime import datetime, timezone
  r.is_deleted = True
  r.deleted_at = datetime.now(timezone.utc)
  db.commit()
  _backtest_progress.pop(run_id, None)
  return {"deleted": True, "id": run_id}


@router.get("/runs/{run_id}/trades")
def get_run_trades(run_id: int, db: Session = Depends(get_db)):
  """Return trades grouped by position. Each position has entry info + exit fills."""
  run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
  if not run:
    raise HTTPException(status_code=404, detail="Run not found")

  raw_trades = (
    db.query(BacktestTrade)
    .filter(BacktestTrade.run_id == run_id)
    .order_by(BacktestTrade.trade_num)
    .all()
  )

  # Build lookup for missing entry prices from OHLCV data
  price_cache = _build_entry_price_cache(db, raw_trades, run)

  # Group by position_id, fallback to parsing exit_reason (e.g. "TP1_T3485" → "T3485")
  from collections import OrderedDict
  positions: OrderedDict[str, list[BacktestTrade]] = OrderedDict()
  for t in raw_trades:
    key = t.position_id or _extract_position_key(t.exit_reason) or f"_trade_{t.trade_num}"
    positions.setdefault(key, []).append(t)

  result = []
  pos_num = 0
  for pos_id, fills in positions.items():
    pos_num += 1
    first = fills[0]
    total_pnl = sum(f.net_profit for f in fills)
    total_qty = sum(f.quantity for f in fills)
    # Use stored entry_price, or recover from OHLCV open price
    entry_price = first.entry_price or price_cache.get(first.entry_time, 0)

    exits = []
    for f in fills:
      exits.append({
        "entry_time": f.entry_time.isoformat() if f.entry_time else None,
        "exit_time": f.exit_time.isoformat() if f.exit_time else None,
        "exit_price": f.exit_price,
        "quantity": f.quantity,
        "net_profit": f.net_profit,
        "exit_reason": f.exit_reason,
      })

    result.append({
      "trade_num": pos_num,
      "position_id": pos_id,
      "symbol": first.symbol,
      "side": first.side,
      "entry_time": first.entry_time.isoformat() if first.entry_time else None,
      "entry_price": entry_price,
      "sl_price": first.sl_price,
      "tp_price": first.tp_price,
      "total_qty": total_qty,
      "net_profit": total_pnl,
      "net_profit_pct": sum(f.net_profit_pct for f in fills),
      "exit_count": len(fills),
      "exits": exits,
    })

  return result


@router.get("/runs/{run_id}/equity")
def get_run_equity(run_id: int, db: Session = Depends(get_db)):
  r = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
  if not r:
    raise HTTPException(status_code=404, detail="Run not found")
  metrics = json.loads(r.metrics) if r.metrics else {}
  return {"equity_curve": metrics.get("equity_curve", [])}


@router.post("/run")
def start_backtest(
  request: Request,
  req: BacktestRequest,
  background_tasks: BackgroundTasks,
  db: Session = Depends(get_db),
):
  """Start a backtest run in the background."""
  rate_limit(request, max_requests=20, window_seconds=60)
  strategy = db.query(Strategy).filter(Strategy.id == req.strategy_id).first()
  if not strategy:
    raise HTTPException(status_code=404, detail="Strategy not found")
  if not strategy.python_source:
    raise HTTPException(status_code=400, detail="Strategy has no transpiled Python code")

  # Check data exists (query per-TF OHLCV database)
  from backend.db.database import ohlcv_router
  _bt_ohlcv = ohlcv_router.get_session(req.timeframe)
  try:
    candle_count = (
      _bt_ohlcv.query(OHLCVData)
      .filter(OHLCVData.symbol == req.symbol, OHLCVData.timeframe == req.timeframe)
      .count()
    )
  finally:
    _bt_ohlcv.close()
  if candle_count == 0:
    raise HTTPException(
      status_code=400,
      detail=f"No data for {req.symbol} {req.timeframe}. Download data first.",
    )

  # Check ADV filter (min and/or max)
  if req.min_avg_daily_volume > 0 or req.max_avg_daily_volume > 0:
    _adv_ohlcv = ohlcv_router.get_session(req.timeframe)
    try:
      stats = (
        _adv_ohlcv.query(
          func.sum(OHLCVData.close * OHLCVData.volume).label("turnover"),
          func.min(OHLCVData.timestamp).label("earliest"),
          func.max(OHLCVData.timestamp).label("latest"),
        )
        .filter(OHLCVData.symbol == req.symbol, OHLCVData.timeframe == req.timeframe)
        .first()
      )
    finally:
      _adv_ohlcv.close()
    adv = 0.0
    if stats and stats.turnover and stats.earliest and stats.latest:
      earliest = stats.earliest
      latest = stats.latest
      if isinstance(earliest, str):
        earliest = datetime.fromisoformat(earliest)
      if isinstance(latest, str):
        latest = datetime.fromisoformat(latest)
      days = (latest - earliest).days + 1
      adv = float(stats.turnover) / max(days, 1)
    if req.min_avg_daily_volume > 0 and adv < req.min_avg_daily_volume:
      raise HTTPException(
        status_code=400,
        detail=f"{req.symbol} ADV is ${adv:,.0f} — below minimum ${req.min_avg_daily_volume:,.0f}",
      )
    if req.max_avg_daily_volume > 0 and adv > req.max_avg_daily_volume:
      raise HTTPException(
        status_code=400,
        detail=f"{req.symbol} ADV is ${adv:,.0f} — above maximum ${req.max_avg_daily_volume:,.0f}",
      )

  # Resolve strategy parameter overrides
  inputs: dict = {}
  if req.config_id:
    config = db.query(StrategyConfig).filter(
      StrategyConfig.id == req.config_id,
      StrategyConfig.strategy_id == req.strategy_id,
    ).first()
    if not config:
      raise HTTPException(status_code=404, detail="Config not found")
    inputs = json.loads(config.parameter_values) if config.parameter_values else {}
  elif req.strategy_inputs:
    inputs = req.strategy_inputs

  # Extract strategy's risk reference from defaults BEFORE stripping.
  # This lets the engine scale qty by ratio for exact matching.
  strat_defaults = {}
  if strategy.parameters:
    for p in json.loads(strategy.parameters):
      strat_defaults[p["name"]] = p["default"]

  # Effective inputs merges the defaults with any UI or config overrides
  effective_inputs = {**strat_defaults, **inputs}

  strat_risk_mode = "fixed"
  strat_risk_value = 0.0
  # Detect strategy's risk mode from its effective run inputs
  rm = effective_inputs.get("Risk Mode", "")
  if "%" in str(rm) or "equity" in str(rm).lower() or "pct" in str(rm).lower():
    strat_risk_mode = "pct"
    strat_risk_value = float(effective_inputs.get("Risk % of Equity", 0) or 0)
  else:
    strat_risk_mode = "fixed"
    strat_risk_value = float(effective_inputs.get("Risk Amount ($)", 0) or 0)

  # Strip backtester-level params from direct UI inputs so the strategy
  # uses its baked-in defaults. DON'T strip when using a daughter config —
  # config values are intentional overrides that should pass through.
  if not req.config_id:
    inputs = _strip_backtester_params(inputs)

  # Extract pyramiding from strategy inputs if present, else use UI value
  max_pyramiding = req.max_pyramiding
  mct = inputs.get("Max Concurrent Trades")
  if mct is not None:
    max_pyramiding = int(mct)

  # Auto-detect extra timeframes needed by strategy config (RSI TF, MA Filter TF)
  extra_tfs = set(req.extra_timeframes or [])
  _TF_MAP = {"1h": "60", "4h": "240", "12h": "720", "1D": "D", "3D": "3D"}
  for param_key in ("RSI Timeframe", "MA Filter Timeframe"):
    tf_label = inputs.get(param_key)
    if tf_label and tf_label != "Current" and tf_label in _TF_MAP:
      extra_tfs.add(_TF_MAP[tf_label])
  resolved_extra_tfs = list(extra_tfs) if extra_tfs else None

  # Create run record
  from datetime import datetime, timezone
  run = BacktestRun(
    strategy_id=req.strategy_id,
    symbol=req.symbol,
    timeframe=req.timeframe,
    start_date=datetime.now(timezone.utc),
    end_date=datetime.now(timezone.utc),
    parameters=json.dumps({
      "initial_capital": req.initial_capital,
      "commission_pct": req.commission_pct,
      "leverage": req.leverage,
      "max_pyramiding": max_pyramiding,
      "config_id": req.config_id,
      "strategy_inputs": inputs,
      "direction_filter": req.direction_filter,
      "risk_mode": req.risk_mode,
      "risk_value": req.risk_value,
      "martingale_enabled": req.martingale_enabled,
      "martingale_type": req.martingale_type,
      "martingale_multiplier": req.martingale_multiplier,
      "martingale_max_streak": req.martingale_max_streak,
      "martingale_scope": req.martingale_scope,
      "vol_filter_enabled": req.vol_filter_enabled,
      "vol_filter_atr_ceiling": req.vol_filter_atr_ceiling,
      "vol_filter_atr_length": req.vol_filter_atr_length,
      "process_orders_on_close": req.process_orders_on_close,
    }),
    status="running",
  )
  db.add(run)
  db.commit()
  db.refresh(run)

  _backtest_progress[run.id] = {"status": "running", "bars_done": 0, "total_bars": candle_count}

  background_tasks.add_task(
    _run_backtest, run.id, strategy.python_source, req.symbol,
    req.timeframe, req.direction_filter, req.initial_capital,
    req.commission_pct, req.leverage,
    inputs, max_pyramiding, req.warmup_bars, resolved_extra_tfs,
    req.risk_pct, req.start_date, req.end_date,
    req.slippage_mode, req.slippage_value,
    req.risk_mode, req.risk_value,
    req.martingale_enabled, req.martingale_type,
    req.martingale_multiplier, req.martingale_max_streak, req.martingale_scope,
    req.vol_filter_enabled, req.vol_filter_atr_ceiling, req.vol_filter_atr_length,
    strat_risk_mode, strat_risk_value,
    req.process_orders_on_close,
    req.enforce_bybit_limits,
  )

  return {"run_id": run.id, "status": "running", "candles": candle_count}


@router.get("/progress/{run_id}")
def backtest_progress(run_id: int):
  return _backtest_progress.get(run_id, {"status": "unknown"})


@router.get("/status")
def backtest_status():
  running = sum(1 for p in _backtest_progress.values() if p.get("status") == "running")
  return {"running": running > 0, "queue_size": running}


def _load_candles_df(db: Session, symbol: str, timeframe: str) -> "pd.DataFrame":
  """Load candles from DB — delegates to shared candle_loader."""
  from backend.utils.candle_loader import load_candles_df
  return load_candles_df(db, symbol, timeframe)


def _to_nyt(df: "pd.DataFrame") -> "pd.DataFrame":
  """Convert UTC → NYT — delegates to shared candle_loader."""
  from backend.utils.candle_loader import to_nyt
  return to_nyt(df)


def _run_backtest(
  run_id: int, python_source: str, symbol: str, timeframe: str,
  direction_filter: str = "both",
  initial_capital: float = 10000.0, commission_pct: float = COMMISSION_PCT,
  leverage: int = 1,
  inputs: dict | None = None, max_pyramiding: int = 1,
  warmup_bars: int = 0, extra_timeframes: list[str] | None = None,
  risk_pct: float = 0.0,
  start_date: str | None = None, end_date: str | None = None,
  slippage_mode: str = "pct", slippage_value: float = 0.0,
  risk_mode: str = "pct", risk_value: float = 0.0,
  martingale_enabled: bool = False, martingale_type: str = "anti",
  martingale_multiplier: float = 2.0, martingale_max_streak: int = 3,
  martingale_scope: str = "asset",
  vol_filter_enabled: bool = False, vol_filter_atr_ceiling: float = 0.0,
  vol_filter_atr_length: int = 14,
  strategy_risk_mode: str = "fixed", strategy_risk_value: float = 0.0,
  process_orders_on_close: bool = False,
  enforce_bybit_limits: bool = False,
):
  """Execute a backtest in background thread."""
  import pandas as pd

  from backend.backtester.engine import BacktestConfig, BacktestEngine
  from backend.db.database import SessionLocal

  db = SessionLocal()
  try:
    df = _load_candles_df(db, symbol, timeframe)

    # Convert from UTC to New York Time — all strategy time logic uses NYT
    df = _to_nyt(df)

    # Apply date range filter (dates interpreted as NYT)
    if start_date:
      df = df[df.index >= pd.Timestamp(start_date)]
    if end_date:
      df = df[df.index <= pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)]
    if df.empty:
      raise ValueError(f"No data for {symbol} {timeframe} in the selected date range")

    # Load extra timeframe data (also convert to NYT)
    extra_tf_dfs: dict[str, "pd.DataFrame"] = {}
    if extra_timeframes:
      for tf in extra_timeframes:
        try:
          extra_tf_dfs[tf] = _to_nyt(_load_candles_df(db, symbol, tf))
        except ValueError:
          logger.warning("No %s data for %s — skipping", tf, symbol)

    from backend.utils.safe_exec import safe_exec
    strategy_cls = safe_exec(python_source, label=f"backtest_{symbol}")
    if not strategy_cls:
      raise ValueError("Transpiled code does not define TranspiledStrategy")

    # Auto-detect process_orders_on_close from transpiled strategy if not set by frontend
    if not process_orders_on_close:
      if getattr(strategy_cls, 'process_orders_on_close', False) is True:
        process_orders_on_close = True

    # Handle "long_plus_short" via dual-pass engine
    if direction_filter == "long_plus_short":
      from backend.backtester.dual_pass import run_dual_pass
      result = run_dual_pass(
        python_source, df, symbol, timeframe, inputs,
        extra_tf_dfs, initial_capital, commission_pct,
        slippage_mode, slippage_value, leverage,
        max_pyramiding, warmup_bars, risk_pct,
        risk_mode=risk_mode, risk_value=risk_value,
        martingale_enabled=martingale_enabled, martingale_type=martingale_type,
        martingale_multiplier=martingale_multiplier, martingale_max_streak=martingale_max_streak,
        vol_filter_enabled=vol_filter_enabled, vol_filter_atr_ceiling=vol_filter_atr_ceiling,
        vol_filter_atr_length=vol_filter_atr_length,
        strategy_risk_mode=strategy_risk_mode, strategy_risk_value=strategy_risk_value,
        process_orders_on_close=process_orders_on_close,
      )
      run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
      if run:
        run.status = "completed"
        run.start_date = df.index[0]
        run.end_date = df.index[-1]
        run.metrics = json.dumps(sanitize_for_json(dict(result.metrics)))
        store_trades(db, run_id, symbol, result.trades, initial_capital)
        db.commit()
      _backtest_progress[run_id] = {
        "status": "completed",
        "bars_done": result.bars_processed,
        "total_bars": result.bars_processed,
        "metrics": result.metrics,
      }
      logger.info("Backtest %d (dual-pass) completed: %d trades", run_id, result.metrics.get("total_trades", 0))
      return

    # Fetch Bybit instrument specs if enforcement is enabled
    bybit_lot_size = 0.0
    bybit_min_qty = 0.0
    bybit_max_qty = 0.0
    if enforce_bybit_limits:
      try:
        import asyncio
        from backend.data.bybit_downloader import BybitDownloader
        async def _fetch_specs():
          dl = BybitDownloader()
          try:
            return await dl.get_all_instrument_specs()
          finally:
            await dl.close()
        specs = asyncio.run(_fetch_specs())
        sym_spec = specs.get(symbol, {})
        bybit_lot_size = sym_spec.get("lot_size", 0.0)
        bybit_min_qty = sym_spec.get("min_qty", 0.0)
        bybit_max_qty = sym_spec.get("max_qty", 0.0)
      except Exception as exc:
        logger.warning("Failed to fetch Bybit specs for %s: %s", symbol, exc)

    config = BacktestConfig(
      initial_capital=initial_capital, commission_pct=commission_pct,
      slippage_mode=slippage_mode, slippage_value=slippage_value,
      leverage=leverage, direction_filter=direction_filter,
      max_pyramiding=max_pyramiding,
      warmup_bars=warmup_bars, risk_pct=risk_pct,
      risk_mode=risk_mode, risk_value=risk_value,
      strategy_risk_mode=strategy_risk_mode, strategy_risk_value=strategy_risk_value,
      martingale_enabled=martingale_enabled, martingale_type=martingale_type,
      martingale_multiplier=martingale_multiplier,
      martingale_max_streak=martingale_max_streak,
      martingale_scope=martingale_scope,
      vol_filter_enabled=vol_filter_enabled, vol_filter_atr_ceiling=vol_filter_atr_ceiling,
      vol_filter_atr_length=vol_filter_atr_length,
      process_orders_on_close=process_orders_on_close,
      bybit_lot_size=bybit_lot_size, bybit_min_qty=bybit_min_qty,
      bybit_max_qty=bybit_max_qty,
    )
    engine = BacktestEngine(config)
    result = engine.run(
      strategy_cls(), df, symbol=symbol, timeframe=timeframe,
      inputs=inputs, extra_timeframes=extra_tf_dfs,
    )

    run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
    if run:
      run.status = "completed"
      run.start_date = df.index[0]
      run.end_date = df.index[-1]
      run.metrics = json.dumps(sanitize_for_json(dict(result.metrics)))
      store_trades(db, run_id, symbol, result.trades, initial_capital)
      db.commit()

    _backtest_progress[run_id] = {
      "status": "completed",
      "bars_done": result.bars_processed,
      "total_bars": result.bars_processed,
      "metrics": result.metrics,
    }
    logger.info("Backtest %d completed: %d trades", run_id, result.metrics.get("total_trades", 0))

  except Exception as e:
    logger.error("Backtest %d failed: %s", run_id, e)
    logger.error(traceback.format_exc())
    run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
    if run:
      run.status = "error"
      run.error_message = str(e)
      db.commit()
    _backtest_progress[run_id] = {"status": "error", "error": str(e)}
  finally:
    db.close()


_config_name_cache: dict[int, str] = {}


def _ensure_config_cache(db: Session):
  """Load config names into module cache."""
  _config_name_cache.clear()
  for cfg in db.query(StrategyConfig.id, StrategyConfig.name).all():
    _config_name_cache[cfg.id] = cfg.name


def _run_to_dict(r: BacktestRun) -> dict:
  d = {
    "id": r.id,
    "strategy_id": r.strategy_id,
    "batch_id": r.batch_id,
    "symbol": r.symbol,
    "timeframe": r.timeframe,
    "status": r.status,
    "start_date": r.start_date.isoformat() if r.start_date else None,
    "end_date": r.end_date.isoformat() if r.end_date else None,
    "error_message": r.error_message,
    "created_at": (r.created_at.isoformat() + "Z") if r.created_at else None,
  }
  # Include config name from parameters.config_id
  if r.parameters:
    try:
      p = json.loads(r.parameters)
      cid = p.get("config_id")
      if cid:
        d["config_name"] = _config_name_cache.get(cid)
    except (json.JSONDecodeError, TypeError):
      pass
  # Include summary metrics for list views (single backtests)
  if r.metrics:
    try:
      m = json.loads(r.metrics)
      d["net_profit"] = m.get("net_profit")
      d["total_trades"] = m.get("total_trades")
      d["win_rate"] = m.get("win_rate")
      d["profit_factor"] = m.get("profit_factor")
    except (json.JSONDecodeError, TypeError):
      pass
  return d
