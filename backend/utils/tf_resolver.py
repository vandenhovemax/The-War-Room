"""Shared timeframe resolution utilities.

Single source of truth for timeframe normalization (canonical_tf) and
resolution from portfolio source_strategies JSON with batch fallback.
"""
import json

from sqlalchemy.orm import Session

from backend.db.models import BatchBacktestRun


def canonical_tf(tf: str) -> str:
  """Normalize any timeframe format to Bybit canonical.

  Converts display formats like '5m','30m','1h','4h','1d','1w'
  to Bybit API format: '5','30','60','240','D','W'.
  Already-canonical values pass through unchanged.
  """
  _map = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W",
  }
  result = _map.get(tf)
  if result:
    return result
  # Handle bare numeric+m like "60m" -> "60"
  if tf.endswith("m") and tf[:-1].isdigit():
    return tf[:-1]
  return tf


def resolve_timeframe(db: Session, source_data: dict) -> str:
  """Resolve base timeframe from source_strategies JSON with batch fallback.

  Checks source_data["timeframe"] first, then falls back to the first
  batch's timeframe. Returns "30" as ultimate default.
  """
  tf = source_data.get("timeframe")
  if not tf and source_data.get("batch_ids"):
    for bid in source_data["batch_ids"]:
      batch = db.get(BatchBacktestRun, bid)
      if batch and batch.timeframe:
        return batch.timeframe
  return tf or "30"


def resolve_extra_tfs(db: Session, source_data: dict) -> list[str]:
  """Resolve extra timeframes from source_strategies JSON + batches.

  Merges extra_timeframes from source_data with extra_timeframes stored
  on each referenced BatchBacktestRun. Returns sorted, deduplicated list.
  """
  extra: set[str] = set()
  for etf in source_data.get("extra_timeframes", []):
    extra.add(etf)
  if source_data.get("batch_ids"):
    for bid in source_data["batch_ids"]:
      batch = db.get(BatchBacktestRun, bid)
      if batch:
        for etf in json.loads(batch.extra_timeframes or "[]"):
          extra.add(etf)
  return sorted(extra)
