"""T-002 Step 4 — `load_wfas_forward_append` as-of-time basket gating.

Regression tests for the live-portfolio UI shift bug.

Before the fix, `load_wfas_forward_append` filtered every forward-period
trade through `PortfolioAsset.is_active=True` (today's basket). When the
nightly refresh flipped `is_active`, every prior forward-period trade
was retroactively re-filtered through the new basket — so a trade that
fired in week 1 under basket {A, B} would silently disappear from the
UI when basket rotated to {B, C} in week 2.

The fix walks LiveEvent rows for the portfolio and assigns each trade
to the basket that was active at the trade's `entry_time`, so the
forward-period view stays stable across rotations.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.analysis.portfolio_wfo_replay import load_wfas_forward_append
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  LiveEvent,
  Portfolio,
  PortfolioAsset,
)
from backend.services.phase2_recorder import (
  EVENT_TYPE_HYBRID_REFRESH,
  iter_basket_segments,
  resolve_basket_at_t,
)


# ── In-memory DB fixture ──────────────────────────────────────────

_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _set_pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA journal_mode=WAL")
  cur.execute("PRAGMA foreign_keys=OFF")  # we don't seed full FK graph
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
    s.query(BacktestTrade).delete()
    s.query(BacktestRun).delete()
    s.query(BatchBacktestRun).delete()
    s.query(LiveEvent).delete()
    s.query(PortfolioAsset).delete()
    s.query(Portfolio).delete()
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


# ── Test helpers ─────────────────────────────────────────────────

def _seed_portfolio(db, pid: int = 9000) -> Portfolio:
  p = Portfolio(
    id=pid,
    name="forward_test",
    mode="hybrid",
    is_active=True,
    is_deleted=False,
    source_strategies="{}",
  )
  db.add(p)
  db.commit()
  return p


def _seed_batch_run(db, batch_id: int, run_id: int, symbol: str = "X") -> None:
  if not db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first():
    db.add(BatchBacktestRun(
      id=batch_id,
      strategy_id=1,  # FK off; just satisfy NOT NULL
      timeframe="60",
      name=f"B{batch_id}",
      group_id=None,
      status="completed",
    ))
  db.add(BacktestRun(
    id=run_id,
    strategy_id=1,
    symbol=symbol,
    timeframe="60",
    start_date=datetime(2025, 1, 1),
    end_date=datetime(2026, 1, 1),
    batch_id=batch_id,
    status="completed",
  ))
  db.commit()


def _seed_pa(db, pid: int, batch_id: int, symbol: str, side: str, is_active: bool = True) -> None:
  db.add(PortfolioAsset(
    portfolio_id=pid,
    symbol=symbol,
    trade_side=side,
    strategy_name="X",
    raw_name=f"{symbol}_{side}",
    batch_id=batch_id,
    config_name="cfg",
    timeframe="60",
    group_name="A",
    is_active=is_active,
  ))
  db.commit()


_trade_seq = {"n": 0}


def _seed_trade(db, run_id: int, symbol: str, side: str, entry_time_nyt_naive: datetime, pnl: float = 10.0) -> None:
  _trade_seq["n"] += 1
  db.add(BacktestTrade(
    run_id=run_id,
    trade_num=_trade_seq["n"],
    symbol=symbol,
    side=side,
    entry_time=entry_time_nyt_naive,
    exit_time=entry_time_nyt_naive + timedelta(hours=1),
    entry_price=100.0,
    exit_price=101.0,
    quantity=1.0,
    net_profit=pnl,
  ))
  db.commit()


def _seed_event(
  db,
  pid: int,
  prior_basket: list[str],
  chosen_basket: list[str],
  created_at_utc: datetime,
) -> None:
  details = {
    "decided_at_iso": created_at_utc.isoformat(),
    "trigger": "nightly_cron",
    "recorder_version": 1,
    "prior_basket": sorted(prior_basket),
    "chosen_basket": sorted(chosen_basket),
    "added": sorted(set(chosen_basket) - set(prior_basket)),
    "removed": sorted(set(prior_basket) - set(chosen_basket)),
    "basket_unchanged": sorted(prior_basket) == sorted(chosen_basket),
    "dispatch_outcome": "success",
  }
  db.add(LiveEvent(
    portfolio_id=pid,
    event_type=EVENT_TYPE_HYBRID_REFRESH,
    symbol="",
    details=json.dumps(details),
    created_at=created_at_utc,
  ))
  db.commit()


def _settings_with_cutoff(cutoff_iso: str) -> dict:
  """Build a `settings` dict whose `get_wfas_forward_cutoff` returns
  the date encoded in `cutoff_iso`. Uses the selected_records fallback
  path (the only branch that actually works as of 2026-04-25)."""
  return {
    "step2_result": {
      "selected_records": [
        {"dt": f"{cutoff_iso}T00:00:00", "pnl": 0.0, "symbol": "z", "side": "long"},
      ],
    },
  }


# ── T1. Bug repro — basket rotation must not retroactively hide trades

def test_old_basket_trades_remain_visible_after_rotation(db):
  """Before T-002: rotation flipped is_active, hiding old trades.
  After T-002: each trade is filtered through the basket active at its
  entry_time, so trades from before the rotation stay visible."""
  pid = 9100
  _seed_portfolio(db, pid)
  _seed_batch_run(db, batch_id=1, run_id=11)
  _seed_batch_run(db, batch_id=2, run_id=22)

  # Universe = both A (batch 1) and C (batch 2) are saved PortfolioAssets.
  # is_active reflects today's state: only C is active (post-rotation).
  _seed_pa(db, pid, batch_id=1, symbol="A", side="Long", is_active=False)
  _seed_pa(db, pid, batch_id=2, symbol="C", side="Long", is_active=True)

  # Forward window starts 2026-01-02 (cutoff 2026-01-01).
  # Trade #1 fires 2026-01-05 under basket {A}. Rotation happens
  # 2026-01-10. Trade #2 fires 2026-01-15 under basket {C}.
  _seed_trade(db, run_id=11, symbol="A", side="Long",
              entry_time_nyt_naive=datetime(2026, 1, 5, 14, 0))
  _seed_trade(db, run_id=22, symbol="C", side="Long",
              entry_time_nyt_naive=datetime(2026, 1, 15, 14, 0))

  # Recorder events: Jan-2 initial (basket={A}), Jan-10 rotation A→C.
  _seed_event(
    db, pid,
    prior_basket=["B1_A_Long"],  # day-0 state
    chosen_basket=["B1_A_Long"],
    created_at_utc=datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc),
  )
  _seed_event(
    db, pid,
    prior_basket=["B1_A_Long"],
    chosen_basket=["B2_C_Long"],
    created_at_utc=datetime(2026, 1, 10, 7, 0, tzinfo=timezone.utc),
  )

  p = db.query(Portfolio).filter(Portfolio.id == pid).one()
  df = load_wfas_forward_append(p, _settings_with_cutoff("2026-01-01"), db)
  assert not df.empty, "expected trades from BOTH baskets — got empty DF"
  assets = set(df["Asset"].tolist())
  assert "A_Long" in assets, f"old-basket trade was hidden by today's is_active flip; got {assets}"
  assert "C_Long" in assets, f"new-basket trade missing; got {assets}"


# ── T2. Trade outside any basket window is filtered out ──────────

def test_trade_under_no_active_basket_excluded(db):
  """A trade at a time when the asset was NOT in the basket must be
  excluded, even if the asset is currently is_active=True."""
  pid = 9101
  _seed_portfolio(db, pid)
  _seed_batch_run(db, batch_id=1, run_id=11)
  _seed_batch_run(db, batch_id=2, run_id=22)
  _seed_pa(db, pid, batch_id=1, symbol="A", side="Long", is_active=True)
  _seed_pa(db, pid, batch_id=2, symbol="C", side="Long", is_active=False)

  # C trade fires Jan-5 — but C wasn't in the basket until Jan-10.
  _seed_trade(db, run_id=22, symbol="C", side="Long",
              entry_time_nyt_naive=datetime(2026, 1, 5, 14, 0))
  _seed_event(
    db, pid,
    prior_basket=["B1_A_Long"],
    chosen_basket=["B1_A_Long"],
    created_at_utc=datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc),
  )
  _seed_event(
    db, pid,
    prior_basket=["B1_A_Long"],
    chosen_basket=["B1_A_Long", "B2_C_Long"],
    created_at_utc=datetime(2026, 1, 10, 7, 0, tzinfo=timezone.utc),
  )

  p = db.query(Portfolio).filter(Portfolio.id == pid).one()
  df = load_wfas_forward_append(p, _settings_with_cutoff("2026-01-01"), db)
  assert df.empty, f"trade fired when C was not yet in basket; expected exclude, got {df}"


# ── T3. Legacy fallback when no recorder events exist ───────────

def test_legacy_fallback_uses_is_active_when_no_events(db):
  """Pre-T-002 portfolios have no LiveEvent rows. The function must
  fall back to `is_active` filtering so the UI doesn't go blank."""
  pid = 9102
  _seed_portfolio(db, pid)
  _seed_batch_run(db, batch_id=1, run_id=11)
  _seed_pa(db, pid, batch_id=1, symbol="A", side="Long", is_active=True)
  _seed_trade(db, run_id=11, symbol="A", side="Long",
              entry_time_nyt_naive=datetime(2026, 1, 5, 14, 0))

  p = db.query(Portfolio).filter(Portfolio.id == pid).one()
  df = load_wfas_forward_append(p, _settings_with_cutoff("2026-01-01"), db)
  assert not df.empty
  assert "A_Long" in set(df["Asset"].tolist())


# ── T4. iter_basket_segments + resolve_basket_at_t unit tests ────

def test_iter_basket_segments_chronological(db):
  pid = 9103
  _seed_portfolio(db, pid)
  _seed_event(db, pid, [], ["B1_A_Long"],
              datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc))
  _seed_event(db, pid, ["B1_A_Long"], ["B1_A_Long", "B2_B_Long"],
              datetime(2026, 1, 10, 7, 0, tzinfo=timezone.utc))
  _seed_event(db, pid, ["B1_A_Long", "B2_B_Long"], ["B2_B_Long"],
              datetime(2026, 1, 20, 7, 0, tzinfo=timezone.utc))

  segs = iter_basket_segments(db, pid)
  # 1 sentinel + 3 events = 4 segments
  assert len(segs) == 4
  # First segment is the prior_basket of the first event = empty.
  assert segs[0][0] == datetime.min
  assert segs[0][1] == set()
  # Subsequent segments are NYT-naive.
  assert all(seg[0].tzinfo is None for seg in segs)


def test_resolve_basket_at_t_picks_correct_segment(db):
  pid = 9104
  _seed_portfolio(db, pid)
  _seed_event(db, pid, [], ["B1_A_Long"],
              datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc))
  _seed_event(db, pid, ["B1_A_Long"], ["B2_B_Long"],
              datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc))

  segs = iter_basket_segments(db, pid)
  # Before any event → sentinel (empty prior_basket).
  before = resolve_basket_at_t(segs, datetime(2025, 12, 1, 0, 0))
  assert before == set()
  # Between events → first chosen_basket {A}.
  mid = resolve_basket_at_t(segs, datetime(2026, 1, 5, 0, 0))
  assert mid == {"B1_A_Long"}
  # After second event → {B}.
  after = resolve_basket_at_t(segs, datetime(2026, 1, 15, 0, 0))
  assert after == {"B2_B_Long"}


def test_resolve_basket_at_t_drops_tz_from_aware_input(db):
  """Caller may pass a tz-aware `t` defensively; resolver must not
  crash and must produce the same result as the naive version."""
  pid = 9105
  _seed_portfolio(db, pid)
  _seed_event(db, pid, [], ["B1_A_Long"],
              datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc))
  segs = iter_basket_segments(db, pid)
  aware = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
  naive = datetime(2026, 1, 5, 0, 0)
  assert resolve_basket_at_t(segs, aware) == resolve_basket_at_t(segs, naive)


def test_iter_basket_segments_skips_other_portfolios(db):
  """Multiple portfolios share the LiveEvent table — the iterator must
  scope to the requested portfolio_id only."""
  _seed_portfolio(db, 9200)
  _seed_portfolio(db, 9201)
  _seed_event(db, 9200, [], ["B1_A_Long"],
              datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc))
  _seed_event(db, 9201, [], ["B9_X_Long"],
              datetime(2026, 1, 2, 7, 0, tzinfo=timezone.utc))
  segs_a = iter_basket_segments(db, 9200)
  segs_b = iter_basket_segments(db, 9201)
  # Each gets sentinel + 1 event = 2 segments.
  assert len(segs_a) == 2
  assert len(segs_b) == 2
  assert segs_a[1][1] == {"B1_A_Long"}
  assert segs_b[1][1] == {"B9_X_Long"}
