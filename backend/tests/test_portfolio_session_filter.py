"""Pins the session / execution-time filter on /full-metrics.

Bug (April 22): portfolios saved from the optimizer with
`session_filter_enabled=True` showed all-hours trades in the KPI panel
while the optimization page showed only exec-eligible trades — producing
a ~3x PnL mismatch and inflated trade counts.

Fix: `_load_portfolio_trades` now applies the stored
`session_filter_exclude_hours` / `session_filter_exclude_days` before
risk scaling, matching cross_strategy and the WFO stitch path.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.app import create_app
import backend.db.database as _db_module
from backend.db.database import get_db
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  Portfolio,
  PortfolioAsset,
  Strategy,
)


_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _pragma(dbapi_conn, _):
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
    for m in (BacktestTrade, BacktestRun, BatchBacktestRun, Strategy,
              PortfolioAsset, Portfolio):
      s.query(m).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


def _seed_portfolio_with_hourly_trades(db) -> int:
  """Create a portfolio with one asset and 24 trades — one per NYT hour.

  Each trade has entry_time at h:00 and net_profit = 100 (all wins, uniform).
  With exclude_hours = {0..8, 16..23} (keep 9..15 inclusive = 7 hours) the
  filter should leave exactly 7 trades regardless of the filter mode.
  """
  strat = Strategy(name="t", source_type="manual", python_source="class Strategy: pass")
  db.add(strat); db.flush()

  batch = BatchBacktestRun(
    strategy_id=strat.id, timeframe="60", direction_filter="long_plus_short",
    status="completed", total_symbols=1, completed_symbols=1, name="B1",
  )
  db.add(batch); db.flush()

  run = BacktestRun(
    strategy_id=strat.id, batch_id=batch.id, symbol="BTCUSDT",
    timeframe="60", status="completed",
    start_date=datetime(2024, 1, 1), end_date=datetime(2024, 12, 31),
  )
  db.add(run); db.flush()

  # 24 trades — 1 per NYT hour. entry_time is naive NYT.
  for h in range(24):
    db.add(BacktestTrade(
      run_id=run.id, trade_num=h + 1, position_id=f"P{h}",
      symbol="BTCUSDT", side="Long",
      entry_time=datetime(2024, 2, 5, h, 0),  # Monday (weekday=0)
      exit_time=datetime(2024, 2, 5, h, 30),
      entry_price=100.0, exit_price=101.0,
      quantity=1.0, net_profit=100.0,
      net_profit_pct=1.0, commission=0.0,
    ))

  p = Portfolio(
    name="test_sf",
    portfolio_type="paper",
    mode="non_hybrid",
    is_active=True,
    is_deleted=False,
    optimization_end_date=datetime(2024, 3, 1),
    source_strategies=json.dumps({
      "batch_id": batch.id,
      "batch_ids": [batch.id],
      "timeframe": "60",
      "session_filter_enabled": True,
      "session_filter_mode": "fixed",
      # Exclude all hours except 9..15 inclusive (7 hours kept)
      "session_filter_exclude_hours": list(range(0, 9)) + list(range(16, 24)),
      "session_filter_exclude_days": [],
    }),
  )
  db.add(p); db.flush()

  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="Long",
    batch_id=batch.id, is_active=True,
    config_name="default", timeframe="60",
  ))
  db.commit()
  return p.id


def test_full_metrics_applies_session_filter(client, db):
  """With 24 hourly trades and hours 9..15 kept, the KPI panel must show 7
  trades and $700 net — not 24 / $2400 which the pre-fix code returned."""
  pid = _seed_portfolio_with_hourly_trades(db)
  r = client.get(f"/api/portfolios/{pid}/full-metrics")
  assert r.status_code == 200, r.text
  payload = r.json()

  m = payload["metrics"]
  assert m, "metrics should not be empty"
  # Pre-fix this was 24 trades / $2400 PnL
  assert int(m.get("Total Positions") or 0) == 7
  assert float(m.get("Total Net Profit") or 0) == pytest.approx(700.0, abs=0.01)


def test_full_metrics_no_filter_when_disabled(client, db):
  """When session_filter_enabled=False, all 24 trades should survive."""
  pid = _seed_portfolio_with_hourly_trades(db)
  # Flip the flag off — leftover exclude list should be ignored.
  p = db.query(Portfolio).filter(Portfolio.id == pid).first()
  s = json.loads(p.source_strategies)
  s["session_filter_enabled"] = False
  p.source_strategies = json.dumps(s)
  db.commit()

  r = client.get(f"/api/portfolios/{pid}/full-metrics")
  assert r.status_code == 200
  m = r.json()["metrics"]
  assert int(m.get("Total Positions") or 0) == 24
  assert float(m.get("Total Net Profit") or 0) == pytest.approx(2400.0, abs=0.01)


def test_full_metrics_weekday_exclusion(client, db):
  """Monday trades must be dropped when weekday 0 is in exclude_days."""
  pid = _seed_portfolio_with_hourly_trades(db)
  p = db.query(Portfolio).filter(Portfolio.id == pid).first()
  s = json.loads(p.source_strategies)
  s["session_filter_exclude_hours"] = []   # keep all hours
  s["session_filter_exclude_days"] = [0]   # drop Monday (our seed day)
  p.source_strategies = json.dumps(s)
  db.commit()

  r = client.get(f"/api/portfolios/{pid}/full-metrics")
  assert r.status_code == 200
  payload = r.json()
  # All seeded trades are Monday → all excluded → empty payload.
  assert payload["metrics"] == {}
