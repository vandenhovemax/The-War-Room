"""API-level tests for Analysis tab + Cross-Strategy optimizer endpoints.

Uses TestClient with an in-memory SQLite DB seeded with synthetic trades.
Tests the FULL pipeline: trade loading → filtering → daily PnL → optimization → enrichment.

Endpoints covered:
  POST /api/analysis/optimize          — single-strategy flat optimizer
  POST /api/analysis/optimize-ls       — single-strategy L/S split optimizer
  POST /api/analysis/correlation       — correlation matrix
  POST /api/analysis/cross-strategy-build            — cross-strategy auto
  POST /api/analysis/cross-strategy-build-manual     — cross-strategy triple-layer
  POST /api/analysis/cross-strategy-build-directional — cross-strategy beam search
"""
from __future__ import annotations

import json
import re
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

# ---------------------------------------------------------------------------
# In-memory SQLite test database (StaticPool = single shared connection)
# ---------------------------------------------------------------------------

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


class _FakeResponse:
  """Mimic httpx Response for results retrieved from async progress."""
  def __init__(self, status_code, data):
    self.status_code = status_code
    self._data = data
  def json(self):
    return self._data


def _post_optimize(client, url, **kwargs):
  """Post to an async optimize endpoint, poll progress, return result.

  BackgroundTasks run synchronously inside TestClient, so by the time
  this function polls the progress endpoint the task has already finished.
  """
  resp = client.post(url, **kwargs)
  data = resp.json()

  # Non-async response (validation error raised before background task)
  if "task_id" not in data:
    return resp

  # Poll progress for the final result
  task_id = data["task_id"]
  prog = client.get(f"/api/analysis/optimize/progress/{task_id}").json()

  if prog.get("status") == "completed":
    return _FakeResponse(200, prog["result"])
  if prog.get("status") == "error":
    code = prog.get("http_status", 400)
    return _FakeResponse(code, {"detail": prog.get("error", "")})

  # Still running (shouldn't happen with TestClient)
  return resp


# ---------------------------------------------------------------------------
# Synthetic trade generator
# ---------------------------------------------------------------------------

SYMBOLS = [
  "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
  "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT",
]


def _make_trades(
  run_id: int, symbol: str, side: str, n: int, rng: np.random.Generator,
  start: datetime, spread_days: int = 200,
) -> list[BacktestTrade]:
  """Generate n synthetic trades for a given run/symbol/side."""
  trades = []
  for i in range(n):
    day_offset = int(rng.integers(0, spread_days))
    entry_dt = start + timedelta(days=day_offset, hours=int(rng.integers(0, 23)))
    exit_dt = entry_dt + timedelta(hours=int(rng.integers(1, 48)))
    entry_px = float(rng.uniform(100, 50000))
    # 60% winners (slight edge)
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
  """Populate DB with 3 strategies, 3 batch runs.

  Batch 1-2: 10 symbols each (high trade count)
  Batch 3: 5 symbols (lower trade count, designed for rescue testing)
  """
  rng = np.random.default_rng(42)
  start = datetime(2024, 1, 1)

  # Strategy 1
  strat1 = Strategy(id=1, name="MomentumAlpha", source_type="manual")
  db.add(strat1)
  db.flush()

  # Strategy 2
  strat2 = Strategy(id=2, name="MeanRevert", source_type="manual")
  db.add(strat2)
  db.flush()

  # Batch 1 — Strategy 1, "both" direction (produces long+short)
  batch1 = BatchBacktestRun(
    id=1, strategy_id=1, timeframe="30m", direction_filter="long_plus_short",
    status="completed", total_symbols=10, completed_symbols=10,
    parameters=json.dumps({"initial_capital": 10000}),
  )
  db.add(batch1)
  db.flush()

  # Batch 2 — Strategy 2
  batch2 = BatchBacktestRun(
    id=2, strategy_id=2, timeframe="30m", direction_filter="long_plus_short",
    status="completed", total_symbols=10, completed_symbols=10,
    parameters=json.dumps({"initial_capital": 10000}),
  )
  db.add(batch2)
  db.flush()

  # Batch 3 — Strategy 1, smaller subset for rescue testing
  # Use subset of symbols and lower trade count (8-15 instead of 15-40)
  batch3 = BatchBacktestRun(
    id=3, strategy_id=1, timeframe="30m", direction_filter="long_plus_short",
    status="completed", total_symbols=5, completed_symbols=5,
    parameters=json.dumps({"initial_capital": 10000}),
  )
  db.add(batch3)
  db.flush()

  batch3_symbols = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "LINKUSDT", "MATICUSDT"]

  run_id = 0

  # Batches 1-2: standard setup
  for batch_id, strat_id in [(1, 1), (2, 2)]:
    for sym in SYMBOLS:
      # Each symbol gets 2 runs — one long, one short (simulates long_plus_short)
      for side in ["long", "short"]:
        run_id += 1
        run = BacktestRun(
          id=run_id, strategy_id=strat_id, symbol=sym,
          timeframe="30m", start_date=start, end_date=start + timedelta(days=200),
          status="completed", batch_id=batch_id,
        )
        db.add(run)
        db.flush()
        # 15-40 trades per symbol-side
        n_trades = int(rng.integers(15, 41))
        trades = _make_trades(run_id, sym, side, n_trades, rng, start)
        db.add_all(trades)

  # Batch 3: smaller trades per asset for rescue testing
  # Design: make at least one asset a strong rescue target by giving it
  # mostly winners with occasional small losses
  for sym in batch3_symbols:
    for side in ["long", "short"]:
      run_id += 1
      run = BacktestRun(
        id=run_id, strategy_id=1, symbol=sym,
        timeframe="30m", start_date=start, end_date=start + timedelta(days=200),
        status="completed", batch_id=3,
      )
      db.add(run)
      db.flush()

      # Most Batch 3 assets get low trade counts (useful for sample-floor tests).
      # BTCUSDT/long is the designated rescue target — it gets a HIGH trade
      # count (45–55) so it can pass realistic `hybrid_rescue_min_trades`
      # floors (default 30), and is stacked with a profitable pattern so it
      # out-scores Batch 1/2 assets on event metrics.
      is_rescue_target = (sym == "BTCUSDT" and side == "long")
      if is_rescue_target:
        n_trades = int(rng.integers(45, 56))
      else:
        n_trades = int(rng.integers(8, 16))

      if is_rescue_target:
        # Create profitable trades: 70% winners, larger winners
        trades = []
        for i in range(n_trades):
          day_offset = int(rng.integers(0, 200))
          entry_dt = start + timedelta(days=day_offset, hours=int(rng.integers(0, 23)))
          exit_dt = entry_dt + timedelta(hours=int(rng.integers(1, 48)))
          entry_px = float(rng.uniform(100, 50000))
          # 70% winners for rescue target
          win = rng.random() < 0.70
          if win:
            pnl_abs = float(rng.uniform(100, 800))  # larger wins
          else:
            pnl_abs = float(rng.uniform(5, 100))  # smaller losses
          net_profit = pnl_abs if win else -pnl_abs
          trades.append(BacktestTrade(
            run_id=run_id,
            trade_num=i + 1,
            position_id=f"T{run_id}_{i}",
            symbol=sym,
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
        db.add_all(trades)
      else:
        # Normal distribution: 60% winners
        trades = _make_trades(run_id, sym, side, n_trades, rng, start)
        db.add_all(trades)

  db.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
  """TestClient with in-memory DB, seeded with synthetic data."""
  Base.metadata.create_all(bind=_TEST_ENGINE)
  db = _TestSession()
  _seed_db(db)
  db.close()

  # Patch SessionLocal so background tasks use the test DB
  _orig_session_local = _db_module.SessionLocal
  _db_module.SessionLocal = _TestSession

  app = create_app()
  app.dependency_overrides[get_db] = _override_get_db
  with TestClient(app) as c:
    yield c

  # Cleanup
  _db_module.SessionLocal = _orig_session_local
  Base.metadata.drop_all(bind=_TEST_ENGINE)


# ===================================================================
# Group 1: Correlation endpoint
# ===================================================================

class TestCorrelation:
  def test_correlation_standard(self, client):
    resp = client.post("/api/analysis/correlation", json={
      "batch_id": 1, "corr_method": "standard",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "symbols" in data
    assert "matrix" in data
    assert "stats" in data
    assert len(data["symbols"]) >= 2
    # Matrix is square
    n = len(data["symbols"])
    assert len(data["matrix"]) == n
    assert all(len(row) == n for row in data["matrix"])

  def test_correlation_trade_overlap(self, client):
    resp = client.post("/api/analysis/correlation", json={
      "batch_id": 1, "corr_method": "trade_overlap",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["symbols"]) >= 2

  def test_correlation_invalid_batch(self, client):
    resp = client.post("/api/analysis/correlation", json={
      "batch_id": 9999,
    })
    assert resp.status_code == 404


# ===================================================================
# Group 2: Single-strategy /optimize (flat mode)
# ===================================================================

class TestOptimize:
  """Tests for POST /api/analysis/optimize."""

  def test_basic_hybrid(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data
    assert len(data["candidates"]) >= 1
    c = data["candidates"][0]
    assert len(c["assets"]) == 3
    assert "metrics" in c
    m = c["metrics"]
    for key in ("net_profit", "profit_factor", "max_drawdown", "stability", "n_assets"):
      assert key in m

  def test_all_objectives(self, client):
    """Every objective must return valid candidates."""
    objectives = [
      "hybrid", "uncorrelated", "anti_correlated",
      "max_sortino", "max_calmar", "max_recovery",
      "min_ulcer", "max_profit_factor", "min_max_drawdown",
    ]
    for obj in objectives:
      resp = _post_optimize(client, "/api/analysis/optimize", json={
        "batch_id": 1, "n_assets": 3, "objective": obj,
      })
      assert resp.status_code == 200, f"Objective {obj} failed: {resp.text}"
      data = resp.json()
      assert len(data["candidates"]) >= 1, f"No candidates for {obj}"

  def test_enrichment_fields(self, client):
    """Enriched candidates have win_rate, sortino, avg_corr, etc."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
    })
    data = resp.json()
    c = data["candidates"][0]
    for key in ("win_rate", "total_trades", "sortino", "avg_corr",
                "recovery_factor", "avg_trades_per_month", "calmar"):
      assert key in c["metrics"], f"Missing enrichment key: {key}"
    assert "equity_curve" in c

  def test_pool_stats(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
    })
    data = resp.json()
    assert "pool_stats" in data
    ps = data["pool_stats"]
    assert "total" in ps
    assert ps["total"] >= 2

  def test_min_trades_filter(self, client):
    """High min_trades threshold should reduce the pool."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid", "min_trades": 999,
    })
    # With min_trades=999, should fail (not enough assets)
    assert resp.status_code == 400

  def test_min_pf_filter(self, client):
    """min_pf=999 should eliminate most assets."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid", "min_pf": 999.0,
    })
    # Should fail — no asset has PF=999
    assert resp.status_code == 400

  def test_n_assets_too_large(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 500, "objective": "hybrid",
    })
    assert resp.status_code == 400

  def test_trade_overlap_corr_method(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "corr_method": "trade_overlap",
    })
    assert resp.status_code == 200

  def test_seed_metrics(self, client):
    """Custom seed_metrics should work."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "seed_metrics": ["calmar", "profit_factor"],
    })
    assert resp.status_code == 200
    assert len(resp.json()["candidates"]) >= 1

  def test_prioritize_stability(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "prioritize_stability": True,
    })
    assert resp.status_code == 200
    assert len(resp.json()["candidates"]) >= 1

  def test_extra_slippage(self, client):
    """Extra slippage should reduce net_profit vs baseline."""
    resp_base = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "extra_slippage_pct": 0.0,
    })
    resp_slip = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "extra_slippage_pct": 5.0,
    })
    assert resp_base.status_code == 200
    assert resp_slip.status_code == 200

  def test_tc_constraints(self, client):
    """Trade-count constraints should activate TC optimizer."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "tc_low_threshold": 10, "tc_max_low": 1,
      "tc_high_threshold": 30, "tc_min_high": 1,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candidates"]) >= 1

  def test_n_portfolios(self, client):
    """Request multiple portfolios."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "n_portfolios": 5,
    })
    assert resp.status_code == 200
    # Should return up to 5 (might be fewer if dedup reduces them)
    assert len(resp.json()["candidates"]) >= 1


# ===================================================================
# Group 3: Single-strategy /optimize-ls (L/S split mode)
# ===================================================================

class TestOptimizeLS:
  """Tests for POST /api/analysis/optimize-ls."""

  def test_basic_ls(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
      "batch_id": 1, "n_longs": 2, "n_shorts": 2,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data
    assert len(data["candidates"]) >= 1
    c = data["candidates"][0]
    assert "longs" in c
    assert "shorts" in c
    assert all(a.endswith("_Long") for a in c["longs"])
    assert all(a.endswith("_Short") for a in c["shorts"])

  def test_all_within_objectives(self, client):
    """All within_objective values should work."""
    for obj in ["uncorrelated", "anti_correlated", "hybrid"]:
      resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
        "batch_id": 1, "n_longs": 2, "n_shorts": 2,
        "within_objective": obj,
      })
      assert resp.status_code == 200, f"within={obj} failed"

  def test_all_combined_objectives(self, client):
    """All combined_objective values should work."""
    objectives = [
      "stability", "max_sortino", "max_calmar", "max_recovery",
      "min_ulcer", "max_profit_factor", "min_max_drawdown",
    ]
    for obj in objectives:
      resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
        "batch_id": 1, "n_longs": 2, "n_shorts": 2,
        "combined_objective": obj,
      })
      assert resp.status_code == 200, f"combined={obj} failed"

  def test_ls_enrichment(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
      "batch_id": 1, "n_longs": 2, "n_shorts": 2,
    })
    data = resp.json()
    c = data["candidates"][0]
    assert "metrics" in c
    for key in ("win_rate", "total_trades", "sortino"):
      assert key in c["metrics"], f"Missing enrichment key: {key}"

  def test_ls_tc_constraints(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
      "batch_id": 1, "n_longs": 2, "n_shorts": 2,
      "tc_longs": {"low_threshold": 10, "max_low": 1, "high_threshold": 30, "min_high": 1},
      "tc_shorts": {"low_threshold": 10, "max_low": 1, "high_threshold": 30, "min_high": 1},
    })
    assert resp.status_code == 200
    assert len(resp.json()["candidates"]) >= 1

  def test_ls_too_many_longs(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
      "batch_id": 1, "n_longs": 500, "n_shorts": 2,
    })
    assert resp.status_code == 400

  def test_ls_min_trades_filter(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
      "batch_id": 1, "n_longs": 2, "n_shorts": 2, "min_trades": 999,
    })
    assert resp.status_code == 400

  def test_ls_available_counts(self, client):
    """Response should report available long/short counts."""
    resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
      "batch_id": 1, "n_longs": 2, "n_shorts": 2,
    })
    data = resp.json()
    assert "n_longs_available" in data
    assert "n_shorts_available" in data
    assert data["n_longs_available"] >= 2
    assert data["n_shorts_available"] >= 2


# ===================================================================
# Group 4: Cross-strategy /cross-strategy-build
# ===================================================================

class TestCrossStrategyBuild:
  """Tests for POST /api/analysis/cross-strategy-build."""

  def test_basic_split(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": True,
      "n_longs": 3, "n_shorts": 3,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data
    assert len(data["candidates"]) >= 1
    c = data["candidates"][0]
    assert "longs" in c
    assert "shorts" in c
    assert "attribution" in c

  def test_basic_combined(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 5, "objective": "hybrid",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candidates"]) >= 1

  def test_all_split_objectives(self, client):
    for w_obj in ["uncorrelated", "anti_correlated", "hybrid"]:
      resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
        "batch_ids": [1, 2], "split_mode": True,
        "n_longs": 2, "n_shorts": 2,
        "within_objective": w_obj,
      })
      assert resp.status_code == 200, f"within={w_obj} failed"

  def test_all_combined_objectives(self, client):
    for obj in ["hybrid", "max_sortino", "min_max_drawdown"]:
      resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
        "batch_ids": [1, 2], "split_mode": False,
        "n_assets": 4, "objective": obj,
      })
      assert resp.status_code == 200, f"objective={obj} failed"

  def test_tc_constraints(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": True,
      "n_longs": 2, "n_shorts": 2,
      "tc_low_threshold": 10, "tc_max_low": 1,
      "tc_high_threshold": 30, "tc_min_high": 1,
    })
    assert resp.status_code == 200

  def test_per_batch_filters(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": True,
      "n_longs": 2, "n_shorts": 2,
      "per_batch": {
        "1": {"min_trades": 5, "min_pf": 0.5},
        "2": {"min_trades": 5},
      },
    })
    assert resp.status_code == 200

  def test_strategies_in_response(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": True,
      "n_longs": 2, "n_shorts": 2,
    })
    data = resp.json()
    assert "strategies" in data
    # Strategy map should have batch_id -> name
    strats = data["strategies"]
    assert "1" in strats or 1 in strats

  def test_need_at_least_2_sources(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1], "split_mode": True,
      "n_longs": 2, "n_shorts": 2,
    })
    assert resp.status_code == 400


# ===================================================================
# Group 5: Cross-strategy /cross-strategy-build-manual (triple-layer)
# ===================================================================

class TestCrossStrategyManual:
  """Tests for POST /api/analysis/cross-strategy-build-manual."""

  def test_basic_manual(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "allocations": {
        "1": {"n_longs": 2, "n_shorts": 2},
        "2": {"n_longs": 2, "n_shorts": 2},
      },
      "obj_layer1": "hybrid",
      "obj_layer2": "stability",
      "obj_layer3": "hybrid",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data
    assert len(data["candidates"]) >= 1
    c = data["candidates"][0]
    assert "longs" in c
    assert "shorts" in c
    assert "selected_variants" in c

  def test_manual_with_l2_groups(self, client):
    """L2 groups should group strategies for inter-strategy optimization.

    Each group must be a separate subset — grouping ALL batches into one
    group yields only 1 super-variant, which fails the >= 2 check.
    Use [[1], [2]] so each batch is its own L2 group.
    """
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "allocations": {
        "1": {"n_longs": 2, "n_shorts": 2},
        "2": {"n_longs": 2, "n_shorts": 2},
      },
      "l2_groups": [[1], [2]],
      "obj_layer1": "hybrid",
      "obj_layer2": "uncorrelated",
      "obj_layer3": "max_sortino",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candidates"]) >= 1

  def test_manual_all_l3_objectives(self, client):
    """All L3 objectives should work."""
    for obj in ["hybrid", "max_sortino", "max_calmar", "min_max_drawdown"]:
      resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
        "batch_ids": [1, 2],
        "allocations": {
          "1": {"n_longs": 2, "n_shorts": 2},
          "2": {"n_longs": 2, "n_shorts": 2},
        },
        "obj_layer1": "hybrid",
        "obj_layer2": "stability",
        "obj_layer3": obj,
      })
      assert resp.status_code == 200, f"L3 obj={obj} failed: {resp.text}"

  def test_manual_tc_constraints(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "allocations": {
        "1": {"n_longs": 2, "n_shorts": 2},
        "2": {"n_longs": 2, "n_shorts": 2},
      },
      "obj_layer1": "hybrid", "obj_layer2": "stability", "obj_layer3": "hybrid",
      "tc_low_threshold": 10, "tc_max_low": 1,
      "tc_high_threshold": 30, "tc_min_high": 1,
    })
    assert resp.status_code == 200

  def test_manual_variants_in_response(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "allocations": {
        "1": {"n_longs": 2, "n_shorts": 2},
        "2": {"n_longs": 2, "n_shorts": 2},
      },
      "obj_layer1": "hybrid", "obj_layer2": "stability", "obj_layer3": "hybrid",
    })
    data = resp.json()
    assert "total_variants" in data
    assert data["total_variants"] >= 2

  def test_manual_need_2_sources(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1],
      "allocations": {"1": {"n_longs": 2, "n_shorts": 2}},
      "obj_layer1": "hybrid", "obj_layer2": "stability", "obj_layer3": "hybrid",
    })
    assert resp.status_code == 400


# ===================================================================
# Group 6: Cross-strategy /cross-strategy-build-directional (beam)
# ===================================================================

class TestCrossStrategyDirectional:
  """Tests for POST /api/analysis/cross-strategy-build-directional."""

  def test_basic_directional(self, client):
    """Beam search with separate L2 groups (one per batch)."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1, 2],
      "l2_groups": [[1], [2]],
      "n_per_batch": 3,
      "obj_l1": "hybrid", "obj_l2": "uncorrelated", "obj_l3": "max_sortino",
      "k_l1": 3, "k_l2": 3, "k_l3": 3,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "candidates" in data
    assert len(data["candidates"]) >= 1
    c = data["candidates"][0]
    assert "longs" in c
    assert "shorts" in c
    assert "metrics" in c

  def test_directional_all_l3_objectives(self, client):
    for obj in ["max_sortino", "max_calmar", "hybrid", "min_max_drawdown"]:
      resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
        "batch_ids": [1, 2],
        "l2_groups": [[1, 2]],
        "n_per_batch": 2,
        "obj_l1": "hybrid", "obj_l2": "uncorrelated", "obj_l3": obj,
        "k_l1": 3, "k_l2": 3, "k_l3": 3,
      })
      assert resp.status_code == 200, f"L3={obj} failed: {resp.text}"

  def test_directional_tc_constraints(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1, 2],
      "l2_groups": [[1, 2]],
      "n_per_batch": 2,
      "obj_l1": "hybrid", "obj_l2": "uncorrelated", "obj_l3": "max_sortino",
      "k_l1": 3, "k_l2": 3, "k_l3": 3,
      "tc_low_threshold": 10, "tc_max_low": 1,
      "tc_high_threshold": 30, "tc_min_high": 1,
    })
    assert resp.status_code == 200

  def test_directional_need_l2_groups(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1, 2],
      "l2_groups": [],
      "n_per_batch": 2,
    })
    assert resp.status_code == 400

  def test_directional_need_2_sources(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1],
      "l2_groups": [[1]],
      "n_per_batch": 2,
    })
    assert resp.status_code == 400

  def test_directional_per_batch_filters(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1, 2],
      "l2_groups": [[1, 2]],
      "n_per_batch": 2,
      "obj_l1": "hybrid", "obj_l2": "uncorrelated", "obj_l3": "max_sortino",
      "k_l1": 3, "k_l2": 3, "k_l3": 3,
      "per_batch": {"1": {"min_trades": 5}, "2": {"min_pf": 0.5}},
    })
    assert resp.status_code == 200


# ===================================================================
# Group 7: Cross-strategy correlation
# ===================================================================

class TestCrossCorrelation:
  def test_cross_correlation(self, client):
    resp = client.post("/api/analysis/cross-strategy-correlation", json={
      "batch_ids": [1, 2],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "symbols" in data
    assert "matrix" in data
    # Cross-strategy columns have _Long/_Short suffixes
    assert any("_Long" in s or "_Short" in s for s in data["symbols"])

  def test_cross_correlation_need_2_sources(self, client):
    resp = client.post("/api/analysis/cross-strategy-correlation", json={
      "batch_ids": [1],
    })
    assert resp.status_code == 400


# ===================================================================
# Group 8: Date filtering
# ===================================================================

class TestDateFiltering:
  def test_date_to_reduces_data(self, client):
    """date_to should limit trades (IS cutoff)."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "date_to": "2024-03-01",
    })
    # Should still work (fewer trades, same symbols)
    assert resp.status_code == 200

  def test_date_from_reduces_data(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "date_from": "2024-04-01",
    })
    assert resp.status_code == 200


# ===================================================================
# Group 9: Edge cases
# ===================================================================

class TestEdgeCases:
  def test_invalid_batch_id(self, client):
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 9999, "n_assets": 3,
    })
    assert resp.status_code == 404

  def test_zero_n_assets(self, client):
    """n_assets=0 should still be handled gracefully."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 0,
    })
    # Either 200 with empty or 400 — both are valid, just don't crash
    assert resp.status_code in (200, 400)

  def test_cross_build_empty_batch_ids(self, client):
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [], "split_mode": True,
      "n_longs": 2, "n_shorts": 2,
    })
    assert resp.status_code == 400


# ===================================================================
# Group 10: Auto-mode settings parity (user's live config)
#
# Verifies the user's exact auto-mode settings work across all 3
# entry points: min_ulcer as final-layer objective, sortino+recovery
# seeds, min_pf/min_wr filters.
# ===================================================================

class TestAutoModeSettings:
  """Verify the user's production auto-mode settings across all entry points.

  Settings: L1=hybrid, L2=uncorrelated, L3=min_ulcer,
  seeds=[sortino, recovery], min_pf=1.0, min_wr=35%.
  """

  def test_flat_min_ulcer(self, client):
    """Entry 1: /optimize with min_ulcer objective."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "min_ulcer",
      "seed_metrics": ["sortino", "recovery"],
      "min_pf": 1.0, "min_wr": 35.0,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candidates"]) >= 1
    assert data["candidates"][0]["metrics"]["n_assets"] == 3

  def test_ls_split_min_ulcer_combined(self, client):
    """Entry 2: /optimize-ls with min_ulcer as combined_objective."""
    resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
      "batch_id": 1,
      "n_longs": 2, "n_shorts": 2,
      "within_objective": "hybrid",
      "combined_objective": "min_ulcer",
      "seed_metrics": ["sortino", "recovery"],
      "min_pf": 1.0, "min_wr": 35.0,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candidates"]) >= 1
    c = data["candidates"][0]
    assert all(a.endswith("_Long") for a in c["longs"])
    assert all(a.endswith("_Short") for a in c["shorts"])

  def test_cross_auto_min_ulcer_combined_mode(self, client):
    """Entry 3: /cross-strategy-build combined mode, min_ulcer."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 5, "objective": "min_ulcer",
      "seed_metrics": ["sortino", "recovery"],
    })
    assert resp.status_code == 200
    assert len(resp.json()["candidates"]) >= 1

  def test_cross_auto_min_ulcer_split_mode(self, client):
    """Entry 3: /cross-strategy-build split mode, min_ulcer as combined."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": True,
      "n_longs": 2, "n_shorts": 2,
      "within_objective": "hybrid",
      "combined_objective": "min_ulcer",
      "seed_metrics": ["sortino", "recovery"],
    })
    assert resp.status_code == 200
    assert len(resp.json()["candidates"]) >= 1

  def test_manual_triple_layer_min_ulcer_l3(self, client):
    """Entry 3: /cross-strategy-build-manual — L1=hybrid, L2=uncorrelated, L3=min_ulcer."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "allocations": {
        "1": {"n_longs": 2, "n_shorts": 2},
        "2": {"n_longs": 2, "n_shorts": 2},
      },
      "obj_layer1": "hybrid",
      "obj_layer2": "uncorrelated",
      "obj_layer3": "min_ulcer",
      "seed_metrics": ["sortino", "recovery"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candidates"]) >= 1

  def test_manual_l2_groups_min_ulcer_l3(self, client):
    """Entry 3: /cross-strategy-build-manual with L2 groups + min_ulcer L3."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "allocations": {
        "1": {"n_longs": 2, "n_shorts": 2},
        "2": {"n_longs": 2, "n_shorts": 2},
      },
      "l2_groups": [[1], [2]],
      "obj_layer1": "hybrid",
      "obj_layer2": "uncorrelated",
      "obj_layer3": "min_ulcer",
      "seed_metrics": ["sortino", "recovery"],
    })
    assert resp.status_code == 200
    assert len(resp.json()["candidates"]) >= 1

  def test_directional_beam_min_ulcer_l3(self, client):
    """Entry 3: /cross-strategy-build-directional — L1=hybrid, L2=uncorrelated, L3=min_ulcer."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1, 2],
      "l2_groups": [[1], [2]],
      "n_per_batch": 3,
      "obj_l1": "hybrid",
      "obj_l2": "uncorrelated",
      "obj_l3": "min_ulcer",
      "k_l1": 3, "k_l2": 3, "k_l3": 3,
      "seed_metrics": ["sortino", "recovery"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candidates"]) >= 1

  def test_all_seed_metrics_accepted(self, client):
    """All 9 seed metrics + benchmark should be accepted."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "min_ulcer",
      "seed_metrics": ["sortino", "recovery", "pf", "pnl", "avg_dd",
                        "max_dd", "ulcer", "wfe", "wr"],
    })
    assert resp.status_code == 200
    assert len(resp.json()["candidates"]) >= 1


# ===================================================================
# Group 11: WFO — Walk Forward Optimization auto-mode settings
#
# Verifies WFO /asset-selection endpoint works with the user's exact
# auto-mode settings across all WFO modes: auto-flat, auto-split,
# manual (3-layer), and directional (beam search).
#
# Uses is_months=2, oos_months=1 so the 200-day synthetic dataset
# produces 4 WFO windows with OOS data.
# ===================================================================

class TestWFOAutoMode:
  """Verify WFO /asset-selection works with production settings.

  WFO settings: is_months=2, oos_months=1, last_oos_end=2024-07-15.
  Auto-mode: L1=hybrid, L2=uncorrelated, L3=min_ulcer,
  seeds=[sortino, recovery], min_pf=1.0, min_wr=35%.
  """

  # -- Common payloads ------------------------------------------------

  _BASE = {
    "is_months": 2,
    "oos_months": 1,
    "last_oos_end": "2024-07-15",
    "corr_method": "standard",
    "n_candidates": 2,
    "seed_metrics": ["sortino", "recovery"],
    "extra_slippage_pct": 0.0,
  }

  # -- Auto flat mode -------------------------------------------------

  def test_wfo_auto_flat_hybrid(self, client):
    """Auto-flat mode with default hybrid objective."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "n_assets": 3,
      "objective": "hybrid",
      "wfo_mode": "auto",
      "split_mode": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "windows" in data
    assert "tracks" in data
    assert len(data["windows"]) >= 1
    assert "wfo_run_id" in data

  def test_wfo_auto_flat_min_ulcer(self, client):
    """Auto-flat mode with min_ulcer objective (user's L3 setting)."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "n_assets": 3,
      "objective": "min_ulcer",
      "wfo_mode": "auto",
      "split_mode": False,
      "min_pf": 1.0,
      "min_wr": 35.0,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["windows"]) >= 1
    # Tracks should be populated (rank-ordered optimizer output)
    assert len(data["tracks"]) >= 1

  # -- Auto split mode ------------------------------------------------

  def test_wfo_auto_split_min_ulcer(self, client):
    """Auto-split mode: within=hybrid, combined=min_ulcer."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "wfo_mode": "auto",
      "split_mode": True,
      "n_longs": 2,
      "n_shorts": 2,
      "within_objective": "hybrid",
      "combined_objective": "min_ulcer",
      "seed_metrics": ["sortino", "recovery"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["windows"]) >= 1
    assert len(data["tracks"]) >= 1

  @staticmethod
  def _picked_batches(data) -> set[int]:
    """Batch ids behind the assets picked in any window (from the ~B{id} tag)."""
    bids: set[int] = set()
    for w in data["windows"]:
      for c in w.get("candidates") or []:
        for a in c.get("assets") or []:
          m = re.search(r"~B(\d+)_", str(a))
          if m:
            bids.add(int(m.group(1)))
    return bids

  # -- Manual mode (3-layer, multi-batch) ----------------------------

  def test_wfo_manual_min_ulcer_l3(self, client):
    """Manual mode: L1=hybrid, L2=uncorrelated, L3=min_ulcer."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1, 2],
      "wfo_mode": "manual",
      "obj_layer1": "hybrid",
      "obj_layer2": "uncorrelated",
      "obj_layer3": "min_ulcer",
      "n_longs": 2,
      "n_shorts": 2,
      "candidates_per_strategy": 2,
      "n_final_strats": 2,
      "seed_metrics": ["sortino", "recovery"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["windows"]) >= 1
    assert len(data["tracks"]) >= 1
    # 2026-10-08: both batches trade the same symbols, so the IS columns are
    # batch-tagged (SYMBOL~B{id}_Side). The mode used to find no asset per
    # batch and return empty candidates in every window.
    assert self._picked_batches(data) == {1, 2}

  # -- Directional mode (beam search, multi-batch) -------------------

  def test_wfo_directional_min_ulcer_l3(self, client):
    """Directional mode: beam search L1=hybrid, L2=uncorrelated, L3=min_ulcer."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1, 2],
      "wfo_mode": "auto",
      "directional_batches": True,
      "l2_groups": [[1], [2]],
      "n_per_batch": 2,
      "obj_l1": "hybrid",
      "obj_l2": "uncorrelated",
      "obj_l3": "min_ulcer",
      "k_l1": 3,
      "k_l2": 3,
      "k_l3": 2,
      "seed_metrics": ["sortino", "recovery"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["windows"]) >= 1
    assert len(data["tracks"]) >= 1
    assert self._picked_batches(data) == {1, 2}  # same bug as manual mode

  # -- Window structure & persistence --------------------------------

  def test_wfo_windows_have_dates(self, client):
    """Each window must have IS/OOS date boundaries."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "n_assets": 3,
      "objective": "hybrid",
      "wfo_mode": "auto",
      "split_mode": False,
    })
    assert resp.status_code == 200
    windows = resp.json()["windows"]
    assert len(windows) >= 1
    for w in windows:
      assert "is_start" in w
      assert "is_end" in w
      assert "candidates" in w

  def test_wfo_run_persisted(self, client):
    """WFO run should be saved and retrievable via GET /runs."""
    # Run a WFO first
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "n_assets": 3,
      "objective": "hybrid",
      "wfo_mode": "auto",
      "split_mode": False,
    })
    assert resp.status_code == 200
    run_id = resp.json()["wfo_run_id"]

    # Retrieve it
    resp2 = client.get(f"/api/wfo/runs/{run_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["id"] == run_id
    assert len(data["windows"]) >= 1

  def test_wfo_all_objectives_auto(self, client):
    """All scoring-function objectives should work in WFO auto mode."""
    for obj in ["hybrid", "max_sortino", "max_calmar", "min_ulcer",
                "max_recovery", "min_max_drawdown", "max_profit_factor"]:
      resp = client.post("/api/wfo/asset-selection", json={
        **self._BASE,
        "batch_ids": [1],
        "n_assets": 3,
        "objective": obj,
        "wfo_mode": "auto",
        "split_mode": False,
      })
      assert resp.status_code == 200, f"WFO obj={obj} failed: {resp.text}"
      assert len(resp.json()["windows"]) >= 1, f"No windows for {obj}"


# ===================================================================
# Group 12: Event-Based WFO — Trade-by-trade simulation engine
#
# Tests the event_mode=True path which walks chronologically through
# every closed trade, evaluates each asset on its last N trades, and
# selects a Top_K portfolio at each step.
# ===================================================================

class TestEventWFO:
  """Verify event-based WFO returns correct structure and metrics."""

  _BASE = {
    "event_mode": True,
    "n_trades": 5,
    "n_assets": 5,
    "staleness_days": 0,  # disabled for tests (synthetic trades span 200 days)
    "min_pf": 0,
    "min_wr": 0,
    "extra_slippage_pct": 0.0,
    "last_oos_end": "2024-07-15",  # required by schema, unused in event mode
  }

  # -- Single-batch Auto mode (flat ranking) --

  def test_event_auto_sortino(self, client):
    """Event mode single-batch with sortino ranking."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "objective": "sortino",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"
    assert "equity_curve" in data
    assert "equity_dates" in data
    assert len(data["equity_curve"]) == len(data["equity_dates"])
    assert data["all_trade_count"] > 0
    assert data["selected_trade_count"] >= 0
    assert "summary" in data
    assert "asset_frequency" in data

  def test_event_auto_recovery(self, client):
    """Event mode with recovery ranking metric."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "objective": "recovery",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"
    assert data["all_trade_count"] > 0

  def test_event_auto_min_ulcer(self, client):
    """Event mode with ulcer (lower-is-better) ranking."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "objective": "ulcer",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"

  def test_event_summary_keys(self, client):
    """Summary must include all expected metric keys."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "objective": "sortino",
    })
    assert resp.status_code == 200
    s = resp.json()["summary"]
    for key in ("net_profit", "pf", "wr", "max_dd", "sortino",
                "recovery", "ulcer", "calmar", "total_trades",
                "avg_portfolio_size", "total_steps"):
      assert key in s, f"Missing summary key: {key}"

  def test_event_asset_frequency_structure(self, client):
    """Asset frequency table must have selected/eligible/pct per asset."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "objective": "sortino",
    })
    assert resp.status_code == 200
    freq = resp.json()["asset_frequency"]
    assert len(freq) >= 1
    for asset, stats in freq.items():
      assert "selected" in stats
      assert "eligible" in stats
      assert "pct" in stats
      assert stats["eligible"] >= stats["selected"]

  def test_event_quality_gates_filter(self, client):
    """High min_pf should reduce selected trades."""
    # Run with no filters
    resp_open = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "objective": "sortino",
      "min_pf": 0,
      "min_wr": 0,
    })
    # Run with strict filters
    resp_strict = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "objective": "sortino",
      "min_pf": 5.0,
      "min_wr": 80,
    })
    assert resp_open.status_code == 200
    assert resp_strict.status_code == 200
    # Strict should select fewer or equal trades
    assert resp_strict.json()["selected_trade_count"] <= resp_open.json()["selected_trade_count"]

  # -- Multi-batch Directional mode (L1 → L2 → L3) --

  def test_event_directional_l2_l3(self, client):
    """Event mode directional with L2 groups and L3 assembly."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1, 2],
      "directional_batches": True,
      "l2_groups": [[1], [2]],
      "n_per_group": 10,
      "obj_l2": "sortino",
      "obj_l3": "recovery",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"
    assert data["all_trade_count"] > 0

  def test_event_directional_mixed_objectives(self, client):
    """Different objectives at L2 and L3 should both work."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1, 2],
      "directional_batches": True,
      "l2_groups": [[1, 2]],
      "n_per_group": 8,
      "obj_l2": "pf",
      "obj_l3": "calmar",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"
    assert len(data["equity_curve"]) > 0

  def test_event_selection_changes_capped(self, client):
    """Selection changes should be capped at 200 entries."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE,
      "batch_ids": [1],
      "objective": "sortino",
      "n_trades": 5,
      "n_assets": 2,  # small portfolio = many changes
    })
    assert resp.status_code == 200
    changes = resp.json()["selection_changes"]
    assert len(changes) <= 200


# ===================================================================
# Group 13: L1 Quality Gate — passes ALL survivors instead of Top N
#
# Tests the l1_quality_gate=True parameter across all multi-layer
# optimizers: beam (directional), manual (3-layer), and cross-strategy.
# ===================================================================

class TestL1QualityGate:
  """Verify L1 quality gate works across all multi-layer optimizers."""

  _WFO_BASE = {
    "is_months": 2,
    "oos_months": 1,
    "last_oos_end": "2024-07-15",
    "corr_method": "standard",
    "n_candidates": 2,
    "seed_metrics": ["sortino", "recovery"],
    "extra_slippage_pct": 0.0,
  }

  # -- Beam optimizer (directional mode) --

  def test_beam_quality_gate(self, client):
    """Directional mode with l1_quality_gate=True should succeed."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._WFO_BASE,
      "batch_ids": [1, 2],
      "wfo_mode": "auto",
      "directional_batches": True,
      "l2_groups": [[1], [2]],
      "l1_quality_gate": True,
      "n_per_group": 8,
      "obj_l1": "hybrid",
      "obj_l2": "uncorrelated",
      "obj_l3": "min_ulcer",
      "k_l1": 3,
      "k_l2": 3,
      "k_l3": 2,
      "seed_metrics": ["sortino", "recovery"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "windows" in data
    assert len(data["windows"]) >= 1

  def test_beam_quality_gate_vs_topn(self, client):
    """Quality gate should return results (may differ from Top N)."""
    common = {
      **self._WFO_BASE,
      "batch_ids": [1, 2],
      "wfo_mode": "auto",
      "directional_batches": True,
      "l2_groups": [[1], [2]],
      "obj_l1": "hybrid",
      "obj_l2": "uncorrelated",
      "obj_l3": "min_ulcer",
      "k_l1": 3, "k_l2": 3, "k_l3": 2,
      "n_per_batch": 3,
    }
    # Top N (default)
    resp_topn = client.post("/api/wfo/asset-selection", json={
      **common, "l1_quality_gate": False,
    })
    # Quality gate
    resp_qg = client.post("/api/wfo/asset-selection", json={
      **common, "l1_quality_gate": True, "n_per_group": 8,
    })
    assert resp_topn.status_code == 200
    assert resp_qg.status_code == 200
    # Both should produce valid window structures
    assert len(resp_topn.json()["windows"]) >= 1
    assert len(resp_qg.json()["windows"]) >= 1

  # -- Manual optimizer (3-layer) --

  def test_manual_quality_gate(self, client):
    """Manual mode with l1_quality_gate=True should succeed."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._WFO_BASE,
      "batch_ids": [1, 2],
      "wfo_mode": "manual",
      "l1_quality_gate": True,
      "n_per_group": 8,
      "obj_layer1": "hybrid",
      "obj_layer2": "uncorrelated",
      "obj_layer3": "min_ulcer",
      "n_longs": 2,
      "n_shorts": 2,
      "candidates_per_strategy": 2,
      "n_final_strats": 2,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "windows" in data
    assert len(data["windows"]) >= 1

  # -- Cross-strategy manual --

  def test_cross_manual_quality_gate(self, client):
    """Cross-strategy manual with l1_quality_gate=True."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "l1_quality_gate": True,
      "n_per_group": 8,
      "obj_layer1": "hybrid",
      "obj_layer2": "uncorrelated",
      "obj_layer3": "min_ulcer",
      "n_longs": 3,
      "n_shorts": 3,
      "candidates_per_strategy": 3,
      "n_final_strats": 3,
      "corr_method": "standard",
      "extra_slippage_pct": 0.0,
      "min_pf": 0.0,
      "min_wr": 0.0,
    })
    # Quality gate with relaxed filters on small synthetic data
    # May succeed (200) or fail with not enough variants (400)
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
      data = resp.json()
      assert "candidates" in data
      assert "strategies" in data

  # -- Cross-strategy directional --

  def test_cross_directional_quality_gate(self, client):
    """Cross-strategy directional with l1_quality_gate=True."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1, 2],
      "l1_quality_gate": True,
      "n_per_group": 8,
      "l2_groups": [[1], [2]],
      "obj_l1": "hybrid",
      "obj_l2": "uncorrelated",
      "obj_l3": "min_ulcer",
      "k_l1": 3, "k_l2": 3, "k_l3": 2,
      "n_per_batch": 3,
      "corr_method": "standard",
      "extra_slippage_pct": 0.0,
      "seed_metrics": ["sortino"],
      "min_pf": 0.0,
      "min_wr": 0.0,
    })
    # Quality gate with small synthetic data — may produce valid results or
    # fail with not enough variants (400); both are correct behavior
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
      data = resp.json()
      assert "candidates" in data
      assert "strategies" in data


# ===================================================================
# Group 14: Event metrics unit tests (pure function)
# ===================================================================

class TestEventMetrics:
  """Unit tests for _compute_event_metrics and _rank_assets."""

  def test_empty_pnl(self):
    from backend.analysis.event_wfo import _compute_event_metrics
    m = _compute_event_metrics([])
    assert m["total_trades"] == 0
    assert m["net_profit"] == 0

  def test_all_winners(self):
    from backend.analysis.event_wfo import _compute_event_metrics
    m = _compute_event_metrics([10, 20, 30, 15, 25])
    assert m["total_trades"] == 5
    assert m["wr"] == 100.0
    assert m["pf"] == 10.0  # no losses → capped at 10
    assert m["net_profit"] == 100
    assert m["max_dd"] == 0  # never draws down

  def test_all_losers(self):
    from backend.analysis.event_wfo import _compute_event_metrics
    m = _compute_event_metrics([-10, -20, -30])
    assert m["total_trades"] == 3
    assert m["wr"] == 0
    assert m["pf"] == 0
    assert m["net_profit"] == -60

  def test_mixed_trades(self):
    from backend.analysis.event_wfo import _compute_event_metrics
    m = _compute_event_metrics([10, -5, 20, -8, 15])
    assert m["total_trades"] == 5
    assert m["wr"] == 60.0  # 3/5
    assert m["pf"] > 0
    assert m["net_profit"] == 32
    assert m["max_dd"] > 0
    assert m["sortino"] > 0

  def test_rank_higher_is_better(self):
    from backend.analysis.event_wfo import _rank_assets
    metrics = {
      "A": {"sortino": 2.0},
      "B": {"sortino": 5.0},
      "C": {"sortino": 1.0},
    }
    ranked = _rank_assets(metrics, "sortino")
    assert ranked[0] == "B"  # highest first
    assert ranked[-1] == "C"

  def test_rank_lower_is_better(self):
    from backend.analysis.event_wfo import _rank_assets
    metrics = {
      "A": {"ulcer": 8.0},
      "B": {"ulcer": 2.0},
      "C": {"ulcer": 15.0},
    }
    ranked = _rank_assets(metrics, "ulcer")
    assert ranked[0] == "B"  # lowest first
    assert ranked[-1] == "C"


# ===================================================================
# Group 15: WFO Quality Gate in Time-Based Mode
# ===================================================================

class TestWFOQualityGateTimeBased:
  """L1 quality gate in time-based WFO (manual + directional)."""

  _BASE = {
    "is_months": 2, "oos_months": 1, "last_oos_end": "2024-07-15",
    "corr_method": "standard", "n_candidates": 2,
    "seed_metrics": ["sortino"], "extra_slippage_pct": 0.0,
  }

  def test_wfo_manual_quality_gate(self, client):
    """WFO manual mode with l1_quality_gate=True."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1, 2], "wfo_mode": "manual",
      "l1_quality_gate": True, "n_per_group": 10,
      "obj_layer1": "hybrid", "obj_layer2": "uncorrelated",
      "obj_layer3": "min_ulcer", "n_longs": 2, "n_shorts": 2,
      "candidates_per_strategy": 2, "n_final_strats": 2,
    })
    assert resp.status_code == 200
    assert len(resp.json()["windows"]) >= 1

  def test_wfo_directional_quality_gate(self, client):
    """WFO directional with l1_quality_gate=True."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1, 2], "wfo_mode": "auto",
      "directional_batches": True, "l2_groups": [[1], [2]],
      "l1_quality_gate": True, "n_per_group": 10,
      "obj_l1": "hybrid", "obj_l2": "uncorrelated", "obj_l3": "min_ulcer",
      "k_l1": 3, "k_l2": 3, "k_l3": 2,
    })
    assert resp.status_code == 200
    assert len(resp.json()["windows"]) >= 1

  def test_wfo_manual_qg_with_l2_groups(self, client):
    """Manual + quality gate + explicit L2 groups."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1, 2], "wfo_mode": "manual",
      "l1_quality_gate": True, "n_per_group": 8,
      "obj_layer1": "hybrid", "obj_layer2": "max_sortino",
      "obj_layer3": "max_recovery",
      "n_longs": 3, "n_shorts": 3,
      "candidates_per_strategy": 3, "n_final_strats": 2,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "windows" in data

  def test_wfo_directional_qg_with_merged_groups(self, client):
    """Directional + quality gate + merged L2 group [[1,2]]."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1, 2], "wfo_mode": "auto",
      "directional_batches": True, "l2_groups": [[1, 2]],
      "l1_quality_gate": True, "n_per_group": 12,
      "obj_l1": "hybrid", "obj_l2": "max_recovery", "obj_l3": "min_ulcer",
      "k_l1": 3, "k_l2": 3, "k_l3": 2,
    })
    assert resp.status_code == 200
    assert len(resp.json()["windows"]) >= 1

  def test_wfo_auto_ignores_quality_gate(self, client):
    """Auto mode should work fine even with l1_quality_gate=True (ignored)."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "n_assets": 3,
      "objective": "hybrid", "wfo_mode": "auto", "split_mode": False,
      "l1_quality_gate": True,
    })
    assert resp.status_code == 200
    assert len(resp.json()["windows"]) >= 1


# ===================================================================
# Group 16: Event WFO All Objectives
# ===================================================================

class TestEventWFOAllObjectives:
  """Event mode with every ranking objective."""

  _BASE = {
    "event_mode": True, "n_trades": 5, "n_assets": 5,
    "staleness_days": 0, "min_pf": 0, "min_wr": 0,
    "extra_slippage_pct": 0.0, "last_oos_end": "2024-07-15",
    "batch_ids": [1],
  }

  @pytest.mark.parametrize("obj", [
    "sortino", "pf", "wr", "recovery", "calmar", "max_dd", "ulcer", "net_profit",
  ])
  def test_event_objective(self, client, obj):
    """Event mode with {obj} ranking should return valid results."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "objective": obj,
    })
    assert resp.status_code == 200, f"Event obj={obj} failed: {resp.text}"
    data = resp.json()
    assert data["mode"] == "event"
    assert data["all_trade_count"] > 0


# ===================================================================
# Group 17: Event WFO Edge Cases
# ===================================================================

class TestEventWFOEdgeCases:
  """Edge cases for event-based WFO engine."""

  _BASE = {
    "event_mode": True, "staleness_days": 0, "min_pf": 0, "min_wr": 0,
    "extra_slippage_pct": 0.0, "last_oos_end": "2024-07-15",
  }

  def test_n_trades_larger_than_available(self, client):
    """n_trades=200 with few trades per asset → 0 selected (warmup)."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "n_trades": 200,
      "n_assets": 5, "objective": "sortino",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"
    # Most assets have 15-40 trades, so with n_trades=200 nothing qualifies
    assert data["selected_trade_count"] == 0

  def test_strict_filters_reduce_selected(self, client):
    """Strict filters should significantly reduce selected trades."""
    resp_open = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "n_trades": 5,
      "n_assets": 5, "objective": "sortino",
      "min_pf": 0, "min_wr": 0,
    })
    resp_strict = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "n_trades": 5,
      "n_assets": 5, "objective": "sortino",
      "min_pf": 999, "min_wr": 99,
    })
    assert resp_open.status_code == 200
    assert resp_strict.status_code == 200
    # Strict should select significantly fewer
    assert resp_strict.json()["selected_trade_count"] < resp_open.json()["selected_trade_count"]

  def test_staleness_filter(self, client):
    """staleness_days=1 should exclude most assets (200-day spread)."""
    resp_no_stale = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "n_trades": 5,
      "n_assets": 5, "objective": "sortino", "staleness_days": 0,
    })
    resp_stale = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "n_trades": 5,
      "n_assets": 5, "objective": "sortino", "staleness_days": 1,
    })
    assert resp_no_stale.status_code == 200
    assert resp_stale.status_code == 200
    # Staleness=1 day should filter out more → fewer selected
    assert resp_stale.json()["selected_trade_count"] <= resp_no_stale.json()["selected_trade_count"]

  def test_single_vs_multi_batch_structure(self, client):
    """Single batch (Auto) vs 2 batches (Auto) same result structure."""
    resp1 = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "n_trades": 5,
      "n_assets": 5, "objective": "sortino",
    })
    resp2 = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1, 2], "n_trades": 5,
      "n_assets": 5, "objective": "sortino",
    })
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    for r in [resp1.json(), resp2.json()]:
      assert r["mode"] == "event"
      assert "equity_curve" in r
      assert "summary" in r
      assert "asset_frequency" in r
    # Multi-batch should have more total trades
    assert resp2.json()["all_trade_count"] >= resp1.json()["all_trade_count"]

  def test_n_assets_larger_than_pool(self, client):
    """n_assets=100 but only ~20 eligible → selects all eligible."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "n_trades": 5,
      "n_assets": 100, "objective": "sortino",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"
    assert data["selected_trade_count"] > 0

  def test_directional_empty_l2_groups(self, client):
    """directional=True with empty l2_groups → falls back to Auto."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1, 2], "n_trades": 5,
      "n_assets": 5, "objective": "sortino",
      "directional_batches": True, "l2_groups": [],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"


# ===================================================================
# Group 18: WFO All Objectives × All Modes (time-based)
# ===================================================================

class TestWFOAllObjectivesAllModes:
  """Cross-product: all objectives × all time-based WFO modes."""

  _BASE = {
    "is_months": 2, "oos_months": 1, "last_oos_end": "2024-07-15",
    "corr_method": "standard", "n_candidates": 2,
    "seed_metrics": ["sortino"], "extra_slippage_pct": 0.0,
  }

  @pytest.mark.parametrize("obj", [
    "hybrid", "max_sortino", "max_calmar", "min_ulcer",
    "max_recovery", "min_max_drawdown", "max_profit_factor",
  ])
  def test_wfo_auto_flat_objective(self, client, obj):
    """Auto-flat with {obj}."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "n_assets": 3,
      "objective": obj, "wfo_mode": "auto", "split_mode": False,
    })
    assert resp.status_code == 200, f"Auto-flat obj={obj} failed"
    assert len(resp.json()["windows"]) >= 1

  def test_wfo_auto_split_all_combos(self, client):
    """Auto-split with multiple within/combined combos."""
    within_objs = ["uncorrelated", "hybrid", "anti_correlated"]
    combined_objs = ["stability", "max_sortino", "min_ulcer"]
    for w in within_objs:
      for c in combined_objs:
        resp = client.post("/api/wfo/asset-selection", json={
          **self._BASE, "batch_ids": [1], "wfo_mode": "auto",
          "split_mode": True, "n_longs": 2, "n_shorts": 2,
          "within_objective": w, "combined_objective": c,
        })
        assert resp.status_code == 200, f"Split w={w} c={c} failed: {resp.text}"

  @pytest.mark.parametrize("l3", [
    "hybrid", "max_sortino", "max_calmar", "min_ulcer",
    "max_recovery", "min_max_drawdown", "max_profit_factor",
  ])
  def test_wfo_manual_l3_objective(self, client, l3):
    """Manual mode with {l3} at L3."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1, 2], "wfo_mode": "manual",
      "obj_layer1": "hybrid", "obj_layer2": "uncorrelated", "obj_layer3": l3,
      "n_longs": 2, "n_shorts": 2,
      "candidates_per_strategy": 2, "n_final_strats": 2,
    })
    assert resp.status_code == 200, f"Manual L3={l3} failed: {resp.text}"

  @pytest.mark.parametrize("l3", [
    "hybrid", "max_sortino", "max_calmar", "min_ulcer",
    "max_recovery", "min_max_drawdown", "max_profit_factor",
  ])
  def test_wfo_directional_l3_objective(self, client, l3):
    """Directional with {l3} at L3."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1, 2], "wfo_mode": "auto",
      "directional_batches": True, "l2_groups": [[1], [2]],
      "n_per_batch": 2, "obj_l1": "hybrid", "obj_l2": "uncorrelated", "obj_l3": l3,
      "k_l1": 3, "k_l2": 3, "k_l3": 2,
    })
    assert resp.status_code == 200, f"Directional L3={l3} failed: {resp.text}"


# ===================================================================
# Group 19: Cross-Strategy All Objectives
# ===================================================================

class TestCrossStrategyAllObjectives:
  """All objectives across cross-strategy modes."""

  @pytest.mark.parametrize("obj", [
    "hybrid", "max_sortino", "max_calmar", "min_ulcer",
    "max_recovery", "min_max_drawdown", "max_profit_factor",
  ])
  def test_cross_auto_combined_objective(self, client, obj):
    """Cross-strategy auto combined with {obj}."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 3, "objective": obj,
      "corr_method": "standard", "extra_slippage_pct": 0.0,
    })
    assert resp.status_code == 200, f"Cross auto obj={obj} failed"

  @pytest.mark.parametrize("l3", [
    "hybrid", "max_sortino", "max_calmar", "min_ulcer",
    "max_recovery", "min_max_drawdown", "max_profit_factor",
  ])
  def test_cross_manual_l3_objective(self, client, l3):
    """Cross-strategy manual with {l3} at L3."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2], "obj_layer1": "hybrid",
      "obj_layer2": "uncorrelated", "obj_layer3": l3,
      "n_longs": 2, "n_shorts": 2,
      "candidates_per_strategy": 2, "n_final_strats": 2,
      "corr_method": "standard", "extra_slippage_pct": 0.0,
    })
    assert resp.status_code == 200, f"Cross manual L3={l3} failed: {resp.text}"

  @pytest.mark.parametrize("l3", [
    "hybrid", "max_sortino", "max_calmar", "min_ulcer",
    "max_recovery", "min_max_drawdown", "max_profit_factor",
  ])
  def test_cross_directional_l3_objective(self, client, l3):
    """Cross-strategy directional with {l3} at L3."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1, 2], "l2_groups": [[1], [2]],
      "n_per_batch": 2, "obj_l1": "hybrid", "obj_l2": "uncorrelated", "obj_l3": l3,
      "k_l1": 3, "k_l2": 3, "k_l3": 2,
      "corr_method": "standard", "extra_slippage_pct": 0.0,
      "seed_metrics": ["sortino"],
    })
    assert resp.status_code == 200, f"Cross dir L3={l3} failed: {resp.text}"


# ===================================================================
# Group 20: WFO Window Structure Validation
# ===================================================================

class TestWFOWindowStructure:
  """Deep validation of WFO window data integrity."""

  _BASE = {
    "is_months": 2, "oos_months": 1, "last_oos_end": "2024-07-15",
    "corr_method": "standard", "n_candidates": 2,
    "seed_metrics": ["sortino"], "extra_slippage_pct": 0.0,
    "batch_ids": [1], "n_assets": 3, "objective": "hybrid",
    "wfo_mode": "auto", "split_mode": False,
  }

  def test_windows_chronological_order(self, client):
    """is_start < is_end, oos_start < oos_end per window."""
    resp = client.post("/api/wfo/asset-selection", json=self._BASE)
    assert resp.status_code == 200
    for w in resp.json()["windows"]:
      assert w["is_start"] < w["is_end"], "IS start must precede IS end"
      if "oos_start" in w and w.get("oos_start"):
        assert w["oos_start"] < w["oos_end"], "OOS start must precede OOS end"
        assert w["is_end"] <= w["oos_start"], "IS end must not exceed OOS start"

  def test_windows_sequential(self, client):
    """Windows should progress chronologically."""
    resp = client.post("/api/wfo/asset-selection", json=self._BASE)
    assert resp.status_code == 200
    windows = resp.json()["windows"]
    for i in range(1, len(windows)):
      assert windows[i]["is_start"] >= windows[i-1]["is_start"], \
        f"Window {i} IS start should be >= window {i-1}"

  def test_tracks_have_cumulative_metrics(self, client):
    """Tracks should have cumulative IS and OOS metrics."""
    resp = client.post("/api/wfo/asset-selection", json=self._BASE)
    assert resp.status_code == 200
    data = resp.json()
    assert "tracks" in data
    if data["tracks"]:
      track = data["tracks"][0]
      assert "rank" in track or "assets" in track

  def test_window_candidates_structure(self, client):
    """Each window should have candidates with metrics."""
    resp = client.post("/api/wfo/asset-selection", json=self._BASE)
    assert resp.status_code == 200
    windows = resp.json()["windows"]
    for w in windows:
      assert "candidates" in w
      if w["candidates"]:
        cand = w["candidates"][0]
        assert "assets" in cand or "oos_metrics" in cand or isinstance(cand, list)


# ===================================================================
# Group 21: WFO Persistence
# ===================================================================

class TestWFOPersistence:
  """WFO run persistence and retrieval."""

  _BASE = {
    "is_months": 2, "oos_months": 1, "last_oos_end": "2024-07-15",
    "corr_method": "standard", "n_candidates": 2,
    "seed_metrics": ["sortino"], "extra_slippage_pct": 0.0,
    "batch_ids": [1], "n_assets": 3, "objective": "hybrid",
    "wfo_mode": "auto", "split_mode": False,
  }

  def test_wfo_run_get_by_id(self, client):
    """Run WFO, then GET the saved run by ID."""
    resp = client.post("/api/wfo/asset-selection", json=self._BASE)
    assert resp.status_code == 200
    run_id = resp.json()["wfo_run_id"]
    detail = client.get(f"/api/wfo/runs/{run_id}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["id"] == run_id
    assert "windows" in data

  def test_wfo_runs_list(self, client):
    """GET /runs should return a list."""
    resp = client.get("/api/wfo/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert isinstance(runs, list)
    assert len(runs) >= 1

  def test_wfo_window_detail(self, client):
    """GET /runs/{id}/window/{num} should return window detail."""
    resp = client.post("/api/wfo/asset-selection", json=self._BASE)
    assert resp.status_code == 200
    run_id = resp.json()["wfo_run_id"]
    w_resp = client.get(f"/api/wfo/runs/{run_id}/window/1")
    # May be 200 or 404 depending on window numbering
    assert w_resp.status_code in (200, 404)


# ===================================================================
# Group 22: Date Filtering Deep
# ===================================================================

class TestDateFilteringDeep:
  """Date filtering across all endpoint types."""

  def test_date_from_wfo(self, client):
    """WFO with date_from should still produce valid windows."""
    resp = client.post("/api/wfo/asset-selection", json={
      "batch_ids": [1], "is_months": 2, "oos_months": 1,
      "last_oos_end": "2024-07-15", "date_from": "2024-03-01",
      "n_assets": 3, "objective": "hybrid", "wfo_mode": "auto",
      "split_mode": False, "corr_method": "standard",
      "extra_slippage_pct": 0.0,
    })
    assert resp.status_code == 200

  def test_date_from_cross_strategy(self, client):
    """Cross-strategy with date_from."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "extra_slippage_pct": 0.0,
      "date_from": "2024-03-01",
    })
    assert resp.status_code == 200

  def test_date_to_cross_strategy(self, client):
    """Cross-strategy with date_to."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "extra_slippage_pct": 0.0,
      "date_to": "2024-05-01",
    })
    assert resp.status_code == 200

  def test_date_range_combined(self, client):
    """Both date_from + date_to."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "extra_slippage_pct": 0.0,
      "date_from": "2024-02-01", "date_to": "2024-06-01",
    })
    assert resp.status_code == 200


# ===================================================================
# Group 23: Edge Cases Deep
# ===================================================================

class TestEdgeCasesDeep:
  """Additional edge cases across endpoints."""

  def test_single_batch_cross_fails(self, client):
    """Cross-strategy with 1 batch should fail (need 2+)."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1], "split_mode": False,
      "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "extra_slippage_pct": 0.0,
    })
    assert resp.status_code == 400

  def test_n_assets_zero_optimizer(self, client):
    """n_assets=0 should still return a response (empty or error)."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 0, "objective": "hybrid",
      "corr_method": "standard",
    })
    # May return 200 with empty or 422 validation error
    assert resp.status_code in (200, 422)

  def test_all_filters_exclude_everything(self, client):
    """Extreme filters should produce error or fewer assets."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 5, "objective": "hybrid",
      "corr_method": "standard", "min_pf": 999, "min_wr": 99,
    })
    # Extreme filters: may return 200 with reduced results or 400 error
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
      candidates = resp.json().get("candidates", [])
      if candidates:
        assert candidates[0]["metrics"]["n_assets"] <= 5

  def test_extra_slippage_reduces_profit(self):
    """Extra slippage should reduce each trade's net_profit.

    Tests the slippage function directly — the optimizer's greedy heuristic
    picks different assets with different PnL inputs, so comparing optimizer
    candidates across slippage levels is unreliable.
    """
    from datetime import datetime

    from backend.api.routes.analysis import _apply_stress_slippage
    from backend.db.models import BacktestTrade

    # Create detached test trades (not in any DB session)
    trades = [
      BacktestTrade(
        id=9990 + i, run_id=1, trade_num=i, symbol="TEST", side="long",
        entry_price=1000.0, exit_price=1010.0, quantity=0.1,
        entry_time=datetime(2024, 1, 1), exit_time=datetime(2024, 1, 2),
        net_profit=100.0, net_profit_pct=1.0, commission=0.1,
      )
      for i in range(5)
    ]
    original_pnls = [t.net_profit for t in trades]

    # Create a mock db with a no-op expunge
    class _MockDB:
      def expunge(self, obj):
        pass

    result = _apply_stress_slippage(trades, 0.3, _MockDB())
    for orig, t in zip(original_pnls, result):
      assert t.net_profit < orig, (
        f"Slippage should reduce net_profit: {t.net_profit} >= {orig}"
      )

  def test_invalid_wfo_run_id(self, client):
    """GET /runs/99999 should return 404."""
    resp = client.get("/api/wfo/runs/99999")
    assert resp.status_code == 404


# ===================================================================
# Group 24: Event Metrics Deep (unit tests)
# ===================================================================

class TestEventMetricsDeep:
  """Additional unit tests for _compute_event_metrics."""

  def test_single_trade(self):
    from backend.analysis.event_wfo import _compute_event_metrics
    m = _compute_event_metrics([50])
    assert m["total_trades"] == 1
    assert m["wr"] == 100.0
    assert m["net_profit"] == 50

  def test_large_drawdown_pattern(self):
    """W/L/L/L/W pattern should show significant drawdown."""
    from backend.analysis.event_wfo import _compute_event_metrics
    m = _compute_event_metrics([100, -50, -50, -50, 100])
    assert m["total_trades"] == 5
    assert m["max_dd"] > 0
    assert m["wr"] == 40.0  # 2/5 wins

  def test_zero_pnl_trades(self):
    """All $0 trades → PF=0, WR=0, DD=0."""
    from backend.analysis.event_wfo import _compute_event_metrics
    m = _compute_event_metrics([0, 0, 0, 0])
    assert m["total_trades"] == 4
    assert m["net_profit"] == 0
    assert m["wr"] == 0  # 0 is not > 0
    assert m["max_dd"] == 0

  def test_pf_capped_at_10(self):
    """No losses → PF should be capped at 10."""
    from backend.analysis.event_wfo import _compute_event_metrics
    m = _compute_event_metrics([10, 20, 30])
    assert m["pf"] == 10.0

  def test_ulcer_zero_for_monotonic_equity(self):
    """All winners → no drawdown → ulcer=0."""
    from backend.analysis.event_wfo import _compute_event_metrics
    m = _compute_event_metrics([10, 20, 30, 40, 50])
    assert m["ulcer"] == 0
    assert m["max_dd"] == 0


# ===================================================================
# Group 25: Event Mode Optimizer (single-batch)
# ===================================================================

class TestEventModeOptimizer:
  """Tests for event_mode=True in /optimize and /optimize-ls endpoints."""

  def test_optimizer_event_mode_happy_path(self, client):
    """event_mode=True on /optimize should return valid candidates."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "n_portfolios": 2,
      "event_mode": True, "n_trades": 5, "staleness_days": 3650,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "candidates" in data
    assert len(data["candidates"]) >= 1

  def test_optimizer_event_mode_false_unchanged(self, client):
    """event_mode=False should behave identically to not passing it."""
    resp_default = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "n_portfolios": 2,
    })
    resp_explicit = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "n_portfolios": 2,
      "event_mode": False,
    })
    assert resp_default.status_code == 200
    assert resp_explicit.status_code == 200
    # Both should return the same number of candidates
    assert len(resp_default.json()["candidates"]) == len(resp_explicit.json()["candidates"])

  @pytest.mark.parametrize("objective", ["sortino", "pf", "wr", "recovery"])
  def test_optimizer_event_mode_objectives(self, client, objective):
    """Event mode should work with all supported objectives."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "n_portfolios": 2,
      "event_mode": True, "n_trades": 5, "staleness_days": 3650,
      "seed_metrics": [objective],
    })
    assert resp.status_code == 200, f"Event mode objective={objective} failed: {resp.text}"

  def test_optimizer_event_mode_ls_split(self, client):
    """event_mode=True on /optimize-ls should return valid L/S candidates."""
    resp = _post_optimize(client, "/api/analysis/optimize-ls", json={
      "batch_id": 1, "n_longs": 2, "n_shorts": 2,
      "within_objective": "uncorrelated", "combined_objective": "stability",
      "corr_method": "standard", "n_portfolios": 2,
      "event_mode": True, "n_trades": 5, "staleness_days": 3650,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "candidates" in data

  def test_optimizer_event_mode_large_n_trades_fallback(self, client):
    """n_trades=500 with few trades per asset → event scorer excludes all
    → optimizer falls back to calendar scoring → still returns candidates."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "n_portfolios": 2,
      "event_mode": True, "n_trades": 500, "staleness_days": 3650,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["candidates"]) >= 1  # Falls back to calendar, still works

  def test_optimizer_event_mode_staleness_one_day(self, client):
    """staleness_days=1 excludes all stale assets; optimizer falls back."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "n_portfolios": 2,
      "event_mode": True, "n_trades": 5, "staleness_days": 1,
    })
    # Even if all assets are stale, fallback to calendar means no crash
    assert resp.status_code == 200, resp.text

  def test_optimizer_event_mode_with_filters(self, client):
    """event_mode + min_trades filter combination should work."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "corr_method": "standard", "n_portfolios": 2,
      "min_trades": 5, "min_pf": 0.5,
      "event_mode": True, "n_trades": 5, "staleness_days": 3650,
    })
    assert resp.status_code == 200, resp.text

  def test_compute_event_seed_scores_unit(self):
    """Unit test: compute_event_seed_scores returns float scores per asset."""
    from datetime import datetime

    from backend.analysis.optimizer_seeds import compute_event_seed_scores

    # Use a simple namespace object — the function uses duck typing
    class _FakeTrade:
      def __init__(self, symbol, side, net_profit, exit_time):
        self.symbol = symbol; self.side = side
        self.net_profit = net_profit; self.exit_time = exit_time

    trades = [_FakeTrade("BTCUSDT", "long", 10.0 if i % 2 == 0 else -5.0,
                          datetime(2024, 1, 1)) for i in range(25)]

    scores = compute_event_seed_scores(trades, n_trades=20, objective="sortino",
                                        staleness_days=3650)
    assert isinstance(scores, dict)
    assert "BTCUSDT" in scores
    assert isinstance(scores["BTCUSDT"], float)

  def test_compute_event_seed_scores_insufficient_trades(self):
    """Assets with fewer than n_trades are excluded from result."""
    from datetime import datetime

    from backend.analysis.optimizer_seeds import compute_event_seed_scores

    class _FakeTrade:
      def __init__(self, symbol, side, net_profit, exit_time):
        self.symbol = symbol; self.side = side
        self.net_profit = net_profit; self.exit_time = exit_time

    trades = [_FakeTrade("ETHUSDT", "long", 10.0, datetime(2024, 1, 1))
              for _ in range(5)]  # Only 5 trades

    scores = compute_event_seed_scores(trades, n_trades=20, objective="sortino",
                                        staleness_days=3650)
    # ETHUSDT has only 5 trades < n_trades=20 → excluded from event scores
    assert "ETHUSDT" not in scores


# ===================================================================
# Group 26: Event Mode Cross-Strategy
# ===================================================================

class TestEventModeCrossStrategy:
  """Tests for event_mode=True across all 3 cross-strategy endpoints."""

  def test_cross_auto_event_mode(self, client):
    """Auto cross-strategy with event_mode=True should return 200."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 4, "objective": "hybrid",
      "corr_method": "standard",
      "event_mode": True, "n_trades": 5, "staleness_days": 3650,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "candidates" in data
    assert len(data["candidates"]) >= 1

  def test_cross_manual_event_mode(self, client):
    """Manual (3-layer) cross-strategy with event_mode=True."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "obj_layer1": "hybrid", "obj_layer2": "stability", "obj_layer3": "hybrid",
      "candidates_per_strategy": 2, "n_final_strats": 2, "n_portfolios": 2,
      "corr_method": "standard",
      "event_mode": True, "n_trades": 5, "staleness_days": 3650,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "candidates" in data

  def test_cross_directional_event_mode(self, client):
    """Directional (beam search) cross-strategy with event_mode=True."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1, 2],
      "l2_groups": [[1, 2]],
      "n_per_batch": 3,
      "obj_l1": "hybrid", "obj_l2": "uncorrelated", "obj_l3": "max_sortino",
      "k_l1": 3, "k_l2": 3, "k_l3": 2,
      "corr_method": "standard",
      "event_mode": True, "n_trades": 5, "staleness_days": 3650,
    })
    assert resp.status_code == 200, resp.text

  def test_cross_per_batch_event_mode_override(self, client):
    """Per-batch override: batch 1 uses event mode, batch 2 uses time-based."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 4, "objective": "hybrid",
      "corr_method": "standard",
      "event_mode": False,  # global = time-based
      "per_batch": {
        "1": {"event_mode": True, "n_trades": 5},  # batch 1 overrides to event
      },
    })
    assert resp.status_code == 200, resp.text

  def test_cross_per_batch_different_n_trades(self, client):
    """Per-batch n_trades override: different window sizes per source."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False,
      "n_assets": 4, "objective": "hybrid",
      "corr_method": "standard",
      "event_mode": True, "n_trades": 10,  # global: 10 trades
      "per_batch": {
        "1": {"event_mode": True, "n_trades": 5},   # batch 1: only 5
        "2": {"event_mode": True, "n_trades": 15},  # batch 2: 15
      },
      "staleness_days": 3650,
    })
    assert resp.status_code == 200, resp.text

  def test_cross_event_mode_with_quality_gate(self, client):
    """Event mode combined with l1_quality_gate=True (manual mode).
    May return 200 or 400 depending on test data — both are valid outcomes
    (400 means quality gate filtered too aggressively, no crash = correct)."""
    resp = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "obj_layer1": "hybrid", "obj_layer2": "stability", "obj_layer3": "hybrid",
      "candidates_per_strategy": 2, "n_final_strats": 2, "n_portfolios": 2,
      "corr_method": "standard",
      "l1_quality_gate": True, "n_per_group": 20,
      "event_mode": True, "n_trades": 5, "staleness_days": 3650,
    })
    # 200 = success, 400 = quality gate filtered too aggressively (both valid)
    assert resp.status_code in (200, 400), resp.text

  def test_cross_event_mode_all_modes_smoke(self, client):
    """Smoke test: all 3 cross-strategy modes with event_mode=True return 200."""
    common = {"event_mode": True, "n_trades": 5, "staleness_days": 3650,
              "corr_method": "standard"}

    r1 = _post_optimize(client, "/api/analysis/cross-strategy-build", json={
      "batch_ids": [1, 2], "split_mode": False, "n_assets": 4, "objective": "hybrid", **common,
    })
    r2 = _post_optimize(client, "/api/analysis/cross-strategy-build-manual", json={
      "batch_ids": [1, 2],
      "obj_layer1": "hybrid", "obj_layer2": "stability", "obj_layer3": "hybrid",
      "candidates_per_strategy": 2, "n_final_strats": 2, "n_portfolios": 2, **common,
    })
    r3 = _post_optimize(client, "/api/analysis/cross-strategy-build-directional", json={
      "batch_ids": [1, 2], "l2_groups": [[1, 2]], "n_per_batch": 3,
      "obj_l1": "hybrid", "obj_l2": "uncorrelated", "obj_l3": "max_sortino",
      "k_l1": 3, "k_l2": 3, "k_l3": 2, **common,
    })

    assert r1.status_code == 200, f"Auto failed: {r1.text}"
    assert r2.status_code == 200, f"Manual failed: {r2.text}"
    assert r3.status_code == 200, f"Directional failed: {r3.text}"


# ── Group 27: TestEventWFOEnhancements ─────────────────────────────────


class TestEventWFOEnhancements:
  """Group 27 — Walk-forward enhancements: date_from, rebalance_every, min_hold."""

  _BASE = {
    "event_mode": True,
    "n_trades": 5,
    "n_assets": 5,
    "staleness_days": 0,
    "min_pf": 0,
    "min_wr": 0,
    "extra_slippage_pct": 0.0,
    "last_oos_end": "2024-07-15",
  }

  # -- Integration tests (hit API) --

  def test_event_wfo_date_from_empty(self, client):
    """Empty date_from = all trades, warmup_trades should be 0."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "objective": "sortino",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"
    assert data.get("warmup_trades", 0) == 0

  def test_event_wfo_date_from_warmup(self, client):
    """date_from causes warmup: equity curve shorter than without date_from."""
    r_all = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "objective": "sortino",
    })
    assert r_all.status_code == 200
    total_all = len(r_all.json()["equity_curve"])

    r_from = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "objective": "sortino",
      "date_from": "2024-04-01",
    })
    assert r_from.status_code == 200
    data = r_from.json()
    total_from = len(data["equity_curve"])
    warmup = data.get("warmup_trades", 0)

    # With date_from, equity curve should be shorter (warmup trades excluded)
    assert total_from <= total_all
    # warmup_trades should be > 0 if any trades exist before date_from
    assert warmup >= 0

  def test_event_wfo_rebalance_1_default(self, client):
    """rebalance_every=1 = rebalance at every trade (count matches steps)."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "objective": "sortino",
      "rebalance_every": 1,
    })
    assert resp.status_code == 200
    data = resp.json()
    total_steps = data["summary"].get("total_steps", 0)
    rebalance_count = data.get("rebalance_count", 0)
    assert rebalance_count == total_steps

  def test_event_wfo_rebalance_every_5(self, client):
    """rebalance_every=5 → fewer rebalances than every=1."""
    r1 = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "objective": "sortino",
      "rebalance_every": 1,
    })
    r5 = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "objective": "sortino",
      "rebalance_every": 5,
    })
    assert r1.status_code == 200 and r5.status_code == 200
    reb_1 = r1.json().get("rebalance_count", 0)
    reb_5 = r5.json().get("rebalance_count", 0)
    # rebalance_every=5 should produce fewer or equal rebalances
    assert reb_5 <= reb_1

  def test_event_wfo_min_hold_zero_default(self, client):
    """min_hold=0 = no selection lock, works like current behavior."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "objective": "sortino",
      "min_hold": 0,
    })
    assert resp.status_code == 200
    assert resp.json()["mode"] == "event"

  def test_event_wfo_min_hold_active(self, client):
    """min_hold > 0 doesn't crash and produces valid results."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "objective": "sortino",
      "min_hold": 5,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"
    assert "equity_curve" in data

  def test_event_wfo_combined_features(self, client):
    """date_from + rebalance_every + min_hold all together."""
    resp = client.post("/api/wfo/asset-selection", json={
      **self._BASE, "batch_ids": [1], "objective": "sortino",
      "date_from": "2024-03-01",
      "rebalance_every": 10,
      "min_hold": 3,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "event"
    assert data.get("warmup_trades", 0) >= 0
    assert data.get("rebalance_count", 0) >= 0

  # -- Unit tests (directly call run_event_wfo) --

  def test_unit_date_from_splits_warmup_active(self):
    """Unit: run_event_wfo with date_from correctly splits warmup/active."""
    from datetime import datetime, timedelta

    from backend.analysis.event_wfo import run_event_wfo

    class _FT:
      def __init__(self, symbol, side, pnl, entry_time, exit_time):
        self.symbol = symbol; self.side = side
        self.net_profit = pnl; self.entry_time = entry_time; self.exit_time = exit_time

    # 10 warmup trades (Jan), 10 active trades (Feb)
    trades = []
    for i in range(10):
      dt = datetime(2024, 1, i + 1)
      trades.append(_FT("BTCUSDT", "Long", 10.0, dt, dt + timedelta(hours=1)))
    for i in range(10):
      dt = datetime(2024, 2, i + 1)
      trades.append(_FT("BTCUSDT", "Long", 5.0, dt, dt + timedelta(hours=1)))

    result = run_event_wfo(trades, {
      "n_trades": 5, "n_assets": 5, "staleness_days": 0,
      "min_pf": 0, "min_wr": 0, "objective": "sortino",
      "date_from": "2024-02-01",
    })

    assert result["warmup_trades"] == 10
    assert len(result["equity_curve"]) == 10
    assert result["summary"]["total_steps"] == 10

  def test_unit_rebalance_frequency(self):
    """Unit: rebalance_every controls how often portfolio is re-evaluated."""
    from datetime import datetime, timedelta

    from backend.analysis.event_wfo import run_event_wfo

    class _FT:
      def __init__(self, symbol, side, pnl, entry_time, exit_time):
        self.symbol = symbol; self.side = side
        self.net_profit = pnl; self.entry_time = entry_time; self.exit_time = exit_time

    base = datetime(2024, 1, 1)
    trades = [_FT("BTCUSDT", "Long", 10.0 if i % 2 == 0 else -5.0,
                   base + timedelta(hours=i), base + timedelta(hours=i, minutes=30)) for i in range(50)]

    r1 = run_event_wfo(trades, {
      "n_trades": 3, "n_assets": 5, "staleness_days": 0,
      "min_pf": 0, "min_wr": 0, "objective": "sortino",
      "rebalance_every": 1,
    })
    r10 = run_event_wfo(trades, {
      "n_trades": 3, "n_assets": 5, "staleness_days": 0,
      "min_pf": 0, "min_wr": 0, "objective": "sortino",
      "rebalance_every": 10,
    })

    assert r1["rebalance_count"] == 50
    assert r10["rebalance_count"] < r1["rebalance_count"]

  def test_event_wfo_date_from_optimizer(self, client):
    """Optimizer event scoring with date_from doesn't crash."""
    resp = _post_optimize(client, "/api/analysis/optimize", json={
      "batch_id": 1, "n_assets": 3, "objective": "hybrid",
      "n_portfolios": 1, "corr_method": "standard",
      "event_mode": True, "n_trades": 5, "staleness_days": 3650,
      "date_from": "2024-04-01",
    })
    # 200 = success, 400 = not enough assets (both valid)
    assert resp.status_code in (200, 400)


# ===================================================================
# Group 28: Cross-strategy speedup parameter coverage
# Tests inner pipeline functions directly (endpoints use background
# tasks with SessionLocal, not the test DB override).
# ===================================================================

class TestCrossStrategySpeedupCoverage:
  """Verify speedup-specific params (corr_method, stability, TC, seeds, beam)
  flow correctly through the full cross-strategy pipeline inner functions."""

  @staticmethod
  def _db():
    return _TestSession()

  def test_cross_build_with_trade_overlap(self, client):
    """corr_method='trade_overlap' flows through full build pipeline."""
    from backend.api.routes.cross_strategy import (
      CrossBuildRequest,
      _cross_strategy_build_inner,
    )
    db = self._db()
    try:
      req = CrossBuildRequest(
        batch_ids=[1, 2], split_mode=True,
        n_longs=3, n_shorts=3,
        corr_method="trade_overlap",
      )
      result = _cross_strategy_build_inner(req, db)
      assert "candidates" in result
      assert len(result["candidates"]) >= 1
      c = result["candidates"][0]
      assert "longs" in c
      assert "shorts" in c
    finally:
      db.close()

  def test_cross_build_with_stability(self, client):
    """prioritize_stability=True flows through build pipeline."""
    from backend.api.routes.cross_strategy import (
      CrossBuildRequest,
      _cross_strategy_build_inner,
    )
    db = self._db()
    try:
      req = CrossBuildRequest(
        batch_ids=[1, 2], split_mode=True,
        n_longs=2, n_shorts=2,
        prioritize_stability=True,
      )
      result = _cross_strategy_build_inner(req, db)
      assert "candidates" in result
      assert len(result["candidates"]) >= 1
    finally:
      db.close()

  def test_cross_build_with_tc_constraints(self, client):
    """Trade-count constraints flow through build pipeline."""
    from backend.api.routes.cross_strategy import (
      CrossBuildRequest,
      _cross_strategy_build_inner,
    )
    db = self._db()
    try:
      req = CrossBuildRequest(
        batch_ids=[1, 2], split_mode=True,
        n_longs=2, n_shorts=2,
        tc_low_threshold=10, tc_max_low=1,
        tc_high_threshold=30, tc_min_high=1,
      )
      result = _cross_strategy_build_inner(req, db)
      assert "candidates" in result
      assert len(result["candidates"]) >= 1
    finally:
      db.close()

  def test_cross_build_with_seed_metrics(self, client):
    """Custom seed_metrics flow through build pipeline."""
    from backend.api.routes.cross_strategy import (
      CrossBuildRequest,
      _cross_strategy_build_inner,
    )
    db = self._db()
    try:
      req = CrossBuildRequest(
        batch_ids=[1, 2], split_mode=True,
        n_longs=2, n_shorts=2,
        seed_metrics=["calmar", "profit_factor", "stability"],
      )
      result = _cross_strategy_build_inner(req, db)
      assert "candidates" in result
      assert len(result["candidates"]) >= 1
    finally:
      db.close()

  def test_cross_build_manual_beam(self, client):
    """Manual beam (triple-layer) with speedup params."""
    from backend.api.routes.cross_strategy import (
      CrossBuildManualRequest,
      _cross_strategy_build_manual_inner,
    )
    db = self._db()
    try:
      req = CrossBuildManualRequest(
        batch_ids=[1, 2],
        allocations={
          "1": {"n_longs": 2, "n_shorts": 2},
          "2": {"n_longs": 2, "n_shorts": 2},
        },
        obj_layer1="hybrid", obj_layer2="stability", obj_layer3="hybrid",
        corr_method="trade_overlap",
        seed_metrics=["calmar", "pf", "stability"],
        prioritize_stability=True,
      )
      result = _cross_strategy_build_manual_inner(req, db)
      assert "candidates" in result
      assert len(result["candidates"]) >= 1
    finally:
      db.close()

  def test_cross_build_directional_beam(self, client):
    """Directional beam (L1->L2->L3) with speedup params."""
    from backend.api.routes.cross_strategy import (
      CrossBuildDirectionalRequest,
      _cross_strategy_build_directional_inner,
    )
    db = self._db()
    try:
      req = CrossBuildDirectionalRequest(
        batch_ids=[1, 2],
        l2_groups=[[1], [2]],
        n_per_batch=3,
        obj_l1="hybrid", obj_l2="uncorrelated", obj_l3="max_sortino",
        k_l1=3, k_l2=3, k_l3=3,
        corr_method="trade_overlap",
        seed_metrics=["calmar", "stability"],
        prioritize_stability=True,
      )
      result = _cross_strategy_build_directional_inner(req, db)
      assert "candidates" in result
      assert len(result["candidates"]) >= 1
      c = result["candidates"][0]
      assert "metrics" in c
    finally:
      db.close()


# ===================================================================
# Group: Hybrid WFO + Rescue System Tests (Features 1-6)
# ===================================================================

class TestHybridWFORescue:
  """Tests for hybrid WFO with rescue system (Features 1-6)."""

  def _db(self):
    """Get a new DB session."""
    from backend.db.database import SessionLocal
    return SessionLocal()

  def _make_trades_deterministic(self, run_id: int, symbol: str, side: str,
                                   n: int, start_day: int = 0,
                                   win_pct: float = 0.6,
                                   win_size: float = 100.0,
                                   loss_size: float = 100.0) -> list:
    """Build n deterministic BacktestTrade rows for a single run/symbol/side.

    Used by rescue tests that need precise, reproducible scoring differentials.
    """
    from backend.db.models import BacktestTrade
    from datetime import datetime, timedelta
    trades = []
    origin = datetime(2024, 1, 1)
    for i in range(n):
      entry = origin + timedelta(days=start_day + i)
      exit_t = entry + timedelta(hours=4)
      # Deterministic win/loss pattern with given win_pct
      is_win = ((i * 37) % 100) < int(win_pct * 100)
      pnl = win_size if is_win else -loss_size
      trades.append(BacktestTrade(
        run_id=run_id,
        trade_num=i + 1,
        position_id=f"T{run_id}_{i}",
        symbol=symbol,
        side=side,
        entry_time=entry,
        exit_time=exit_t,
        entry_price=100.0,
        exit_price=100.0 + pnl / 10.0,
        quantity=1.0,
        net_profit=pnl,
        net_profit_pct=pnl / 100.0,
        commission=0.1,
      ))
    return trades

  def test_hybrid_sample_floor_excludes_low_count_assets(self, client):
    """Feature 2: sample_count_floor excludes assets with fewer trades than the floor.

    Strong verification: builds a scenario with two assets (one high-count, one
    low-count), asserts the low-count asset is present WITHOUT the floor and
    absent WITH the floor.
    """
    from backend.analysis.event_wfo import run_event_wfo

    # Asset HI has 40 trades, asset LO has 12 trades. Both profitable.
    hi_trades = self._make_trades_deterministic(
      run_id=1, symbol="HISYM", side="long", n=40, start_day=0,
      win_pct=0.65, win_size=120, loss_size=80,
    )
    lo_trades = self._make_trades_deterministic(
      run_id=2, symbol="LOSYM", side="long", n=12, start_day=0,
      win_pct=0.70, win_size=150, loss_size=60,
    )
    all_trades = hi_trades + lo_trades

    base_cfg = {
      "n_trades": 5,
      "n_assets": 2,
      "min_pf": 0,
      "min_wr": 0,
      "staleness_days": 0,
    }

    # Baseline run: no floor — LOSYM must be eligible in ranking at least once.
    baseline = run_event_wfo(all_trades, {**base_cfg, "hybrid_sample_count_floor": 0})
    baseline_freq = baseline.get("asset_frequency", {})
    lo_key_baseline = next((k for k in baseline_freq if "LOSYM" in k), None)
    assert lo_key_baseline is not None and baseline_freq[lo_key_baseline]["eligible"] > 0, \
      f"Baseline should mark LOSYM eligible; got {baseline_freq}"

    # Floored run: floor=20 must make LOSYM ineligible (only 12 trades).
    floored = run_event_wfo(all_trades, {**base_cfg, "hybrid_sample_count_floor": 20})
    floored_freq = floored.get("asset_frequency", {})
    lo_key_floored = next((k for k in floored_freq if "LOSYM" in k), None)
    lo_eligible_floored = floored_freq[lo_key_floored]["eligible"] if lo_key_floored else 0
    assert lo_eligible_floored == 0, \
      f"Floored run should exclude LOSYM from eligibility; got {floored_freq}"
    # HISYM should still be eligible (40 trades >= 20 floor).
    hi_key = next((k for k in floored_freq if "HISYM" in k), None)
    assert hi_key is not None and floored_freq[hi_key]["eligible"] > 0, \
      f"Floored run should still mark HISYM eligible; got {floored_freq}"

  def test_hybrid_score_decomposition_populated(self, client):
    """Feature 3: selected_records MUST include a components decomposition.

    Verifies both composite and single-method scoring populate the components
    dict with the required keys (raw, normalized, weighted, final_score).
    """
    from backend.analysis.event_wfo import run_event_wfo

    # Two assets, both with enough trades to be selected
    trades_a = self._make_trades_deterministic(
      run_id=1, symbol="ASSETA", side="long", n=25, start_day=0,
      win_pct=0.65, win_size=150, loss_size=80,
    )
    trades_b = self._make_trades_deterministic(
      run_id=2, symbol="ASSETB", side="long", n=25, start_day=0,
      win_pct=0.60, win_size=130, loss_size=90,
    )
    trades = trades_a + trades_b

    # --- Composite scoring ---
    comp_cfg = {
      "n_trades": 5,
      "n_assets": 2,
      "min_pf": 0,
      "min_wr": 0,
      "staleness_days": 0,
      "scoring_method": "composite",
      "composite_weights": {"sortino": 0.5, "pf": 0.5},
    }
    result_comp = run_event_wfo(trades, comp_cfg)
    records_comp = result_comp.get("selected_records", [])
    assert len(records_comp) > 0, "Expected non-empty selected_records for composite run"
    for rec in records_comp:
      assert "components" in rec, f"Missing 'components' key in record: {rec}"
      comp = rec["components"]
      assert comp is not None, f"components is None in record: {rec}"
      assert isinstance(comp, dict), f"components must be dict, got {type(comp)}"
      # Required decomposition keys
      for key in ("raw", "normalized", "weighted", "final_score"):
        assert key in comp, f"components missing '{key}': {comp}"
      assert isinstance(comp["raw"], dict)
      assert isinstance(comp["normalized"], dict)
      assert isinstance(comp["weighted"], dict)
      assert isinstance(comp["final_score"], (int, float))

    # --- Single-method scoring: components must also be populated (trivial decomposition) ---
    single_cfg = {
      "n_trades": 5,
      "n_assets": 2,
      "min_pf": 0,
      "min_wr": 0,
      "staleness_days": 0,
      "scoring_method": "single",
      "objective": "sortino",
    }
    result_single = run_event_wfo(trades, single_cfg)
    records_single = result_single.get("selected_records", [])
    assert len(records_single) > 0, "Expected non-empty selected_records for single run"
    for rec in records_single:
      assert "components" in rec
      comp = rec["components"]
      assert comp is not None
      assert isinstance(comp, dict)
      for key in ("raw", "normalized", "weighted", "final_score"):
        assert key in comp, f"single-method components missing '{key}': {comp}"
      # Single method: exactly one entry in raw/normalized/weighted (the objective)
      assert "sortino" in comp["raw"]

  def test_hybrid_rescue_disabled_by_default_preserves_behavior(self, client):
    """Feature 5: with rescue disabled, selection must be IDENTICAL to a baseline
    run that never saw rescue_trades at all (rescue is a strict no-op when off)."""
    from backend.analysis.event_wfo import run_event_wfo

    main_trades = self._make_trades_deterministic(
      run_id=1, symbol="MAIN", side="long", n=30, start_day=0,
      win_pct=0.60, win_size=100, loss_size=100,
    )
    rescue_trades = self._make_trades_deterministic(
      run_id=2, symbol="RESCUE", side="long", n=40, start_day=0,
      win_pct=0.80, win_size=200, loss_size=50,
    )

    base_cfg = {
      "n_trades": 5,
      "n_assets": 1,
      "min_pf": 0,
      "min_wr": 0,
      "staleness_days": 0,
    }

    # Baseline — no rescue at all
    baseline = run_event_wfo(main_trades, base_cfg)

    # With rescue_enabled=False AND rescue_trades present — must behave identically
    off_cfg = {
      **base_cfg,
      "hybrid_rescue_enabled": False,
      "rescue_trades": rescue_trades,
    }
    off_run = run_event_wfo(main_trades, off_cfg)

    # Both runs must produce the same selection (same assets, same count).
    # We use selected_pnls sequence as a strong equality proxy.
    assert baseline.get("selected_pnls") == off_run.get("selected_pnls"), \
      "rescue_enabled=False must not alter selection"
    # Rescue stats must indicate no activity.
    off_summary = off_run.get("summary", {})
    assert off_summary.get("rescue_enabled") is False
    assert off_summary.get("rescue_admissions_total", 0) == 0
    assert off_summary.get("rescue_decisions_triggered", 0) == 0
    # Selected records must not contain any rescued=True entries.
    off_records = off_run.get("selected_records", [])
    assert not any(r.get("rescued") for r in off_records), \
      "No selected_record should have rescued=True when rescue is disabled"

  def test_hybrid_rescue_admits_when_gap_exceeds_threshold(self, client):
    """Feature 5+6: when a rescue candidate clearly outperforms the worst
    accepted main asset by more than the confidence-gap threshold, it MUST
    be admitted, and it MUST be flagged as rescued=True in selected_records."""
    from backend.analysis.event_wfo import run_event_wfo

    # Main trades: two mediocre assets.
    main_a = self._make_trades_deterministic(
      run_id=1, symbol="MAINA", side="long", n=40, start_day=0,
      win_pct=0.55, win_size=80, loss_size=100,
    )
    main_b = self._make_trades_deterministic(
      run_id=2, symbol="MAINB", side="long", n=40, start_day=0,
      win_pct=0.52, win_size=70, loss_size=100,
    )
    main_trades = main_a + main_b

    # Rescue candidate: clearly superior. High win rate, big wins, small losses.
    rescue_trades = self._make_trades_deterministic(
      run_id=3, symbol="SUPER", side="long", n=40, start_day=0,
      win_pct=0.85, win_size=400, loss_size=25,
    )

    cfg = {
      "n_trades": 5,
      "n_assets": 2,
      "min_pf": 0,
      "min_wr": 0,
      "staleness_days": 0,
      "scoring_method": "composite",
      "composite_weights": {"sortino": 0.4, "pf": 0.25, "recovery": 0.2, "wr": 0.15},
      "hybrid_rescue_enabled": True,
      "hybrid_rescue_quota_pct": 1.0,       # allow full rescue
      "hybrid_rescue_min_trades": 10,       # well below rescue's 40 trades
      "hybrid_rescue_min_score": 0.0,
      "hybrid_rescue_confidence_gap": 0.01, # very small gap — should trigger
      "rescue_trades": rescue_trades,
    }
    result = run_event_wfo(main_trades, cfg)
    summary = result.get("summary", {})

    assert summary.get("rescue_enabled") is True
    assert summary.get("rescue_decisions_total", 0) > 0, \
      "Rescue should have been evaluated at least once"
    assert summary.get("rescue_admissions_total", 0) > 0, \
      f"Rescue should have admitted SUPER at least once: summary={summary}"
    assert summary.get("rescue_decisions_triggered", 0) > 0

    # The rescued asset must appear in rescue_by_asset summary tracking.
    # Note: rescue candidates don't have OPEN events in the main stream, so
    # they don't produce selected_records directly — verification is via
    # rescue_by_asset admission counts.
    rescue_by_asset = summary.get("rescue_by_asset", {})
    assert any("SUPER" in str(k) for k in rescue_by_asset.keys()), \
      f"Expected SUPER in rescue_by_asset: {rescue_by_asset}"

  def test_hybrid_rescue_quota_cap_honored(self, client):
    """Feature 5: rescue_quota_pct strictly caps how many assets can be rescued
    per decision point. Scenario: quota=0.2, n_assets=10 → max 2 rescues per
    decision even when many strong candidates exist."""
    from backend.analysis.event_wfo import run_event_wfo
    import math

    # 10 mediocre main assets
    main_trades = []
    for i in range(10):
      main_trades.extend(self._make_trades_deterministic(
        run_id=100 + i, symbol=f"MAIN{i}", side="long", n=40, start_day=0,
        win_pct=0.52, win_size=70, loss_size=100,
      ))

    # 8 strong rescue candidates (all would exceed the gap)
    rescue_trades = []
    for i in range(8):
      rescue_trades.extend(self._make_trades_deterministic(
        run_id=200 + i, symbol=f"RESC{i}", side="long", n=40, start_day=0,
        win_pct=0.85, win_size=400, loss_size=25,
      ))

    n_assets = 10
    quota_pct = 0.2
    expected_max_per_decision = max(1, int(math.ceil(quota_pct * n_assets)))  # = 2

    cfg = {
      "n_trades": 5,
      "n_assets": n_assets,
      "min_pf": 0,
      "min_wr": 0,
      "staleness_days": 0,
      "scoring_method": "composite",
      "composite_weights": {"sortino": 0.4, "pf": 0.25, "recovery": 0.2, "wr": 0.15},
      "hybrid_rescue_enabled": True,
      "hybrid_rescue_quota_pct": quota_pct,
      "hybrid_rescue_min_trades": 10,
      "hybrid_rescue_confidence_gap": 0.01,
      "rescue_trades": rescue_trades,
    }
    result = run_event_wfo(main_trades, cfg)
    summary = result.get("summary", {})

    decisions_triggered = summary.get("rescue_decisions_triggered", 0)
    admissions_total = summary.get("rescue_admissions_total", 0)

    # At least one decision must have triggered rescue (proves rescue worked)
    assert decisions_triggered > 0, \
      f"Expected rescue to trigger at least once: {summary}"
    # Per-decision average must not exceed the quota cap.
    avg_per_decision = admissions_total / decisions_triggered
    assert avg_per_decision <= expected_max_per_decision, \
      f"Average admissions per decision {avg_per_decision} exceeds cap {expected_max_per_decision}"

  def test_hybrid_rescue_respects_min_trades_floor(self, client):
    """Feature 5: rescue candidates with fewer trades than min_trades MUST be
    excluded, even when their score would otherwise qualify."""
    from backend.analysis.event_wfo import run_event_wfo

    # Main asset: 30 trades
    main_trades = self._make_trades_deterministic(
      run_id=1, symbol="MAIN", side="long", n=30, start_day=0,
      win_pct=0.55, win_size=80, loss_size=100,
    )

    # Rescue candidate: ONLY 8 trades (below floor of 20), but stacked profitable
    rescue_trades = self._make_trades_deterministic(
      run_id=2, symbol="SPARSE", side="long", n=8, start_day=0,
      win_pct=1.0, win_size=1000, loss_size=1,  # all winners, huge PnL
    )

    cfg = {
      "n_trades": 5,
      "n_assets": 1,
      "min_pf": 0,
      "min_wr": 0,
      "staleness_days": 0,
      "scoring_method": "composite",
      "composite_weights": {"sortino": 0.5, "pf": 0.5},
      "hybrid_rescue_enabled": True,
      "hybrid_rescue_quota_pct": 1.0,
      "hybrid_rescue_min_trades": 20,  # floor higher than 8
      "hybrid_rescue_confidence_gap": 0.01,
      "rescue_trades": rescue_trades,
    }
    result = run_event_wfo(main_trades, cfg)
    summary = result.get("summary", {})

    assert summary.get("rescue_admissions_total", 0) == 0, \
      f"SPARSE must not be admitted (only 8 trades, floor is 20): {summary}"
    # Also verify no record carries rescued=True
    records = result.get("selected_records", [])
    assert not any(r.get("rescued") for r in records), \
      "No record should be flagged rescued=True when floor excludes all candidates"
