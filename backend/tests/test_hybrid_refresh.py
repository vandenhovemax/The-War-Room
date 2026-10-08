"""Tests for backend/services/hybrid_portfolio_refresh.py — Y-B refresh contract.

Replaces the Stage 8-obsolete tests (test_hybrid_rotation.py,
test_hybrid_update.py, test_hybrid_rotation_handler.py,
test_t002k_addition_path.py, test_hybrid_graded_weights.py,
test_a3_step2_merge.py) which targeted the legacy rotation-handler
gate architecture. Those files are deleted at the same time as this
file lands.

13 tests covering:
  Identity guards (6): not_found / not_hybrid / deleted / inactive /
    no_basket / no_windows_meta
  Window selection (3): no_new_window / picks newest / cursor advances
  Basket diff/flip (4): unchanged / changed / empty refused / seed used
"""
from __future__ import annotations

import json
import os
import sys
from collections import namedtuple
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Make the backend importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))  # The_War_Room root, machine-independent

from backend.db.models import (  # noqa: E402
  Base, KellyAllocation, LiveEvent, Portfolio, PortfolioAsset,
)

import backend.services.hybrid_portfolio_refresh as yb


# ── In-memory SQLite ────────────────────────────────────────────

_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _set_pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
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


# ── Fixtures ────────────────────────────────────────────────────

_StubTrade = namedtuple(
  "_StubTrade",
  ["run_id", "symbol", "side", "net_profit", "entry_time", "exit_time", "timeframe"],
)

WINDOWS_6MO = [
  {"window_num": 1, "is_start": "2024-04-01", "is_end": "2024-09-30",
   "oos_start": "2024-10-01", "oos_end": "2025-03-31"},
  {"window_num": 2, "is_start": "2024-10-01", "is_end": "2025-03-31",
   "oos_start": "2025-04-01", "oos_end": "2025-09-30"},
  {"window_num": 3, "is_start": "2025-04-01", "is_end": "2025-09-30",
   "oos_start": "2025-10-01", "oos_end": "2026-03-31"},
  {"window_num": 4, "is_start": "2025-10-01", "is_end": "2026-03-31",
   "oos_start": "2026-04-01", "oos_end": "2026-09-30"},  # future
]


def _src_cfg(**overrides):
  cfg = {
    "hybrid_mode": True,
    "hybrid_n_assets": 3,
    "strategy_name": "Test Strategy",
    "wfo_windows_meta": list(WINDOWS_6MO),
    "continuation_state": {
      "asset_buffers": {"FOO": [1, 2]},
      "asset_trade_times": {},
      "asset_last_time": {},
      "rescue_asset_buffers": {},
      "rescue_asset_trade_times": {},
      "rescue_asset_last_time": {},
    },
  }
  cfg.update(overrides)
  return cfg


def _seed_p(db, pid, *, mode="hybrid", is_active=True, is_deleted=False,
            src=None, with_basket=True):
  db.query(LiveEvent).filter(LiveEvent.portfolio_id == pid).delete()
  db.query(KellyAllocation).filter(KellyAllocation.portfolio_id == pid).delete()
  db.query(PortfolioAsset).filter(PortfolioAsset.portfolio_id == pid).delete()
  db.query(Portfolio).filter(Portfolio.id == pid).delete()
  db.commit()

  pf = Portfolio(
    id=pid, name=f"yb_p{pid}", portfolio_type="paper", mode=mode,
    is_active=is_active, is_deleted=is_deleted,
    source_strategies=json.dumps(src if src is not None else _src_cfg()),
    optimization_end_date=datetime(2025, 9, 30, tzinfo=timezone.utc),
  )
  db.add(pf)
  db.flush()

  if with_basket:
    db.add(PortfolioAsset(
      portfolio_id=pid, symbol="BTCUSDT", trade_side="Long",
      strategy_name="Test Strategy", raw_name="BTCUSDT_Long",
      batch_id=46, is_active=True,
    ))
    db.add(PortfolioAsset(
      portfolio_id=pid, symbol="ETHUSDT", trade_side="Short",
      strategy_name="Test Strategy", raw_name="ETHUSDT_Short",
      batch_id=48, is_active=True,
    ))
  db.commit()
  return pid


def _baseline(db, pid):
  pf = db.query(Portfolio).filter(Portfolio.id == pid).first()
  return {
    "ev": db.query(LiveEvent).filter(LiveEvent.portfolio_id == pid).count(),
    "ka": db.query(KellyAllocation).filter(KellyAllocation.portfolio_id == pid).count(),
    "pa": db.query(PortfolioAsset).filter(PortfolioAsset.portfolio_id == pid).count(),
    "active": db.query(PortfolioAsset).filter(
      PortfolioAsset.portfolio_id == pid, PortfolioAsset.is_active.is_(True)
    ).count(),
    "src": pf.source_strategies if pf else None,
    "refreshed": pf.last_refreshed_at if pf else None,
  }


def _assert_no_writes(db, pid, base):
  pf = db.query(Portfolio).filter(Portfolio.id == pid).first()
  assert db.query(LiveEvent).filter(LiveEvent.portfolio_id == pid).count() == base["ev"]
  assert db.query(KellyAllocation).filter(KellyAllocation.portfolio_id == pid).count() == base["ka"]
  assert db.query(PortfolioAsset).filter(PortfolioAsset.portfolio_id == pid).count() == base["pa"]
  assert db.query(PortfolioAsset).filter(
    PortfolioAsset.portfolio_id == pid, PortfolioAsset.is_active.is_(True)
  ).count() == base["active"]
  if pf:
    assert pf.source_strategies == base["src"], "source_strategies rewritten"
    assert pf.last_refreshed_at == base["refreshed"], "last_refreshed_at bumped"


class _Spy:
  def __init__(self, result=None):
    self.calls = []
    self.result = result or _default_step2(["B46_BTCUSDT_Long", "B48_ETHUSDT_Short", "B46_SOLUSDT_Long"])

  def __call__(self, *, step1_result, all_trades, run_batch_map, config, db):
    self.calls.append({
      "step1_result": step1_result,
      "trade_count": len(all_trades),
      "config": dict(config),
    })
    return self.result


def _default_step2(basket):
  return {
    "windows_processed": 1, "windows_total": 1,
    "latest_asset_scores": {a: 1.0 for a in basket},
    "latest_portfolio_at_end": list(basket),
    "asset_frequency": {a: {"selected": 1, "eligible": 1, "pct": 100.0} for a in basket},
    "selection_changes": [{"date": "2026-04-20", "added": list(basket), "removed": []}],
    "selected_records": [
      {"dt": f"2026-03-{15+i:02d}", "pnl": 100.0, "asset": basket[0] if basket else "X",
       "symbol": "BTCUSDT", "side": "Long", "timeframe": "30",
       "run_id": 1, "batch_id": 46}
      for i in range(3)
    ] if basket else [],
    "summary": {"net_profit": 300.0},
    "per_window": [{"final_state_snapshot": {"asset_buffers": {"X": [1]}}}],
    "aggregate_monthly_matrix": {"matrix": {"2026": {"3": 300.0}}},
    "aggregate_equity_curve": [], "aggregate_breakdown": [],
    "cumulative_metrics": {},
  }


def _stub_load(db, **kw):
  return (
    [_StubTrade(1, "BTCUSDT", "Long", 10.0,
                datetime(2025, 11, 1, tzinfo=timezone.utc),
                datetime(2025, 11, 2, tzinfo=timezone.utc), "30")],
    {1: 46, 2: 48},
    {46: {"timeframe": "30", "group": "A"}, 48: {"timeframe": "15", "group": "B"}},
  )


# ============================================================
# 1-6. Identity guards (no DB writes)
# ============================================================

def test_not_found(db):
  base = _baseline(db, 200001)
  res = yb.refresh_portfolio(200001, db=db, run_step2_fn=_Spy(), load_trades_fn=_stub_load)
  assert res["ok"] is False and res["reason"] == "not_found"
  _assert_no_writes(db, 200001, base)


def test_not_hybrid(db):
  pid = _seed_p(db, 200002, mode="non_hybrid")
  base = _baseline(db, pid)
  res = yb.refresh_portfolio(pid, db=db, run_step2_fn=_Spy(), load_trades_fn=_stub_load)
  assert res["ok"] is False and res["reason"] == "not_hybrid"
  _assert_no_writes(db, pid, base)


def test_deleted(db):
  pid = _seed_p(db, 200003, is_deleted=True)
  base = _baseline(db, pid)
  res = yb.refresh_portfolio(pid, db=db, run_step2_fn=_Spy(), load_trades_fn=_stub_load)
  assert res["ok"] is False and res["reason"] == "deleted"
  _assert_no_writes(db, pid, base)


def test_inactive(db):
  pid = _seed_p(db, 200004, is_active=False)
  base = _baseline(db, pid)
  res = yb.refresh_portfolio(pid, db=db, run_step2_fn=_Spy(), load_trades_fn=_stub_load)
  assert res["ok"] is False and res["reason"] == "inactive"
  _assert_no_writes(db, pid, base)


def test_no_basket(db):
  pid = _seed_p(db, 200005, with_basket=False)
  base = _baseline(db, pid)
  res = yb.refresh_portfolio(pid, db=db, run_step2_fn=_Spy(), load_trades_fn=_stub_load)
  assert res["ok"] is False and res["reason"] == "no_basket_batches"
  _assert_no_writes(db, pid, base)


def test_no_windows_meta(db):
  pid = _seed_p(db, 200006, src=_src_cfg(wfo_windows_meta=[]))
  base = _baseline(db, pid)
  res = yb.refresh_portfolio(pid, db=db, run_step2_fn=_Spy(), load_trades_fn=_stub_load)
  assert res["ok"] is False and res["reason"] == "no_windows_meta"
  _assert_no_writes(db, pid, base)


# ============================================================
# 7-9. Window selection logic
# ============================================================

def test_re_grades_trailing_window_each_tick(db):
  """C1 fix: cursor is informational only. Y-B re-grades the live
  trailing window on every tick, even when cursor matches the saved
  oos_end. The trailing window's oos_end extends to today each call.
  """
  src = _src_cfg()
  # Cursor at last canonical window's oos_end - the OLD code would skip
  src["refresh_step2_result"] = {"last_oos_end_walked": "2026-03-31"}
  pid = _seed_p(db, 200007, src=src)
  spy = _Spy()
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  # New contract: tick STILL grades, OOS extended to today
  assert res["ok"] is True
  assert res["reason"] != "no_new_window", (
    "C1 regression: cursor should not block trailing-window re-grade"
  )
  assert len(spy.calls) == 1, "run_step2 should be called every tick"
  walked_oos_end = spy.calls[0]["step1_result"]["windows"][0]["oos_end"]
  assert walked_oos_end == "2026-04-24", (
    f"trailing window oos_end should extend to today, got {walked_oos_end}"
  )


def test_no_anchor_when_truly_no_eligible(db):
  """Anchored-walk contract: when EVERY saved window has oos_end > today
  and there are no save-time records (brand-new portfolio whose only
  saved window is in the future), there is no committed boundary to
  anchor on — benign no-op, no writes.
  """
  # All windows in the future relative to today
  src = _src_cfg(wfo_windows_meta=[
    {"window_num": 1, "is_start": "2026-05-01", "is_end": "2026-10-31",
     "oos_start": "2026-11-01", "oos_end": "2027-04-30"},
  ])
  pid = _seed_p(db, 200007, src=src)
  base = _baseline(db, pid)
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=_Spy(), load_trades_fn=_stub_load,
  )
  assert res["ok"] is True and res["reason"].startswith("no_walk_anchor")
  _assert_no_writes(db, pid, base)


def test_picks_newest_completed_window(db):
  """First-ever refresh: picks the latest canonical window AND extends
  its oos_end to today so the live trailing period is captured.
  """
  pid = _seed_p(db, 200008)
  spy = _Spy()
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  assert res["ok"] is True
  # Window 4 has oos_end=2026-09-30 (future, skipped). Window 3 has
  # oos_end=2026-03-31 (latest completed) — Y-B picks w3. Per Q1=(A) +
  # Path-A-1 (ATLAS audit), because today (2026-04-24) is past
  # saved.oos_end (2026-03-31), the trailing window EXTENDS forward:
  # IS absorbs the prior OOS (is_end → 2026-03-31), is_start stays
  # ANCHORED at saved value (2025-04-01, anchor preservation), and OOS
  # becomes [2026-04-01 → today].
  assert res["walked_window"]["window_num"] == 3
  assert res["walked_window"]["oos_end"] == "2026-04-24"   # extended to today
  assert res["walked_window"]["oos_start"] == "2026-04-01" # prior_ose + 1
  assert res["walked_window"]["is_end"] == "2026-03-31"    # prior_ose
  assert res["walked_window"]["is_start"] == "2025-04-01"  # anchored (saved)
  assert len(spy.calls) == 1
  assert len(spy.calls[0]["step1_result"]["windows"]) == 1


def test_cursor_advances_to_walked_oos_end(db):
  """The cursor should land on the EXTENDED oos_end (today), not the
  saved oos_end. That way tomorrow's tick correctly skips today's
  already-graded period and only grades the new day.
  """
  pid = _seed_p(db, 200009)
  spy = _Spy()
  yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  pf = db.query(Portfolio).filter(Portfolio.id == 200009).first()
  src = json.loads(pf.source_strategies)
  assert src["refresh_step2_result"]["last_oos_end_walked"] == "2026-04-24"
  assert src["refresh_step2_result"]["last_window_num_walked"] == 3


# ============================================================
# 10-13. Basket diff + flip + seed
# ============================================================

def test_basket_unchanged_persists_state_writes_no_change_event(db):
  """Step-2 returns the same 2 active assets. is_active doesn't flip,
  no Kelly row, but T-002 writes one LiveEvent of type
  'phase2_evaluated_no_change' so the audit trail covers no-op nights."""
  pid = _seed_p(db, 200010)
  spy = _Spy(result=_default_step2(["B46_BTCUSDT_Long", "B48_ETHUSDT_Short"]))
  base = _baseline(db, pid)
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  assert res["ok"] is True and res["reason"] == "basket_unchanged"
  ka = db.query(KellyAllocation).filter(KellyAllocation.portfolio_id == pid).count()
  assert ka == base["ka"], "no KellyAllocation on unchanged basket"

  # T-002: exactly one no-change event on the unchanged path.
  events = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type == "phase2_evaluated_no_change",
  ).all()
  assert len(events) == 1, "exactly one phase2_evaluated_no_change event"
  details = json.loads(events[0].details)
  assert details["basket_unchanged"] is True
  assert details["added"] == []
  assert details["removed"] == []
  assert details["recorder_version"] >= 1
  assert details["decision_inputs_hash"]
  # T-002 v2 Item 4: no-change tick stamps dispatch_outcome=success
  # inline (nothing to dispatch) so phase2_audit.dispatch_pending_stale
  # doesn't false-positive after the lookback threshold elapses.
  assert details["dispatch_outcome"] == "success"

  pf = db.query(Portfolio).filter(Portfolio.id == pid).first()
  src = json.loads(pf.source_strategies)
  assert "refresh_step2_result" in src
  assert pf.last_refreshed_at is not None


def test_basket_changed_flips_writes_kelly_writes_event(db, monkeypatch):
  """Step-2 picks a different basket. is_active flips, Kelly row written,
  LiveEvent emitted with event_type='hybrid_refresh' and the T-002
  Pydantic-locked details shape.

  No live engine in the test env -> verify_dispatch_outcome reports
  `engine_offline`, which is the correct verdict (engine_provider=None
  is distinct from a real silent dispatch failure)."""
  monkeypatch.setattr(
    "backend.services.phase2_recorder._default_engine_provider",
    lambda: None,
  )
  pid = _seed_p(db, 200011)
  new_basket = ["B46_BTCUSDT_Long", "B46_SOLUSDT_Long"]
  spy = _Spy(result=_default_step2(new_basket))
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  assert res["ok"] is True and res["reason"] is None
  assert "B46_SOLUSDT_Long" in res["added"]
  assert "B48_ETHUSDT_Short" in res["removed"]

  ka = db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == pid,
    KellyAllocation.is_current == True,  # noqa: E712
  ).all()
  assert len(ka) == 1
  assert ka[0].trigger == "step2_refresh"

  events = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type == "hybrid_refresh",
  ).all()
  assert len(events) == 1

  # T-002: Pydantic-locked details shape.
  details = json.loads(events[0].details)
  assert details["basket_unchanged"] is False
  assert "B46_SOLUSDT_Long" in details["added"]
  assert "B48_ETHUSDT_Short" in details["removed"]
  assert details["chosen_basket"] == sorted(new_basket)
  assert details["recorder_version"] >= 1
  assert details["decision_inputs_hash"]
  # engine_provider patched to None -> verify_dispatch_outcome verdict
  assert details["dispatch_outcome"] == "engine_offline"
  assert details["kelly_allocation_id"] == ka[0].id
  # Backward-compat extras propagated through `extra=`
  assert details["weights_count"] == len(new_basket)
  assert details["walked_window"]["window_num"] is not None
  assert isinstance(details["used_seed"], bool)


def test_basket_changed_dispatch_verifies_success_when_stub_cache_populated(db, monkeypatch):
  """End-to-end success path: refresh_portfolio writes the recorder
  row, dispatches to a stub engine that pre-populates
  `_kelly_alloc_cache[pid]` with non-empty weights, and
  verify_dispatch_outcome flips dispatch_outcome to 'success'."""
  class _StubEng:
    def __init__(self, pid: int):
      self._kelly_alloc_cache = {
        pid: {
          "weights": {"BTCUSDT_long": 0.6, "SOLUSDT_long": 0.4},
          "n_assets": 2,
          "computed_at": "2026-04-25T03:00:00+00:00",
        }
      }

  pid = _seed_p(db, 200111)
  monkeypatch.setattr(
    "backend.services.phase2_recorder._default_engine_provider",
    lambda: _StubEng(pid),
  )
  new_basket = ["B46_BTCUSDT_Long", "B46_SOLUSDT_Long"]
  spy = _Spy(result=_default_step2(new_basket))
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  assert res["ok"] is True

  ev = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type == "hybrid_refresh",
  ).one()
  details = json.loads(ev.details)
  assert details["dispatch_outcome"] == "success"


def test_basket_changed_dispatch_verifies_failed_when_stub_cache_empty(db, monkeypatch):
  """End-to-end failure path: stub engine resolves but its cache stays
  empty. verify_dispatch_outcome polls until timeout then flips
  dispatch_outcome to 'failed' — distinct from 'engine_offline'."""
  class _StubEng:
    def __init__(self):
      self._kelly_alloc_cache: dict[int, dict] = {}

  pid = _seed_p(db, 200112)
  stub = _StubEng()
  monkeypatch.setattr(
    "backend.services.phase2_recorder._default_engine_provider",
    lambda: stub,
  )
  # Speed up the verify loop so the test isn't slow.
  import backend.services.hybrid_portfolio_refresh as hpr
  orig_verify = hpr.verify_dispatch_outcome

  def _fast_verify(**kwargs):
    kwargs.setdefault("timeout_s", 0.05)
    kwargs.setdefault("poll_interval_s", 0.01)
    return orig_verify(**kwargs)

  monkeypatch.setattr(hpr, "verify_dispatch_outcome", _fast_verify)

  new_basket = ["B46_BTCUSDT_Long", "B46_SOLUSDT_Long"]
  spy = _Spy(result=_default_step2(new_basket))
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  assert res["ok"] is True

  ev = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type == "hybrid_refresh",
  ).one()
  details = json.loads(ev.details)
  assert details["dispatch_outcome"] == "failed"


def test_step2_empty_basket_refuses_to_flip(db):
  """Defensive: empty basket -> no flip, preserve existing is_active."""
  pid = _seed_p(db, 200012)
  base = _baseline(db, pid)
  empty = _default_step2([])
  empty["asset_frequency"] = {}
  empty["latest_asset_scores"] = {}
  empty["latest_portfolio_at_end"] = []
  spy = _Spy(result=empty)
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  assert res["ok"] is False and res["reason"] == "step2_picked_empty"
  _assert_no_writes(db, pid, base)


def test_no_seed_state_even_when_continuation_present(db):
  """Anchored-walk contract (2026-06-10, FLAW 2 fix): the walk must run
  UNSEEDED even when a continuation_state blob exists in the saved
  config. Seeding yesterday's end-state and re-walking the same window
  double-counted every buffer and leaked future trades into past
  decision points — the root cause of nightly selection drift."""
  pid = _seed_p(db, 200013)
  spy = _Spy()
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  assert res["ok"] is True
  assert res["used_seed"] is False
  assert "_seed_state" not in spy.calls[0]["config"]


# ============================================================
# T-002: SAVEPOINT failure isolation (ATLAS ask #4)
# ============================================================

def test_recorder_failure_does_not_block_trading_state_commit(db, monkeypatch):
  """If the phase-2 recorder raises mid-write, the trading state (Kelly,
  PortfolioAsset.is_active flips, refresh_step2_result) MUST still
  commit. The SAVEPOINT rolls back only the recorder rows."""
  pid = _seed_p(db, 200014)
  new_basket = ["B46_BTCUSDT_Long", "B46_SOLUSDT_Long"]
  spy = _Spy(result=_default_step2(new_basket))

  # Force the recorder to raise by patching the underlying Pydantic
  # serialiser. This simulates any mid-write recorder bug.
  from backend.services import phase2_recorder

  def _boom(*args, **kwargs):  # noqa: ANN001
    raise RuntimeError("simulated recorder failure")

  monkeypatch.setattr(
    phase2_recorder.Phase2EventDetails, "model_dump_json", _boom,
  )

  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )

  # Trading state still commits.
  assert res["ok"] is True and res["reason"] is None
  ka = db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == pid,
    KellyAllocation.is_current == True,  # noqa: E712
  ).all()
  assert len(ka) == 1, "Kelly row must commit even if recorder fails"
  assert ka[0].trigger == "step2_refresh"

  # is_active flipped to the new basket
  active_keys = {
    f"B{pa.batch_id}_{pa.symbol}_{pa.trade_side}"
    for pa in db.query(PortfolioAsset).filter(
      PortfolioAsset.portfolio_id == pid,
      PortfolioAsset.is_active == True,  # noqa: E712
    ).all()
  }
  assert active_keys == set(new_basket)

  # refresh_step2_result advanced
  pf = db.query(Portfolio).filter(Portfolio.id == pid).first()
  src = json.loads(pf.source_strategies)
  assert src.get("refresh_step2_result", {}).get("last_oos_end_walked")

  # No phase-2 event row written (SAVEPOINT rolled it back).
  events = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type.in_(["hybrid_refresh", "phase2_evaluated_no_change"]),
  ).count()
  assert events == 0, "SAVEPOINT must roll back recorder writes"


def test_recorder_failure_isolates_no_change_path(db, monkeypatch):
  """Same SAVEPOINT contract on the basket-unchanged early-return path:
  recorder failure cannot prevent last_refreshed_at from advancing."""
  pid = _seed_p(db, 200015)
  spy = _Spy(result=_default_step2(["B46_BTCUSDT_Long", "B48_ETHUSDT_Short"]))

  from backend.services import phase2_recorder

  def _boom(*args, **kwargs):  # noqa: ANN001
    raise RuntimeError("simulated recorder failure (no-change path)")

  monkeypatch.setattr(
    phase2_recorder.Phase2EventDetails, "model_dump_json", _boom,
  )

  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  assert res["ok"] is True and res["reason"] == "basket_unchanged"

  pf = db.query(Portfolio).filter(Portfolio.id == pid).first()
  assert pf.last_refreshed_at is not None

  events = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type.in_(["hybrid_refresh", "phase2_evaluated_no_change"]),
  ).count()
  assert events == 0


# ============================================================
# T-002 v2 Item 4 — No-change branch dispatch_outcome=success
# (data fix on the write path + defense-in-depth audit guard)
# ============================================================

def test_v2_no_change_event_marks_dispatch_outcome_success(db):
  """A no-change tick stamps the just-written event with
  dispatch_outcome='success'. No subsequent verify_dispatch_outcome
  call is needed (basket didn't change → nothing to dispatch)."""
  from backend.services.phase2_audit import run_phase2_audit

  pid = _seed_p(db, 200020)
  spy = _Spy(result=_default_step2(["B46_BTCUSDT_Long", "B48_ETHUSDT_Short"]))
  res = yb.refresh_portfolio(
    pid, db=db, simulate_today=date(2026, 4, 24),
    run_step2_fn=spy, load_trades_fn=_stub_load,
  )
  assert res["ok"] is True and res["reason"] == "basket_unchanged"

  ev = db.query(LiveEvent).filter(
    LiveEvent.portfolio_id == pid,
    LiveEvent.event_type == "phase2_evaluated_no_change",
  ).one()
  details = json.loads(ev.details)
  assert details["dispatch_outcome"] == "success", (
    "v2 Item 4 contract: no-change events ship dispatch_outcome=success "
    "inline (nothing to dispatch)"
  )

  # Even with pending_threshold_minutes=0 (any age qualifies as 'stale'),
  # the no-change success event must NOT surface as a defect.
  audit = run_phase2_audit(
    db,
    lookback_days=7,
    pending_threshold_minutes=0,
    silence_days_threshold=10**6,  # never flag silence in this test
  )
  pending_stale = [d for d in audit["defects"]
                   if d["category"] == "dispatch_pending_stale"
                   and d["live_event_id"] == ev.id]
  assert pending_stale == [], (
    "no-change event with dispatch_outcome=success must not trigger "
    "dispatch_pending_stale"
  )


def test_v2_audit_guard_skips_legacy_pending_no_change_events(db):
  """Belt-and-suspenders: a legacy pre-fix no-change row that still
  carries dispatch_outcome='pending' must NOT be flagged as
  dispatch_pending_stale. The data fix above prevents future rows
  from landing in that state, but the audit also guards explicitly
  to keep historical rows quiet."""
  from datetime import timedelta

  from backend.services.phase2_audit import run_phase2_audit

  pid = _seed_p(db, 200021, with_basket=False)
  # Inject a synthetic legacy pre-fix no-change row stuck at 'pending'.
  legacy_age = datetime.now(timezone.utc) - timedelta(hours=6)
  legacy = LiveEvent(
    portfolio_id=pid,
    event_type="phase2_evaluated_no_change",
    symbol="",
    details=json.dumps({
      "decided_at_iso": legacy_age.isoformat(),
      "trigger": "nightly_cron",
      "dispatch_outcome": "pending",  # legacy: never updated
      "basket_unchanged": True,
      "added": [],
      "removed": [],
      "recorder_version": 1,
    }),
    created_at=legacy_age,
  )
  db.add(legacy)
  db.commit()

  audit = run_phase2_audit(
    db,
    lookback_days=7,
    pending_threshold_minutes=0,
    silence_days_threshold=10**6,
  )
  pending_stale = [d for d in audit["defects"]
                   if d["category"] == "dispatch_pending_stale"]
  assert pending_stale == [], (
    "audit guard must skip event_type=phase2_evaluated_no_change rows "
    "from the dispatch_pending_stale check, even when they carry the "
    "legacy 'pending' sentinel"
  )


# ── T-002 Delta 7: Path A IS-extension fix ─────────────────────────

def test_path_a_extends_is_when_today_past_saved_oos_end():
  """Path-A-1 (ATLAS audit): when `today > saved.oos_end` AND saved is in
  the canonical chain, the trailing window EXTENDS forward — IS absorbs
  the prior OOS (`is_end = prior_ose`), `is_start` is ANCHORED at saved,
  `oos_start = prior_ose + 1d`, `oos_end = today`. Q1=(A) + Path-A-1
  in COLLAB_T-002.
  """
  saved = {
    "window_num": 3,
    "is_start": "2025-04-01", "is_end": "2025-09-30",
    "oos_start": "2025-10-01", "oos_end": "2026-03-31",
  }
  prior = {
    "window_num": 2,
    "is_start": "2024-10-01", "is_end": "2025-03-31",
    "oos_start": "2025-04-01", "oos_end": "2025-09-30",
  }
  out = yb._build_live_trailing_window(
    windows_meta=[prior, saved],
    saved_target=saved,
    today=date(2026, 4, 25),
  )
  assert out["oos_end"] == "2026-04-25"
  assert out["oos_start"] == "2026-04-01"   # prior_ose + 1d
  assert out["is_end"] == "2026-03-31"      # prior_ose (= saved.oos_end)
  assert out["is_start"] == "2025-04-01"    # ANCHORED at saved (Path-A-1)
  assert out["window_num"] == 3             # unchanged


def test_path_a_unchanged_when_today_inside_saved_oos():
  """Back-compat: when `today <= saved.oos_end`, return saved bounds verbatim."""
  saved = {
    "window_num": 5,
    "is_start": "2025-04-01", "is_end": "2026-03-01",
    "oos_start": "2026-03-02", "oos_end": "2026-04-30",
  }
  out = yb._build_live_trailing_window(
    windows_meta=[saved],
    saved_target=saved,
    today=date(2026, 4, 25),
  )
  # No-op: every field identical to saved
  assert out == saved


def test_path_a_is_start_anchor_preserved_with_18_month_is():
  """Regression guard (mandatory per ATLAS audit): a non-12-month IS
  shape would have surfaced the original is_offset_days arithmetic bug
  (where today's IS got recomputed from new_oos_start - is_offset_months,
  drifting away from the saved anchor). Pin to anchor-preservation
  under Path-A-1 so any future "roll" reintroduction is caught.
  """
  saved = {
    "window_num": 5,
    "is_start": "2024-10-01", "is_end": "2026-03-01",   # 18-month IS
    "oos_start": "2026-03-02", "oos_end": "2026-03-31",
  }
  out = yb._build_live_trailing_window(
    windows_meta=[saved],
    saved_target=saved,
    today=date(2026, 4, 25),
  )
  assert out["oos_end"] == "2026-04-25"
  assert out["oos_start"] == "2026-04-01"
  assert out["is_end"] == "2026-03-31"
  # ANCHOR: would diverge under any "roll IS forward by N months" rule.
  assert out["is_start"] == "2024-10-01"
