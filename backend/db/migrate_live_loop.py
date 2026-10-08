"""Live Loop schema migration — adds columns/tables needed for the
offline->live closed loop (hybrid nightly refresh + non-hybrid freeze).

Safe to run multiple times: checks existence before ALTER / CREATE.

Changes:
  1. portfolios.mode — non_hybrid | hybrid (default non_hybrid preserves today's
     frozen behavior for any portfolio created before the migration).
     Backfill: rows where source_strategies.hybrid_mode == 1 -> mode='hybrid'.
  2. rebalance_proposals table (Ship 2) — stores Step-1 re-run results pending
     human approval at the OOS boundary. Only legal basket-write path for
     non_hybrid portfolios.
  3. parity_canary_reports table (Ship 3) — monthly from-scratch-vs-incremental
     audit results. Audit-only; no mutations.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from backend.db.database import SessionLocal

log = logging.getLogger(__name__)


def _column_exists(db, table: str, column: str) -> bool:
  rows = db.execute(text(f"PRAGMA table_info({table})")).fetchall()
  return any(r[1] == column for r in rows)


def _table_exists(db, table: str) -> bool:
  row = db.execute(text(
    "SELECT name FROM sqlite_master WHERE type='table' AND name=:n"
  ), {"n": table}).fetchone()
  return row is not None


def _migrate_rebalance_proposals(db) -> None:
  """Create the rebalance_proposals table + indexes if missing."""
  if _table_exists(db, "rebalance_proposals"):
    log.info("rebalance_proposals table already present — skipping")
    return

  db.execute(text("""
    CREATE TABLE rebalance_proposals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
      trigger_type TEXT NOT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      status TEXT NOT NULL DEFAULT 'pending',
      current_basket_json TEXT NOT NULL DEFAULT '[]',
      proposed_basket_json TEXT NOT NULL DEFAULT '[]',
      diff_json TEXT NOT NULL DEFAULT '{}',
      metrics_preview_json TEXT,
      reviewed_at DATETIME,
      reviewed_by TEXT,
      resulting_portfolio_id INTEGER
    )
  """))
  db.execute(text(
    "CREATE INDEX IF NOT EXISTS idx_rebalance_proposals_status_created "
    "ON rebalance_proposals(status, created_at)"
  ))
  db.execute(text(
    "CREATE INDEX IF NOT EXISTS idx_rebalance_proposals_portfolio "
    "ON rebalance_proposals(portfolio_id)"
  ))
  db.commit()
  log.info("Created rebalance_proposals table with indexes")


def _migrate_parity_canary_reports(db) -> None:
  """Create the parity_canary_reports table + index if missing."""
  if _table_exists(db, "parity_canary_reports"):
    log.info("parity_canary_reports table already present — skipping")
    return

  db.execute(text("""
    CREATE TABLE parity_canary_reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_date TEXT NOT NULL,
      portfolio_id INTEGER REFERENCES portfolios(id) ON DELETE SET NULL,
      total_combos INTEGER DEFAULT 0,
      combos_with_diff INTEGER DEFAULT 0,
      report_json TEXT NOT NULL DEFAULT '{}',
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
  """))
  db.execute(text(
    "CREATE INDEX IF NOT EXISTS idx_parity_canary_run_date "
    "ON parity_canary_reports(run_date)"
  ))
  db.execute(text(
    "CREATE INDEX IF NOT EXISTS idx_parity_canary_created_at "
    "ON parity_canary_reports(created_at)"
  ))
  db.commit()
  log.info("Created parity_canary_reports table with indexes")


def migrate_live_loop_columns() -> None:
  """Add portfolios.mode column and backfill from source_strategies.hybrid_mode."""
  db = SessionLocal()
  try:
    if not _column_exists(db, "portfolios", "mode"):
      db.execute(text(
        "ALTER TABLE portfolios ADD COLUMN mode TEXT NOT NULL DEFAULT 'non_hybrid'"
      ))
      log.info("Added column portfolios.mode (default='non_hybrid')")

      # Backfill: any saved portfolio whose source_strategies JSON carried
      # hybrid_mode=1 is classed as hybrid. Everything else stays non_hybrid.
      # Uses json_extract for SQLite compatibility.
      db.execute(text(
        "UPDATE portfolios SET mode = 'hybrid' "
        "WHERE json_valid(source_strategies) = 1 "
        "  AND json_extract(source_strategies, '$.hybrid_mode') = 1"
      ))
      db.commit()
      log.info("Backfilled portfolios.mode for rows with hybrid_mode=1 in source_strategies")
    else:
      log.info("portfolios.mode already present — skipping")

    # Ship 2 — OOS boundary proposals
    _migrate_rebalance_proposals(db)

    # Ship 3 — parity canary audit log
    _migrate_parity_canary_reports(db)

  except Exception as e:
    db.rollback()
    log.warning("migrate_live_loop_columns failed (non-fatal): %s", e)
  finally:
    db.close()
