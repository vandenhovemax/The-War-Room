"""Add is_paper flag to trades table — prevents paper/live martingale streak crossover.

Why:
  Before this migration, `trades.portfolio_id` was the only scope on the martingale
  streak query. If a portfolio ever switched modes (paper→live or vice versa),
  or if a user flipped `.env paper_trading` between activations, paper trades
  would silently feed into live streak calculations. Martingale multiplier based
  on phantom history → position sized way off on first live entry.

What:
  1. Add `is_paper BOOLEAN NOT NULL DEFAULT 0` to `trades`.
  2. Backfill existing rows: `is_paper=1` when owning portfolio.portfolio_type='paper'.
  3. Add index on (portfolio_id, is_paper, closed_at) for streak query perf.

Safe to run multiple times — checks before altering.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from backend.db.database import SessionLocal

log = logging.getLogger(__name__)


def _column_exists(db, table: str, column: str) -> bool:
  rows = db.execute(text(f"PRAGMA table_info({table})")).fetchall()
  return any(r[1] == column for r in rows)


def _index_exists(db, name: str) -> bool:
  rows = db.execute(text(
    "SELECT name FROM sqlite_master WHERE type='index' AND name=:n"
  ), {"n": name}).fetchall()
  return bool(rows)


def migrate_trades_is_paper() -> None:
  """Add is_paper column to trades + backfill from portfolio_type."""
  db = SessionLocal()
  try:
    if _column_exists(db, "trades", "is_paper"):
      log.info("trades.is_paper: already present — skipping")
    else:
      # SQLite ALTER TABLE ADD COLUMN with NOT NULL DEFAULT is safe and fast.
      db.execute(text(
        "ALTER TABLE trades ADD COLUMN is_paper BOOLEAN NOT NULL DEFAULT 0"
      ))
      db.commit()
      log.info("trades.is_paper: column added with default 0")

      # Backfill: set is_paper=1 for trades whose portfolio is 'paper'.
      result = db.execute(text(
        "UPDATE trades SET is_paper = 1 "
        "WHERE portfolio_id IN ("
        "  SELECT id FROM portfolios WHERE portfolio_type = 'paper'"
        ")"
      ))
      db.commit()
      log.info("trades.is_paper: backfilled %d rows as paper", result.rowcount)

    # Index to keep streak query fast (portfolio_id + is_paper + closed_at DESC).
    if not _index_exists(db, "idx_trades_portfolio_paper_closed"):
      db.execute(text(
        "CREATE INDEX idx_trades_portfolio_paper_closed "
        "ON trades(portfolio_id, is_paper, closed_at DESC)"
      ))
      db.commit()
      log.info("trades.idx_trades_portfolio_paper_closed: created")
    else:
      log.info("trades.idx_trades_portfolio_paper_closed: already exists")

  except Exception as e:
    db.rollback()
    log.error("trades.is_paper migration failed: %s", e)
    raise
  finally:
    db.close()


if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
  migrate_trades_is_paper()
  print("Migration complete.")
