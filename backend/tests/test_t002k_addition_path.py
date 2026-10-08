"""T-002k — Addition path: hybrid nightly refresh must create PortfolioAsset
rows for newcomer assets (assets picked by Step-2 that had no prior PA row).

Scope:
  Pre-T-002k, the flip loop at hybrid_portfolio_refresh.py:311-321 only toggles
  is_active on EXISTING PA rows. If Step-2 picks an asset the portfolio never
  traded before (a newcomer), no PA row is created — the executor never sees
  it and the asset never fires a live trade. Portfolios 23/24 are the prod
  example: April basket additions never materialized in the DB.

  T-002k fix: after the flip loop, insert `db.add(PortfolioAsset(...))` for
  every key in (added - existing_pa_keys) with:
    - strategy_name ← src_cfg["strategy_name"] (portfolio-level, set at save-time)
    - config_name   ← BatchBacktestRun.parameters.config_name (per-batch, cached)
    - timeframe     ← batch_info[bid]["timeframe"]
    - group_name    ← batch_info[bid]["group"]  (note: key is "group", not "group_name")

Cases:
  1. Happy path — newcomer in existing batches creates PA rows with correct
     strategy_name / config_name / timeframe / is_active=True.
  2. Non-hybrid invariant — a mode='non_hybrid' sibling portfolio in the same
     DB must not gain or lose any PA rows during a hybrid refresh.

These tests are expected to FAIL red before the fix lands and PASS green after.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  KellyAllocation,
  LiveEvent,
  Portfolio,
  PortfolioAsset,
  Strategy,
)

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


# ── Seed helpers ──────────────────────────────────────────────────

# Anchored-walk metadata required since the June 2026 refresh refactor:
# `refresh_portfolio` exits early with `no_windows_meta` unless src_cfg
# carries a committed window chain. This single-window chain ends
# 2024-05-31 → anchor = 2024-06-01 (== every fixture's
# optimization_end_date), and its is_start supplies the warmup runway
# (the role the retired 90-day δ extension + _seed_state used to play).
_WMETA = [{
  "window_num": 1,
  "is_start": "2024-01-01",
  "is_end": "2024-04-30",
  "oos_start": "2024-05-01",
  "oos_end": "2024-05-31",
}]


def _seed_universe(db):
  """One strategy, two batches with `config_name` in parameters, four
  (symbol, side) BacktestRuns per batch with 6 trades each.

  The `config_name` per-batch is the field T-002k must read via
  BatchBacktestRun.parameters JSON lookup.
  """
  if db.query(Strategy).filter(Strategy.id == 1).first() is not None:
    return
  strat = Strategy(id=1, name="TestStrat", source_type="manual")
  db.add(strat)
  db.flush()

  for bid, cfg_name in [(1, "cfg_B1"), (2, "cfg_B2")]:
    b = BatchBacktestRun(
      id=bid,
      strategy_id=1,
      timeframe="30",
      direction_filter="long_plus_short",
      status="completed",
      total_symbols=2,
      completed_symbols=2,
      parameters=json.dumps({
        "initial_capital": 10000,
        "config_name": cfg_name,
      }),
    )
    db.add(b)
  db.flush()

  start = datetime(2024, 1, 1)
  run_id = 0
  for bid in (1, 2):
    for sym in ("BTCUSDT", "ETHUSDT"):
      for side in ("long", "short"):
        run_id += 1
        r = BacktestRun(
          id=run_id, strategy_id=1, symbol=sym, timeframe="30",
          start_date=start, end_date=start + timedelta(days=180),
          status="completed", batch_id=bid,
        )
        db.add(r)
        db.flush()
        for i in range(6):
          entry = start + timedelta(days=10 * (i + 1))
          pnl = 100.0 if i % 2 == 0 else -30.0
          db.add(BacktestTrade(
            run_id=run_id, trade_num=i + 1, position_id=f"P{run_id}_{i}",
            symbol=sym, side=side,
            entry_time=entry, exit_time=entry + timedelta(hours=4),
            entry_price=1000.0, exit_price=1010.0, quantity=0.1,
            net_profit=pnl, net_profit_pct=pnl / 10.0,
            commission=0.1,
          ))
  db.commit()


def _make_hybrid_portfolio_with_two_assets(db) -> int:
  """Hybrid portfolio seeded with PA rows in TWO batches (1 & 2).

  Pre-existing basket: {B1_BTCUSDT_long, B2_ETHUSDT_long}
  `source_strategies` includes `strategy_name` — the T-002k addition path
  reads this for newcomer rows.
  """
  p = Portfolio(
    name="hybrid_addition_test",
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    optimization_end_date=datetime(2024, 6, 1),
    source_strategies=json.dumps({
      "strategy_id": 1,
      "strategy_name": "TestStrat",
      "timeframe": "30",
      "hybrid_mode": True,
      "oos_months": 2,
      "hybrid_n_trades": 3,
      "hybrid_n_assets": 3,
      "wfo_windows_meta": _WMETA,
    }),
  )
  db.add(p)
  db.flush()
  for sym, side, bid in [
    ("BTCUSDT", "long", 1),
    ("ETHUSDT", "long", 2),
  ]:
    db.add(PortfolioAsset(
      portfolio_id=p.id, symbol=sym, trade_side=side,
      batch_id=bid, is_active=True,
      strategy_name="TestStrat",
      timeframe="30",
    ))
  db.commit()
  return p.id


def _make_non_hybrid_sibling(db) -> int:
  """Non-hybrid portfolio in the same DB — must remain byte-identical after
  any hybrid refresh runs. Guards the Safety Contract at the PA row level.
  """
  p = Portfolio(
    name="nh_sibling_t002k",
    portfolio_type="paper",
    mode="non_hybrid",
    is_active=True,
    optimization_end_date=datetime(2024, 6, 1),
    source_strategies=json.dumps({
      "strategy_id": 1,
      "strategy_name": "TestStrat",
      "timeframe": "30",
      "hybrid_mode": False,
    }),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="long",
    batch_id=1, is_active=True,
    strategy_name="TestStrat", config_name="cfg_B1", timeframe="30",
  ))
  db.commit()
  return p.id


# ── Fake Step-2 output ────────────────────────────────────────────

def _fake_step2_result(selected_basket: list[str]) -> dict:
  """Minimal Step-2 output shape — only fields refresh_portfolio reads."""
  return {
    "mode": "event",
    "walk_forward": True,
    "selection_changes": [
      {"window": 1, "added": selected_basket, "removed": []},
    ],
    "asset_frequency": {
      a: {"selected": 1, "eligible": 1, "pct": 100.0}
      for a in selected_basket
    },
    "per_window": [{"window_num": 1}],
    "winning_batches": [1, 2],
    "summary": {"total_trades": 0, "net_profit": 0.0},
    "step2_config": {},
    "config_used": {},
  }


# ── Tests ─────────────────────────────────────────────────────────

def test_t002k_addition_path_creates_newcomer_rows(db, monkeypatch):
  """Happy path — two newcomers must gain PA rows with correct metadata.

  Pre-refresh PA: B1_BTCUSDT_long (active), B2_ETHUSDT_long (active)
  Step-2 basket:  B1_BTCUSDT_long (kept), B1_ETHUSDT_short (newcomer),
                  B2_BTCUSDT_short (newcomer)

  Post-refresh expectation:
    - 4 PA rows total (2 pre-existing + 2 newcomers)
    - Newcomer rows have correct symbol / trade_side / batch_id /
      strategy_name (from src_cfg) / config_name (from BatchBacktestRun) /
      timeframe (from batch_info) / is_active=True
    - B1_BTCUSDT_long stays active; B2_ETHUSDT_long flips inactive
  """
  _seed_universe(db)
  pid = _make_hybrid_portfolio_with_two_assets(db)

  new_basket = [
    "B1_BTCUSDT_long",    # kept
    "B1_ETHUSDT_short",   # newcomer in batch 1
    "B2_BTCUSDT_short",   # newcomer in batch 2
  ]

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(svc, "run_step2", lambda **kw: _fake_step2_result(new_basket))

  result = svc.refresh_portfolio(pid)

  assert result["ok"] is True, result
  assert result["basket_size"] == 3
  assert set(result["added"]) == {"B1_ETHUSDT_short", "B2_BTCUSDT_short"}
  assert set(result["removed"]) == {"B2_ETHUSDT_long"}

  # ── PA row count: 2 pre-existing + 2 newcomers = 4 ──
  assets = (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == pid)
    .all()
  )
  assert len(assets) == 4, (
    f"expected 4 PA rows (2 existing + 2 newcomers), got {len(assets)}: "
    f"{[(a.symbol, a.trade_side, a.batch_id, a.is_active) for a in assets]}"
  )

  # ── Keyed lookup for asserting per-row fields ──
  by_key = {
    f"B{a.batch_id}_{a.symbol}_{a.trade_side}": a
    for a in assets
  }
  assert set(by_key.keys()) == {
    "B1_BTCUSDT_long",
    "B2_ETHUSDT_long",
    "B1_ETHUSDT_short",
    "B2_BTCUSDT_short",
  }

  # ── Pre-existing row is_active flips correctly ──
  assert by_key["B1_BTCUSDT_long"].is_active is True, "kept asset must stay active"
  assert by_key["B2_ETHUSDT_long"].is_active is False, "dropped asset must flip inactive"

  # ── Newcomer rows: correct core fields ──
  newcomer_b1 = by_key["B1_ETHUSDT_short"]
  assert newcomer_b1.is_active is True
  assert newcomer_b1.symbol == "ETHUSDT"
  assert newcomer_b1.trade_side == "short"
  assert newcomer_b1.batch_id == 1
  assert newcomer_b1.strategy_name == "TestStrat", (
    f"strategy_name must come from src_cfg['strategy_name'], "
    f"got {newcomer_b1.strategy_name!r}"
  )
  assert newcomer_b1.config_name == "cfg_B1", (
    f"config_name must come from BatchBacktestRun.parameters['config_name'] "
    f"for batch 1, got {newcomer_b1.config_name!r}"
  )
  assert newcomer_b1.timeframe == "30", (
    f"timeframe must come from batch_info[bid]['timeframe'], "
    f"got {newcomer_b1.timeframe!r}"
  )

  newcomer_b2 = by_key["B2_BTCUSDT_short"]
  assert newcomer_b2.is_active is True
  assert newcomer_b2.symbol == "BTCUSDT"
  assert newcomer_b2.trade_side == "short"
  assert newcomer_b2.batch_id == 2
  assert newcomer_b2.strategy_name == "TestStrat"
  assert newcomer_b2.config_name == "cfg_B2", (
    f"config_name must come from BatchBacktestRun.parameters['config_name'] "
    f"for batch 2, got {newcomer_b2.config_name!r}"
  )
  assert newcomer_b2.timeframe == "30"

  # ── LiveEvent still emitted (audit trail unchanged) ──
  events = (
    db.query(LiveEvent)
    .filter(
      LiveEvent.portfolio_id == pid,
      LiveEvent.event_type == "hybrid_refresh",
    )
    .all()
  )
  assert len(events) == 1
  details = json.loads(events[0].details)
  assert set(details["added"]) == {"B1_ETHUSDT_short", "B2_BTCUSDT_short"}
  assert set(details["removed"]) == {"B2_ETHUSDT_long"}


def test_t002k_non_hybrid_sibling_untouched(db, monkeypatch):
  """Safety invariant — refresh_all_hybrid_portfolios must not create or
  modify any PA rows on a mode='non_hybrid' sibling.

  The T-002k addition path touches PortfolioAsset creation for the first
  time in the nightly refresh code path. Regression-proof the Safety
  Contract: the non-hybrid sibling's PA rows must be byte-identical
  before and after.
  """
  _seed_universe(db)
  hpid = _make_hybrid_portfolio_with_two_assets(db)
  nhpid = _make_non_hybrid_sibling(db)

  nh_assets_before = [
    (a.symbol, a.trade_side, a.batch_id, a.is_active,
     a.strategy_name, a.config_name, a.timeframe, a.group_name)
    for a in db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == nhpid)
    .order_by(PortfolioAsset.id)
    .all()
  ]
  nh_assets_count_before = len(nh_assets_before)
  assert nh_assets_count_before == 1, "sibling must start with 1 PA row"

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  # Step-2 returns a basket with newcomers — the hybrid-only filter is what
  # must prevent any write to the sibling.
  monkeypatch.setattr(
    svc, "run_step2",
    lambda **kw: _fake_step2_result([
      "B1_BTCUSDT_long",
      "B1_ETHUSDT_short",  # newcomer
      "B2_BTCUSDT_short",  # newcomer
    ]),
  )

  report = svc.refresh_all_hybrid_portfolios()

  # Sibling MUST NOT appear in the refresh results
  assert all(r.get("portfolio_id") != nhpid for r in report["results"]), (
    "non_hybrid sibling was processed by hybrid refresh — "
    "Safety Contract violation"
  )
  # Our hybrid MUST have been processed
  assert any(r.get("ok") and r.get("portfolio_id") == hpid
             for r in report["results"]), (
    "hybrid portfolio was not refreshed"
  )

  nh_assets_after = [
    (a.symbol, a.trade_side, a.batch_id, a.is_active,
     a.strategy_name, a.config_name, a.timeframe, a.group_name)
    for a in db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == nhpid)
    .order_by(PortfolioAsset.id)
    .all()
  ]
  assert nh_assets_after == nh_assets_before, (
    "non_hybrid sibling PA rows mutated during hybrid refresh — "
    f"before={nh_assets_before}, after={nh_assets_after}"
  )


# ── A2 — Metadata must persist even on empty basket ──────────────

def _empty_basket_step2_result() -> dict:
  """Step-2 result with an empty basket BUT fully populated metadata.

  This simulates a stable-basket night: Step-2 ran successfully, produced a
  fresh aggregate_monthly_matrix / per_window / continuation snapshot, but
  had no basket-level deltas (no additions, no removals). The metadata still
  needs to land on the portfolio so the monthly grid reflects April PnL.
  """
  return {
    "mode": "event",
    "walk_forward": True,
    "selection_changes": [],   # empty — no deltas
    "asset_frequency": {},     # empty fallback also yields nothing
    "per_window": [{
      "window_num": 1,
      "final_state_snapshot": {"buf": "snap_v2"},
    }],
    "winning_batches": [1, 2],
    "summary": {"total_trades": 42, "net_profit": 1234.56},
    "step2_config": {},
    "config_used": {},
    "selected_records": [{"asset": "B1_BTCUSDT_long", "pnl": 100.0}],
    "aggregate_monthly_matrix": {"2026": {"4": {"net_pnl": 123.45}}},
    "aggregate_equity_curve": [{"t": "2026-04-01", "v": 10100.0}],
    "aggregate_breakdown": {"wins": 5, "losses": 2},
    "cumulative_metrics": {"total_pnl": 1234.56},
    "latest_asset_scores": {},
  }


def test_refresh_fails_closed_on_empty_basket(db, monkeypatch):
  """Anchored-walk contract: a genuinely EMPTY basket means Step-2 graded
  nothing at all — a degenerate run. Refresh must fail closed: no PA flips,
  no newcomers, no Kelly row, no event, no metadata write, ok=False.

  This replaces the pre-anchored-walk "persist metadata on empty basket"
  contract (the old A2 fix). Under the anchored model a stable-basket night
  returns the incumbent basket via `latest_portfolio_at_end` /
  `asset_frequency` — non-empty — and lands on the `basket_unchanged` path,
  which DOES persist metadata (see next test). An empty basket is no longer
  "no deltas"; it is "selected literally nothing", and overwriting good
  refresh_step2_result metadata with a degenerate run's output is worse
  than failing closed.
  """
  _seed_universe(db)
  pid = _make_hybrid_portfolio_with_two_assets(db)

  p_before = db.query(Portfolio).filter(Portfolio.id == pid).first()
  last_refreshed_before = p_before.last_refreshed_at
  src_cfg_before = p_before.source_strategies

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(
    svc, "run_step2", lambda **kw: _empty_basket_step2_result(),
  )

  result = svc.refresh_portfolio(pid)
  db.expire_all()

  assert result["ok"] is False, (
    f"degenerate empty basket must fail closed, got {result}"
  )
  assert result.get("reason") == "step2_picked_empty"
  assert result["basket_size"] == 0
  assert result["weights_written"] == 0
  assert result["added"] == [] and result["removed"] == []

  p_after = db.query(Portfolio).filter(Portfolio.id == pid).first()
  assert p_after.last_refreshed_at == last_refreshed_before
  assert p_after.source_strategies == src_cfg_before, (
    "fail-closed path must not write refresh_step2_result — a degenerate "
    "Step-2 run must not overwrite the last good metadata"
  )

  pa_rows = db.query(PortfolioAsset).filter(
    PortfolioAsset.portfolio_id == pid).all()
  assert len(pa_rows) == 2
  for pa in pa_rows:
    assert pa.is_active is True

  kelly_count = db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == pid).count()
  assert kelly_count == 0

  for evt_type in ("hybrid_refresh", "phase2_evaluated_no_change",
                   "hybrid_nightly_refresh",
                   "hybrid_nightly_refresh_metadata_only"):
    evts = db.query(LiveEvent).filter(
      LiveEvent.portfolio_id == pid,
      LiveEvent.event_type == evt_type).all()
    assert evts == [], f"fail-closed path emitted a {evt_type} event"


def test_refresh_persists_metadata_on_unchanged_basket(db, monkeypatch):
  """Anchored-walk successor of the A2 fix: a stable-basket night still
  lands the fresh monthly-matrix metadata.

  Under the anchored model Step-2 keeps returning the incumbent basket
  (same assets, no deltas), which lands on the `basket_unchanged` path:
  `_persist_refresh` writes `refresh_step2_result` (monthly matrix, equity
  curve, cumulative metrics) WITHOUT touching PA rows, Kelly rows, or the
  optimizer's saved `step2_result` / `wfo_per_window` / `continuation_state`.

  Assertions:
    - ok=True, reason="basket_unchanged", basket_size=2.
    - refresh_step2_result.aggregate_monthly_matrix reflects the fresh run.
    - continuation_state / wfo_per_window NOT written (deliberately dropped
      in the anchored refactor — seed-contamination vector).
    - NO new KellyAllocation (basket is identical — nothing to publish).
    - PA rows unchanged in count AND is_active.
    - Exactly one `phase2_evaluated_no_change` LiveEvent; no `hybrid_refresh`.
  """
  _seed_universe(db)
  pid = _make_hybrid_portfolio_with_two_assets(db)

  # Fake returns the SAME basket the portfolio already trades → unchanged path.
  same_basket = ["B1_BTCUSDT_long", "B2_ETHUSDT_long"]

  def _unchanged_step2_result() -> dict:
    r = _fake_step2_result(same_basket)
    r["latest_portfolio_at_end"] = list(same_basket)
    r["selected_records"] = [{"asset": "B1_BTCUSDT_long", "pnl": 100.0}]
    r["aggregate_monthly_matrix"] = {"2026": {"4": {"net_pnl": 123.45}}}
    r["aggregate_equity_curve"] = [{"t": "2026-04-01", "v": 10100.0}]
    r["aggregate_breakdown"] = {"wins": 5, "losses": 2}
    r["cumulative_metrics"] = {"total_pnl": 1234.56}
    r["latest_asset_scores"] = {a: 1.0 for a in same_basket}
    return r

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(svc, "run_step2", lambda **kw: _unchanged_step2_result())

  result = svc.refresh_portfolio(pid)
  db.expire_all()

  assert result["ok"] is True, f"unchanged-basket night must succeed, got {result}"
  assert result.get("reason") == "basket_unchanged"
  assert result["basket_size"] == 2
  assert result["added"] == [] and result["removed"] == []

  p_after = db.query(Portfolio).filter(Portfolio.id == pid).first()
  assert p_after.last_refreshed_at is not None

  src_after = json.loads(p_after.source_strategies or "{}")
  rsr = src_after.get("refresh_step2_result") or {}
  assert rsr.get("aggregate_monthly_matrix") == {"2026": {"4": {"net_pnl": 123.45}}}, (
    "fresh monthly matrix must persist on a stable-basket night — "
    "this is the anchored-walk successor of the A2 missing-April-PnL fix"
  )
  assert rsr.get("cumulative_metrics") == {"total_pnl": 1234.56}
  assert rsr.get("last_oos_end_walked"), "walk cursor must advance"

  assert "continuation_state" not in src_after, (
    "anchored walk deliberately does NOT write continuation_state — "
    "seed-contamination vector removed in the June refactor"
  )
  assert "wfo_per_window" not in src_after, (
    "anchored walk must not overwrite the optimizer's saved window history"
  )

  kelly_count = db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == pid).count()
  assert kelly_count == 0, (
    "unchanged basket must not write a new KellyAllocation row"
  )

  pa_rows = (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == pid)
    .order_by(PortfolioAsset.id)
    .all()
  )
  assert len(pa_rows) == 2
  for pa in pa_rows:
    assert pa.is_active is True

  no_change = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type == "phase2_evaluated_no_change").all()
  assert len(no_change) == 1, (
    "stable-basket night must emit exactly one phase2_evaluated_no_change "
    "event so the Activity feed shows the refresh ran"
  )
  changed = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type == "hybrid_refresh").all()
  assert changed == [], (
    "stable-basket night must NOT emit the rotation event"
  )



# ── B — Cross-strat per-batch strategy_name resolution ────────────

def _seed_cross_strat_universe(db):
  """Two strategies, each with their own batch.

  B3 ← strategy_id=10 (CrossStratA)
  B4 ← strategy_id=11 (CrossStratB)

  This models a cross-strat portfolio where the anchor strategy is per-batch,
  not portfolio-level. The saved src_cfg carries strategy_name="" — newcomer
  resolution must fall through to BatchBacktestRun.strategy_id → Strategy.name.
  """
  for sid, sname in [(10, "CrossStratA"), (11, "CrossStratB")]:
    if db.query(Strategy).filter(Strategy.id == sid).first() is None:
      db.add(Strategy(id=sid, name=sname, source_type="manual"))
  db.flush()

  for bid, sid, cfg_name in [(3, 10, "cfg_A"), (4, 11, "cfg_B")]:
    if db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first() is None:
      db.add(BatchBacktestRun(
        id=bid, strategy_id=sid, timeframe="30",
        direction_filter="long_plus_short", status="completed",
        total_symbols=1, completed_symbols=1,
        parameters=json.dumps({"config_name": cfg_name, "initial_capital": 10000}),
      ))
  db.flush()

  start = datetime(2024, 1, 1)
  run_id = 100
  for bid, sid in [(3, 10), (4, 11)]:
    for sym in ("BTCUSDT", "ETHUSDT"):
      for side in ("long", "short"):
        run_id += 1
        if db.query(BacktestRun).filter(BacktestRun.id == run_id).first() is not None:
          continue
        db.add(BacktestRun(
          id=run_id, strategy_id=sid, symbol=sym, timeframe="30",
          start_date=start, end_date=start + timedelta(days=60),
          status="completed", batch_id=bid,
        ))
        db.flush()
        for i in range(3):
          entry = start + timedelta(days=5 * (i + 1))
          db.add(BacktestTrade(
            run_id=run_id, trade_num=i + 1, position_id=f"X{run_id}_{i}",
            symbol=sym, side=side,
            entry_time=entry, exit_time=entry + timedelta(hours=4),
            entry_price=1000.0, exit_price=1010.0, quantity=0.1,
            net_profit=10.0, net_profit_pct=1.0, commission=0.1,
          ))
  db.commit()


def _make_cross_strat_portfolio(db) -> int:
  """Cross-strat hybrid portfolio: strategy_name="" at the portfolio level.

  Pre-existing PA: {B3_BTCUSDT_long} so the basket diff has both kept +
  newcomer assets during refresh.
  """
  p = Portfolio(
    name="cross_strat_hybrid",
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    is_cross_strat=True,
    optimization_end_date=datetime(2024, 6, 1),
    source_strategies=json.dumps({
      "strategy_name": "",
      "is_cross_strat": True,
      "hybrid_mode": True,
      "oos_months": 2,
      "hybrid_n_trades": 1,
      "hybrid_n_assets": 3,
      "wfo_windows_meta": _WMETA,
    }),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="long",
    batch_id=3, is_active=True,
    strategy_name="CrossStratA",
    timeframe="30",
  ))
  db.commit()
  return p.id


def test_b_cross_strat_newcomers_resolve_per_batch_strategy_name(db, monkeypatch):
  """B — When src_cfg.strategy_name is empty (cross-strat portfolios),
  newcomer PA rows must be created with the per-batch strategy resolved via
  BatchBacktestRun.strategy_id → Strategy.name.

  Pre-B: the old `if not _portfolio_strategy_name: skip all newcomers` branch
  meant portfolios 23/24 (which save with strategy_name="") would never gain
  newcomer PA rows — the addition path silently skipped them all.

  B fix: `_resolve_batch_meta(bid)` returns `(strategy_name, config_name)`;
  the newcomer loop prefers portfolio-level strategy_name, falls back to the
  batch's own strategy, and only skips the individual row if BOTH are empty.
  """
  _seed_cross_strat_universe(db)
  pid = _make_cross_strat_portfolio(db)

  new_basket = [
    "B3_BTCUSDT_long",    # kept
    "B3_ETHUSDT_short",   # newcomer in batch 3 → CrossStratA
    "B4_BTCUSDT_short",   # newcomer in batch 4 → CrossStratB
  ]

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(
    svc, "run_step2", lambda **kw: _fake_step2_result(new_basket),
  )

  result = svc.refresh_portfolio(pid)
  db.expire_all()

  assert result["ok"] is True, f"refresh must succeed, got {result}"
  assert sorted(result.get("newcomers_created") or []) == sorted([
    "B3_ETHUSDT_short", "B4_BTCUSDT_short",
  ]), (
    f"both cross-strat newcomers must create PA rows via per-batch resolver. "
    f"created={result.get('newcomers_created')}, "
    f"skipped={result.get('skipped_newcomers')}"
  )
  assert not result.get("skipped_newcomers"), (
    f"no newcomer should be skipped when per-batch strategy is resolvable: "
    f"{result.get('skipped_newcomers')}"
  )

  pa_rows = (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == pid, PortfolioAsset.is_active == True)  # noqa: E712
    .all()
  )
  by_key = {
    f"B{pa.batch_id}_{pa.symbol}_{pa.trade_side}": pa
    for pa in pa_rows
  }

  pa_b3 = by_key.get("B3_ETHUSDT_short")
  assert pa_b3 is not None, "B3_ETHUSDT_short PA row missing"
  assert pa_b3.strategy_name == "CrossStratA", (
    f"B3 newcomer must inherit CrossStratA (strategy_id=10 on batch 3), "
    f"got {pa_b3.strategy_name!r}"
  )
  assert pa_b3.config_name == "cfg_A"

  pa_b4 = by_key.get("B4_BTCUSDT_short")
  assert pa_b4 is not None, "B4_BTCUSDT_short PA row missing"
  assert pa_b4.strategy_name == "CrossStratB", (
    f"B4 newcomer must inherit CrossStratB (strategy_id=11 on batch 4) — "
    f"NOT CrossStratA. Per-batch resolution is what makes cross-strat "
    f"portfolios work. Got {pa_b4.strategy_name!r}"
  )
  assert pa_b4.config_name == "cfg_B"


def test_b_portfolio_strategy_name_takes_precedence(db, monkeypatch):
  """B — When src_cfg.strategy_name IS set (single-strategy portfolio), it
  must take precedence over per-batch resolution. Keeps T-002k's original
  contract intact for non-cross-strat portfolios.
  """
  _seed_universe(db)
  pid = _make_hybrid_portfolio_with_two_assets(db)

  new_basket = [
    "B1_BTCUSDT_long",
    "B1_ETHUSDT_short",   # newcomer
  ]

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(
    svc, "run_step2", lambda **kw: _fake_step2_result(new_basket),
  )

  result = svc.refresh_portfolio(pid)
  db.expire_all()

  assert result["ok"] is True
  assert "B1_ETHUSDT_short" in (result.get("newcomers_created") or [])
  pa = (
    db.query(PortfolioAsset)
    .filter(
      PortfolioAsset.portfolio_id == pid,
      PortfolioAsset.symbol == "ETHUSDT",
      PortfolioAsset.trade_side == "short",
      PortfolioAsset.batch_id == 1,
    )
    .first()
  )
  assert pa is not None
  assert pa.strategy_name == "TestStrat", (
    f"portfolio-level strategy_name='TestStrat' must take precedence, "
    f"got {pa.strategy_name!r}"
  )


# ── δ — Bootstrap-aware synthetic window ──────────────────────────

def _capture_step2_args():
  """Factory: returns (captured, fake_run_step2) pair.

  `captured` is a dict that will be filled with the args run_step2 was called
  with. `fake_run_step2` can be monkeypatched onto the service so the real
  Step-2 engine never runs — we only care about how refresh_portfolio built
  the step1_result.
  """
  captured: dict = {}

  def fake(**kw):
    captured["step1_result"] = kw.get("step1_result")
    captured["config"] = kw.get("config")
    # Return the incumbent basket -> basket_unchanged success path, while
    # still capturing the synthetic step1_result / config under test.
    return _fake_step2_result(["B1_BTCUSDT_long"])

  return captured, fake


def _make_cold_portfolio(db) -> tuple[int, datetime]:
  """Cross-strat portfolio with NO continuation_state — models P23/P24 reality.

  Returns (portfolio_id, optimization_end_date).
  """
  cutoff = datetime(2024, 6, 1)
  p = Portfolio(
    name="cold_start_test",
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    is_cross_strat=True,
    optimization_end_date=cutoff,
    source_strategies=json.dumps({
      "strategy_name": "TestStrat",
      "is_cross_strat": True,
      "hybrid_mode": True,
      "oos_months": 2,
      "hybrid_n_trades": 20,
      "hybrid_n_assets": 3,
      "wfo_windows_meta": _WMETA,
    }),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="long",
    batch_id=1, is_active=True, strategy_name="TestStrat", timeframe="30",
  ))
  db.commit()
  return p.id, cutoff


def _make_warm_portfolio(db) -> tuple[int, datetime]:
  """Portfolio with `continuation_state.asset_buffers` populated — models a
  portfolio that has been refreshed at least once and now has warm per-asset
  scoring buffers.

  Returns (portfolio_id, optimization_end_date).
  """
  cutoff = datetime(2024, 6, 1)
  p = Portfolio(
    name="warm_start_test",
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    optimization_end_date=cutoff,
    source_strategies=json.dumps({
      "strategy_name": "TestStrat",
      "hybrid_mode": True,
      "oos_months": 2,
      "hybrid_n_trades": 20,
      "hybrid_n_assets": 3,
      "wfo_windows_meta": _WMETA,
      # Leftover warm seed — the anchored walk must IGNORE it (explicitly
      # dropped as `_seed_state`; warmup is rebuilt from the IS span instead).
      "continuation_state": {
        "asset_buffers": {
          "B1_BTCUSDT_long": [{"pnl": 10.0, "ts": "2024-05-28T00:00:00"}],
          "B2_ETHUSDT_long": [{"pnl": 5.0, "ts": "2024-05-29T00:00:00"}],
        },
        "other_state": "whatever",
      },
    }),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="long",
    batch_id=1, is_active=True, strategy_name="TestStrat", timeframe="30",
  ))
  db.commit()
  return p.id, cutoff


def test_refresh_anchored_window_carries_warmup_span(db, monkeypatch):
  """Anchored-walk contract (supersedes the δ 90-day extension): the walk
  window is [anchor .. today] where anchor = last committed oos_end + 1,
  and the IS span [is_start .. anchor-1] supplies the warmup runway that
  rebuilds Step-2's scoring buffers.

  The old δ fix extended oos_start backward on cold start; the June 2026
  anchored refactor instead builds a deep IS span from the saved window's
  is_start (or anchor - max(oos_months,6) months when absent). Trades in
  the IS span populate buffers but are never recorded (date_from gate).
  """
  _seed_universe(db)
  pid, cutoff = _make_cold_portfolio(db)

  captured, fake = _capture_step2_args()

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(svc, "run_step2", fake)

  result = svc.refresh_portfolio(pid)
  db.expire_all()

  assert result["ok"] is True, f"refresh must succeed, got {result}"

  step1 = captured.get("step1_result")
  assert step1 is not None, "run_step2 was not called"
  windows = step1.get("windows") or []
  assert len(windows) == 1, f"synthetic step1 must have exactly one window, got {len(windows)}"

  w = windows[0]
  # anchor = _WMETA chain's last oos_end (2024-05-31) + 1 = 2024-06-01
  assert w["oos_start"] == "2024-06-01", (
    f"oos_start must be the committed anchor boundary, got {w['oos_start']}"
  )
  # warmup runway: newest saved window's is_start (2024-01-01) gives ~5
  # months of buffer-rebuilding history — the anchored replacement for the
  # retired δ 90-day extension.
  assert w["is_start"] == "2024-01-01", (
    f"is_start must carry the warmup span, got {w['is_start']}"
  )
  assert w["is_end"] == "2024-05-31", (
    f"is_end must end the day before the anchor, got {w['is_end']}"
  )

  # No seed is ever passed — warmup comes from the IS span, not a snapshot.
  cfg = captured.get("config") or {}
  assert "_seed_state" not in cfg, (
    "anchored walk must never pass _seed_state — the warmup IS span "
    "rebuilds buffers without double-counting (FLAW 2 fix)"
  )
  assert result["used_seed"] is False


def test_refresh_drops_stale_continuation_state(db, monkeypatch):
  """Anti-contamination guard: even when src_cfg still carries a stale
  `continuation_state` blob (written by the retired δ self-heal), the
  anchored walk must NOT forward it to run_step2 as `_seed_state`.

  Seeding a live walk with a stale snapshot would double-count trades the
  warmup span already graded — the exact FLAW 2 the anchored refactor
  fixed. This portfolio has warm-looking continuation_state; the walk must
  ignore it entirely.
  """
  _seed_universe(db)
  pid, cutoff = _make_warm_portfolio(db)

  captured, fake = _capture_step2_args()

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(svc, "run_step2", fake)

  result = svc.refresh_portfolio(pid)
  db.expire_all()

  assert result["ok"] is True, f"refresh must succeed, got {result}"

  cfg = captured.get("config") or {}
  assert cfg.get("_seed_state") is None, (
    f"stale continuation_state must NOT become _seed_state — got {cfg.get('_seed_state')!r}"
  )
  assert result["used_seed"] is False, (
    "anchored walk always runs unseeded; used_seed must stay False"
  )

  step1 = captured.get("step1_result")
  windows = (step1 or {}).get("windows") or []
  assert windows[0]["oos_start"] == "2024-06-01", (
    "warm state must not move the anchor — the boundary is fixed"
  )


# ── E2E — full nightly pipeline integration ──────────────────────

def _full_refresh_step2_result(basket: list[str]) -> dict:
  """Step-2 result that simulates a full warm pass: carries
  `aggregate_monthly_matrix`, `selection_changes`, and a `per_window` whose
  tail has `final_state_snapshot.asset_buffers`. Under the anchored walk the
  snapshot is NOT persisted to `continuation_state` (seed contamination);
  what must persist is `refresh_step2_result` metadata + `refresh_anchor`.
  """
  return {
    "mode": "event",
    "walk_forward": True,
    "selection_changes": [
      {"window": 1, "added": basket, "removed": []},
    ],
    "asset_frequency": {
      a: {"selected": 1, "eligible": 1, "pct": 100.0} for a in basket
    },
    "per_window": [{
      "window_num": 1,
      "final_state_snapshot": {
        "asset_buffers": {
          a: [{"pnl": 12.5, "ts": "2024-05-30T00:00:00"}]
          for a in basket
        },
        "warmup_counts": {a: 5 for a in basket},
      },
    }],
    "winning_batches": [1, 2],
    "summary": {"total_trades": 9, "net_profit": 42.0},
    "step2_config": {},
    "config_used": {},
    "selected_records": [{"asset": basket[0], "pnl": 100.0}] if basket else [],
    "aggregate_monthly_matrix": {"2026": {"4": {"net_pnl": 789.01}}},
    "aggregate_equity_curve": [{"t": "2026-04-01", "v": 10100.0}],
    "aggregate_breakdown": {"wins": 4, "losses": 2},
    "cumulative_metrics": {"total_pnl": 789.01},
    "latest_asset_scores": {a: 1.0 for a in basket},
  }


def test_nightly_refresh_pipeline_end_to_end(db, monkeypatch):
  """E2E — `refresh_all_hybrid_portfolios` (the top-level nightly entrypoint)
  must drive the full pipeline cleanly for a cold-start cross-strat portfolio.

  This is the regression test Leader flagged as missing at session start: the
  overnight refresh silently didn't fire, and even manual triggers didn't
  produce the expected writes because A2 short-circuited before metadata
  persistence. After A2 + B + δ, a full pass on a cold-start portfolio must:

    1. Return ok=1 / failed=0 in the wrapper report
    2. Update `last_refreshed_at` on the portfolio
    3. Persist `aggregate_monthly_matrix` with April 2026 data under
       `refresh_step2_result` (the anchored-walk metadata key)
    4. Persist `refresh_anchor` so later ticks reuse the same boundary
    5. Emit a LiveEvent (basket-change here — `hybrid_refresh`)
    6. Create PA rows for newcomers (T-002k + B for cross-strat)
    7. Leave any non-hybrid sibling untouched (Safety Contract)
  """
  _seed_cross_strat_universe(db)
  pid, _cutoff = (_make_cold_portfolio_cs(db))
  nh_pid = _make_non_hybrid_sibling(db)

  # Snapshot non-hybrid sibling for Safety Contract check
  nh_before = [
    (a.symbol, a.trade_side, a.batch_id, a.is_active,
     a.strategy_name, a.config_name, a.timeframe)
    for a in db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == nh_pid)
    .order_by(PortfolioAsset.id)
    .all()
  ]

  # Basket: one kept, two newcomers across two different strategies
  basket = [
    "B3_BTCUSDT_long",    # kept — pre-existing PA
    "B3_ETHUSDT_short",   # newcomer → CrossStratA
    "B4_BTCUSDT_short",   # newcomer → CrossStratB (different strategy!)
  ]

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(
    svc, "run_step2", lambda **kw: _full_refresh_step2_result(basket),
  )

  # ── Nightly entrypoint ──
  wrapper = svc.refresh_all_hybrid_portfolios()
  db.expire_all()

  # 1. Wrapper contract
  assert wrapper["total_hybrid"] >= 1, f"wrapper must see at least our hybrid portfolio: {wrapper}"
  assert wrapper["ok"] >= 1, f"nightly entrypoint must report ok>=1, got {wrapper}"
  assert wrapper["failed"] == 0, (
    f"no failures expected on clean pipeline pass, got failed={wrapper['failed']}: "
    f"{wrapper.get('results')}"
  )
  our_result = next(
    (r for r in wrapper["results"] if r.get("portfolio_id") == pid),
    None,
  )
  assert our_result is not None, "our cold-start portfolio missing from wrapper results"
  assert our_result["ok"] is True
  assert our_result.get("basket_size") == 3, (
    "basket was non-empty — a real rotation should be reported"
  )

  # 2. last_refreshed_at
  p_after = db.query(Portfolio).filter(Portfolio.id == pid).first()
  assert p_after.last_refreshed_at is not None

  # 3 + 4. source_strategies persisted artifacts (anchored-walk keys: the
  # live walk writes `refresh_step2_result`; the optimizer's `step2_result`,
  # `wfo_per_window` and `continuation_state` are deliberately untouched).
  src_after = json.loads(p_after.source_strategies or "{}")
  rsr = src_after.get("refresh_step2_result") or {}
  assert rsr.get("aggregate_monthly_matrix") == {
    "2026": {"4": {"net_pnl": 789.01}}
  }, "April monthly grid must persist — the root fix for P23/P24"
  assert src_after.get("refresh_anchor") == "2024-06-01", (
    "the resolved walk anchor must persist so every later tick reuses the "
    "exact same boundary"
  )
  assert rsr.get("last_oos_end_walked"), "walk cursor must advance"

  # 5. LiveEvent (basket-change event, since basket is non-empty)
  change_events = (
    db.query(LiveEvent)
    .filter(
      LiveEvent.portfolio_id == pid,
      LiveEvent.event_type == "hybrid_refresh",
    )
    .all()
  )
  assert len(change_events) == 1, (
    f"non-empty basket must emit exactly one basket-change LiveEvent, "
    f"got {len(change_events)}"
  )

  # 6. Newcomer PA rows
  pa_rows = (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == pid, PortfolioAsset.is_active == True)  # noqa: E712
    .all()
  )
  by_key = {f"B{p.batch_id}_{p.symbol}_{p.trade_side}": p for p in pa_rows}
  assert "B3_ETHUSDT_short" in by_key, "newcomer B3_ETHUSDT_short PA missing"
  assert "B4_BTCUSDT_short" in by_key, "newcomer B4_BTCUSDT_short PA missing"
  assert by_key["B3_ETHUSDT_short"].strategy_name == "CrossStratA"
  assert by_key["B4_BTCUSDT_short"].strategy_name == "CrossStratB"

  # 7. Safety Contract: non-hybrid sibling untouched
  nh_after = [
    (a.symbol, a.trade_side, a.batch_id, a.is_active,
     a.strategy_name, a.config_name, a.timeframe)
    for a in db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == nh_pid)
    .order_by(PortfolioAsset.id)
    .all()
  ]
  assert nh_after == nh_before, (
    f"non-hybrid sibling mutated during nightly refresh (Safety Contract "
    f"violation). before={nh_before}, after={nh_after}"
  )


def _make_cold_portfolio_cs(db) -> tuple[int, datetime]:
  """Cold-start cross-strat portfolio for E2E test: no continuation_state,
  seeded with ONE PA row in batch 3 so the basket diff sees kept + newcomers.
  """
  cutoff = datetime(2024, 6, 1)
  p = Portfolio(
    name="e2e_cold_cross_strat",
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    is_cross_strat=True,
    optimization_end_date=cutoff,
    source_strategies=json.dumps({
      "strategy_name": "",   # cross-strat → empty portfolio-level anchor
      "is_cross_strat": True,
      "hybrid_mode": True,
      "oos_months": 2,
      "hybrid_n_trades": 1,
      "hybrid_n_assets": 3,
      "wfo_windows_meta": _WMETA,
      # continuation_state intentionally missing to trigger cold-start branch
    }),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="long",
    batch_id=3, is_active=True, strategy_name="CrossStratA", timeframe="30",
  ))
  db.commit()
  return p.id, cutoff
