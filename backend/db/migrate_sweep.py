"""Sweep schema migration — adds sweep_id column to batch_backtest_runs.

Safe to run multiple times: checks if column exists before ALTER.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from backend.db.database import SessionLocal

log = logging.getLogger(__name__)


def _column_exists(db, table: str, column: str) -> bool:
  rows = db.execute(text(f"PRAGMA table_info({table})")).fetchall()
  return any(r[1] == column for r in rows)


def migrate_sweep_columns() -> None:
  db = SessionLocal()
  try:
    if not _column_exists(db, "batch_backtest_runs", "sweep_id"):
      db.execute(text(
        "ALTER TABLE batch_backtest_runs ADD COLUMN sweep_id INTEGER"
        " REFERENCES sweep_runs(id) ON DELETE SET NULL"
      ))
      log.info("Added column batch_backtest_runs.sweep_id")
      db.commit()
  finally:
    db.close()


def recover_stale_sweeps() -> None:
  """Fix sweeps and batches left as 'running' after a server restart.

  1. Marks incomplete sweep child batches as 'cancelled' and sets the
     sweep status to 'interrupted'.
  2. Also marks any standalone batches (non-sweep) stuck as 'running'
     or 'queued' as 'error' so the frontend stops polling them.
  """
  db = SessionLocal()
  try:
    # --- Sweep recovery ---
    stale = db.execute(text(
      "SELECT id FROM sweep_runs WHERE status = 'running'"
    )).fetchall()

    for (sweep_id,) in stale:
      # Mark incomplete child batches as cancelled
      updated = db.execute(text(
        "UPDATE batch_backtest_runs SET status = 'cancelled'"
        " WHERE sweep_id = :sid AND status IN ('running', 'queued', 'downloading_extra_tfs')"
      ), {"sid": sweep_id}).rowcount

      # Count how many actually completed
      completed = db.execute(text(
        "SELECT COUNT(*) FROM batch_backtest_runs"
        " WHERE sweep_id = :sid AND status = 'completed'"
      ), {"sid": sweep_id}).scalar() or 0

      # Update sweep status
      db.execute(text(
        "UPDATE sweep_runs SET status = 'interrupted', completed_combos = :cc"
        " WHERE id = :sid"
      ), {"sid": sweep_id, "cc": completed})

      log.info(
        "Recovered stale sweep %d: %d batches completed, %d cancelled",
        sweep_id, completed, updated,
      )

    # --- Standalone batch recovery ---
    # Any batch stuck as running/queued with no sweep_id (standalone)
    orphaned = db.execute(text(
      "UPDATE batch_backtest_runs SET status = 'error',"
      " error_message = 'Interrupted by server restart'"
      " WHERE status IN ('running', 'queued', 'downloading_extra_tfs')"
      " AND sweep_id IS NULL"
    )).rowcount
    if orphaned:
      log.info("Recovered %d orphaned standalone batches", orphaned)

    db.commit()
  finally:
    db.close()
