"""Migrations for audit-identified constraint issues.

1. LiveStrategyState: add batch_id to unique constraint (multi-batch safety)
2. DailyPnL: add partial unique index for NULL portfolio_id (prevents duplicates)

Safe to run multiple times.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from backend.db.database import SessionLocal

log = logging.getLogger(__name__)


def migrate_live_state_constraint() -> None:
  """Replace uq_live_state_portfolio_symbol with batch-aware version."""
  db = SessionLocal()
  try:
    # Check if old index exists
    rows = db.execute(text(
      "SELECT name FROM sqlite_master "
      "WHERE type='index' AND name='uq_live_state_portfolio_symbol'"
    )).fetchall()

    if not rows:
      # Old constraint doesn't exist — either already migrated or fresh DB
      # Check if new one exists
      new_rows = db.execute(text(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='uq_live_state_portfolio_symbol_batch'"
      )).fetchall()
      if not new_rows:
        # Fresh DB — create_all will handle it via the ORM model
        log.info("LiveStrategyState constraint: no migration needed (fresh DB)")
      else:
        log.info("LiveStrategyState constraint: already migrated")
      return

    # Drop old constraint, create new one
    db.execute(text("DROP INDEX IF EXISTS uq_live_state_portfolio_symbol"))
    db.execute(text(
      "CREATE UNIQUE INDEX uq_live_state_portfolio_symbol_batch "
      "ON live_strategy_states(portfolio_id, symbol, batch_id)"
    ))
    db.commit()
    log.info("LiveStrategyState constraint migrated: added batch_id")

  except Exception as e:
    db.rollback()
    log.warning("LiveStrategyState constraint migration failed (non-fatal): %s", e)
  finally:
    db.close()


def migrate_daily_pnl_null_constraint() -> None:
  """Add partial unique index for DailyPnL rows where portfolio_id IS NULL.

  SQLite treats NULLs as distinct in UNIQUE constraints, so the existing
  (date, portfolio_id) constraint allows duplicate rows when portfolio_id
  is NULL. This partial index covers that gap.
  """
  db = SessionLocal()
  try:
    existing = db.execute(text(
      "SELECT name FROM sqlite_master "
      "WHERE type='index' AND name='uq_daily_pnl_date_null_portfolio'"
    )).fetchall()
    if existing:
      log.info("DailyPnL NULL constraint: already exists")
      return

    db.execute(text(
      "CREATE UNIQUE INDEX uq_daily_pnl_date_null_portfolio "
      "ON daily_pnl(date) WHERE portfolio_id IS NULL"
    ))
    db.commit()
    log.info("DailyPnL NULL constraint: partial index created")

  except Exception as e:
    db.rollback()
    log.warning("DailyPnL NULL constraint migration failed (non-fatal): %s", e)
  finally:
    db.close()
