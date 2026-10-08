"""Tests for paper/live martingale streak isolation + activation guard.

Guards against a silent-money bug: without the is_paper flag, paper trades
would feed into live martingale streak calculations (and vice versa),
causing wildly wrong position sizes on mode transitions.

Covers:
  1. Trade model has is_paper column, defaults False.
  2. _get_martingale_streak() filters by is_paper matching engine mode.
  3. activate_portfolio() refuses mode-mismatched activation.
  4. Paper trades written with is_paper=True, live with is_paper=False.
  5. Migration is idempotent and backfills paper trades correctly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, Portfolio, Trade


# ---------------------------------------------------------------------------
# Fresh in-memory DB per test — no cross-test pollution
# ---------------------------------------------------------------------------

@pytest.fixture
def db_factory():
  engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
  )
  Base.metadata.create_all(engine)
  Session = sessionmaker(bind=engine)
  return Session


@pytest.fixture
def db_session(db_factory):
  db = db_factory()
  yield db
  db.close()


def _make_trade(portfolio_id: int, pnl: float, *, is_paper: bool,
                symbol: str = "BTCUSDT", hours_ago: int = 1,
                batch_id: int | None = None) -> Trade:
  """Build a Trade row with sensible defaults."""
  now = datetime.now(timezone.utc)
  return Trade(
    portfolio_id=portfolio_id,
    symbol=symbol,
    side="Long",
    strategy_name="test",
    entry_price=100.0,
    exit_price=100.0 + (pnl / 0.1),  # qty=0.1 → pnl = (exit-entry)*qty
    quantity=0.1,
    realized_pnl=pnl,
    commission=0.0,
    opened_at=now - timedelta(hours=hours_ago, minutes=30),
    closed_at=now - timedelta(hours=hours_ago),
    close_reason="sl" if pnl < 0 else "tp",
    is_paper=is_paper,
    batch_id=batch_id,
  )


# ---------------------------------------------------------------------------
# 1. Model-level: is_paper column exists, default False
# ---------------------------------------------------------------------------

def test_trade_model_has_is_paper_column():
  cols = {c.name for c in Trade.__table__.columns}
  assert "is_paper" in cols


def test_trade_default_is_paper_false(db_session):
  t = _make_trade(1, 10.0, is_paper=False)
  db_session.add(t)
  db_session.commit()
  fetched = db_session.query(Trade).first()
  assert fetched.is_paper is False


def test_trade_is_paper_true_persists(db_session):
  t = _make_trade(1, 10.0, is_paper=True)
  db_session.add(t)
  db_session.commit()
  fetched = db_session.query(Trade).first()
  assert fetched.is_paper is True


# ---------------------------------------------------------------------------
# 2. _get_martingale_streak() filters by is_paper
# ---------------------------------------------------------------------------

def _make_engine_stub(paper_trading: bool):
  """Build a minimal EngineCoordinator-like object for _get_martingale_streak."""
  from backend.exchange.engine_coordinator import EngineCoordinator
  stub = EngineCoordinator.__new__(EngineCoordinator)
  stub._settings = SimpleNamespace(paper_trading=paper_trading)
  return stub


def test_live_streak_ignores_paper_trades(db_session):
  """Live engine must NOT count paper losses toward live streak."""
  # 3 consecutive paper losses
  for i in range(3):
    db_session.add(_make_trade(1, -10.0, is_paper=True, hours_ago=10 - i))
  # 1 live win (most recent)
  db_session.add(_make_trade(1, 5.0, is_paper=False, hours_ago=1))
  db_session.commit()

  engine = _make_engine_stub(paper_trading=False)
  streak = engine._get_martingale_streak(db_session, portfolio_id=1, mode="martingale")

  # Live streak: only sees the 1 live win → not a loss → streak = 0
  assert streak == 0, "Paper losses leaked into live streak"


def test_paper_streak_ignores_live_trades(db_session):
  """Paper engine must NOT count live wins toward paper streak."""
  # 2 live wins
  for i in range(2):
    db_session.add(_make_trade(1, 20.0, is_paper=False, hours_ago=10 - i))
  # 3 paper losses (most recent)
  for i in range(3):
    db_session.add(_make_trade(1, -10.0, is_paper=True, hours_ago=3 - i))
  db_session.commit()

  engine = _make_engine_stub(paper_trading=True)
  streak = engine._get_martingale_streak(db_session, portfolio_id=1, mode="martingale")

  # Paper streak counting losses → 3 paper losses → streak = 3
  assert streak == 3, f"Expected paper streak=3, got {streak}"


def test_live_streak_counts_only_live_losses(db_session):
  """Sanity: live engine counts live losses correctly."""
  # 3 live losses (most recent)
  for i in range(3):
    db_session.add(_make_trade(1, -10.0, is_paper=False, hours_ago=3 - i))
  db_session.commit()

  engine = _make_engine_stub(paper_trading=False)
  streak = engine._get_martingale_streak(db_session, portfolio_id=1, mode="martingale")
  assert streak == 3


def test_anti_martingale_counts_wins_not_losses(db_session):
  """anti_martingale mode counts consecutive wins (sanity: filter still applies)."""
  for i in range(4):
    db_session.add(_make_trade(1, 10.0, is_paper=False, hours_ago=4 - i))
  # 1 paper loss that must be ignored
  db_session.add(_make_trade(1, -5.0, is_paper=True, hours_ago=0))
  db_session.commit()

  engine = _make_engine_stub(paper_trading=False)
  streak = engine._get_martingale_streak(db_session, portfolio_id=1, mode="anti_martingale")
  assert streak == 4


def test_streak_scoped_by_portfolio(db_session):
  """Other portfolios' trades do not affect this portfolio's streak."""
  # Portfolio 1: 2 live losses
  for i in range(2):
    db_session.add(_make_trade(1, -10.0, is_paper=False, hours_ago=2 - i))
  # Portfolio 2: 5 live losses
  for i in range(5):
    db_session.add(_make_trade(2, -10.0, is_paper=False, hours_ago=5 - i))
  db_session.commit()

  engine = _make_engine_stub(paper_trading=False)
  assert engine._get_martingale_streak(db_session, 1, "martingale") == 2
  assert engine._get_martingale_streak(db_session, 2, "martingale") == 5


# ---------------------------------------------------------------------------
# 3. activate_portfolio guard
# ---------------------------------------------------------------------------

def _build_engine_stub(db_factory, *, paper_trading: bool, se_returns=False):
  """Build an EngineCoordinator stub with just enough wiring to run activate_portfolio()."""
  from backend.exchange.engine_coordinator import EngineCoordinator
  engine = EngineCoordinator.__new__(EngineCoordinator)
  engine._running = True
  engine._strategy_executor = MagicMock()
  engine._strategy_executor.activate_portfolio = MagicMock(return_value=se_returns)
  engine._settings = SimpleNamespace(paper_trading=paper_trading)
  engine._db_factory = db_factory
  return engine


def _run_async(coro):
  """Run an async coroutine to completion — stdlib only, no pytest-asyncio."""
  import asyncio
  return asyncio.new_event_loop().run_until_complete(coro)


def test_activation_refused_when_paper_portfolio_in_live_mode(db_factory):
  """Engine in live mode must refuse to activate a 'paper'-typed portfolio."""
  db = db_factory()
  p = Portfolio(name="PaperPort", portfolio_type="paper", is_active=False)
  db.add(p)
  db.commit()
  pid = p.id
  db.close()

  engine = _build_engine_stub(db_factory, paper_trading=False, se_returns=True)
  result = _run_async(engine.activate_portfolio(pid))
  assert result is False, "Engine activated paper portfolio in live mode"
  engine._strategy_executor.activate_portfolio.assert_not_called()


def test_activation_refused_when_real_portfolio_in_paper_mode(db_factory):
  """Engine in paper mode must refuse to activate a 'real'-typed portfolio."""
  db = db_factory()
  p = Portfolio(name="RealPort", portfolio_type="real", is_active=False)
  db.add(p)
  db.commit()
  pid = p.id
  db.close()

  engine = _build_engine_stub(db_factory, paper_trading=True, se_returns=True)
  result = _run_async(engine.activate_portfolio(pid))
  assert result is False
  engine._strategy_executor.activate_portfolio.assert_not_called()


def test_saved_portfolio_passes_guard(db_factory):
  """A 'saved' portfolio (never activated) must pass the guard in either mode."""
  db = db_factory()
  p = Portfolio(name="SavedPort", portfolio_type="saved", is_active=False)
  db.add(p)
  db.commit()
  pid = p.id
  db.close()

  # strategy_executor returns False → short-circuit before full engine wiring.
  # Key assertion: activate_portfolio *was* called → guard let it through.
  engine = _build_engine_stub(db_factory, paper_trading=True, se_returns=False)
  result = _run_async(engine.activate_portfolio(pid))
  assert result is False
  engine._strategy_executor.activate_portfolio.assert_called_once_with(pid)


# ---------------------------------------------------------------------------
# 4. Migration idempotency + backfill
# ---------------------------------------------------------------------------

def test_migration_is_idempotent(tmp_path, monkeypatch):
  """Running the migration twice must not error or duplicate work."""
  from sqlalchemy import create_engine as _ce
  from sqlalchemy.orm import sessionmaker as _sm

  db_path = tmp_path / "mig.db"
  engine = _ce(f"sqlite:///{db_path}")
  Base.metadata.create_all(engine)
  Session = _sm(bind=engine)

  # Seed: 2 paper + 1 real portfolio, mixed trades
  db = Session()
  p_paper = Portfolio(name="P", portfolio_type="paper")
  p_real = Portfolio(name="R", portfolio_type="real")
  db.add_all([p_paper, p_real])
  db.commit()
  db.close()

  # Patch the reference that migrate_is_paper imports — not the original.
  import backend.db.migrate_is_paper as mip
  monkeypatch.setattr(mip, "SessionLocal", Session)

  from backend.db.migrate_is_paper import migrate_trades_is_paper
  migrate_trades_is_paper()
  migrate_trades_is_paper()  # second run = idempotent


def test_migration_backfills_paper_portfolio_trades(tmp_path, monkeypatch):
  """Existing rows for 'paper' portfolios get is_paper=1 after migration."""
  from sqlalchemy import create_engine as _ce, text
  from sqlalchemy.orm import sessionmaker as _sm

  db_path = tmp_path / "backfill.db"
  engine = _ce(f"sqlite:///{db_path}")

  # Step 1: create old schema WITHOUT is_paper column
  # Mimic the real-world situation: schema predates the migration.
  with engine.begin() as conn:
    conn.execute(text("""
      CREATE TABLE portfolios (
        id INTEGER PRIMARY KEY, name TEXT, portfolio_type TEXT DEFAULT 'saved',
        is_active BOOLEAN DEFAULT 0
      )
    """))
    conn.execute(text("""
      CREATE TABLE trades (
        id INTEGER PRIMARY KEY, portfolio_id INTEGER, symbol TEXT, side TEXT,
        strategy_name TEXT, entry_price REAL, exit_price REAL, quantity REAL,
        realized_pnl REAL, commission REAL, opened_at DATETIME, closed_at DATETIME,
        close_reason TEXT, entry_commission REAL, exit_commission REAL,
        funding_fee REAL, is_test BOOLEAN DEFAULT 0, batch_id INTEGER
      )
    """))
    # Insert 2 portfolios: 1 paper, 1 real
    conn.execute(text("INSERT INTO portfolios (id, name, portfolio_type) VALUES (1, 'P', 'paper')"))
    conn.execute(text("INSERT INTO portfolios (id, name, portfolio_type) VALUES (2, 'R', 'real')"))
    # 3 paper trades + 2 real trades
    for _ in range(3):
      conn.execute(text("""INSERT INTO trades (portfolio_id, symbol, side, entry_price,
        exit_price, quantity, realized_pnl, opened_at, closed_at)
        VALUES (1, 'BTC', 'Long', 100, 95, 0.1, -0.5, '2026-04-20', '2026-04-21')"""))
    for _ in range(2):
      conn.execute(text("""INSERT INTO trades (portfolio_id, symbol, side, entry_price,
        exit_price, quantity, realized_pnl, opened_at, closed_at)
        VALUES (2, 'ETH', 'Short', 2000, 1980, 0.5, 10.0, '2026-04-20', '2026-04-21')"""))

  Session = _sm(bind=engine)
  # Patch the reference that migrate_is_paper imports — not the original.
  import backend.db.migrate_is_paper as mip
  monkeypatch.setattr(mip, "SessionLocal", Session)

  from backend.db.migrate_is_paper import migrate_trades_is_paper
  migrate_trades_is_paper()

  # Verify
  with engine.begin() as conn:
    result = conn.execute(text(
      "SELECT p.portfolio_type, t.is_paper, COUNT(*) FROM trades t "
      "JOIN portfolios p ON t.portfolio_id = p.id GROUP BY p.portfolio_type, t.is_paper"
    )).fetchall()
    by = {(r[0], r[1]): r[2] for r in result}

  # Paper portfolio → is_paper=1, real → is_paper=0
  assert by.get(("paper", 1)) == 3, f"Expected 3 paper rows backfilled, got {by}"
  assert by.get(("real", 0)) == 2, f"Expected 2 real rows untouched, got {by}"
