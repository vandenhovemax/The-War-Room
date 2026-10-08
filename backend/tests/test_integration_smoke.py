"""Integration smoke tests for batch scoring + close mode across the API layer.

Focuses on NEW behavior not already covered by test_analysis_api.py:
  - New scoring objectives via batch optimization
  - process_orders_on_close parameter acceptance
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
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
  Strategy,
)

# ── In-memory test database ─────────────────────────────────────────────

_TEST_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_TEST_ENGINE, "connect")
def _set_pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA journal_mode=WAL")
  cur.execute("PRAGMA foreign_keys=ON")
  cur.close()


_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)


def _override_get_db():
  db = _TestSession()
  try:
    yield db
  finally:
    db.close()


# ── Synthetic data ──────────────────────────────────────────────────────

SYMBOLS = [
  "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
  "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT",
]


def _make_trades(
  run_id: int, symbol: str, side: str, n: int, rng: np.random.Generator,
  start: datetime, spread_days: int = 200,
) -> list[BacktestTrade]:
  trades = []
  for i in range(n):
    day_offset = int(rng.integers(0, spread_days))
    entry_dt = start + timedelta(days=day_offset, hours=int(rng.integers(0, 23)))
    exit_dt = entry_dt + timedelta(hours=int(rng.integers(1, 48)))
    entry_px = float(rng.uniform(100, 50000))
    win = rng.random() < 0.6
    pnl_abs = float(rng.uniform(5, 500))
    net_profit = pnl_abs if win else -pnl_abs
    trades.append(BacktestTrade(
      run_id=run_id,
      trade_num=i + 1,
      position_id=f"T{run_id}_{i}",
      symbol=symbol,
      side=side,
      entry_time=entry_dt,
      exit_time=exit_dt,
      entry_price=entry_px,
      exit_price=entry_px * (1 + net_profit / (entry_px * 0.1)),
      quantity=0.1,
      net_profit=net_profit,
      net_profit_pct=net_profit / (entry_px * 0.1) * 100,
      commission=entry_px * 0.1 * 0.001,
    ))
  return trades


def _seed_db(db):
  rng = np.random.default_rng(42)
  start = datetime(2024, 1, 1)

  strat1 = Strategy(id=1, name="MomentumAlpha", source_type="manual")
  strat2 = Strategy(id=2, name="MeanRevert", source_type="manual")
  db.add_all([strat1, strat2])
  db.flush()

  for batch_id, strat_id in [(1, 1), (2, 2)]:
    batch = BatchBacktestRun(
      id=batch_id, strategy_id=strat_id, timeframe="30m",
      direction_filter="long_plus_short",
      status="completed", total_symbols=10, completed_symbols=10,
      parameters=json.dumps({"initial_capital": 10000}),
    )
    db.add(batch)
    db.flush()

  run_id = 0
  for batch_id in [1, 2]:
    for sym in SYMBOLS:
      for side in ["long", "short"]:
        run_id += 1
        run = BacktestRun(
          id=run_id, strategy_id=(1 if batch_id == 1 else 2),
          symbol=sym, timeframe="30m",
          start_date=start, end_date=start + timedelta(days=200),
          status="completed", batch_id=batch_id,
        )
        db.add(run)
        db.flush()
        n_trades = int(rng.integers(15, 41))
        trades = _make_trades(run_id, sym, side, n_trades, rng, start)
        db.add_all(trades)

  db.commit()


class _FakeResponse:
  def __init__(self, status_code, data):
    self.status_code = status_code
    self._data = data
  def json(self):
    return self._data


def _post_optimize(client, url, **kwargs):
  """Post to async optimize endpoint, poll progress, return result."""
  resp = client.post(url, **kwargs)
  data = resp.json()
  if "task_id" not in data:
    return resp
  task_id = data["task_id"]
  prog = client.get(f"/api/analysis/optimize/progress/{task_id}").json()
  if prog.get("status") == "completed":
    return _FakeResponse(200, prog["result"])
  if prog.get("status") == "error":
    return _FakeResponse(prog.get("http_status", 400), {"detail": prog.get("error", "")})
  return resp


# ── Fixture ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
  Base.metadata.create_all(bind=_TEST_ENGINE)
  db = _TestSession()
  _seed_db(db)
  db.close()

  _orig = _db_module.SessionLocal
  _db_module.SessionLocal = _TestSession

  app = create_app()
  app.dependency_overrides[get_db] = _override_get_db
  with TestClient(app) as c:
    yield c

  _db_module.SessionLocal = _orig
  Base.metadata.drop_all(bind=_TEST_ENGINE)


# ── Tests ───────────────────────────────────────────────────────────────

class TestCrossStrategyBuildSmoke:
  """Smoke tests for cross-strategy-build with new objectives."""

  def test_combined_mode_hybrid(self, client):
    """Combined mode with hybrid objective returns candidates."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 10, "objective": "hybrid",
      "max_seeds": 10, "n_portfolios": 2,
      "prioritize_stability": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data
    assert len(data["candidates"]) >= 1
    c = data["candidates"][0]
    assert "metrics" in c
    assert "net_profit" in c["metrics"]

  @pytest.mark.parametrize("objective", [
    "hybrid", "uncorrelated", "max_sortino", "max_calmar",
    "stability", "max_recovery",
  ])
  def test_combined_mode_all_objectives(self, client, objective):
    """All key objectives should work in combined mode."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 5, "objective": objective,
      "max_seeds": 5, "n_portfolios": 2,
    })
    assert resp.status_code == 200, f"Failed for objective={objective}: {resp.json()}"
    data = resp.json()
    assert len(data.get("candidates", [])) >= 1, f"No candidates for {objective}"


class TestCrossStrategyManualSmoke:
  """Smoke test for manual beam mode."""

  def test_manual_beam_basic(self, client):
    """Manual beam with minimal config should complete."""
    resp = _post_optimize(
      client, "/api/analysis/cross-strategy-build-manual",
      json={
        "batch_ids": [1, 2],
        "allocations": {
          "1": {"n_longs": 3, "n_shorts": 3},
          "2": {"n_longs": 3, "n_shorts": 3},
        },
        "l2_groups": [[1, 2]],
        "n_portfolios": 2,
        "max_seeds": 5,
      },
    )
    assert resp.status_code == 200, f"Failed: {resp.json()}"
    data = resp.json()
    assert "candidates" in data


class TestBacktestCloseModeSmoke:
  """Verify the backtest API accepts process_orders_on_close parameter."""

  def test_backtest_request_schema_accepts_close_mode(self, client):
    """POST /api/backtest/run should accept process_orders_on_close."""
    # We can't run a full backtest without OHLCV data and a real strategy,
    # but we can verify the schema accepts the parameter by checking that
    # the error is about missing strategy/data, NOT about an unknown field.
    resp = client.post("/api/backtest/run", json={
      "strategy_id": 999,
      "symbol": "TESTUSDT",
      "timeframe": "30",
      "process_orders_on_close": True,
    })
    # Should fail with strategy not found, not validation error
    data = resp.json()
    # 404 or 500 from missing strategy, not 422 from schema validation
    assert resp.status_code != 422, (
      f"process_orders_on_close rejected by schema: {data}"
    )

  def test_batch_request_schema_accepts_close_mode(self, client):
    """POST /api/backtest/batch/run should accept process_orders_on_close."""
    resp = client.post("/api/backtest/batch/run", json={
      "strategy_id": 999,
      "symbols": ["TESTUSDT"],
      "timeframe": "30",
      "process_orders_on_close": True,
    })
    data = resp.json()
    assert resp.status_code != 422, (
      f"process_orders_on_close rejected by batch schema: {data}"
    )


# ── CS-058: smoke tests must verify OBJECTIVES DIVERGE ──────────────────
#
# Audit finding: the existing parametrized smoke suite (TestCrossStrategy
# BuildSmoke::test_combined_mode_all_objectives) only asserts
# ``len(candidates) >= 1`` per objective. That means an optimizer that
# silently returns the same basket for every objective passes the suite.
#
# The fix per CS-058 / TC-017: at minimum, assert that 3+ objectives
# produce distinct sort-normalized baskets. "Sort-normalized" so we're
# not brittle to ordering within the basket.

class TestCS058ObjectiveDistinctness:
  """Lock the invariant that semantically-different objectives do NOT
  silently collapse to the same answer on the same seeded data.
  """

  def _basket_signature(self, candidate: dict) -> tuple:
    """Sort-normalized asset tuple — ignores basket ordering."""
    return tuple(sorted(str(a) for a in candidate.get("assets", [])))

  def _top_basket(self, client, url: str, objective: str) -> tuple:
    resp = _post_optimize(client, url, json={
      "batch_id": 1, "n_assets": 3, "objective": objective,
    })
    assert resp.status_code == 200, (
      f"CS-058 FIXTURE: objective {objective!r} failed to optimize: "
      f"{resp.json()}"
    )
    candidates = resp.json()["candidates"]
    assert candidates, (
      f"CS-058 FIXTURE: objective {objective!r} returned zero candidates "
      "— the distinctness check needs at least one basket per objective."
    )
    return self._basket_signature(candidates[0])

  def test_three_objectives_produce_at_least_two_distinct_baskets(self, client):
    """Across ``hybrid``, ``max_calmar``, ``min_ulcer`` the top basket
    MUST not be identical across all three — these are semantically
    different objective functions on the same pool.

    Note: we don't require all three to differ (a small synthetic pool
    can legitimately have two objectives converge to the same global
    optimum). We require that they don't *all* collapse — that's the
    silent-failure mode CS-058 describes.
    """
    baskets = {
      obj: self._top_basket(client, "/api/analysis/optimize", obj)
      for obj in ("hybrid", "max_calmar", "min_ulcer")
    }
    distinct = set(baskets.values())
    assert len(distinct) >= 2, (
      "CS-058 REGRESSION: three semantically-different objectives "
      f"collapsed to the same top basket {next(iter(distinct))!r}. "
      "The optimizer is ignoring the objective parameter or the "
      "metric scoring has broken — the parametrized smoke suite "
      "would have passed without catching this.\n"
      f"Baskets: {baskets!r}"
    )

  def test_cross_strategy_objectives_produce_distinct_baskets(self, client):
    """Same distinctness invariant, but on the cross-strategy-build
    endpoint — this is the path the audit (CS-058) flagged.
    """
    baskets = {}
    for objective in ("hybrid", "max_calmar", "max_sortino"):
      resp = _post_optimize(
        client, "/api/analysis/cross-strategy-build",
        json={
          "batch_ids": [1, 2], "split_mode": False,
          "n_assets": 5, "objective": objective,
          "max_seeds": 5, "n_portfolios": 2,
        },
      )
      assert resp.status_code == 200, (
        f"CS-058 FIXTURE: cross-strategy {objective!r} failed: "
        f"{resp.json()}"
      )
      candidates = resp.json()["candidates"]
      assert candidates, f"CS-058 FIXTURE: zero candidates for {objective!r}"
      baskets[objective] = self._basket_signature(candidates[0])

    distinct = set(baskets.values())
    assert len(distinct) >= 2, (
      "CS-058 REGRESSION: three cross-strategy objectives all produced "
      f"the same top basket {next(iter(distinct))!r}. The objective "
      "parameter is being ignored or the enrichment step is overwriting "
      f"objective-specific scoring.\nBaskets: {baskets!r}"
    )
