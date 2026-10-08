"""Database maintenance — cleanup soft-deleted records, deduplicate OHLCV, VACUUM."""
import os
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import text as sa_text

from backend.config import get_settings
from backend.db.database import engine, SessionLocal, ohlcv_router
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/maintenance", tags=["Maintenance"])

# Module-level progress state (shared between request thread and cleanup thread)
_cleanup_progress: dict = {"step": "", "done": True, "result": None, "error": None}


def _db_path() -> str:
  return get_settings().database_url.replace("sqlite:///", "")


def _total_db_size(path: str) -> int:
  """Return total size of DB + WAL + SHM files."""
  total = 0
  for suffix in ["", "-wal", "-shm"]:
    fp = path + suffix
    if os.path.exists(fp):
      total += os.path.getsize(fp)
  return total


@router.get("/db-size")
def get_db_size():
  """Return current database sizes — main DB + all per-TF OHLCV DBs."""
  path = _db_path()
  main_bytes = _total_db_size(path)
  ohlcv_bytes = ohlcv_router.total_size_bytes()
  total = main_bytes + ohlcv_bytes
  return {
    "size_mb": round(total / (1024 * 1024), 1),
    "main_db_mb": round(main_bytes / (1024 * 1024), 1),
    "ohlcv_db_mb": round(ohlcv_bytes / (1024 * 1024), 1),
    "timeframes": ohlcv_router.list_timeframes(),
  }


@router.get("/cleanup-progress")
def get_cleanup_progress():
  """Return current cleanup progress."""
  return _cleanup_progress


def _run_cleanup():
  """Background cleanup worker — runs in a separate thread."""
  global _cleanup_progress
  path = _db_path()
  before_main = _total_db_size(path)
  before_ohlcv = ohlcv_router.total_size_bytes()
  before_bytes = before_main + before_ohlcv

  db = SessionLocal()
  try:
    # Disable FK checks so we can delete in any order
    db.execute(sa_text("PRAGMA foreign_keys=OFF"))

    _cleanup_progress["step"] = "Deleting trades..."
    r1 = db.execute(sa_text("""
      DELETE FROM backtest_trades WHERE run_id IN (
        SELECT id FROM backtest_runs WHERE batch_id IN (
          SELECT id FROM batch_backtest_runs WHERE is_deleted = 1
        )
      )
    """))
    trade_count = r1.rowcount
    r2 = db.execute(sa_text("""
      DELETE FROM backtest_trades WHERE run_id IN (
        SELECT id FROM backtest_runs WHERE is_deleted = 1
      )
    """))
    trade_count += r2.rowcount

    _cleanup_progress["step"] = "Deleting runs..."
    r3 = db.execute(sa_text("""
      DELETE FROM backtest_runs WHERE batch_id IN (
        SELECT id FROM batch_backtest_runs WHERE is_deleted = 1
      )
    """))
    run_count = r3.rowcount
    r4 = db.execute(sa_text("DELETE FROM backtest_runs WHERE is_deleted = 1"))
    run_count += r4.rowcount

    _cleanup_progress["step"] = "Deleting batches..."
    r5 = db.execute(sa_text("DELETE FROM batch_backtest_runs WHERE is_deleted = 1"))
    batch_count = r5.rowcount

    _cleanup_progress["step"] = "Deleting strategies..."
    r6 = db.execute(sa_text("""
      DELETE FROM strategies WHERE is_deleted = 1
      AND id NOT IN (SELECT DISTINCT strategy_id FROM backtest_runs)
    """))
    strategy_count = r6.rowcount

    _cleanup_progress["step"] = "Deleting WFO runs..."
    r7 = db.execute(sa_text("DELETE FROM wfo_runs WHERE is_deleted = 1"))
    wfo_count = r7.rowcount

    _cleanup_progress["step"] = "Deleting portfolios..."
    # Only purge soft-deleted portfolios that are NOT active/live
    # FK enforcement is OFF, so manually clean up child rows first
    db.execute(sa_text("""
      DELETE FROM portfolio_assets WHERE portfolio_id IN (
        SELECT id FROM portfolios WHERE is_deleted = 1 AND is_active = 0
      )
    """))
    r8 = db.execute(sa_text("""
      DELETE FROM portfolios WHERE is_deleted = 1
      AND is_active = 0
    """))
    portfolio_count = r8.rowcount

    db.commit()
    db.execute(sa_text("PRAGMA foreign_keys=ON"))

    # Deduplicate OHLCV across all per-TF databases
    _cleanup_progress["step"] = "Removing OHLCV duplicates..."
    dup_count = 0
    for tf in ohlcv_router.list_timeframes():
      tf_db = ohlcv_router.get_session(tf)
      try:
        dup_result = tf_db.execute(sa_text("""
          DELETE FROM ohlcv_data
          WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM ohlcv_data
            GROUP BY symbol, timeframe, timestamp
          )
        """))
        dup_count += dup_result.rowcount
        tf_db.commit()
      except Exception as e:
        logger.warning("Dedup failed for TF %s: %s", tf, e)
        tf_db.rollback()
      finally:
        tf_db.close()

    logger.info(
      "Cleanup: %d trades, %d runs, %d batches, %d strategies, %d WFO, %d portfolios, %d OHLCV dups",
      trade_count, run_count, batch_count, strategy_count, wfo_count, portfolio_count, dup_count,
    )

    # Checkpoint WAL on main DB
    _cleanup_progress["step"] = "Checkpointing WAL..."
    try:
      wal_result = db.execute(sa_text("PRAGMA wal_checkpoint(TRUNCATE)"))
      row = wal_result.fetchone()
      logger.info("Main WAL checkpoint: busy=%s, log=%s, checkpointed=%s", row[0], row[1], row[2])
    except Exception as e:
      logger.warning("Main WAL checkpoint failed: %s", e)

    db.close()

    # VACUUM main DB
    _cleanup_progress["step"] = "Vacuuming main database..."
    engine.dispose()
    time.sleep(2)

    vacuum_error = None
    try:
      conn = sqlite3.connect(path, isolation_level=None, timeout=600)
      conn.execute("VACUUM")
      conn.close()
      logger.info("Main DB VACUUM complete")
    except Exception as e:
      vacuum_error = str(e)
      logger.error("Main DB VACUUM failed: %s", e)

    # VACUUM each per-TF OHLCV database
    _cleanup_progress["step"] = "Vacuuming OHLCV databases..."
    ohlcv_router.dispose_all()
    time.sleep(1)

    ohlcv_dir = Path(path).parent / "ohlcv"
    ohlcv_vacuum_errors: list[str] = []
    if ohlcv_dir.exists():
      for db_file in sorted(ohlcv_dir.glob("ohlcv_*.db")):
        if db_file.name.endswith(("-wal", "-shm")):
          continue
        tf_name = db_file.stem.replace("ohlcv_", "")
        _cleanup_progress["step"] = f"Vacuuming ohlcv_{tf_name}.db..."
        try:
          conn = sqlite3.connect(str(db_file), isolation_level=None, timeout=600)
          conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
          conn.execute("VACUUM")
          conn.close()
          logger.info("OHLCV VACUUM complete: %s", db_file.name)
        except Exception as e:
          ohlcv_vacuum_errors.append(f"{tf_name}: {e}")
          logger.error("OHLCV VACUUM failed for %s: %s", db_file.name, e)

    after_main = _total_db_size(path)
    after_ohlcv = ohlcv_router.total_size_bytes()
    after_bytes = after_main + after_ohlcv

    result = {
      "before_mb": round(before_bytes / (1024 * 1024), 1),
      "after_mb": round(after_bytes / (1024 * 1024), 1),
      "saved_mb": round((before_bytes - after_bytes) / (1024 * 1024), 1),
      "deleted_trades": trade_count,
      "deleted_runs": run_count,
      "deleted_batches": batch_count,
      "deleted_strategies": strategy_count,
      "deleted_wfo": wfo_count,
      "deleted_portfolios": portfolio_count,
      "duplicate_ohlcv_removed": dup_count,
      "vacuum_error": vacuum_error,
      "ohlcv_vacuum_errors": ohlcv_vacuum_errors or None,
    }

    _cleanup_progress["step"] = "Complete"
    _cleanup_progress["done"] = True
    _cleanup_progress["result"] = result

  except Exception as e:
    logger.error("Cleanup failed: %s", e)
    _cleanup_progress["step"] = "Failed"
    _cleanup_progress["done"] = True
    _cleanup_progress["error"] = str(e)
    try:
      db.close()
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)


@router.post("/cleanup")
def cleanup_database():
  """Start cleanup in a background thread. Poll /cleanup-progress for status."""
  global _cleanup_progress

  if not _cleanup_progress["done"]:
    return {"status": "already_running", "step": _cleanup_progress["step"]}

  _cleanup_progress = {"step": "Starting...", "done": False, "result": None, "error": None}

  thread = threading.Thread(target=_run_cleanup, daemon=True)
  thread.start()

  return {"status": "started"}


@router.delete("/timeframe/{tf}")
def delete_timeframe(tf: str):
  """Delete an entire timeframe's OHLCV data by removing its database file.

  This is instant — no VACUUM needed. The file is simply deleted from disk.
  """
  available = ohlcv_router.list_timeframes()
  # Normalize the input
  from backend.utils.tf_resolver import canonical_tf
  canonical = canonical_tf(tf)

  if canonical not in available:
    raise HTTPException(
      status_code=404,
      detail=f"Timeframe '{tf}' (canonical: '{canonical}') not found. "
             f"Available: {available}",
    )

  deleted = ohlcv_router.delete_timeframe(canonical)
  if deleted:
    logger.info("Deleted timeframe %s (canonical: %s)", tf, canonical)
    return {"status": "deleted", "timeframe": canonical}
  raise HTTPException(status_code=500, detail="Failed to delete timeframe files")
