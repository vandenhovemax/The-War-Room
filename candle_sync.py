"""Candle sync + signal scanner.

Keeps candle data fresh for all active portfolios, then immediately scans
for trading signals. Wakes up at bar close times (not fixed interval),
probes Bybit until the new candle is available, downloads in parallel,
and runs the signal scanner.

Launch: python candle_sync.py   (from the project root)
"""
import asyncio
import json
import math
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from backend.config import get_settings
from backend.db.database import SessionLocal
from backend.db.models import Portfolio, PortfolioAsset, BatchBacktestRun
from backend.data.data_manager import DataManager
from backend.data.bybit_downloader import BybitDownloader
from backend.scanner.scanner_core import run_scan_cycle
from backend.utils import heartbeats
from backend.utils.logger import get_logger

logger = get_logger("candle_sync")

CATCHUP_DAYS = 90
PROBE_INTERVAL = 2    # seconds between Bybit probes (fast for live trading)
PROBE_TIMEOUT = 60    # max seconds to probe before giving up
PROBE_SYMBOL = "BTCUSDT"
BATCH_SIZE = 100      # download batch size (Bybit limit: 120 req/5s)
BATCH_SLEEP = 5       # seconds to wait between download batches
BAR_CLOSE_OFFSET = 2  # seconds to wait after bar close before fetching


# ── Timeframe helpers ────────────────────────────────────────────


# Timeframe → minutes (for bar close calculation)
TF_MINUTES = {
  "1": 1, "3": 3, "5": 5, "15": 15, "30": 30,
  "60": 60, "120": 120, "240": 240, "360": 360, "720": 720,
  "D": 1440, "W": 10080,
}


def _utc_ms(dt: datetime) -> int:
  """Convert datetime to ms since epoch. Naive datetimes treated as UTC.

  SQLite returns naive datetimes (no tzinfo). Python's .timestamp()
  interprets naive as LOCAL TIME, which shifts the result by the
  system timezone offset. This helper forces UTC interpretation.
  """
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return int(dt.timestamp() * 1000)


def _to_bybit_interval(tf: str) -> str:
  """Convert any timeframe format to Bybit API interval."""
  tf_map = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W",
    "1": "1", "3": "3", "5": "5", "15": "15", "30": "30",
    "60": "60", "120": "120", "240": "240", "360": "360", "720": "720",
    "D": "D", "W": "W",
  }
  return tf_map.get(tf, tf.rstrip("m"))


def _next_bar_close(timeframes: set[str]) -> datetime:
  """Calculate the soonest bar close time across all active timeframes.

  Returns a UTC datetime of when the next bar closes.
  For intraday TFs, bars close at regular intervals from midnight UTC.
  For daily, bars close at 00:00 UTC (next day).
  """
  now = datetime.now(timezone.utc)
  soonest = now + timedelta(days=1)  # fallback: check in 24h

  for tf in timeframes:
    minutes = TF_MINUTES.get(tf, 30)

    if tf == "W":
      # Weekly bars close Monday 00:00 UTC
      days_until_monday = (7 - now.weekday()) % 7
      if days_until_monday == 0 and now.hour == 0 and now.minute == 0:
        days_until_monday = 7
      candidate = (now + timedelta(days=days_until_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0,
      )
    elif tf == "D":
      # Daily bars close at 00:00 UTC next day
      candidate = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
      )
    else:
      # Intraday: bars close at regular intervals from midnight
      midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
      minutes_since_midnight = now.hour * 60 + now.minute
      # Next bar close is the next multiple of `minutes` after current time
      bars_elapsed = math.ceil((minutes_since_midnight + 1) / minutes)
      next_close_min = bars_elapsed * minutes
      candidate = midnight + timedelta(minutes=next_close_min)

    if candidate <= now:
      # Edge case: bar just closed, calculate next one
      if tf in ("D", "W"):
        candidate += timedelta(days=1 if tf == "D" else 7)
      else:
        candidate += timedelta(minutes=minutes)

    if candidate < soonest:
      soonest = candidate

  return soonest


# ── Portfolio symbol/TF resolution ───────────────────────────────


def _normalize_tf(tf: str) -> str:
  """Normalize timeframe: '5m' -> '5', '1h' -> '60', keep 'D'/'W' as-is."""
  if tf.endswith("m") and tf[:-1].isdigit():
    return tf[:-1]
  aliases = {
    "1h": "60", "2h": "120", "4h": "240",
    "6h": "360", "12h": "720", "1d": "D", "1w": "W",
  }
  return aliases.get(tf, tf)


def _get_active_portfolio_pairs() -> tuple[
  list[tuple[str, str]], list[tuple[str, str]], set[str],
]:
  """Get deduplicated (symbol, tf) pairs split by priority.

  Returns:
    real_pairs: pairs for live-trading portfolios (download first)
    other_pairs: pairs for monitoring/paper (download second, excludes real)
    all_timeframes: union of all TFs (for bar-close scheduling)
  """
  db = SessionLocal()
  try:
    active = db.query(Portfolio).filter(Portfolio.is_active == True).all()
    real_pairs: set[tuple[str, str]] = set()
    other_pairs: set[tuple[str, str]] = set()
    all_timeframes: set[str] = set()

    for p in active:
      ptype = getattr(p, "portfolio_type", "saved") or "saved"
      source = json.loads(p.source_strategies or "{}")

      # Resolve this portfolio's base timeframe
      tf = source.get("timeframe")
      if not tf and source.get("batch_ids"):
        for bid in source["batch_ids"]:
          batch = db.get(BatchBacktestRun, bid)
          if batch and batch.timeframe:
            tf = batch.timeframe
            break
      tf = _normalize_tf(tf or "30")
      all_timeframes.add(tf)

      # Resolve extra timeframes (multi-TF strategies)
      extra_tfs: set[str] = set()
      for etf in source.get("extra_timeframes", []):
        extra_tfs.add(_normalize_tf(etf))
      if source.get("batch_ids"):
        for bid in source["batch_ids"]:
          batch = db.get(BatchBacktestRun, bid)
          if batch:
            for etf in json.loads(batch.extra_timeframes or "[]"):
              extra_tfs.add(_normalize_tf(etf))
      all_timeframes.update(extra_tfs)

      # Collect (symbol, tf) pairs for this portfolio only
      target = real_pairs if ptype == "real" else other_pairs
      for a in db.query(PortfolioAsset).filter(
        PortfolioAsset.portfolio_id == p.id,
        PortfolioAsset.is_active == True,
      ).all():
        base_symbol = a.symbol.split("_")[0] if "_" in a.symbol else a.symbol
        target.add((base_symbol, tf))
        for etf in extra_tfs:
          target.add((base_symbol, etf))

    # Deduplicate: if a pair is in real, remove from other
    other_pairs -= real_pairs

    return list(real_pairs), list(other_pairs), all_timeframes
  finally:
    db.close()


def _get_active_portfolio_symbols() -> tuple[set[str], set[str]]:
  """Backward-compatible wrapper for _next_bar_close() scheduling."""
  real, other, tfs = _get_active_portfolio_pairs()
  symbols = {s for s, _ in real} | {s for s, _ in other}
  return symbols, tfs


# ── Probe: wait for new candle on Bybit ──────────────────────────


async def _probe_for_new_candle(
  downloader: BybitDownloader, manager: DataManager,
  timeframe: str,
) -> bool:
  """Poll Bybit until a new candle appears for BTCUSDT (or fallback symbol).

  Returns True if new candle found, False if timeout (60s).
  """
  bybit_interval = _to_bybit_interval(timeframe)

  # Try BTCUSDT first, fall back to any active portfolio symbol
  probe_symbol = PROBE_SYMBOL
  latest_known = manager.get_latest_timestamp(probe_symbol, timeframe)

  if not latest_known:
    # Fallback: use the first portfolio symbol that has data for this TF
    real_pairs, other_pairs, _ = _get_active_portfolio_pairs()
    for sym, tf in (real_pairs + other_pairs):
      if tf == timeframe:
        fallback = manager.get_latest_timestamp(sym, timeframe)
        if fallback:
          probe_symbol = sym
          latest_known = fallback
          logger.info("  Probe fallback: using %s (no %s/%s data)", sym, PROBE_SYMBOL, timeframe)
          break
    if not latest_known:
      logger.info("  No data for any symbol on TF %s — skipping probe", timeframe)
      return True

  known_ms = _utc_ms(latest_known)
  attempts = PROBE_TIMEOUT // PROBE_INTERVAL

  for i in range(attempts):
    try:
      candles = await downloader.fetch_klines(
        probe_symbol, bybit_interval, limit=2,
      )
      if candles:
        newest_ts = candles[-1]["timestamp"]
        newest_ms = _utc_ms(newest_ts) if hasattr(newest_ts, "timestamp") else int(newest_ts)
        if newest_ms > known_ms:
          logger.info("  Probe: new candle detected (attempt %d/%d)", i + 1, attempts)
          return True
    except Exception as e:
      logger.debug("  Probe attempt %d failed: %s", i + 1, e)

    await asyncio.sleep(PROBE_INTERVAL)

  logger.warning("  Probe timeout after %ds — proceeding anyway", PROBE_TIMEOUT)
  return False


# ── Download: parallel fetch for all symbols ─────────────────────


async def _download_symbol(
  downloader: BybitDownloader, manager: DataManager,
  symbol: str, tf: str,
) -> int:
  """Download new candles for one symbol/timeframe pair. Returns count."""
  bybit_interval = _to_bybit_interval(tf)
  latest = manager.get_latest_timestamp(symbol, tf)

  if latest:
    since_ms = _utc_ms(latest) + 1
  else:
    since_ms = _utc_ms(datetime.now(timezone.utc) - timedelta(days=CATCHUP_DAYS))

  candles = await downloader.fetch_all_klines(
    symbol, bybit_interval, since_ms=since_ms,
  )
  if candles:
    manager.upsert_candles(symbol, tf, candles)
    return len(candles)
  return 0


def _tf_sort_key(pair: tuple[str, str]) -> int:
  """Sort by TF minutes ascending — lower TFs downloaded first."""
  _, tf = pair
  return TF_MINUTES.get(tf, 9999)


async def _download_batched(
  downloader: BybitDownloader, manager: DataManager,
  pairs: list[tuple[str, str]], label: str,
) -> tuple[int, int, int]:
  """Download pairs in batches with rate limiting.

  Returns (downloaded, skipped, errors) totals.
  """
  total_dl, total_skip, total_err = 0, 0, 0

  for i in range(0, len(pairs), BATCH_SIZE):
    batch = pairs[i:i + BATCH_SIZE]
    tasks = [_download_symbol(downloader, manager, sym, tf) for sym, tf in batch]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    dl = sum(r for r in results if isinstance(r, int) and r > 0)
    err = sum(1 for r in results if isinstance(r, Exception))
    skip = sum(1 for r in results if isinstance(r, int) and r == 0)
    total_dl += dl
    total_skip += skip
    total_err += err

    # Log per-symbol errors for debugging
    for j, r in enumerate(results):
      if isinstance(r, Exception):
        sym, tf = batch[j]
        logger.warning("  %s/%s download failed: %s", sym, tf, r)

    # Rate limit: sleep between batches (skip after last batch)
    if i + BATCH_SIZE < len(pairs):
      logger.info("  %s batch %d-%d done, sleeping %ds for rate limit...",
                   label, i, i + len(batch), BATCH_SLEEP)
      await asyncio.sleep(BATCH_SLEEP)

  return total_dl, total_skip, total_err


async def sync_cycle(downloader: BybitDownloader, manager: DataManager):
  """Download all symbols with priority ordering + rate limiting, then scan."""
  real_pairs, other_pairs, _ = _get_active_portfolio_pairs()

  # Sort by TF ascending — lower TFs first (they settle faster on exchange)
  real_pairs.sort(key=_tf_sort_key)
  other_pairs.sort(key=_tf_sort_key)

  total = len(real_pairs) + len(other_pairs)
  if total == 0:
    logger.info("No active portfolios — nothing to sync")
    return

  logger.info(
    "Syncing %d pairs (%d priority/live, %d standard/monitoring)",
    total, len(real_pairs), len(other_pairs),
  )

  total_dl, total_skip, total_err = 0, 0, 0

  # Phase 1: Priority — live trading portfolios first
  if real_pairs:
    logger.info("Phase 1: %d live-trading pairs...", len(real_pairs))
    dl, skip, err = await _download_batched(
      downloader, manager, real_pairs, "LIVE",
    )
    total_dl += dl
    total_skip += skip
    total_err += err

  # Phase 2: Standard — monitoring/paper portfolios
  if other_pairs:
    logger.info("Phase 2: %d monitoring pairs...", len(other_pairs))
    dl, skip, err = await _download_batched(
      downloader, manager, other_pairs, "MONITOR",
    )
    total_dl += dl
    total_skip += skip
    total_err += err

  logger.info(
    "Download complete: +%d candles, %d up-to-date, %d errors",
    total_dl, total_skip, total_err,
  )

  # Signal scan — immediately after download
  try:
    signal_count = run_scan_cycle()
    if signal_count > 0:
      logger.info("Signal scan: %d signal(s) found!", signal_count)
    else:
      logger.info("Signal scan: no new signals")
  except Exception as e:
    logger.error("Signal scan failed: %s", e)


# ── Main loop: bar-close triggered ───────────────────────────────


async def main():
  """Main loop: wake at bar close, probe, download, scan, repeat."""
  print("=" * 56)
  print("  CANDLE SYNC + SIGNAL SCANNER")
  print("  Bar-close triggered | Parallel downloads | Auto-scan")
  print("=" * 56)
  print("  Press Ctrl+C to stop")
  print("=" * 56)
  print()

  downloader = BybitDownloader()
  manager = DataManager()

  # Run an initial sync immediately on startup (catch up any gaps)
  logger.info("=== Initial sync (startup catch-up) ===")
  heartbeats.beat("candle_sync")
  try:
    await sync_cycle(downloader, manager)
  except Exception as e:
    logger.error("Initial sync failed: %s", e)

  cycle = 0
  while True:
    # Heartbeat every loop pass — /health uses this to detect stuck sync.
    heartbeats.beat("candle_sync")

    # Get active timeframes and calculate next bar close
    _, timeframes = _get_active_portfolio_symbols()
    if not timeframes:
      logger.info("No active portfolios — sleeping 60s")
      await asyncio.sleep(60)
      continue

    next_close = _next_bar_close(timeframes)
    now = datetime.now(timezone.utc)
    # Wait until bar close + offset to ensure candle is settled on exchange
    sleep_seconds = max(0, (next_close - now).total_seconds()) + BAR_CLOSE_OFFSET

    logger.info(
      "Next bar close: %s UTC (%s)  Sleeping %.0fs (+%ds offset)...",
      next_close.strftime("%H:%M:%S"),
      ", ".join(sorted(timeframes)),
      sleep_seconds,
      BAR_CLOSE_OFFSET,
    )
    await asyncio.sleep(sleep_seconds)

    cycle += 1
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    logger.info("=== Cycle %d at %s ===", cycle, now_str)

    # Probe Bybit until new candle appears (pick smallest TF to probe)
    smallest_tf = min(timeframes, key=lambda t: TF_MINUTES.get(t, 9999))
    await _probe_for_new_candle(downloader, manager, smallest_tf)

    # Download + scan
    try:
      await sync_cycle(downloader, manager)
    except Exception as e:
      logger.error("Cycle %d failed: %s", cycle, e)


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    print("\nCandle sync + scanner stopped.")
