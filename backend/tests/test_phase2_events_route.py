"""T-002 Step 8 — `GET /api/portfolios/{id}/phase2-events` route.

Pins the contract the frontend depends on:
  * 404 when the portfolio is missing
  * 400 when event_type filter is unknown
  * Newest-first ordering, paginated with limit/offset
  * Filter scopes to the requested event_type
  * Only phase-2 events bubble up; unrelated LiveEvent rows are excluded
  * Response shape parses key recorder fields into a thin `details`
    object so the UI does not have to re-parse JSON
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.db.database as _db_module
from backend.api.app import create_app
from backend.db.database import get_db
from backend.db.models import Base, LiveEvent, Portfolio


_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _set_pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA journal_mode=WAL")
  cur.execute("PRAGMA foreign_keys=ON")
  cur.close()


_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


def _override_get_db():
  db = _Session()
  try:
    yield db
  finally:
    db.close()


@pytest.fixture(scope="module", autouse=True)
def _schema():
  Base.metadata.create_all(bind=_ENGINE)
  _prev = _db_module.SessionLocal
  _db_module.SessionLocal = _Session
  yield
  _db_module.SessionLocal = _prev
  Base.metadata.drop_all(bind=_ENGINE)


@pytest.fixture()
def client():
  app = create_app()
  app.dependency_overrides[get_db] = _override_get_db
  with TestClient(app) as c:
    yield c
  app.dependency_overrides.clear()


@pytest.fixture()
def db():
  s = _Session()
  try:
    s.query(LiveEvent).delete()
    s.query(Portfolio).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


def _make_portfolio(db, *, name="p", is_active=True) -> int:
  p = Portfolio(
    name=name,
    portfolio_type="paper",
    mode="hybrid",
    is_active=is_active,
    is_deleted=False,
    source_strategies="{}",
  )
  db.add(p)
  db.commit()
  return p.id


def _add_event(
  db,
  *,
  portfolio_id: int,
  event_type: str,
  created_at: datetime,
  details: dict,
  symbol: str = "",
) -> int:
  ev = LiveEvent(
    portfolio_id=portfolio_id,
    event_type=event_type,
    symbol=symbol,
    details=json.dumps(details),
    created_at=created_at,
  )
  db.add(ev)
  db.commit()
  return ev.id


def test_missing_portfolio_returns_404(client, db):
  r = client.get("/api/portfolios/99999/phase2-events")
  assert r.status_code == 404


def test_invalid_event_type_returns_400(client, db):
  pid = _make_portfolio(db)
  r = client.get(
    f"/api/portfolios/{pid}/phase2-events",
    params={"event_type": "not_a_real_type"},
  )
  assert r.status_code == 400


def test_empty_portfolio_returns_zero_events(client, db):
  pid = _make_portfolio(db)
  r = client.get(f"/api/portfolios/{pid}/phase2-events")
  assert r.status_code == 200
  payload = r.json()
  assert payload["portfolio_id"] == pid
  assert payload["events"] == []
  assert payload["total"] == 0
  assert payload["limit"] == 50
  assert payload["offset"] == 0
  assert payload["event_type_filter"] is None


def test_returns_phase2_events_newest_first(client, db):
  pid = _make_portfolio(db)
  now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=now - timedelta(days=2),
    details={"trigger": "nightly_cron", "basket_unchanged": False,
             "chosen_basket": ["B1_BTCUSDT_Long"]},
  )
  _add_event(
    db, portfolio_id=pid, event_type="phase2_evaluated_no_change",
    created_at=now - timedelta(days=1),
    details={"trigger": "nightly_cron", "basket_unchanged": True},
  )
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_rotation",
    created_at=now,
    details={"trigger": "rotation_day"},
  )
  r = client.get(f"/api/portfolios/{pid}/phase2-events")
  assert r.status_code == 200
  payload = r.json()
  types = [e["event_type"] for e in payload["events"]]
  assert types == ["hybrid_rotation", "phase2_evaluated_no_change", "hybrid_refresh"]
  assert payload["total"] == 3


def test_excludes_non_phase2_event_types(client, db):
  pid = _make_portfolio(db)
  now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  _add_event(
    db, portfolio_id=pid, event_type="order_placed",
    created_at=now, details={"order_id": 1},
  )
  _add_event(
    db, portfolio_id=pid, event_type="error",
    created_at=now, details={"msg": "boom"},
  )
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=now, details={"trigger": "nightly_cron"},
  )
  r = client.get(f"/api/portfolios/{pid}/phase2-events")
  payload = r.json()
  assert payload["total"] == 1
  assert payload["events"][0]["event_type"] == "hybrid_refresh"


def test_event_type_filter_scopes(client, db):
  pid = _make_portfolio(db)
  now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  _add_event(db, portfolio_id=pid, event_type="hybrid_refresh",
             created_at=now, details={})
  _add_event(db, portfolio_id=pid, event_type="hybrid_refresh",
             created_at=now - timedelta(hours=1), details={})
  _add_event(db, portfolio_id=pid, event_type="hybrid_rotation",
             created_at=now - timedelta(hours=2), details={})
  _add_event(db, portfolio_id=pid, event_type="phase2_evaluated_no_change",
             created_at=now - timedelta(hours=3), details={})
  r = client.get(
    f"/api/portfolios/{pid}/phase2-events",
    params={"event_type": "hybrid_refresh"},
  )
  payload = r.json()
  assert payload["total"] == 2
  assert payload["event_type_filter"] == "hybrid_refresh"
  assert all(e["event_type"] == "hybrid_refresh" for e in payload["events"])


def test_pagination_limit_and_offset(client, db):
  pid = _make_portfolio(db)
  base = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  ids = []
  for i in range(5):
    eid = _add_event(
      db, portfolio_id=pid, event_type="hybrid_refresh",
      created_at=base + timedelta(hours=i),
      details={"i": i},
    )
    ids.append(eid)
  # newest-first ordering means events come back as i=4,3,2,1,0
  r = client.get(
    f"/api/portfolios/{pid}/phase2-events",
    params={"limit": 2, "offset": 0},
  )
  payload = r.json()
  assert payload["total"] == 5
  assert payload["limit"] == 2
  assert payload["offset"] == 0
  assert len(payload["events"]) == 2
  assert payload["events"][0]["id"] == ids[4]
  assert payload["events"][1]["id"] == ids[3]

  r2 = client.get(
    f"/api/portfolios/{pid}/phase2-events",
    params={"limit": 2, "offset": 2},
  )
  page2 = r2.json()
  assert [e["id"] for e in page2["events"]] == [ids[2], ids[1]]


def test_response_shape_parses_recorder_details(client, db):
  pid = _make_portfolio(db)
  details = {
    "decided_at_iso": "2026-04-01T08:00:00+00:00",
    "trigger": "nightly_cron",
    "recorder_version": 1,
    "phase1_window_num": 7,
    "phase1_window_num_synth_for_stub": False,
    "phase1_window_oos_start_iso": "2026-03-01",
    "phase1_window_oos_end_iso": "2026-04-01",
    "prior_basket": ["B1_BTCUSDT_Long"],
    "chosen_basket": ["B1_BTCUSDT_Long", "B1_ETHUSDT_Long"],
    "added": ["B1_ETHUSDT_Long"],
    "removed": [],
    "basket_unchanged": False,
    "dispatch_outcome": "success",
    "dispatch_verified_at_iso": "2026-04-01T08:00:30+00:00",
    "reconstructed": False,
    "decision_inputs_hash": "a" * 64,
    # Extra fields that should be silently dropped
    "irrelevant": "noise",
  }
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=datetime(2026, 4, 1, 8, 0, 0, tzinfo=timezone.utc),
    details=details, symbol="BTCUSDT",
  )
  r = client.get(f"/api/portfolios/{pid}/phase2-events")
  ev = r.json()["events"][0]
  assert ev["event_type"] == "hybrid_refresh"
  assert ev["symbol"] == "BTCUSDT"
  assert ev["created_at"].endswith("+00:00")
  d = ev["details"]
  for k in (
    "decided_at_iso", "trigger", "recorder_version",
    "phase1_window_num", "phase1_window_num_synth_for_stub",
    "phase1_window_oos_start_iso", "phase1_window_oos_end_iso",
    "prior_basket", "chosen_basket", "added", "removed",
    "basket_unchanged", "dispatch_outcome", "dispatch_verified_at_iso",
    "reconstructed", "decision_inputs_hash",
  ):
    assert k in d, f"missing key {k} in parsed details"
  assert d["chosen_basket"] == ["B1_BTCUSDT_Long", "B1_ETHUSDT_Long"]
  assert d["added"] == ["B1_ETHUSDT_Long"]
  assert d["dispatch_outcome"] == "success"
  assert d["reconstructed"] is False
  assert "irrelevant" not in d


def test_corrupted_details_does_not_500(client, db):
  pid = _make_portfolio(db)
  ev = LiveEvent(
    portfolio_id=pid, event_type="hybrid_refresh",
    symbol="", details="this is not json",
    created_at=datetime(2026, 4, 1, 8, 0, 0, tzinfo=timezone.utc),
  )
  db.add(ev)
  db.commit()
  r = client.get(f"/api/portfolios/{pid}/phase2-events")
  assert r.status_code == 200
  payload = r.json()
  assert payload["total"] == 1
  assert payload["events"][0]["details"]["chosen_basket"] == []
  assert payload["events"][0]["details"]["basket_unchanged"] is False


def test_scopes_to_requested_portfolio(client, db):
  pid_a = _make_portfolio(db, name="a")
  pid_b = _make_portfolio(db, name="b")
  _add_event(db, portfolio_id=pid_a, event_type="hybrid_refresh",
             created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
             details={})
  _add_event(db, portfolio_id=pid_b, event_type="hybrid_refresh",
             created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
             details={})
  _add_event(db, portfolio_id=pid_b, event_type="hybrid_refresh",
             created_at=datetime(2026, 4, 2, tzinfo=timezone.utc),
             details={})
  ra = client.get(f"/api/portfolios/{pid_a}/phase2-events")
  rb = client.get(f"/api/portfolios/{pid_b}/phase2-events")
  assert ra.json()["total"] == 1
  assert rb.json()["total"] == 2


def test_reconstructed_cold_save_event_serialised(client, db):
  pid = _make_portfolio(db)
  _add_event(
    db, portfolio_id=pid, event_type="phase2_evaluated_no_change",
    created_at=datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
    details={
      "trigger": "cold_save",
      "reconstructed": True,
      "basket_unchanged": True,
      "dispatch_outcome": "success",
      "chosen_basket": ["B1_BTCUSDT_Long"],
      "prior_basket": ["B1_BTCUSDT_Long"],
    },
  )
  r = client.get(f"/api/portfolios/{pid}/phase2-events")
  ev = r.json()["events"][0]
  assert ev["details"]["reconstructed"] is True
  assert ev["details"]["trigger"] == "cold_save"
  assert ev["details"]["basket_unchanged"] is True
