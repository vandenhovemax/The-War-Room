"""Anchored-walk regression suite (2026-06-10 audit fixes).

Locks the invariants that ended the "data keeps changing" failure mode:

  A. The nightly walk's window starts at the FIXED anchor (committed-history
     boundary) — never inside the save-time records' span (FLAW 1: p37
     re-graded Oct-2025→today nightly and the merged view double-mixed two
     simulations of the same months).
  B. The walk runs with NO seed state (FLAW 2: seeding yesterday's end-state
     and re-walking the same window double-counted buffers and leaked future
     trades into past decision points).
  C. The refresh persists ONLY refresh_step2_result — it must not overwrite
     the optimizer's saved wfo_per_window or write continuation_state.
  D. The anchor is sticky: stored on first resolution, reused verbatim.
  E. get_effective_step2 rebuilds the monthly matrix / equity / cumulative
     from the MERGED records — a month split across save and refresh sums
     both sides (the old per-blob union silently dropped the save side).
  F. Live `Trade` rows enter the unified stream in NYT-naive time (they are
     stored UTC; backtest trades are NYT-naive — unconverted merge skewed
     live fills 4-5h).
  G. selected_records with negative run_id (live fills) resolve to `trades`
     rows in the replay display instead of being silently dropped.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  Portfolio,
  PortfolioAsset,
  Strategy,
  Trade,
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


WINDOWS_META = [
  {"window_num": 1, "is_start": "2024-01-01", "is_end": "2024-02-29",
   "oos_start": "2024-03-01", "oos_end": "2024-04-30"},
  {"window_num": 2, "is_start": "2024-03-01", "is_end": "2024-04-30",
   "oos_start": "2024-05-01", "oos_end": "2024-06-30"},
]


def _seed_universe(db):
  if db.query(Strategy).filter(Strategy.id == 1).first() is not None:
    return
  db.add(Strategy(id=1, name="AnchorStrat", source_type="manual"))
  db.flush()
  db.add(BatchBacktestRun(
    id=1, strategy_id=1, timeframe="30", direction_filter="long_plus_short",
    status="completed", total_symbols=1, completed_symbols=1,
    parameters=json.dumps({"initial_capital": 10000}),
  ))
  db.flush()
  db.add(BacktestRun(
    id=1, strategy_id=1, symbol="BTCUSDT", timeframe="30",
    start_date=datetime(2024, 1, 1), end_date=datetime(2024, 12, 31),
    status="completed", batch_id=1,
  ))
  db.flush()
  for i in range(4):
    entry = datetime(2024, 3, 5) + timedelta(days=20 * i)
    db.add(BacktestTrade(
      run_id=1, trade_num=i + 1, position_id=f"P1_{i}",
      symbol="BTCUSDT", side="long",
      entry_time=entry, exit_time=entry + timedelta(hours=6),
      entry_price=100.0, exit_price=101.0, quantity=1.0,
      net_profit=50.0, net_profit_pct=1.0, commission=0.1,
    ))
  db.commit()


def _make_portfolio(db, *, save_records=None, extra_cfg=None, name="anchored") -> int:
  cfg = {
    "strategy_id": 1, "timeframe": "30",
    "hybrid_mode": True, "oos_months": 2,
    "hybrid_n_trades": 3, "hybrid_n_assets": 2,
    "hybrid_min_pf": 1.0, "hybrid_min_wr": 0.3,
    "hybrid_min_hold": 0, "hybrid_rebalance_every": 5,
    "hybrid_rebalance_mode": "global",
    "hybrid_scoring_method": "composite",
    "hybrid_objective": "sortino",
    "wfo_windows_meta": WINDOWS_META,
    # Optimizer residue the refresh must leave untouched (invariant C)
    "wfo_per_window": [{"window_num": 1, "final_state_snapshot": {"asset_buffers": {"X": [1.0]}}}],
    "continuation_state": {"asset_buffers": {"X": [1.0, 2.0]}},
  }
  if save_records is not None:
    cfg["step2_result"] = {"selected_records": save_records}
  if extra_cfg:
    cfg.update(extra_cfg)
  p = Portfolio(
    name=name, portfolio_type="paper", mode="hybrid", is_active=True,
    optimization_end_date=datetime(2024, 6, 30),
    source_strategies=json.dumps(cfg),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="long",
    batch_id=1, is_active=True,
  ))
  db.commit()
  return p.id


def _fake_step2(basket):
  return {
    "mode": "event", "walk_forward": True,
    "latest_portfolio_at_end": list(basket),
    "latest_asset_scores": {a: 1.0 for a in basket},
    "selected_records": [
      {"dt": "2024-07-02T10:00:00", "pnl": 10.0, "asset": a,
       "symbol": a.split("_")[1], "side": a.split("_")[2],
       "timeframe": "30", "run_id": 1, "batch_id": 1}
      for a in basket
    ],
    "asset_frequency": {a: {"selected": 1, "eligible": 1, "pct": 100.0} for a in basket},
    "per_window": [{"window_num": 2}],
    "winning_batches": [1],
    "summary": {"total_trades": len(basket), "net_profit": 0.0},
    "step2_config": {}, "config_used": {},
  }


# ── A + B: window contract, no seed ───────────────────────────────

def test_walk_starts_at_anchor_with_no_seed(db, monkeypatch):
  _seed_universe(db)
  pid = _make_portfolio(db, name="anch_a")

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)

  captured: dict = {}

  def _capture(step1_result, all_trades, run_batch_map, config, db):
    captured["window"] = step1_result["windows"][0]
    captured["config"] = config
    return _fake_step2(["B1_BTCUSDT_long"])

  monkeypatch.setattr(svc, "run_step2", _capture)
  r = svc.refresh_portfolio(pid, simulate_today=date(2024, 7, 15))
  assert r["ok"] is True, r

  w = captured["window"]
  # Anchor = chain last oos_end (2024-06-30) + 1 day (no save records)
  assert w["oos_start"] == "2024-07-01"
  assert w["oos_end"] == "2024-07-15"
  assert w["is_end"] == "2024-06-30"
  # IS warmup span comes from the newest saved window
  assert w["is_start"] == "2024-03-01"
  # NO seed — FLAW 2 fix
  assert "_seed_state" not in captured["config"]


def test_anchor_from_save_records_never_inside_committed(db, monkeypatch):
  _seed_universe(db)
  recs = [
    {"dt": "2024-05-10T09:00:00", "pnl": 5.0, "asset": "B1_BTCUSDT_long",
     "symbol": "BTCUSDT", "side": "long", "run_id": 1, "batch_id": 1},
    {"dt": "2024-06-12T09:00:00", "pnl": 7.0, "asset": "B1_BTCUSDT_long",
     "symbol": "BTCUSDT", "side": "long", "run_id": 1, "batch_id": 1},
  ]
  pid = _make_portfolio(db, save_records=recs, name="anch_b")

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  captured: dict = {}

  def _capture(step1_result, all_trades, run_batch_map, config, db):
    captured["window"] = step1_result["windows"][0]
    return _fake_step2(["B1_BTCUSDT_long"])

  monkeypatch.setattr(svc, "run_step2", _capture)
  r = svc.refresh_portfolio(pid, simulate_today=date(2024, 7, 15))
  assert r["ok"] is True, r
  # Anchor = last committed record (2024-06-12) + 1 day — STRICTLY after
  # every save-time record, so refresh records can never overlap them.
  assert captured["window"]["oos_start"] == "2024-06-13"


# ── C: persistence hygiene ────────────────────────────────────────

def test_refresh_leaves_optimizer_state_untouched(db, monkeypatch):
  _seed_universe(db)
  pid = _make_portfolio(db, name="anch_c")

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  monkeypatch.setattr(svc, "run_step2", lambda **kw: _fake_step2(["B1_BTCUSDT_long"]))

  r = svc.refresh_portfolio(pid, simulate_today=date(2024, 7, 15))
  assert r["ok"] is True, r

  p = db.query(Portfolio).filter(Portfolio.id == pid).first()
  db.refresh(p)
  cfg = json.loads(p.source_strategies)
  # Optimizer residue byte-identical (invariant C)
  assert cfg["wfo_per_window"] == [
    {"window_num": 1, "final_state_snapshot": {"asset_buffers": {"X": [1.0]}}}
  ]
  assert cfg["continuation_state"] == {"asset_buffers": {"X": [1.0, 2.0]}}
  # Refresh result persisted with the anchor stamped
  rsr = cfg["refresh_step2_result"]
  assert rsr["walk_anchor"] == "2024-07-01"
  assert rsr["last_walked_used_seed"] is False
  assert cfg["refresh_anchor"] == "2024-07-01"


# ── D: anchor stickiness ──────────────────────────────────────────

def test_anchor_is_sticky_across_refreshes(db, monkeypatch):
  _seed_universe(db)
  pid = _make_portfolio(db, name="anch_d", extra_cfg={"refresh_anchor": "2024-07-05"})

  import backend.services.hybrid_portfolio_refresh as svc
  monkeypatch.setattr(svc, "SessionLocal", _Session)
  captured: dict = {}

  def _capture(step1_result, all_trades, run_batch_map, config, db):
    captured["window"] = step1_result["windows"][0]
    return _fake_step2(["B1_BTCUSDT_long"])

  monkeypatch.setattr(svc, "run_step2", _capture)
  r = svc.refresh_portfolio(pid, simulate_today=date(2024, 7, 20))
  assert r["ok"] is True, r
  # Stored anchor wins over both save-records and chain derivation.
  assert captured["window"]["oos_start"] == "2024-07-05"


# ── E: merged aggregates rebuilt from records ─────────────────────

def test_effective_step2_sums_boundary_month_from_both_blobs():
  from backend.analysis.portfolio_wfo_replay import get_effective_step2

  settings = {
    "step2_result": {
      "selected_records": [
        {"dt": "2026-04-05T10:00:00", "pnl": 100.0, "asset": "A~1",
         "symbol": "AAA", "side": "long", "run_id": 1},
        {"dt": "2026-04-18T10:00:00", "pnl": 50.0, "asset": "A~1",
         "symbol": "AAA", "side": "long", "run_id": 1},
      ],
      # Save blob carries a matrix that the merge must NOT blindly union
      "aggregate_monthly_matrix": {"matrix": {"2026": {"4": 150.0}}},
      "cumulative_metrics": {"sortino": 1.5},
    },
    "refresh_step2_result": {
      "selected_records": [
        {"dt": "2026-04-25T10:00:00", "pnl": 70.0, "asset": "A~1",
         "symbol": "AAA", "side": "long", "run_id": 2},
      ],
      "aggregate_monthly_matrix": {"matrix": {"2026": {"4": 70.0}}},
    },
  }
  out = get_effective_step2(settings)
  assert len(out["selected_records"]) == 3
  # April = save side (150) + refresh side (70). The old per-blob union
  # returned 70 here — the refresh blob's partial month overwrote save.
  assert out["aggregate_monthly_matrix"]["matrix"]["2026"]["4"] == pytest.approx(220.0)
  assert out["aggregate_monthly_matrix"]["grand_total"] == pytest.approx(220.0)
  # Equity rebuilt across all records, cumulative counts refreshed
  assert out["aggregate_equity_curve"][-1]["equity"] == pytest.approx(220.0)
  cm = out["cumulative_metrics"]
  assert cm["total_trades"] == 3
  assert cm["net_profit"] == pytest.approx(220.0)
  # Ratio metrics we can't rebuild are inherited from the save blob
  assert cm["sortino"] == 1.5


# ── F: live Trade rows convert UTC → NYT ──────────────────────────

def test_unified_trades_convert_live_times_to_nyt(db):
  _seed_universe(db)
  live_pid = _make_portfolio(db, name="anch_f")
  # 2024-07-10 14:30 UTC == 10:30 NYT (EDT, UTC-4)
  db.add(Trade(
    portfolio_id=live_pid, symbol="BTCUSDT", side="long",
    entry_price=100.0, exit_price=101.0, quantity=1.0,
    realized_pnl=25.0, commission=0.1,
    opened_at=datetime(2024, 7, 10, 14, 30),
    closed_at=datetime(2024, 7, 10, 18, 30),
    batch_id=1, is_paper=True, is_test=False,
  ))
  db.commit()

  from backend.analysis.trade_log_unified import load_unified_trades
  trades, rbm, batch_info = load_unified_trades(
    db, batch_ids=[1], cutoff=datetime(2024, 7, 1), portfolio_id=live_pid,
  )
  live = [t for t in trades if t.run_id < 0]
  assert len(live) == 1
  assert live[0].entry_time == datetime(2024, 7, 10, 10, 30)
  assert live[0].exit_time == datetime(2024, 7, 10, 14, 30)
  assert rbm[-1] == 1


# ── G: live-fill records resolve in the replay display ────────────

def test_replay_hybrid_resolves_negative_run_id_records(db):
  _seed_universe(db)
  live_pid = _make_portfolio(db, name="anch_g")
  db.add(Trade(
    portfolio_id=live_pid, symbol="ETHUSDT", side="short",
    entry_price=200.0, exit_price=195.0, quantity=2.0,
    realized_pnl=42.0, commission=0.2,
    opened_at=datetime(2024, 7, 11, 13, 0),  # 09:00 NYT
    closed_at=datetime(2024, 7, 11, 17, 0),
    batch_id=1, is_paper=True, is_test=False,
  ))
  db.commit()

  from backend.analysis.portfolio_wfo_replay import _load_replay_hybrid
  records = [
    # Backtest record — resolves via BacktestTrade
    {"dt": "2024-03-05T00:00:00", "pnl": 50.0, "asset": "B1_BTCUSDT_long",
     "symbol": "BTCUSDT", "side": "long", "run_id": 1, "batch_id": 1},
    # Live-fill record — negative run_id, NYT dt (trade_log_unified shape)
    {"dt": "2024-07-11T09:00:00", "pnl": 42.0, "asset": "B1_ETHUSDT_short",
     "symbol": "ETHUSDT", "side": "short", "run_id": -1, "batch_id": 1},
  ]
  df = _load_replay_hybrid(records, db)
  assert len(df) == 2, df
  live_row = df[df["Side"] == "Short"].iloc[0]
  assert live_row["Net Profit"] == pytest.approx(42.0)
  assert "(live)" in live_row["Exit Reason"]
