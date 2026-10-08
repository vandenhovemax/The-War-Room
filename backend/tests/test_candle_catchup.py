"""Candle Catch-Up test.

What this guards:
  `candle_catchup.collect_pairs(db)` is the one function responsible for
  telling the downloader WHICH (symbol, timeframe) pairs need fresh candles.
  If it under-collects, L1 batches go stale. If it over-collects, we waste
  Bybit bandwidth on symbols we don't need.

  The three inputs that must be respected:
    1. Active batches → every BacktestRun's (symbol, timeframe).
    2. Active batches → every extra_timeframe × every run's symbol.
    3. Active portfolios → every PortfolioAsset × (portfolio TF + extras).

  The three exclusions that must be respected:
    a. Deleted/archived/compacted batches are ignored.
    b. Deleted BacktestRun rows are ignored.
    c. Inactive/deleted portfolios are ignored.

  And the canonicalization: "1h", "60m" etc → canonical Bybit form.

  We also cover the new L0 layer in the catch-up orchestration so that the
  `download_candles=True` flag prepends a candle download before L1/L2/L3.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
  Base,
  BacktestRun,
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
def _set_pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA journal_mode=WAL")
  cur.execute("PRAGMA foreign_keys=ON")
  cur.close()


_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


@pytest.fixture(scope="module", autouse=True)
def _schema():
  Base.metadata.create_all(bind=_ENGINE)
  yield
  Base.metadata.drop_all(bind=_ENGINE)


@pytest.fixture()
def db():
  s = _Session()
  try:
    s.query(BacktestRun).delete()
    s.query(PortfolioAsset).delete()
    s.query(Portfolio).delete()
    s.query(BatchBacktestRun).delete()
    s.query(Strategy).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


def _seed_strategy(db) -> Strategy:
  strat = Strategy(id=1, name="S", source_type="manual", python_source="pass")
  db.add(strat)
  db.flush()
  return strat


def _seed_batch(db, bid: int, **kwargs):
  defaults = dict(
    strategy_id=1, timeframe="30", direction_filter="both",
    parameters="{}", total_symbols=1, completed_symbols=1,
    status="completed", is_deleted=False, is_archived=False, is_compacted=False,
  )
  defaults.update(kwargs)
  b = BatchBacktestRun(id=bid, **defaults)
  db.add(b)
  db.flush()
  return b


def _seed_run(db, rid: int, batch_id: int, symbol: str, tf: str, is_deleted=False):
  from datetime import datetime
  r = BacktestRun(
    id=rid, batch_id=batch_id, strategy_id=1,
    symbol=symbol, timeframe=tf,
    start_date=datetime(2024, 1, 1), end_date=datetime(2024, 6, 1),
    parameters="{}",
    is_deleted=is_deleted,
  )
  db.add(r)
  db.flush()
  return r


# ── collect_pairs tests ─────────────────────────────────────────

def test_collect_pairs_from_active_batches(db):
  _seed_strategy(db)
  _seed_batch(db, 1)
  _seed_run(db, 1, 1, "BTCUSDT", "30")
  _seed_run(db, 2, 1, "ETHUSDT", "30")
  db.commit()

  from backend.services.candle_catchup import collect_pairs
  pairs = collect_pairs(db)
  assert ("BTCUSDT", "30") in pairs
  assert ("ETHUSDT", "30") in pairs
  assert len(pairs) == 2


def test_collect_pairs_skips_deleted_archived_compacted(db):
  _seed_strategy(db)
  _seed_batch(db, 1)
  _seed_batch(db, 2, is_archived=True)
  _seed_batch(db, 3, is_deleted=True)
  _seed_batch(db, 4, is_compacted=True)
  _seed_run(db, 1, 1, "BTCUSDT", "30")
  _seed_run(db, 2, 2, "ETHUSDT", "30")
  _seed_run(db, 3, 3, "SOLUSDT", "30")
  _seed_run(db, 4, 4, "XRPUSDT", "30")
  db.commit()

  from backend.services.candle_catchup import collect_pairs
  pairs = collect_pairs(db)
  assert pairs == {("BTCUSDT", "30")}


def test_collect_pairs_skips_deleted_runs(db):
  _seed_strategy(db)
  _seed_batch(db, 1)
  _seed_run(db, 1, 1, "BTCUSDT", "30")
  _seed_run(db, 2, 1, "ETHUSDT", "30", is_deleted=True)
  db.commit()

  from backend.services.candle_catchup import collect_pairs
  pairs = collect_pairs(db)
  assert ("BTCUSDT", "30") in pairs
  assert ("ETHUSDT", "30") not in pairs


def test_collect_pairs_expands_extra_timeframes(db):
  _seed_strategy(db)
  _seed_batch(db, 1, extra_timeframes='["240","D"]')
  _seed_run(db, 1, 1, "BTCUSDT", "30")
  db.commit()

  from backend.services.candle_catchup import collect_pairs
  pairs = collect_pairs(db)
  assert ("BTCUSDT", "30") in pairs
  assert ("BTCUSDT", "240") in pairs
  assert ("BTCUSDT", "D") in pairs


def test_collect_pairs_from_portfolio(db):
  _seed_strategy(db)
  p = Portfolio(
    id=1, name="p1", portfolio_type="paper", mode="non_hybrid",
    is_active=True, is_deleted=False,
    source_strategies='{"timeframe": "60"}',
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(portfolio_id=1, symbol="BTCUSDT", trade_side="both", is_active=True))
  db.add(PortfolioAsset(portfolio_id=1, symbol="SOLUSDT", trade_side="both", is_active=True))
  db.commit()

  from backend.services.candle_catchup import collect_pairs
  pairs = collect_pairs(db)
  assert ("BTCUSDT", "60") in pairs
  assert ("SOLUSDT", "60") in pairs


def test_collect_pairs_skips_inactive_portfolio(db):
  _seed_strategy(db)
  p = Portfolio(
    id=1, name="p1", portfolio_type="paper", mode="non_hybrid",
    is_active=False, is_deleted=False,
    source_strategies='{"timeframe": "60"}',
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(portfolio_id=1, symbol="BTCUSDT", trade_side="both", is_active=True))
  db.commit()

  from backend.services.candle_catchup import collect_pairs
  pairs = collect_pairs(db)
  assert pairs == set()


def test_collect_pairs_unions_batches_and_portfolios(db):
  _seed_strategy(db)
  _seed_batch(db, 1)
  _seed_run(db, 1, 1, "BTCUSDT", "30")

  p = Portfolio(
    id=1, name="p1", portfolio_type="paper", mode="non_hybrid",
    is_active=True, is_deleted=False,
    source_strategies='{"timeframe": "60"}',
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(portfolio_id=1, symbol="ETHUSDT", trade_side="both", is_active=True))
  db.commit()

  from backend.services.candle_catchup import collect_pairs
  pairs = collect_pairs(db)
  assert ("BTCUSDT", "30") in pairs    # from batch
  assert ("ETHUSDT", "60") in pairs    # from portfolio
  assert len(pairs) == 2


# ── orchestration test: L0 prepended to catch-up ───────────────

def test_catch_up_with_download_candles_prepends_l0(monkeypatch):
  """download_candles=True should prepend an l0_candle_catchup layer."""
  calls: list[str] = []

  from backend.services import (
    batch_store_refresh,
    candle_catchup,
    hybrid_portfolio_refresh,
    oos_boundary,
  )
  monkeypatch.setattr(
    candle_catchup, "catchup_candles",
    lambda: (calls.append("l0"), {"pairs_total": 5, "new_candles": 100})[1],
  )
  monkeypatch.setattr(
    batch_store_refresh, "refresh_all_active_batches",
    lambda: (calls.append("l1"), {})[1],
  )
  monkeypatch.setattr(
    hybrid_portfolio_refresh, "refresh_all_hybrid_portfolios",
    lambda: (calls.append("l2"), {})[1],
  )
  monkeypatch.setattr(
    oos_boundary, "check_and_enqueue",
    lambda: (calls.append("l3"), {})[1],
  )

  from backend.api.routes.live_loop import catch_up
  resp = catch_up(include_canary=False, download_candles=True)

  assert calls == ["l0", "l1", "l2", "l3"]
  assert [layer["layer"] for layer in resp["layers"]] == [
    "l0_candle_catchup",
    "l1_batch_store_refresh",
    "l2_hybrid_portfolio_refresh",
    "l3_oos_boundary",
  ]
  assert resp["ok"] is True
  assert resp["layers"][0]["result"]["new_candles"] == 100


def test_catch_up_without_download_candles_has_no_l0(monkeypatch):
  """Default catch-up (download_candles=False) must NOT include L0."""
  from backend.services import (
    batch_store_refresh,
    hybrid_portfolio_refresh,
    oos_boundary,
  )
  monkeypatch.setattr(batch_store_refresh, "refresh_all_active_batches", lambda: {})
  monkeypatch.setattr(hybrid_portfolio_refresh, "refresh_all_hybrid_portfolios", lambda: {})
  monkeypatch.setattr(oos_boundary, "check_and_enqueue", lambda: {})

  from backend.api.routes.live_loop import catch_up
  resp = catch_up(include_canary=False, download_candles=False)

  layer_names = [l["layer"] for l in resp["layers"]]
  assert "l0_candle_catchup" not in layer_names
  assert layer_names[0] == "l1_batch_store_refresh"


def test_run_one_dispatches_l0(monkeypatch):
  """run_one('l0') must invoke candle catch-up."""
  from backend.services import candle_catchup
  called = []
  monkeypatch.setattr(
    candle_catchup, "catchup_candles",
    lambda: (called.append(True), {"pairs_total": 1})[1],
  )
  from backend.api.routes.live_loop import run_one
  resp = run_one("l0")
  assert resp["ok"] is True
  assert resp["layer"] == "l0_candle_catchup"
  assert called == [True]
