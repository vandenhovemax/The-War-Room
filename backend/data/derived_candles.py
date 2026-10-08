"""Derived candle timeframes — built from existing OHLCV data, not fetched from Bybit.

Currently supports:
  - 3D (3-day) candles: aggregated from 1D data, aligned from Jan 1st each year.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.data.data_manager import DataManager
from backend.db.database import ohlcv_router
from backend.db.models import OHLCVData
from backend.utils.logger import get_logger

logger = get_logger(__name__)


def rebuild_3d_candles(symbol: str) -> int:
  """Build 3-day candles from daily data for one symbol.

  Alignment: 3D periods start from Jan 1st of each year.
  Day 0-2 → first 3D bar, Day 3-5 → second, etc.
  Last bar of each year may have 1-2 days (365 mod 3 = 2).

  Returns count of 3D candles written.
  """
  # Load all 1D candles sorted by timestamp
  db = ohlcv_router.get_session("D")
  try:
    rows = (
      db.query(OHLCVData)
      .filter(OHLCVData.symbol == symbol, OHLCVData.timeframe == "D")
      .order_by(OHLCVData.timestamp)
      .all()
    )
  finally:
    db.close()

  if not rows:
    return 0

  # Group daily bars into 3-day periods aligned from Jan 1
  groups: dict[tuple[int, int], list] = {}  # (year, group_idx) → [rows]
  for row in rows:
    ts = row.timestamp
    if ts.tzinfo is None:
      ts = ts.replace(tzinfo=timezone.utc)
    year_start = datetime(ts.year, 1, 1, tzinfo=timezone.utc)
    day_of_year = (ts - year_start).days  # 0-indexed
    group_idx = day_of_year // 3
    key = (ts.year, group_idx)
    if key not in groups:
      groups[key] = []
    groups[key].append(row)

  # Aggregate each group into one 3D candle
  candles_3d = []
  for (_year, _gidx), bars in sorted(groups.items()):
    ts0 = bars[0].timestamp
    if ts0.tzinfo is None:
      ts0 = ts0.replace(tzinfo=timezone.utc)
    candles_3d.append({
      "timestamp": ts0,  # first day's timestamp
      "open": bars[0].open,
      "high": max(b.high for b in bars),
      "low": min(b.low for b in bars),
      "close": bars[-1].close,
      "volume": sum(b.volume for b in bars),
    })

  if not candles_3d:
    return 0

  manager = DataManager()
  count = manager.upsert_candles(symbol, "3D", candles_3d)
  logger.info("Built %d 3D candles for %s from %d daily bars", count, symbol, len(rows))
  return count


def rebuild_3d_all_symbols() -> dict[str, int]:
  """Rebuild 3D candles for ALL symbols that have daily data.

  Returns dict of {symbol: candle_count}.
  """
  db = ohlcv_router.get_session("D")
  try:
    symbols = [
      r[0] for r in db.query(OHLCVData.symbol).distinct().all()
    ]
  finally:
    db.close()

  results: dict[str, int] = {}
  for sym in sorted(symbols):
    try:
      results[sym] = rebuild_3d_candles(sym)
    except Exception as e:
      logger.warning("Failed to build 3D for %s: %s", sym, e)
      results[sym] = 0
  return results
