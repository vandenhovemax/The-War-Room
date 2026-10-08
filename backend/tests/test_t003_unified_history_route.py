"""T-003 Delta 7 — `GET /api/portfolios/{id}/history` route.

Pins the contract for the unified portfolio history feed:
  * 404 when the portfolio is missing
  * 400 when ?type filter is unknown
  * Newest-first ordering across BOTH stores (swap_history + LiveEvent)
  * Per-row type discriminator: manual_swap | phase2_refresh
    | phase2_rotation | phase2_no_change
  * ?type filter scopes to the requested type
  * Pagination respects limit/offset on the merged feed
  * Cross-portfolio isolation (portfolio A's events never leak into B)
  * Empty cases (no swap_history, no LiveEvent) return 200 + total=0
  * Non-basket LiveEvent rows (order_placed, error, ...) are excluded
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


def _make_portfolio(db, *, name="p", swap_history=None) -> int:
  p = Portfolio(
    name=name,
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    is_deleted=False,
    source_strategies="{}",
    swap_history=json.dumps(swap_history) if swap_history is not None else "[]",
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


# ── 404 / 400 paths ───────────────────────────────────────────────────


def test_missing_portfolio_returns_404(client, db):
  r = client.get("/api/portfolios/99999/history")
  assert r.status_code == 404


def test_invalid_type_filter_returns_400(client, db):
  pid = _make_portfolio(db)
  r = client.get(
    f"/api/portfolios/{pid}/history",
    params={"type": "not_a_real_type"},
  )
  assert r.status_code == 400


# ── Empty cases ───────────────────────────────────────────────────────


def test_empty_portfolio_returns_zero_entries(client, db):
  pid = _make_portfolio(db)
  r = client.get(f"/api/portfolios/{pid}/history")
  assert r.status_code == 200
  payload = r.json()
  assert payload["portfolio_id"] == pid
  assert payload["entries"] == []
  assert payload["total"] == 0
  assert payload["limit"] == 100
  assert payload["offset"] == 0
  assert payload["type_filter"] is None


def test_swap_history_only_no_live_events(client, db):
  swap_rows = [
    {
      "date": "2026-04-10T08:00:00+00:00",
      "removed": "BTCUSDT_Long",
      "added": "ETHUSDT_Long",
      "reason": "manual rebalance",
    },
  ]
  pid = _make_portfolio(db, swap_history=swap_rows)
  r = client.get(f"/api/portfolios/{pid}/history")
  payload = r.json()
  assert payload["total"] == 1
  e = payload["entries"][0]
  assert e["type"] == "manual_swap"
  assert e["actor"] == "user"
  assert e["source_store"] == "swap_history"
  assert "BTCUSDT_Long" in e["summary"]
  assert "ETHUSDT_Long" in e["summary"]


def test_live_events_only_no_swap_history(client, db):
  pid = _make_portfolio(db)
  now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=now,
    details={
      "trigger": "nightly_cron",
      "basket_unchanged": False,
      "added": ["B1_SOLUSDT_Long"],
      "removed": ["B1_DOGEUSDT_Long"],
      "chosen_basket": ["B1_BTCUSDT_Long", "B1_SOLUSDT_Long"],
      "decided_at_iso": now.isoformat(),
    },
  )
  r = client.get(f"/api/portfolios/{pid}/history")
  payload = r.json()
  assert payload["total"] == 1
  e = payload["entries"][0]
  assert e["type"] == "phase2_refresh"
  assert e["actor"] == "nightly_cron"
  assert e["source_store"] == "live_event"
  assert e["details"]["added"] == ["B1_SOLUSDT_Long"]
  assert e["details"]["removed"] == ["B1_DOGEUSDT_Long"]


# ── Type discriminator coverage ───────────────────────────────────────


def test_all_four_type_discriminators_present(client, db):
  swap_rows = [
    {
      "date": "2026-04-01T09:00:00+00:00",
      "removed": "AUSDT_Long",
      "added": "BUSDT_Long",
      "reason": "manual",
    },
  ]
  pid = _make_portfolio(db, swap_history=swap_rows)
  base = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=base,
    details={"trigger": "nightly_cron", "basket_unchanged": False,
             "decided_at_iso": base.isoformat()},
  )
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_rotation",
    created_at=base + timedelta(days=1),
    details={"trigger": "rotation_day", "added": ["X"], "removed": ["Y"],
             "decided_at_iso": (base + timedelta(days=1)).isoformat()},
  )
  _add_event(
    db, portfolio_id=pid, event_type="phase2_evaluated_no_change",
    created_at=base + timedelta(days=2),
    details={"trigger": "nightly_cron", "basket_unchanged": True,
             "decided_at_iso": (base + timedelta(days=2)).isoformat()},
  )
  r = client.get(f"/api/portfolios/{pid}/history")
  payload = r.json()
  types = sorted({e["type"] for e in payload["entries"]})
  assert types == [
    "manual_swap", "phase2_no_change", "phase2_refresh", "phase2_rotation",
  ]
  assert payload["total"] == 4


# ── Newest-first ordering across both stores ──────────────────────────


def test_chronological_merge_newest_first(client, db):
  # swap_history: April 1 + April 15 (intentionally out of source order)
  swap_rows = [
    {
      "date": "2026-04-15T10:00:00+00:00",
      "removed": "C1_Long", "added": "C2_Long", "reason": "later swap",
    },
    {
      "date": "2026-04-01T08:00:00+00:00",
      "removed": "A1_Long", "added": "A2_Long", "reason": "earlier swap",
    },
  ]
  pid = _make_portfolio(db, swap_history=swap_rows)

  # LiveEvent: April 8 (between the two swaps)
  ev_time = datetime(2026, 4, 8, 12, 0, 0, tzinfo=timezone.utc)
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=ev_time,
    details={
      "trigger": "nightly_cron",
      "decided_at_iso": ev_time.isoformat(),
      "basket_unchanged": False,
    },
  )

  r = client.get(f"/api/portfolios/{pid}/history")
  payload = r.json()
  assert payload["total"] == 3
  # Newest first: Apr 15 (manual) → Apr 8 (live event) → Apr 1 (manual)
  iso_order = [e["timestamp_iso"] for e in payload["entries"]]
  assert iso_order[0].startswith("2026-04-15")
  assert iso_order[1].startswith("2026-04-08")
  assert iso_order[2].startswith("2026-04-01")


# ── Type filter ───────────────────────────────────────────────────────


def test_type_filter_scopes_to_one_type(client, db):
  swap_rows = [
    {"date": "2026-04-01T08:00:00+00:00", "removed": "A", "added": "B"},
    {"date": "2026-04-02T08:00:00+00:00", "removed": "C", "added": "D"},
  ]
  pid = _make_portfolio(db, swap_history=swap_rows)
  now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=now, details={"trigger": "nightly_cron"},
  )
  _add_event(
    db, portfolio_id=pid, event_type="phase2_evaluated_no_change",
    created_at=now + timedelta(hours=1),
    details={"trigger": "nightly_cron", "basket_unchanged": True},
  )

  r = client.get(
    f"/api/portfolios/{pid}/history",
    params={"type": "manual_swap"},
  )
  payload = r.json()
  assert payload["total"] == 2
  assert payload["type_filter"] == "manual_swap"
  assert all(e["type"] == "manual_swap" for e in payload["entries"])

  r2 = client.get(
    f"/api/portfolios/{pid}/history",
    params={"type": "phase2_no_change"},
  )
  payload2 = r2.json()
  assert payload2["total"] == 1
  assert payload2["entries"][0]["type"] == "phase2_no_change"


# ── Pagination ────────────────────────────────────────────────────────


def test_pagination_limit_and_offset(client, db):
  pid = _make_portfolio(db)
  base = datetime(2026, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
  for i in range(5):
    _add_event(
      db, portfolio_id=pid, event_type="hybrid_refresh",
      created_at=base + timedelta(hours=i),
      details={
        "trigger": "nightly_cron",
        "decided_at_iso": (base + timedelta(hours=i)).isoformat(),
      },
    )
  r = client.get(
    f"/api/portfolios/{pid}/history",
    params={"limit": 2, "offset": 1},
  )
  payload = r.json()
  assert payload["total"] == 5
  assert payload["limit"] == 2
  assert payload["offset"] == 1
  assert len(payload["entries"]) == 2
  # Newest-first: full order is hour 4, 3, 2, 1, 0
  # offset=1 → starts at hour 3, limit=2 → returns [hour 3, hour 2]
  ts0 = payload["entries"][0]["timestamp_iso"]
  ts1 = payload["entries"][1]["timestamp_iso"]
  assert "T03:" in ts0
  assert "T02:" in ts1


# ── Cross-portfolio isolation ─────────────────────────────────────────


def test_cross_portfolio_isolation(client, db):
  swap_a = [{"date": "2026-04-01T08:00:00+00:00", "removed": "A", "added": "B"}]
  swap_b = [{"date": "2026-04-02T08:00:00+00:00", "removed": "C", "added": "D"}]
  pid_a = _make_portfolio(db, name="A", swap_history=swap_a)
  pid_b = _make_portfolio(db, name="B", swap_history=swap_b)

  now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
  _add_event(
    db, portfolio_id=pid_a, event_type="hybrid_refresh",
    created_at=now, details={"trigger": "nightly_cron"},
  )
  _add_event(
    db, portfolio_id=pid_b, event_type="hybrid_rotation",
    created_at=now, details={"trigger": "rotation_day"},
  )

  r_a = client.get(f"/api/portfolios/{pid_a}/history").json()
  r_b = client.get(f"/api/portfolios/{pid_b}/history").json()

  # A: manual_swap (Apr 1) + phase2_refresh (Apr 5) — exactly 2
  assert r_a["total"] == 2
  types_a = {e["type"] for e in r_a["entries"]}
  assert types_a == {"manual_swap", "phase2_refresh"}

  # B: manual_swap (Apr 2) + phase2_rotation (Apr 5) — exactly 2
  assert r_b["total"] == 2
  types_b = {e["type"] for e in r_b["entries"]}
  assert types_b == {"manual_swap", "phase2_rotation"}


# ── Excludes non-basket event types ──────────────────────────────────


def test_excludes_non_basket_live_events(client, db):
  pid = _make_portfolio(db)
  now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
  # These should NOT show up in /history
  _add_event(db, portfolio_id=pid, event_type="order_placed",
             created_at=now, details={"order_id": 1})
  _add_event(db, portfolio_id=pid, event_type="error",
             created_at=now, details={"msg": "boom"})
  _add_event(db, portfolio_id=pid, event_type="circuit_breaker",
             created_at=now, details={})
  # This SHOULD show up
  _add_event(db, portfolio_id=pid, event_type="hybrid_refresh",
             created_at=now,
             details={"trigger": "nightly_cron",
                      "decided_at_iso": now.isoformat()})

  r = client.get(f"/api/portfolios/{pid}/history")
  payload = r.json()
  assert payload["total"] == 1
  assert payload["entries"][0]["type"] == "phase2_refresh"


# ── WFAS-rebuild swap_history shape ──────────────────────────────────


def test_wfas_rebuild_swap_history_mapped_to_phase2_refresh(client, db):
  """`wfo.py:save_phase2_rebuild` writes a typed entry with shape:
       {date, type:'phase2_refresh', added, removed, unchanged,
        new_count, old_count}
  Verify it lands as type=phase2_refresh actor=user, with the diff
  exposed in details.
  """
  swap_rows = [
    {
      "date": "2026-04-10T08:00:00+00:00",
      "type": "phase2_refresh",
      "added": ["B1_SOLUSDT_Long"],
      "removed": ["B1_DOGEUSDT_Long"],
      "unchanged": ["B1_BTCUSDT_Long"],
      "new_count": 2,
      "old_count": 2,
    },
  ]
  pid = _make_portfolio(db, swap_history=swap_rows)
  r = client.get(f"/api/portfolios/{pid}/history")
  payload = r.json()
  assert payload["total"] == 1
  e = payload["entries"][0]
  assert e["type"] == "phase2_refresh"
  assert e["actor"] == "user"
  assert e["source_store"] == "swap_history"
  assert e["details"]["added"] == ["B1_SOLUSDT_Long"]
  assert e["details"]["removed"] == ["B1_DOGEUSDT_Long"]
  assert e["details"]["unchanged"] == ["B1_BTCUSDT_Long"]


# ── Reconstructed flag ────────────────────────────────────────────────


def test_reconstructed_flag_propagated_from_live_event(client, db):
  pid = _make_portfolio(db)
  now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=now,
    details={
      "trigger": "cold_save",
      "reconstructed": True,
      "reconstructed_at_iso": now.isoformat(),
      "decided_at_iso": now.isoformat(),
    },
  )
  r = client.get(f"/api/portfolios/{pid}/history")
  payload = r.json()
  assert payload["total"] == 1
  e = payload["entries"][0]
  assert e["reconstructed"] is True
  assert e["actor"] == "cold_save"


# ── E1 dedup: WFAS-rebuild swap_history twin masks reconstructed LiveEvent ──


def test_dedup_drops_reconstructed_live_event_when_swap_history_twin_exists(client, db):
  """VISION post-ship E1 dedup: the WFAS Refresh button writes BOTH a
  swap_history phase2_refresh row AND a phase2_selection_changes payload
  that cold-save Pass A later reconstructs into a LiveEvent. Without
  dedup the same event surfaces twice in `/history`. Verify the
  reconstructed live_event twin is dropped when a same-day swap-side
  phase2_refresh covers the same added/removed set.
  """
  swap_rows = [
    {
      "date": "2026-04-10T08:00:00+00:00",
      "type": "phase2_refresh",
      "added": ["B1_SOLUSDT_Long"],
      "removed": ["B1_DOGEUSDT_Long"],
      "unchanged": ["B1_BTCUSDT_Long"],
      "new_count": 2,
      "old_count": 2,
    },
  ]
  pid = _make_portfolio(db, swap_history=swap_rows)
  # Cold-save Pass A reconstruction of the same WFAS rebuild — same date,
  # same added/removed sets, reconstructed=true, actor=cold_save.
  _add_event(
    db,
    portfolio_id=pid,
    event_type="hybrid_refresh",
    created_at=datetime(2026, 4, 10, 8, 0, 0, tzinfo=timezone.utc),
    details={
      "trigger": "cold_save",
      "reconstructed": True,
      "decided_at_iso": "2026-04-10T08:00:00+00:00",
      "added": ["B1_SOLUSDT_Long"],
      "removed": ["B1_DOGEUSDT_Long"],
    },
  )
  r = client.get(f"/api/portfolios/{pid}/history")
  payload = r.json()
  # Only the swap_history (user-facing) twin survives.
  assert payload["total"] == 1, payload
  e = payload["entries"][0]
  assert e["source_store"] == "swap_history"
  assert e["actor"] == "user"
  assert e["reconstructed"] is False


def test_dedup_preserves_unrelated_reconstructed_live_events(client, db):
  """Dedup must NOT swallow live events that don't have a twin:
    * different date (real nightly cron, no WFAS rebuild that day)
    * different added/removed set (genuinely separate decision)
    * non-reconstructed live events (live recorder writes)
  All three must survive alongside the swap_history phase2_refresh.
  """
  swap_rows = [
    {
      "date": "2026-04-10T08:00:00+00:00",
      "type": "phase2_refresh",
      "added": ["SOL"],
      "removed": ["DOGE"],
      "unchanged": [],
      "new_count": 1,
      "old_count": 1,
    },
  ]
  pid = _make_portfolio(db, swap_history=swap_rows)

  # 1) Different date — must survive
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=datetime(2026, 4, 11, 8, 0, 0, tzinfo=timezone.utc),
    details={
      "trigger": "cold_save", "reconstructed": True,
      "decided_at_iso": "2026-04-11T08:00:00+00:00",
      "added": ["SOL"], "removed": ["DOGE"],
    },
  )
  # 2) Same date, different basket diff — must survive
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc),
    details={
      "trigger": "cold_save", "reconstructed": True,
      "decided_at_iso": "2026-04-10T12:00:00+00:00",
      "added": ["XRP"], "removed": ["ADA"],
    },
  )
  # 3) Same date + same diff but NOT reconstructed (live recorder write) — must survive
  _add_event(
    db, portfolio_id=pid, event_type="hybrid_refresh",
    created_at=datetime(2026, 4, 10, 23, 0, 0, tzinfo=timezone.utc),
    details={
      "trigger": "nightly_cron", "reconstructed": False,
      "decided_at_iso": "2026-04-10T23:00:00+00:00",
      "added": ["SOL"], "removed": ["DOGE"],
    },
  )

  r = client.get(f"/api/portfolios/{pid}/history")
  payload = r.json()
  # 1 swap_history + 3 unrelated live events = 4
  assert payload["total"] == 4, payload
  # The swap-side row remains
  swap_rows_out = [e for e in payload["entries"] if e["source_store"] == "swap_history"]
  assert len(swap_rows_out) == 1
  # All 3 live events remain (no false-positive dedup)
  live_rows_out = [e for e in payload["entries"] if e["source_store"] == "live_event"]
  assert len(live_rows_out) == 3


# ── Malformed swap_history JSON does not 500 ─────────────────────────


def test_malformed_swap_history_handled_gracefully(client, db):
  p = Portfolio(
    name="malformed",
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    is_deleted=False,
    source_strategies="{}",
    swap_history="this is not valid json",
  )
  db.add(p)
  db.commit()
  pid = p.id

  r = client.get(f"/api/portfolios/{pid}/history")
  assert r.status_code == 200
  payload = r.json()
  assert payload["total"] == 0
  assert payload["entries"] == []
