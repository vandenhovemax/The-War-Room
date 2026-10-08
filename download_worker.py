"""Download worker — handles all manual downloads and refreshes.

Runs as a small FastAPI server on port 8081, completely independent of
the main backend. Downloads never get interrupted by API requests.

Also runs an auto-refresh scheduler that refreshes active paper/real
portfolios ~2 minutes after each candle close (based on portfolio TF).

Launch: python download_worker.py   (from The_War_Room/)
"""
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from threading import Thread

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

sys.path.insert(0, ".")

from backend.data.bybit_downloader import BybitDownloader
from backend.data.data_manager import DataManager, _canonical_tf
from backend.data.derived_candles import rebuild_3d_candles
from backend.utils import heartbeats
from backend.utils.logger import get_logger

logger = get_logger("download_worker")
app = FastAPI(title="Download Worker")

# In-memory progress (same structure as the old backend dicts)
_download_progress: dict[str, dict] = {}
_refresh_progress: dict[str, dict] = {}

# Cancellation flag for batch downloads
_download_cancelled = False

# Auto-refresh: track last refresh time per portfolio to avoid duplicates
_last_auto_refresh: dict[int, float] = {}  # portfolio_id → unix timestamp


# ── Request models ───────────────────────────────────────────────────

class DownloadRequest(BaseModel):
  symbol: str
  timeframe: str = "30"
  start_date: str | None = None


class BatchDownloadRequest(BaseModel):
  symbols: list[str]
  timeframe: str = "30"


class RefreshRequest(BaseModel):
  portfolio_id: int


# ── Download endpoints ───────────────────────────────────────────────

@app.post("/download")
async def download_single(req: DownloadRequest):
  if req.symbol in _download_progress:
    if _download_progress[req.symbol].get("status") == "downloading":
      return {"status": "already_running", "symbol": req.symbol}
  _download_progress[req.symbol] = {
    "status": "downloading", "pages_done": 0,
    "candles_so_far": 0, "error": None, "started_at": time.time(),
  }
  asyncio.create_task(_run_full_download(req.symbol, req.timeframe, req.start_date))
  return {"status": "started", "symbol": req.symbol, "timeframe": req.timeframe}


@app.post("/download/batch")
async def download_batch(req: BatchDownloadRequest):
  queued = []
  for sym in req.symbols:
    if sym in _download_progress and _download_progress[sym].get("status") == "downloading":
      continue
    _download_progress[sym] = {
      "status": "queued", "pages_done": 0, "candles_so_far": 0, "error": None,
    }
    queued.append(sym)
  if queued:
    asyncio.create_task(_run_batch_download(queued, req.timeframe))
  return {"status": "started", "queued": len(queued), "symbols": queued}


@app.get("/download/progress")
def download_progress():
  return _download_progress


@app.post("/download/cancel")
def cancel_downloads():
  """Cancel all active/queued downloads."""
  global _download_cancelled
  _download_cancelled = True
  cancelled_count = 0
  for sym, info in _download_progress.items():
    if info.get("status") in ("downloading", "queued"):
      info["status"] = "cancelled"
      cancelled_count += 1
  logger.info("Downloads cancelled: %d symbols", cancelled_count)
  return {"cancelled": True, "count": cancelled_count}


# ── Refresh endpoints ────────────────────────────────────────────────

@app.post("/refresh")
def start_refresh(req: RefreshRequest):
  task_id = str(uuid.uuid4())[:8]
  _refresh_progress[task_id] = {
    "status": "starting", "current_asset": "", "total_assets": 0,
  }
  from backend.services.paper_refresh import run_refresh
  Thread(
    target=run_refresh,
    args=(req.portfolio_id, task_id, _refresh_progress),
    daemon=True,
  ).start()
  return {"task_id": task_id}


@app.get("/refresh-status/{task_id}")
def refresh_status(task_id: str):
  # Fast path: in-memory progress
  p = _refresh_progress.get(task_id)
  if p:
    return p
  # Slow path: check DB for persisted refresh status (survives restart)
  try:
    from backend.db.database import SessionLocal
    from backend.db.models import Portfolio
    db = SessionLocal()
    try:
      # Search portfolios for this task_id in refresh_status JSON
      rows = db.query(Portfolio).filter(Portfolio.refresh_status.isnot(None)).all()
      for row in rows:
        try:
          saved = json.loads(row.refresh_status)
          if saved.get("task_id") == task_id:
            return saved
        except Exception:
          pass
    finally:
      db.close()
  except Exception:
    pass
  return {"status": "not_found"}


# ── Auto-refresh status endpoint ─────────────────────────────────────

@app.get("/auto-refresh/status")
def auto_refresh_status():
  """Show which portfolios are being auto-refreshed and when."""
  return {
    "last_refresh_times": {
      str(k): datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
      for k, v in _last_auto_refresh.items()
    },
    "active_refreshes": {
      tid: info for tid, info in _refresh_progress.items()
      if info.get("status") not in ("completed", "error", "not_found")
    },
  }


# ── Download logic ───────────────────────────────────────────────────

async def _run_full_download(
  symbol: str, timeframe: str, start_date: str | None = None,
  downloader: BybitDownloader | None = None, manager: DataManager | None = None,
):
  try:
    downloader = downloader or BybitDownloader()
    manager = manager or DataManager()
    total = 0

    if start_date:
      earliest = manager.get_earliest_timestamp(symbol, timeframe)
      parsed = datetime.fromisoformat(start_date)
      if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
      since_ms = int(parsed.timestamp() * 1000)
      # Naive datetimes from DB are UTC — force tzinfo to avoid local-time shift
      if earliest and earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
      end_ms = int(earliest.timestamp() * 1000) - 1 if earliest else None
      if end_ms is None or since_ms < end_ms:
        backfill = await downloader.fetch_all_klines(
          symbol, timeframe, since_ms=since_ms, end_ms=end_ms,
          progress_callback=lambda p, c: _update_progress(symbol, p, c),
        )
        if backfill:
          manager.upsert_candles(symbol, timeframe, backfill)
          total += len(backfill)

    latest = manager.get_latest_timestamp(symbol, timeframe)
    # Naive datetimes from DB are UTC — force tzinfo to avoid local-time shift
    if latest and latest.tzinfo is None:
      latest = latest.replace(tzinfo=timezone.utc)
    since_ms = int(latest.timestamp() * 1000) + 1 if latest else None
    candles = await downloader.fetch_all_klines(
      symbol, timeframe, since_ms=since_ms,
      progress_callback=lambda p, c: _update_progress(symbol, p, c),
    )
    if candles:
      manager.upsert_candles(symbol, timeframe, candles)
      total += len(candles)

    # After daily download, auto-rebuild derived timeframes (3D)
    if _canonical_tf(timeframe) == "D" and total > 0:
      try:
        n3d = rebuild_3d_candles(symbol)
        logger.info("Auto-rebuilt %d 3D candles for %s", n3d, symbol)
      except Exception as e:
        logger.warning("Failed to rebuild 3D for %s: %s", symbol, e)

    _download_progress[symbol] = {
      "status": "done",
      "pages_done": _download_progress.get(symbol, {}).get("pages_done", 0),
      "candles_so_far": total, "error": None,
    }
    logger.info("Done: %d candles for %s/%s", total, symbol, timeframe)
  except Exception as e:
    _download_progress[symbol] = {
      "status": "error", "pages_done": 0, "candles_so_far": 0, "error": str(e),
    }
    logger.error("Download failed for %s: %s", symbol, e)


async def _run_batch_download(symbols: list[str], timeframe: str):
  global _download_cancelled
  _download_cancelled = False  # Reset flag at start of new batch
  logger.info("Batch starting: %d symbols, tf=%s (concurrent)", len(symbols), timeframe)
  sem = asyncio.Semaphore(10)  # max 10 concurrent downloads
  downloader = BybitDownloader()  # shared instance — reuses HTTP connections
  manager = DataManager()  # shared instance

  async def _download_one(sym: str, idx: int):
    if _download_cancelled:
      _download_progress[sym]["status"] = "cancelled"
      return
    async with sem:
      if _download_cancelled:
        _download_progress[sym]["status"] = "cancelled"
        return
      _download_progress[sym]["status"] = "downloading"
      logger.info("Batch [%d/%d] %s", idx + 1, len(symbols), sym)
      await _run_full_download(sym, timeframe, downloader=downloader, manager=manager)

  try:
    await asyncio.gather(*[_download_one(sym, i) for i, sym in enumerate(symbols)])
  finally:
    await downloader.close()
  logger.info("Batch complete: %d symbols", len(symbols))


def _update_progress(symbol: str, pages: int, candles: int):
  if symbol in _download_progress:
    _download_progress[symbol]["pages_done"] = pages
    _download_progress[symbol]["candles_so_far"] = candles


# ── Auto-refresh scheduler ───────────────────────────────────────────

# Timeframe → candle duration in seconds
_TF_SECONDS = {
  "1": 60, "3": 180, "5": 300, "15": 900, "30": 1800,
  "60": 3600, "120": 7200, "240": 14400, "360": 21600, "720": 43200,
  "D": 86400, "W": 604800,
}

# How many seconds after candle close to wait before refreshing
_REFRESH_DELAY = 120  # 2 minutes


def _tf_to_seconds(tf: str) -> int:
  """Convert canonical timeframe to seconds."""
  return _TF_SECONDS.get(tf, 1800)  # default 30m


def _seconds_since_last_candle_close(tf_seconds: int) -> float:
  """How many seconds ago the most recent candle closed."""
  now = time.time()
  # Candles align to UTC epoch boundaries
  return now % tf_seconds


def _should_refresh_now(tf: str, portfolio_id: int) -> bool:
  """Check if we're in the 2-4 minute window after a candle close.

  Returns True if:
  1. We're between REFRESH_DELAY and REFRESH_DELAY+120s after candle close
  2. We haven't already refreshed this portfolio for this candle
  """
  tf_sec = _tf_to_seconds(tf)
  elapsed = _seconds_since_last_candle_close(tf_sec)

  # We want to refresh ~2 min after the candle closes
  # The window is [120s, 240s] after close — gives 2 minutes to trigger
  if elapsed < _REFRESH_DELAY or elapsed > _REFRESH_DELAY + 120:
    return False

  # Check if we already refreshed for this candle period
  now = time.time()
  candle_start = now - elapsed  # start of current (new) candle
  last_refresh = _last_auto_refresh.get(portfolio_id, 0)
  if last_refresh >= candle_start:
    return False  # already refreshed during this candle period

  return True


def _get_active_portfolios() -> list[dict]:
  """Query DB for active paper/real portfolios with their timeframes."""
  try:
    from backend.db.database import SessionLocal
    from backend.db.models import Portfolio
    db = SessionLocal()
    try:
      portfolios = db.query(Portfolio).filter(
        Portfolio.portfolio_type.in_(["paper", "real"]),
        Portfolio.is_active == True,
      ).all()
      result = []
      for p in portfolios:
        source = json.loads(p.source_strategies) if p.source_strategies else {}
        tf = source.get("timeframe", "30")
        result.append({
          "id": p.id,
          "name": p.name,
          "timeframe": tf,
          "portfolio_type": p.portfolio_type,
        })
      return result
    finally:
      db.close()
  except Exception as e:
    logger.warning("Auto-refresh: failed to query portfolios: %s", e)
    return []


def _is_refresh_running() -> bool:
  """Check if any refresh is already in progress (don't pile on)."""
  for tid, info in _refresh_progress.items():
    if info.get("status") in ("starting", "downloading", "backtesting", "computing"):
      return True
  return False


def _auto_refresh_loop():
  """Background thread: check every 60s if any portfolio needs a refresh."""
  logger.info("Auto-refresh scheduler started (check every 60s, refresh %ds after candle close)", _REFRESH_DELAY)
  while True:
    try:
      # Heartbeat on every loop pass so the watchdog can detect
      # a silently-dead download worker the same way it does for
      # candle_sync.
      heartbeats.beat("download_worker")
      time.sleep(60)
      portfolios = _get_active_portfolios()
      if not portfolios:
        continue

      for p in portfolios:
        tf = p["timeframe"]
        pid = p["id"]

        if not _should_refresh_now(tf, pid):
          continue

        if _is_refresh_running():
          logger.debug("Auto-refresh: skipping %s (refresh already running)", p["name"])
          continue

        # Trigger refresh
        logger.info("Auto-refresh: triggering refresh for '%s' (id=%d, tf=%s)", p["name"], pid, tf)
        task_id = f"auto-{str(uuid.uuid4())[:6]}"
        _refresh_progress[task_id] = {
          "status": "starting", "current_asset": "", "total_assets": 0,
        }
        from backend.services.paper_refresh import run_refresh
        Thread(
          target=run_refresh,
          args=(pid, task_id, _refresh_progress),
          daemon=True,
        ).start()
        _last_auto_refresh[pid] = time.time()

    except Exception as e:
      logger.error("Auto-refresh loop error: %s", e)


# ── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
  print("=" * 50)
  print("  DOWNLOAD WORKER — Downloads, Refresh & Auto-Refresh")
  print("=" * 50)
  print("  Listening on http://localhost:8081")
  print(f"  Auto-refresh: {_REFRESH_DELAY}s after candle close")
  print("  Press Ctrl+C to stop")
  print("=" * 50)
  print()

  # Start auto-refresh scheduler in background
  Thread(target=_auto_refresh_loop, daemon=True).start()

  uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
