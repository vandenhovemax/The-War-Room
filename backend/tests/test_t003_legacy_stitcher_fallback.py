"""T-003 Delta 9 regression: legacy stitcher fires only for pre-T-002 portfolios.

What this pins (Delta 5 observability guard):

  `_stitch_event_based_walkforward` is the legacy event-based stitcher. T-003
  retires it for portfolios with `step2_result.selected_records` (those route
  through `load_wfas_replay` + `load_wfas_forward_append` via
  `get_portfolio_detail_dataframe` instead). The stitcher still RUNS for
  legacy callers (no `raise`, by design — non-hybrid pre-T-002 portfolios
  still need it). The Delta 5 guard surfaces the boundary in logs:

    - Hybrid portfolio reaches stitcher  → WARNING ("T-003 regression: ...")
    - Non-hybrid portfolio reaches stitcher → INFO ("legitimate pre-T-002 path")

  These tests assert that contract. If the WARNING fires for a non-hybrid
  portfolio (false positive) or fails to fire for a hybrid portfolio (silent
  regression — the bug we're guarding against), this suite catches it.

Why this test matters:

  Delta 3's route audit confirmed no detail route reaches the stitcher for
  hybrid portfolios post-T-003 (all 5 hybrid detail routes go through the
  unified pair). So in production the WARNING should never fire — it's the
  canary. Without this test, a future refactor could re-route a hybrid path
  back through the stitcher and silently hand back stale / wrongly-stitched
  data; the WARNING would still fire in logs, but no test would block the
  merge.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.db.database as _db_module
from backend.api.routes.portfolio_analysis import _stitch_event_based_walkforward
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  LiveEvent,
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
  c = dbapi_conn.cursor()
  c.execute("PRAGMA foreign_keys=ON")
  c.close()


_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


@pytest.fixture(scope="module", autouse=True)
def _schema():
  Base.metadata.create_all(bind=_ENGINE)
  _db_module.engine = _ENGINE
  _db_module.SessionLocal = _Session
  yield
  Base.metadata.drop_all(bind=_ENGINE)


# ── Seed helpers ────────────────────────────────────────────────────

_T1 = datetime(2026, 3, 10, 14, 0)
_T2 = datetime(2026, 3, 25, 9, 0)
_CUTOFF = datetime(2026, 5, 1)


def _seed_strategy_and_batch(db) -> None:
  """Idempotent — only seeds if absent."""
  if db.query(Strategy).filter(Strategy.id == 1).first() is not None:
    return
  db.add(Strategy(id=1, name="S", source_type="manual"))
  db.add(BatchBacktestRun(
    id=1, strategy_id=1, timeframe="30",
    direction_filter="long_plus_short", status="completed",
    total_symbols=1, completed_symbols=1,
    parameters=json.dumps({"initial_capital": 10000}),
  ))
  db.flush()
  db.add(BacktestRun(
    id=101, strategy_id=1, symbol="BTCUSDT", timeframe="30",
    start_date=_T1 - timedelta(days=5),
    end_date=_T2 + timedelta(days=10),
    status="completed", batch_id=1,
  ))
  db.flush()
  db.add(BacktestTrade(
    run_id=101, trade_num=1, position_id="P1",
    symbol="BTCUSDT", side="long",
    entry_time=_T1, exit_time=_T1 + timedelta(hours=3),
    entry_price=1000.0, exit_price=990.0, quantity=0.1,
    net_profit=-200.0, net_profit_pct=-2.0, commission=0.1,
  ))
  db.commit()


def _selected_records() -> list[dict]:
  return [
    {"run_id": 101, "dt": _T1.isoformat(), "symbol": "BTCUSDT",
     "side": "long", "pnl": -200.0, "batch_id": 1},
    {"run_id": 101, "dt": _T2.isoformat(), "symbol": "BTCUSDT",
     "side": "long", "pnl": 500.0, "batch_id": 1},
  ]


def _make_portfolio(db, *, name: str, include_step2_records: bool,
                    selected_records_value: object = None) -> Portfolio:
  """Build a hybrid-mode portfolio. Step2 metadata is the only knob.

  `selected_records_value` lets the caller test edge cases:
    - `None` (default): include or omit `selected_records` per `include_step2_records`
    - explicit list/value: force that exact value (e.g. `[]` for empty)
  """
  if include_step2_records:
    step2 = {"selected_records": _selected_records()}
  elif selected_records_value is not None:
    step2 = {"selected_records": selected_records_value}
  else:
    step2 = None  # No step2_result key at all

  settings: dict = {
    "strategy_id": 1, "timeframe": "30",
    "hybrid_mode": True, "oos_months": 2,
    "phase2_selection_changes": [
      {"date": "2026-04-01", "added": [], "removed": []},
    ],
    "phase2_initial_basket": ["BTCUSDT_long"],
  }
  if step2 is not None:
    settings["step2_result"] = step2

  p = Portfolio(
    name=name,
    portfolio_type="paper",
    mode="hybrid",
    is_active=True,
    portfolio_source="wfo",
    optimization_end_date=_CUTOFF,
    source_strategies=json.dumps(settings),
  )
  db.add(p)
  db.flush()
  db.add(PortfolioAsset(
    portfolio_id=p.id, symbol="BTCUSDT", trade_side="Long",
    batch_id=1, is_active=True,
  ))
  db.commit()
  db.refresh(p)
  return p


# ── Tests ───────────────────────────────────────────────────────────

_LOGGER_NAME = "backend.api.routes.portfolio_analysis"


def _seed_and_capture(*, name: str, include_step2_records: bool,
                      selected_records_value: object = None) -> tuple[int, str]:
  """Build a portfolio in its own session and return (id, settings_json).

  Detaching the ORM instance and re-fetching in the call session avoids
  cross-session attribute lazy-loads (DetachedInstanceError).
  """
  db = _Session()
  try:
    _seed_strategy_and_batch(db)
    p = _make_portfolio(
      db, name=name,
      include_step2_records=include_step2_records,
      selected_records_value=selected_records_value,
    )
    return p.id, p.source_strategies
  finally:
    db.close()


def _run_stitcher_with_caplog(pid: int, settings_json: str, caplog) -> None:
  """Re-fetch the portfolio inside a fresh session, then call the stitcher.

  Uses the supplied `settings_json` (captured before detach) instead of
  reading `p.source_strategies` after the seeding session closed.
  """
  caplog.clear()
  caplog.set_level(logging.INFO, logger=_LOGGER_NAME)
  db = _Session()
  try:
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    settings = json.loads(settings_json)
    _stitch_event_based_walkforward(
      settings, p, batch_ids_fallback=[1], db=db,
    )
  finally:
    db.close()


def test_legacy_stitcher_fallback_only_for_pre_t002_portfolios(caplog):
  """Single test, two halves — pins the Delta 5 WARNING/INFO contract.

  Half A (hybrid → WARNING): a portfolio with `step2_result.selected_records`
  populated. The stitcher MUST NOT have been the chosen path post-T-003;
  if it fires here, log a WARNING with the regression marker.

  Half B (non-hybrid → INFO): a portfolio with no `step2_result` key. The
  stitcher firing here is the legitimate pre-T-002 fallback — INFO only.
  """
  hybrid_id, hybrid_settings = _seed_and_capture(
    name="hybrid_with_step2", include_step2_records=True,
  )
  legacy_id, legacy_settings = _seed_and_capture(
    name="legacy_no_step2", include_step2_records=False,
  )

  # ── Half A: hybrid portfolio → WARNING with regression marker ────
  _run_stitcher_with_caplog(hybrid_id, hybrid_settings, caplog)

  warnings = [r for r in caplog.records
              if r.levelno == logging.WARNING and r.name == _LOGGER_NAME]
  assert len(warnings) == 1, (
    f"hybrid portfolio should emit exactly 1 WARNING, got {len(warnings)}: "
    f"{[r.getMessage() for r in warnings]}"
  )
  msg = warnings[0].getMessage()
  assert "T-003 regression" in msg, f"WARNING missing T-003 marker: {msg}"
  assert "hybrid portfolio" in msg, f"WARNING missing 'hybrid' phrase: {msg}"
  assert f"id={hybrid_id}" in msg, f"WARNING missing portfolio id: {msg}"
  # Should NOT have logged the non-hybrid INFO too
  infos_pre_t002 = [r for r in caplog.records
                    if r.levelno == logging.INFO and r.name == _LOGGER_NAME
                    and "pre-T-002" in r.getMessage()]
  assert infos_pre_t002 == [], (
    "hybrid portfolio must not emit the pre-T-002 INFO line: "
    f"{[r.getMessage() for r in infos_pre_t002]}"
  )

  # ── Half B: non-hybrid portfolio → INFO, no WARNING ──────────────
  _run_stitcher_with_caplog(legacy_id, legacy_settings, caplog)

  infos = [r for r in caplog.records
           if r.levelno == logging.INFO and r.name == _LOGGER_NAME
           and "pre-T-002" in r.getMessage()]
  assert len(infos) == 1, (
    f"non-hybrid portfolio should emit exactly 1 INFO 'pre-T-002' line, "
    f"got {len(infos)}: {[r.getMessage() for r in infos]}"
  )
  assert f"id={legacy_id}" in infos[0].getMessage(), (
    f"INFO missing portfolio id: {infos[0].getMessage()}"
  )
  warnings_legacy = [r for r in caplog.records
                     if r.levelno == logging.WARNING and r.name == _LOGGER_NAME
                     and "T-003 regression" in r.getMessage()]
  assert warnings_legacy == [], (
    "non-hybrid portfolio must NOT emit the T-003 regression WARNING: "
    f"{[r.getMessage() for r in warnings_legacy]}"
  )


def test_empty_selected_records_treated_as_non_hybrid(caplog):
  """Edge case: `step2_result` present but `selected_records=[]` → INFO not WARNING.

  The Delta 5 guard uses `bool(...selected_records)` — an empty list is
  falsy, so the portfolio is correctly treated as non-hybrid. A
  half-initialised step2 dict (key present, list empty) is the shape some
  pre-recorder code paths produced, and routing those through
  `get_portfolio_detail_dataframe` would return an empty DF (the helper
  also gates on `bool(selected_records)`). Stitcher fallback is correct.
  """
  pid, settings_json = _seed_and_capture(
    name="empty_records",
    include_step2_records=False,
    selected_records_value=[],
  )

  _run_stitcher_with_caplog(pid, settings_json, caplog)

  warnings = [r for r in caplog.records
              if r.levelno == logging.WARNING and r.name == _LOGGER_NAME
              and "T-003 regression" in r.getMessage()]
  assert warnings == [], (
    "empty selected_records → portfolio is effectively non-hybrid → no WARNING. "
    f"Got: {[r.getMessage() for r in warnings]}"
  )
  infos = [r for r in caplog.records
           if r.levelno == logging.INFO and r.name == _LOGGER_NAME
           and "pre-T-002" in r.getMessage()]
  assert len(infos) == 1, (
    f"empty selected_records → expected 1 INFO line, got {len(infos)}"
  )


def test_warning_does_not_block_execution(caplog):
  """Delta 5 guard is observability-only — must NOT raise or short-circuit.

  Even when the regression WARNING fires, the function must return its
  normal `(DataFrame, breakdown)` tuple. A future refactor that converts
  the warning to a `raise` would break legitimate callers; this test pins
  the non-raising behaviour.
  """
  pid, settings_json = _seed_and_capture(
    name="hybrid_continues", include_step2_records=True,
  )

  caplog.clear()
  caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
  db = _Session()
  try:
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    settings = json.loads(settings_json)
    df, breakdown = _stitch_event_based_walkforward(
      settings, p, batch_ids_fallback=[1], db=db,
    )
  finally:
    db.close()

  # Normal return contract: (DataFrame, list of segment dicts)
  import pandas as pd
  assert isinstance(df, pd.DataFrame), (
    f"stitcher must return a DataFrame even when WARNING fires, got {type(df)}"
  )
  assert isinstance(breakdown, list), (
    f"stitcher must return a list breakdown, got {type(breakdown)}"
  )
  # And the WARNING was logged (sanity: this confirms we exercised the guard
  # path, not just the no-op path)
  warnings = [r for r in caplog.records
              if r.levelno == logging.WARNING and r.name == _LOGGER_NAME
              and "T-003 regression" in r.getMessage()]
  assert len(warnings) == 1, (
    "test setup precondition: hybrid path should have triggered the WARNING"
  )


def test_live_event_does_not_change_guard_decision(caplog):
  """The hybrid-vs-not decision is purely settings-driven, not event-driven.

  A non-hybrid portfolio with hybrid-shaped LiveEvent rows in its history
  (e.g. a portfolio that pre-dated T-002 but had post-T-002 events recorded)
  must still be treated as non-hybrid by the guard — `step2_result` absence
  is the only thing that matters. This pins that the Delta 5 guard is NOT
  inferring hybrid-ness from LiveEvent presence.
  """
  pid, settings_json = _seed_and_capture(
    name="legacy_with_live_events", include_step2_records=False,
  )
  # Add a hybrid-shaped LiveEvent in its own session (separate from
  # `_seed_and_capture` so we exercise the cross-session-attached path).
  db = _Session()
  try:
    db.add(LiveEvent(
      portfolio_id=pid,
      event_type="hybrid_refresh",
      symbol="BTCUSDT",
      details=json.dumps({"added": [], "removed": []}),
      created_at=datetime(2026, 5, 15, 10, 0),
    ))
    db.commit()
  finally:
    db.close()

  _run_stitcher_with_caplog(pid, settings_json, caplog)

  warnings = [r for r in caplog.records
              if r.levelno == logging.WARNING and r.name == _LOGGER_NAME
              and "T-003 regression" in r.getMessage()]
  assert warnings == [], (
    "non-hybrid portfolio with LiveEvent history must NOT emit the T-003 "
    "regression WARNING — guard is settings-driven, not event-driven. "
    f"Got: {[r.getMessage() for r in warnings]}"
  )
  infos = [r for r in caplog.records
           if r.levelno == logging.INFO and r.name == _LOGGER_NAME
           and "pre-T-002" in r.getMessage()]
  assert len(infos) == 1, (
    "non-hybrid portfolio with LiveEvent history should emit pre-T-002 INFO; "
    f"got {len(infos)} INFO lines"
  )
