"""Candle catch-up service — ad-hoc download for every (symbol, timeframe) pair
used by any active portfolio OR any active batch.

Motivation:
  The always-on `candle_sync.py` process only downloads candles for symbols
  referenced by an active portfolio. Orphan batches (in the 92-batch pool but
  not attached to any portfolio yet) would miss nightly candles and then L1
  would no-op for them.

  This service closes that gap: one callable that collects every (symbol, tf)
  pair that ANY batch or portfolio cares about, downloads deltas since the
  last stored candle (pure append via upsert — guaranteed contiguous), and
  returns a summary. Safe to invoke any time.

Guarantees:
  * Append-only — the upsert in `DataManager.upsert_candles` is keyed on
    (symbol, timeframe, timestamp). Re-running is idempotent.
  * Never raises — per-pair failures are logged and counted, not propagated.
  * Respects Bybit rate limits (same BATCH_SIZE / BATCH_SLEEP as candle_sync).

Not this service's job:
  * Signal scanning (that belongs to candle_sync.py's live loop).
  * Replaying batch trades (that's L1 — called separately).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from backend.data.bybit_downloader import BybitDownloader
from backend.data.data_manager import DataManager, is_derived_tf
from backend.db.database import SessionLocal
from backend.db.models import (
  BacktestRun,
  BatchBacktestRun,
  Portfolio,
  PortfolioAsset,
)
from backend.utils.tf_resolver import canonical_tf

log = logging.getLogger(__name__)

BATCH_SIZE = 100
BATCH_SLEEP = 5
CATCHUP_DAYS_FALLBACK = 90


def _to_bybit_interval(tf: str) -> str:
  """Accept any TF label and return the Bybit V5 interval string."""
  tf_map = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W",
    "1": "1", "3": "3", "5": "5", "15": "15", "30": "30",
    "60": "60", "120": "120", "240": "240", "360": "360", "720": "720",
    "D": "D", "W": "W",
  }
  return tf_map.get(tf, tf.rstrip("m"))


def _utc_ms(dt: datetime) -> int:
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return int(dt.timestamp() * 1000)


def collect_pairs(db) -> set[tuple[str, str]]:
  """Return every (symbol, timeframe) pair referenced by any active batch OR
  any active portfolio. Deduplicated. Derived timeframes excluded.
  """
  pairs: set[tuple[str, str]] = set()

  # From active batches → every BacktestRun inside
  batch_ids = [
    b.id for b in db.query(BatchBacktestRun).filter(
      BatchBacktestRun.is_deleted == False,  # noqa: E712
      BatchBacktestRun.is_archived == False,  # noqa: E712
      BatchBacktestRun.is_compacted == False,  # noqa: E712
    ).all()
  ]
  if batch_ids:
    runs = (
      db.query(BacktestRun.symbol, BacktestRun.timeframe)
      .filter(
        BacktestRun.batch_id.in_(batch_ids),
        BacktestRun.is_deleted == False,  # noqa: E712
      )
      .distinct()
      .all()
    )
    for sym, tf in runs:
      if not sym or not tf:
        continue
      tf_c = canonical_tf(tf)
      if is_derived_tf(tf_c):
        continue
      pairs.add((sym, tf_c))

    # Extra timeframes from the batch metadata
    for b in db.query(BatchBacktestRun).filter(
      BatchBacktestRun.id.in_(batch_ids)
    ).all():
      try:
        extras = json.loads(b.extra_timeframes or "[]")
      except Exception:
        extras = []
      if not extras:
        continue
      batch_runs = (
        db.query(BacktestRun.symbol)
        .filter(
          BacktestRun.batch_id == b.id,
          BacktestRun.is_deleted == False,  # noqa: E712
        )
        .distinct()
        .all()
      )
      for (sym,) in batch_runs:
        for etf in extras:
          tf_c = canonical_tf(etf)
          if is_derived_tf(tf_c):
            continue
          pairs.add((sym, tf_c))

  # From active portfolios → PortfolioAsset + source_strategies
  active_portfolios = db.query(Portfolio).filter(
    Portfolio.is_active == True,  # noqa: E712
    Portfolio.is_deleted == False,  # noqa: E712
  ).all()
  for p in active_portfolios:
    try:
      source = json.loads(p.source_strategies or "{}")
    except Exception:
      source = {}

    tf = source.get("timeframe")
    if not tf and source.get("batch_ids"):
      for bid in source["batch_ids"]:
        b = db.get(BatchBacktestRun, bid)
        if b and b.timeframe:
          tf = b.timeframe
          break
    tf = canonical_tf(tf or "30")

    extra_tfs: set[str] = set()
    for etf in source.get("extra_timeframes", []) or []:
      extra_tfs.add(canonical_tf(etf))
    if source.get("batch_ids"):
      for bid in source["batch_ids"]:
        b = db.get(BatchBacktestRun, bid)
        if b:
          try:
            for etf in json.loads(b.extra_timeframes or "[]"):
              extra_tfs.add(canonical_tf(etf))
          except Exception as _exc:
            log.debug("swallowed: %s", _exc)

    for a in db.query(PortfolioAsset).filter(
      PortfolioAsset.portfolio_id == p.id,
      PortfolioAsset.is_active == True,  # noqa: E712
    ).all():
      base_symbol = a.symbol.split("_")[0] if "_" in a.symbol else a.symbol
      if not is_derived_tf(tf):
        pairs.add((base_symbol, tf))
      for etf in extra_tfs:
        if not is_derived_tf(etf):
          pairs.add((base_symbol, etf))

  return pairs


async def _download_one(
  downloader: BybitDownloader, manager: DataManager,
  symbol: str, tf: str,
) -> int:
  """Download deltas for one pair. Returns count of new candles (0 if up-to-date)."""
  interval = _to_bybit_interval(tf)
  latest = manager.get_latest_timestamp(symbol, tf)

  if latest:
    since_ms = _utc_ms(latest) + 1
  else:
    since_ms = _utc_ms(datetime.now(timezone.utc) - timedelta(days=CATCHUP_DAYS_FALLBACK))

  candles = await downloader.fetch_all_klines(symbol, interval, since_ms=since_ms)
  if not candles:
    return 0
  manager.upsert_candles(symbol, tf, candles)
  return len(candles)


async def catchup_async() -> dict:
  """Async entry point: collect pairs, download deltas, return summary."""
  started = datetime.now(timezone.utc)

  db = SessionLocal()
  try:
    pairs = sorted(collect_pairs(db))
  finally:
    db.close()

  report = {
    "started_at": started.isoformat(),
    "ended_at": None,
    "pairs_total": len(pairs),
    "pairs_updated": 0,
    "pairs_up_to_date": 0,
    "pairs_failed": 0,
    "new_candles": 0,
  }

  if not pairs:
    report["ended_at"] = datetime.now(timezone.utc).isoformat()
    return report

  log.info("candle_catchup: %d unique (symbol, tf) pairs to check", len(pairs))

  downloader = BybitDownloader()
  manager = DataManager()
  try:
    for i in range(0, len(pairs), BATCH_SIZE):
      batch = pairs[i:i + BATCH_SIZE]
      tasks = [_download_one(downloader, manager, s, t) for s, t in batch]
      results = await asyncio.gather(*tasks, return_exceptions=True)

      for j, r in enumerate(results):
        if isinstance(r, Exception):
          report["pairs_failed"] += 1
          s, t = batch[j]
          log.warning("candle_catchup: %s/%s failed: %s", s, t, r)
        elif isinstance(r, int):
          if r > 0:
            report["pairs_updated"] += 1
            report["new_candles"] += r
          else:
            report["pairs_up_to_date"] += 1

      if i + BATCH_SIZE < len(pairs):
        await asyncio.sleep(BATCH_SLEEP)
  finally:
    await downloader.close()

  report["ended_at"] = datetime.now(timezone.utc).isoformat()
  log.info(
    "candle_catchup: done — %d updated, %d up-to-date, %d failed, +%d candles",
    report["pairs_updated"], report["pairs_up_to_date"],
    report["pairs_failed"], report["new_candles"],
  )
  return report


def catchup_candles() -> dict:
  """Synchronous wrapper so the FastAPI catch-up endpoint can call it inline."""
  try:
    loop = asyncio.get_event_loop()
    if loop.is_running():
      # If an event loop is already running (shouldn't happen from a sync
      # FastAPI route, but guard anyway), schedule and wait.
      future = asyncio.run_coroutine_threadsafe(catchup_async(), loop)
      return future.result()
  except RuntimeError:
    pass
  return asyncio.run(catchup_async())
