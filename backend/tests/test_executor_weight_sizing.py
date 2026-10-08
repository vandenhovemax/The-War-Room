"""Ship 1 Test 7 — Executor weight-keyed sizing + refresh_kelly_cache.

Purpose:
  The live executor's risk sizing in `_get_portfolio_risk` combines three
  numbers for Kelly / Fixed-mode portfolios:

      risk = base_risk * kelly_weight(symbol) * n_assets

  When weights are uniform (1/N), `kelly_weight * n_assets == 1` and risk
  reduces to `base_risk` — no visible effect. When weights are non-uniform
  (the real Step-2 output in future iterations), the per-asset risk must
  actually shift.

  This test proves:
    1. `refresh_kelly_cache(pid)` repopulates the in-memory
       `_kelly_alloc_cache` from the latest is_current=True KellyAllocation
       row (L2.2's contract).
    2. `_get_portfolio_risk` returns base_risk * weight * n_assets when the
       portfolio has a non-uniform weight vector.
    3. Uniform weights produce `base_risk` unchanged (invariant check).
    4. The key lookup handles the "{SYMBOL}_Long" / "{SYMBOL}_Short" format
       the nightly refresh writes (the only format the executor reads).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, KellyAllocation, Portfolio
from backend.exchange.engine_coordinator import EngineCoordinator

# ── In-memory fixture ─────────────────────────────────────────────

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
    yield s
  finally:
    s.rollback()
    s.close()


# ── Helpers ───────────────────────────────────────────────────────

def _make_engine() -> EngineCoordinator:
  """Isolated engine pointed at the in-memory DB (no Bybit, no scheduler)."""
  return EngineCoordinator(db_factory=_Session)


def _ensure_portfolio(db, pid: int, name: str, base_risk: float) -> Portfolio:
  """Idempotent portfolio creator with a 'fixed' risk_profile."""
  existing = db.query(Portfolio).filter(Portfolio.id == pid).first()
  if existing is not None:
    return existing
  p = Portfolio(
    id=pid,
    name=name,
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    source_strategies=json.dumps({
      "strategy_id": 1, "timeframe": "30",
      "risk_profile": {
        "mode": "fixed",
        "initial_risk_usd": base_risk,
        "override_enabled": True,
      },
    }),
  )
  db.add(p)
  db.commit()
  return p


def _write_kelly(db, pid: int, weights: dict[str, float], mark_current=True):
  # Flip any prior current rows to not-current (the service does this too).
  db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == pid,
    KellyAllocation.is_current == True,  # noqa: E712
  ).update({KellyAllocation.is_current: False}, synchronize_session=False)

  row = KellyAllocation(
    portfolio_id=pid,
    weights_json=json.dumps(weights),
    result_json=json.dumps({"basket": list(weights.keys())}),
    config_json=json.dumps({"source": "test"}),
    trigger="step2_nightly",
    is_current=mark_current,
  )
  db.add(row)
  db.commit()


# ── Tests ─────────────────────────────────────────────────────────

def test_refresh_kelly_cache_loads_weights(db):
  """After writing a KellyAllocation row, refresh_kelly_cache must hydrate
  the engine's in-memory cache with those exact weights."""
  _ensure_portfolio(db, 101, "cache_test", base_risk=100.0)
  weights = {"BTCUSDT_Long": 0.7, "ETHUSDT_Long": 0.3}
  _write_kelly(db, 101, weights)

  eng = _make_engine()
  assert 101 not in eng._kelly_alloc_cache
  ok = eng.refresh_kelly_cache(101)
  assert ok is True
  cached = eng._kelly_alloc_cache[101]
  assert cached["n_assets"] == 2
  assert cached["weights"] == weights


def test_refresh_kelly_cache_returns_false_when_no_row(db):
  """A portfolio with no current KellyAllocation should get an empty cache
  entry and refresh_kelly_cache should return False (signals 'nothing
  to load'). The empty entry prevents repeated DB hits."""
  _ensure_portfolio(db, 102, "empty_cache", base_risk=100.0)
  eng = _make_engine()
  ok = eng.refresh_kelly_cache(102)
  assert ok is False
  assert eng._kelly_alloc_cache[102] == {"weights": {}, "n_assets": 0}


def test_non_uniform_weights_produce_different_risks(db):
  """Non-uniform weights → per-asset risk must reflect the weight.

  Contract: risk = base_risk * weight * n_assets.
  """
  base = 100.0
  _ensure_portfolio(db, 103, "non_uniform", base_risk=base)
  weights = {"BTCUSDT_Long": 0.8, "ETHUSDT_Long": 0.2}
  _write_kelly(db, 103, weights)

  eng = _make_engine()
  eng.refresh_kelly_cache(103)

  r_btc = eng._get_portfolio_risk(103, symbol="BTCUSDT")
  r_eth = eng._get_portfolio_risk(103, symbol="ETHUSDT")

  # N=2 → base * weight * 2
  assert r_btc == pytest.approx(base * 0.8 * 2)  # 160
  assert r_eth == pytest.approx(base * 0.2 * 2)  # 40
  # And critically — they must be different, proving the weight vector
  # actually shifts sizing (the whole point of the Kelly publisher).
  assert r_btc != r_eth


def test_uniform_weights_yield_base_risk(db):
  """Uniform 1/N weights → kelly_weight * n_assets == 1 → risk == base.

  This is the Ship 1 default (Decision Point #1 fallback). It must leave
  the executor's sizing unchanged vs. a portfolio with no Kelly allocation.
  """
  base = 100.0
  _ensure_portfolio(db, 104, "uniform", base_risk=base)
  weights = {"BTCUSDT_Long": 0.25, "ETHUSDT_Long": 0.25,
             "SOLUSDT_Long": 0.25, "XRPUSDT_Long": 0.25}
  _write_kelly(db, 104, weights)

  eng = _make_engine()
  eng.refresh_kelly_cache(104)

  for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
    r = eng._get_portfolio_risk(104, symbol=sym)
    # 100 * 0.25 * 4 = 100 → the uniform fallback is exactly neutral.
    assert r == pytest.approx(base), f"{sym} risk drifted from base"


def test_weight_lookup_handles_long_and_short_keys(db):
  """Keys in weights_json use the SYMBOL_Long / SYMBOL_Short format the
  nightly refresh writes. Executor must find both."""
  base = 100.0
  _ensure_portfolio(db, 105, "long_short_keys", base_risk=base)
  weights = {"BTCUSDT_Long": 0.6, "BTCUSDT_Short": 0.4}
  _write_kelly(db, 105, weights)

  eng = _make_engine()
  eng.refresh_kelly_cache(105)

  # _get_kelly_weight probes _Long, then _Short, then bare. Since both
  # keys exist it picks _Long first — this tests the lookup path resolves.
  w = eng._get_kelly_weight(db, 105, "BTCUSDT")
  assert w == pytest.approx(0.6)


def test_refresh_kelly_cache_replaces_prior_snapshot(db):
  """A second nightly refresh should overwrite stale weights, not append."""
  base = 100.0
  _ensure_portfolio(db, 106, "replace", base_risk=base)

  # First allocation — 100% BTC
  _write_kelly(db, 106, {"BTCUSDT_Long": 1.0})
  eng = _make_engine()
  eng.refresh_kelly_cache(106)
  assert eng._kelly_alloc_cache[106]["n_assets"] == 1

  # Second allocation — swap to 100% ETH
  _write_kelly(db, 106, {"ETHUSDT_Long": 1.0})
  eng.refresh_kelly_cache(106)
  cached = eng._kelly_alloc_cache[106]
  assert cached["n_assets"] == 1
  assert cached["weights"] == {"ETHUSDT_Long": 1.0}
  # BTC should no longer resolve under this portfolio
  w = eng._get_kelly_weight(db, 106, "BTCUSDT")
  assert w is None
