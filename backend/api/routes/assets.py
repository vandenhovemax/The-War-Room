"""
Asset structural metrics endpoints.

Computes OHLCV-based structural quality metrics for all symbols
in the database, independent of any strategy or backtest.
"""
import time
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

# Persistent in-memory cache: {timeframe: (computed_timestamp, results)}
# No TTL — results stay until explicitly refreshed (asset structure only
# changes when new candles are downloaded, which the user does manually).
_cache: dict[str, tuple[float, list[dict]]] = {}


@router.get("/available-timeframes")
def available_timeframes():
  """List timeframes that have OHLCV data, with symbol counts."""
  from backend.db.database import ohlcv_router
  results = []
  for tf in ohlcv_router.list_timeframes():
    tf_db = ohlcv_router.get_session(tf)
    try:
      row = tf_db.execute(
        text(
          "SELECT COUNT(DISTINCT symbol) as symbol_count "
          "FROM ohlcv_data WHERE timeframe = :tf"
        ),
        {"tf": tf},
      ).fetchone()
      if row and row[0] > 0:
        results.append({"timeframe": tf, "symbol_count": row[0]})
    finally:
      tf_db.close()
  results.sort(key=lambda r: r["symbol_count"], reverse=True)
  return results


@router.get("/structural-metrics")
def structural_metrics(
  timeframe: str = Query(..., description="Timeframe to compute metrics for"),
  db: Session = Depends(get_db),
):
  """Compute OHLCV structural metrics for all symbols with data for a TF.

  Results are cached permanently until manually refreshed via
  POST /api/assets/structural-metrics/refresh. Asset structure only
  changes when new candles are downloaded.
  """
  import numpy as np

  # Return cached results if available (no expiry — refresh manually)
  if timeframe in _cache:
    ts, cached = _cache[timeframe]
    return {"timeframe": timeframe, "results": cached, "cached_at": ts}

  now = time.time()

  from backend.db.database import ohlcv_router

  # Get all symbols for this timeframe from per-TF database
  tf_db = ohlcv_router.get_session(timeframe)
  try:
    symbols = [
      r[0] for r in tf_db.execute(
        text(
          "SELECT DISTINCT symbol FROM ohlcv_data "
          "WHERE timeframe = :tf"
        ),
        {"tf": timeframe},
      ).fetchall()
    ]
  finally:
    tf_db.close()

  if not symbols:
    return {"timeframe": timeframe, "results": []}

  from backend.api.routes.batch_backtest import (
    _compute_structural_metrics_from_prices,
  )

  results = []
  tf_db2 = ohlcv_router.get_session(timeframe)
  try:
    for symbol in sorted(symbols):
      # Load close prices for this symbol+tf
      rows = tf_db2.execute(
        text(
          "SELECT close FROM ohlcv_data "
          "WHERE symbol = :sym AND timeframe = :tf "
          "ORDER BY timestamp"
        ),
        {"sym": symbol, "tf": timeframe},
      ).fetchall()

      if len(rows) < 50:
        continue

      close_prices = np.array([r[0] for r in rows], dtype=float)
      metrics = _compute_structural_metrics_from_prices(close_prices)
      if metrics:
        metrics["last_close"] = float(close_prices[-1])
        results.append({"symbol": symbol, **metrics})
  finally:
    tf_db2.close()

  # Cache the result
  _cache[timeframe] = (now, results)

  return {"timeframe": timeframe, "results": results, "cached_at": now}


@router.post("/structural-metrics/refresh")
def refresh_structural_metrics(
  timeframe: str = Query(..., description="Timeframe to refresh"),
):
  """Clear the cached structural metrics for a timeframe.

  Next GET will recompute from current OHLCV data.
  """
  if timeframe in _cache:
    del _cache[timeframe]
    return {"status": "cleared", "timeframe": timeframe}
  return {"status": "no_cache", "timeframe": timeframe}


# In-memory cache for instrument specs (rarely changes)
_instrument_specs_cache: dict | None = None
_instrument_specs_ts: float = 0.0


@router.get("/instrument-specs")
def get_instrument_specs():
  """Return Bybit instrument specs (min_qty, max_qty, lot_size) for all USDT perps.

  Cached in memory — specs only change when Bybit updates instrument params.
  """
  global _instrument_specs_cache, _instrument_specs_ts

  # Return cache if fresh (< 1 hour)
  if _instrument_specs_cache and (time.time() - _instrument_specs_ts) < 3600:
    return {"specs": _instrument_specs_cache}

  import asyncio
  from backend.data.bybit_downloader import BybitDownloader

  async def _fetch():
    dl = BybitDownloader()
    try:
      return await dl.get_all_instrument_specs()
    finally:
      await dl.close()

  try:
    specs = asyncio.run(_fetch())
  except Exception as exc:
    logger.error("Failed to fetch instrument specs: %s", exc)
    if _instrument_specs_cache:
      return {"specs": _instrument_specs_cache}
    return {"specs": {}, "error": str(exc)}

  _instrument_specs_cache = specs
  _instrument_specs_ts = time.time()
  return {"specs": specs}
