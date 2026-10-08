"""Regression tests for the phantom-dupe guard in
`backend/backtester/trade_storage.py:store_trades`.

VISION + ATLAS post-T-003 review identified that the guard at
`trade_storage.py:89-120` was never committed (`git log -S "Phantom-dupe"`
returned zero commits) — every dupe currently in the DB pre-dates the
guard. This file pins the guard's contract:

  1. PHANTOM blocked: same `run_id` + `(entry_time, side, entry_price)`
     coordinates with a DIFFERENT `position_id` MUST be skipped. A
     WARNING is emitted so production logs surface the event.
  2. PYRAMIDING ALLOWED: same coordinates with DIFFERENT `position_id`
     AND DIFFERENT `net_profit` (i.e. legit max_pyramiding=3) MUST pass
     when both fills land in the SAME `store_trades` call (no DB-side
     pre-existing row to compare against).
  3. SPLIT-EXIT ALLOWED: same coordinates with the SAME `position_id`
     MUST pass through (legitimate TP1+TP2 second exit row).
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.backtester.models import OrderFill
from backend.backtester.trade_storage import store_trades
from backend.db.models import BacktestRun, BacktestTrade, Base


# ── In-memory SQLite (StaticPool = single shared connection) ────────────────

_ENGINE = create_engine(
  "sqlite:///:memory:",
  connect_args={"check_same_thread": False},
  poolclass=StaticPool,
)


@event.listens_for(_ENGINE, "connect")
def _set_pragma(dbapi_conn, _):
  cur = dbapi_conn.cursor()
  cur.execute("PRAGMA journal_mode=WAL")
  # Foreign keys disabled for this fixture: store_trades is exercised in
  # isolation without seeding upstream Strategy/BatchBacktestRun rows.
  cur.execute("PRAGMA foreign_keys=OFF")
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
    s.commit()
    yield s
  finally:
    s.rollback()
    s.close()


# ── Helpers ─────────────────────────────────────────────────────────────────

# These coordinates mirror a real phantom case from the production DB:
# BLURUSDT short 2026-02-01, entry_price=0.023158414999999998 (bit-identical
# across the phantom pair, ruling out float drift as the cause).
_T = pd.Timestamp("2026-04-15 10:30:00")
_EP = 0.023158414999999998


def _seed_run(db, *, run_id: int = 1) -> BacktestRun:
  run = BacktestRun(
    id=run_id,
    strategy_id=1,
    symbol="BLURUSDT",
    timeframe="30",
    start_date=datetime(2026, 1, 1),
    end_date=datetime(2026, 4, 15),
    parameters="{}",
    status="completed",
    is_deleted=False,
  )
  db.add(run)
  db.commit()
  return run


def _seed_pid_a_trade(db, run_id: int, *, net_profit: float = -6.6) -> None:
  """Insert one stored trade with position_id='T_A'. Subsequent
  store_trades calls will see this in the guard's existing_keys.
  """
  db.add(BacktestTrade(
    run_id=run_id,
    trade_num=1,
    position_id="T_A",
    symbol="BLURUSDT",
    side="short",
    entry_time=_T.to_pydatetime(),
    exit_time=(_T + pd.Timedelta(hours=4)).to_pydatetime(),
    entry_price=_EP,
    exit_price=_EP * 1.001,
    quantity=10.0,
    net_profit=net_profit,
    net_profit_pct=-0.066,
    sl_price=None,
    tp_price=None,
    exit_reason="TP2_T_A",
    sl_past_be_time=None,
    mae=0.0,
    mfe=0.0,
    commission=0.5,
  ))
  db.commit()


def _make_exit_fill(
  *,
  position_id: str,
  pnl: float = -6.6,
  entry_time: pd.Timestamp = _T,
  exit_time: pd.Timestamp | None = None,
  entry_price: float = _EP,
  exit_price: float | None = None,
  side: str = "short",
  qty: float = 10.0,
  label: str | None = None,
) -> OrderFill:
  """Build an OrderFill in the shape store_trades expects."""
  return OrderFill(
    bar_index=42,
    timestamp=exit_time or (entry_time + pd.Timedelta(hours=4)),
    side=side,
    action="exit",
    price=exit_price or (entry_price * 1.001),
    qty=qty,
    label=label or f"TP2_{position_id}",
    pnl=pnl,
    position_id=position_id,
    entry_price=entry_price,
    entry_time=entry_time,
  )


# ── Test 1 — Phantom blocked ─────────────────────────────────────────────────

def test_store_trades_blocks_phantom_dupes_on_run_with_existing_rows(db, caplog):
  """Guard MUST block a re-emitted trade on the SAME run_id when
  `(entry_time, side, entry_price)` collide and `position_id` differs.
  CLAUDE.md trading-critical contract.
  """
  _seed_run(db, run_id=1)
  _seed_pid_a_trade(db, run_id=1)

  # Phantom: same coordinates as the stored T_A row, different position_id.
  incoming = [_make_exit_fill(position_id="T_B")]

  with caplog.at_level("WARNING", logger="backend.backtester.trade_storage"):
    store_trades(db, run_id=1, symbol="BLURUSDT", trades=incoming, initial_capital=10000.0)
    db.commit()

  rows = db.query(BacktestTrade).filter_by(run_id=1).all()
  assert len(rows) == 1, f"expected 1 row (the seeded T_A only), got {len(rows)}"
  assert rows[0].position_id == "T_A", f"expected T_A to survive, got {rows[0].position_id}"

  warning_msgs = [r.message for r in caplog.records if "phantom-dupe" in r.message]
  assert warning_msgs, "expected a 'phantom-dupe' WARNING in logs, none found"


# ── Test 2 — Pyramiding pair allowed (different net_profit) ──────────────────

def test_store_trades_allows_pyramiding_pair_on_same_bar(db):
  """Two legitimate positions on the same bar at the same fill price
  but DIFFERENT `net_profit` (size differential from max_pyramiding=3)
  MUST both persist. Guard must not widen into eating real pyramid
  trades.

  The guard inspects DB-side existing rows only — fills within the
  same `incoming` list are inserted as a unit by bulk_insert_mappings.
  """
  _seed_run(db, run_id=2)

  incoming = [
    _make_exit_fill(position_id="T_PY_1", pnl=-100.0, qty=10.0),
    _make_exit_fill(position_id="T_PY_2", pnl=-150.0, qty=15.0),
  ]
  store_trades(db, run_id=2, symbol="BLURUSDT", trades=incoming, initial_capital=10000.0)
  db.commit()

  rows = db.query(BacktestTrade).filter_by(run_id=2).all()
  assert len(rows) == 2, f"expected 2 pyramid rows, got {len(rows)}"
  pids = {r.position_id for r in rows}
  assert pids == {"T_PY_1", "T_PY_2"}, f"expected both pyramid pids, got {pids}"

  pnls = sorted(r.net_profit for r in rows)
  assert pnls == [-150.0, -100.0], f"expected [-150, -100], got {pnls}"


# ── Test 3 — Split-exit (same position_id) allowed ──────────────────────────

def test_store_trades_allows_split_exit_same_position_id(db):
  """Same coordinates with the SAME `position_id` MUST pass — that's
  how TP1 + TP2 partial exits are represented (one position, two
  exit rows).
  """
  _seed_run(db, run_id=3)
  _seed_pid_a_trade(db, run_id=3)

  incoming = [_make_exit_fill(position_id="T_A", pnl=-3.3, label="TP2_T_A_part2")]
  store_trades(db, run_id=3, symbol="BLURUSDT", trades=incoming, initial_capital=10000.0)
  db.commit()

  rows = db.query(BacktestTrade).filter_by(run_id=3).all()
  assert len(rows) == 2, f"expected 2 split-exit rows under T_A, got {len(rows)}"
  assert all(r.position_id == "T_A" for r in rows), \
    f"expected all rows under T_A, got {sorted(r.position_id for r in rows)}"
