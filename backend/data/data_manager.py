"""
Data manager: stores and retrieves OHLCV candles from SQLite.
Handles upserts for incremental updates.

Reads/writes go to per-timeframe OHLCV databases via ohlcv_router.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.db.models import OHLCVData
from backend.utils.logger import get_logger
from backend.utils.tf_resolver import canonical_tf as _canonical_tf

logger = get_logger(__name__)

# Timeframes derived from other data (not downloaded directly from Bybit)
DERIVED_TIMEFRAMES = {"3D"}


def is_derived_tf(tf: str) -> bool:
  """Check if a timeframe is derived (built from other data, not Bybit)."""
  return _canonical_tf(tf) in DERIVED_TIMEFRAMES


def _get_ohlcv_session(timeframe: str):
  """Get a session bound to the correct per-TF OHLCV database."""
  from backend.db.database import ohlcv_router
  return ohlcv_router.get_session(timeframe)


class DataManager:
  """Manages OHLCV data persistence."""

  def upsert_candles(
    self, symbol: str, timeframe: str, candles: list[dict]
  ) -> int:
    """
    Insert candles into DB, skipping duplicates (upsert by unique constraint).
    Returns the number of rows inserted/updated.
    """
    timeframe = _canonical_tf(timeframe)
    if not candles:
      return 0

    db = _get_ohlcv_session(timeframe)
    try:
      count = 0
      # Batch insert in chunks of 500
      for i in range(0, len(candles), 500):
        batch = candles[i : i + 500]
        rows = [
          {
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": c["timestamp"],
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "volume": c["volume"],
          }
          for c in batch
        ]
        stmt = sqlite_insert(OHLCVData).values(rows)
        stmt = stmt.on_conflict_do_update(
          index_elements=["symbol", "timeframe", "timestamp"],
          set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
          },
        )
        db.execute(stmt)
        count += len(batch)
      db.commit()
      logger.info("Upserted %d candles for %s/%s", count, symbol, timeframe)
      return count
    except Exception:
      db.rollback()
      raise
    finally:
      db.close()

  def get_latest_timestamp(
    self, symbol: str, timeframe: str
  ) -> Optional[datetime]:
    """Get the most recent candle timestamp for incremental downloads."""
    timeframe = _canonical_tf(timeframe)
    db = _get_ohlcv_session(timeframe)
    try:
      result = db.query(func.max(OHLCVData.timestamp)).filter(
        OHLCVData.symbol == symbol,
        OHLCVData.timeframe == timeframe,
      ).scalar()
      return result
    finally:
      db.close()

  def get_earliest_timestamp(
    self, symbol: str, timeframe: str
  ) -> Optional[datetime]:
    """Get the oldest candle timestamp for backfill boundary."""
    timeframe = _canonical_tf(timeframe)
    db = _get_ohlcv_session(timeframe)
    try:
      result = db.query(func.min(OHLCVData.timestamp)).filter(
        OHLCVData.symbol == symbol,
        OHLCVData.timeframe == timeframe,
      ).scalar()
      return result
    finally:
      db.close()

  def get_candles(
    self,
    symbol: str,
    timeframe: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 10000,
  ) -> list[dict]:
    """Retrieve candles from DB as list of dicts."""
    timeframe = _canonical_tf(timeframe)
    db = _get_ohlcv_session(timeframe)
    try:
      query = (
        db.query(OHLCVData)
        .filter(OHLCVData.symbol == symbol, OHLCVData.timeframe == timeframe)
      )
      if start:
        query = query.filter(OHLCVData.timestamp >= start)
      if end:
        query = query.filter(OHLCVData.timestamp <= end)

      rows = query.order_by(OHLCVData.timestamp).limit(limit).all()
      return [
        {
          "timestamp": r.timestamp.isoformat(),
          "open": r.open,
          "high": r.high,
          "low": r.low,
          "close": r.close,
          "volume": r.volume,
        }
        for r in rows
      ]
    finally:
      db.close()

  def get_stored_symbols(self) -> list[dict]:
    """List all symbol/timeframe combinations stored across all TF databases."""
    from backend.db.database import ohlcv_router

    results = []
    for tf in ohlcv_router.list_timeframes():
      db = ohlcv_router.get_session(tf)
      try:
        rows = (
          db.query(
            OHLCVData.symbol,
            OHLCVData.timeframe,
            func.count(OHLCVData.id).label("count"),
            func.min(OHLCVData.timestamp).label("earliest"),
            func.max(OHLCVData.timestamp).label("latest"),
          )
          .group_by(OHLCVData.symbol, OHLCVData.timeframe)
          .all()
        )
        for r in rows:
          results.append({
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "candle_count": r.count,
            "earliest": r.earliest.isoformat() if r.earliest else None,
            "latest": r.latest.isoformat() if r.latest else None,
          })
      finally:
        db.close()
    return results
