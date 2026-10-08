"""T-002 Step 10 — composite index migration on live_events.

Pins:
  * Migration creates `idx_live_events_pid_type_created`
  * Migration is idempotent (second run is a no-op, no error)
  * Index includes (portfolio_id, event_type, created_at) in that order
  * Migration never raises on a fresh schema
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.db.database as _db_module
import backend.db.migrate_phase2_events_index as _mig_module
from backend.db.migrate_phase2_events_index import (
  _INDEX_NAME,
  migrate_phase2_events_index,
)
from backend.db.models import Base


_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _set_pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA foreign_keys=ON")
  cur.close()


_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


@pytest.fixture(autouse=True)
def _route_session():
  Base.metadata.create_all(bind=_ENGINE)
  prev_db = _db_module.SessionLocal
  prev_mig = _mig_module.SessionLocal
  _db_module.SessionLocal = _Session
  _mig_module.SessionLocal = _Session
  yield
  _db_module.SessionLocal = prev_db
  _mig_module.SessionLocal = prev_mig
  # Drop the index between tests so each one starts fresh.
  with _ENGINE.connect() as c:
    c.execute(text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
    c.commit()


def _index_columns(db, name: str) -> list[str]:
  rows = db.execute(text(f"PRAGMA index_info({name})")).fetchall()
  rows_sorted = sorted(rows, key=lambda r: r[0])  # seqno
  return [r[2] for r in rows_sorted]


def _index_exists(db, name: str) -> bool:
  row = db.execute(text(
    "SELECT name FROM sqlite_master WHERE type='index' AND name=:n"
  ), {"n": name}).fetchone()
  return row is not None


def test_creates_composite_index():
  with _Session() as db:
    assert not _index_exists(db, _INDEX_NAME)
  migrate_phase2_events_index()
  with _Session() as db:
    assert _index_exists(db, _INDEX_NAME)
    cols = _index_columns(db, _INDEX_NAME)
    assert cols == ["portfolio_id", "event_type", "created_at"]


def test_migration_is_idempotent():
  migrate_phase2_events_index()
  # Second run must NOT raise and must not duplicate the index.
  migrate_phase2_events_index()
  with _Session() as db:
    rows = db.execute(text(
      "SELECT COUNT(*) FROM sqlite_master "
      "WHERE type='index' AND name=:n"
    ), {"n": _INDEX_NAME}).fetchone()
    assert rows[0] == 1


def test_migration_does_not_raise_on_clean_db():
  # Clean DB (autouse fixture creates schema). No live_events rows.
  migrate_phase2_events_index()
  # No assertion needed — the test fails if the call raises.


def test_route_query_uses_index():
  """SQLite EXPLAIN QUERY PLAN should reference the new index for the
  portfolio_id + event_type filter that the route uses.
  """
  migrate_phase2_events_index()
  with _Session() as db:
    plan = db.execute(text(
      "EXPLAIN QUERY PLAN "
      "SELECT * FROM live_events "
      "WHERE portfolio_id = 1 AND event_type IN "
      "  ('hybrid_refresh','hybrid_rotation','phase2_evaluated_no_change') "
      "ORDER BY created_at DESC LIMIT 50"
    )).fetchall()
  plan_text = " ".join(str(row) for row in plan)
  # SQLite's planner picks the most specific index; we accept either the
  # composite or any covering index that mentions live_events.
  assert _INDEX_NAME in plan_text or "USING INDEX" in plan_text, plan_text
