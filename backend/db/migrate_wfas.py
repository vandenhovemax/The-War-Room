"""WFAS schema migration — adds new columns for time modes + H1/H2.

Safe to run multiple times: checks if column exists before ALTER.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from backend.db.database import SessionLocal

log = logging.getLogger(__name__)

# (table, column, sql_type, default)
_WFO_RUN_COLS = [
  ("wfo_runs", "time_mode", "TEXT", "'rolling'"),
  ("wfo_runs", "event_mode", "INTEGER", "0"),
  ("wfo_runs", "portfolio_ids", "TEXT", "'[]'"),
  ("wfo_runs", "cumulative_oos_h1", "TEXT", None),
  ("wfo_runs", "cumulative_oos_h2", "TEXT", None),
  ("wfo_runs", "avg_degradation", "REAL", None),
  ("wfo_runs", "parent_run_id", "INTEGER", None),
]

_WFO_WINDOW_COLS = [
  ("wfo_windows", "oos_h1_start", "TEXT", None),
  ("wfo_windows", "oos_h1_end", "TEXT", None),
  ("wfo_windows", "oos_h2_start", "TEXT", None),
  ("wfo_windows", "oos_h2_end", "TEXT", None),
  ("wfo_windows", "oos_h1_results", "TEXT", None),
  ("wfo_windows", "oos_h2_results", "TEXT", None),
  ("wfo_windows", "degradation_ratio", "REAL", None),
  ("wfo_windows", "is_pool_size", "INTEGER", "0"),
]


def _column_exists(db, table: str, column: str) -> bool:
  rows = db.execute(text(f"PRAGMA table_info({table})")).fetchall()
  return any(r[1] == column for r in rows)


def migrate_wfas_columns() -> None:
  db = SessionLocal()
  try:
    for table, col, sql_type, default in _WFO_RUN_COLS + _WFO_WINDOW_COLS:
      if _column_exists(db, table, col):
        continue
      dflt = f" DEFAULT {default}" if default is not None else ""
      stmt = f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}{dflt}"
      db.execute(text(stmt))
      log.info("Added column %s.%s", table, col)
    db.commit()
  finally:
    db.close()
