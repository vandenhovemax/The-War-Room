"""T-002 Step 10 — Composite index on `live_events`.

Without an index, the new `GET /api/portfolios/{id}/phase2-events` route
and the `phase2_audit` lookup do a full scan of `live_events`. With ~10k
recorder rows per active portfolio per year, that scan becomes the
slowest part of the portfolio detail view within a quarter or two.

The composite `(portfolio_id, event_type, created_at DESC)` index covers:

  * Route: `WHERE portfolio_id = :pid AND event_type IN (...)
            ORDER BY created_at DESC`
  * Audit: `WHERE event_type IN (phase2 types) AND created_at >= :since`
  * Per-portfolio segment walks in `iter_basket_segments`:
            `WHERE portfolio_id = :pid AND event_type = :type
             ORDER BY created_at`

SQLite uses leftmost-prefix lookup, so `(portfolio_id, event_type,
created_at)` also accelerates the audit's `event_type IN (...)` scan
when no portfolio filter is set (it falls back to a range scan on
`event_type`, still a big win over a full table scan).

Safe to run multiple times — uses `CREATE INDEX IF NOT EXISTS`.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from backend.db.database import SessionLocal

log = logging.getLogger(__name__)


_INDEX_NAME = "idx_live_events_pid_type_created"


def _index_exists(db, name: str) -> bool:
  row = db.execute(text(
    "SELECT name FROM sqlite_master WHERE type='index' AND name=:n"
  ), {"n": name}).fetchone()
  return row is not None


def migrate_phase2_events_index() -> None:
  """Create the composite phase-2 lookup index on live_events if missing."""
  db = SessionLocal()
  try:
    if _index_exists(db, _INDEX_NAME):
      log.info("%s already present — skipping", _INDEX_NAME)
      return

    db.execute(text(
      f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} "
      "ON live_events(portfolio_id, event_type, created_at)"
    ))
    db.commit()
    log.info("Created composite index %s on live_events", _INDEX_NAME)
  except Exception as e:
    db.rollback()
    log.warning("migrate_phase2_events_index failed (non-fatal): %s", e)
  finally:
    db.close()
