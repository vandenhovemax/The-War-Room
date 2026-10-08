"""Engine coordinator: activated_at preservation across re-activations.

Regression test for the bug where every call to
`engine_coordinator.activate_portfolio` was unconditionally setting
`Portfolio.activated_at = datetime.now()`. Auto-resume after a backend
restart calls activate_portfolio for every is_active portfolio, so
every restart wiped the original activation date. paper_refresh.run_refresh
filters trades by `exit_time >= activated_at` for paper/real portfolios,
so users saw zero historical trades after a restart even though the data
was sitting in `backtest_trades`.

Fix: only set `activated_at` when it is None. Subsequent activations
preserve the original timestamp.

These tests use an in-memory SQLite DB and mock the strategy_executor +
ws_manager so they isolate the activate_portfolio body without spinning
up the full live engine.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import Base, Portfolio
from backend.exchange.engine_coordinator import EngineCoordinator


@pytest.fixture
def db_session_factory():
  """In-memory SQLite with all tables created."""
  engine = create_engine("sqlite:///:memory:")
  Base.metadata.create_all(engine)
  return sessionmaker(bind=engine, expire_on_commit=False)


def _build_coord(db_factory, *, paper_trading: bool = False) -> EngineCoordinator:
  """Build an EngineCoordinator with mocked settings + a real db_factory.
  Mocks strategy_executor + ws_manager so activate_portfolio's tail can run."""
  fake_settings = MagicMock()
  fake_settings.paper_trading = paper_trading
  with patch("backend.exchange.engine_coordinator.get_settings", return_value=fake_settings):
    coord = EngineCoordinator(db_factory=db_factory)
  coord._running = True
  coord._settings = fake_settings  # belt-and-suspenders
  # Mocked dependencies
  coord._strategy_executor = MagicMock()
  coord._strategy_executor.activate_portfolio.return_value = True
  coord._strategy_executor._active = {}
  coord._ws_manager = MagicMock()
  return coord


def _make_portfolio(
  session: Session, *,
  portfolio_type: str = "real",
  activated_at: datetime | None = None,
) -> Portfolio:
  p = Portfolio(
    name="test-portfolio",
    portfolio_type=portfolio_type,
    mode="non_hybrid",
    is_active=False,
    is_deleted=False,
    activated_at=activated_at,
    source_strategies="{}",
  )
  session.add(p)
  session.commit()
  return p


def test_activated_at_set_on_first_activation_when_none(db_session_factory):
  """First activation: activated_at was None → set to now()."""
  s = db_session_factory()
  p = _make_portfolio(s, portfolio_type="real", activated_at=None)
  pid = p.id
  s.close()

  coord = _build_coord(db_session_factory, paper_trading=False)
  before = datetime.now(timezone.utc)
  result = asyncio.run(coord.activate_portfolio(pid))
  after = datetime.now(timezone.utc)
  assert result is True

  s2 = db_session_factory()
  refreshed = s2.get(Portfolio, pid)
  assert refreshed.activated_at is not None
  ts = refreshed.activated_at
  if ts.tzinfo is None:
    ts = ts.replace(tzinfo=timezone.utc)
  assert before <= ts <= after, (
    f"first activation should set activated_at within [now-before, now-after]; "
    f"got {ts}"
  )


def test_activated_at_preserved_on_reactivation(db_session_factory):
  """REGRESSION: re-activation must NOT overwrite existing activated_at.

  This is the fix that lets paper_refresh continue to show trades since
  the user's original activation date even after a backend restart."""
  original = datetime(2026, 4, 25, 19, 13, 49, tzinfo=timezone.utc)

  s = db_session_factory()
  p = _make_portfolio(s, portfolio_type="real", activated_at=original)
  pid = p.id
  s.close()

  coord = _build_coord(db_session_factory, paper_trading=False)
  result = asyncio.run(coord.activate_portfolio(pid))
  assert result is True

  s2 = db_session_factory()
  refreshed = s2.get(Portfolio, pid)
  ts = refreshed.activated_at
  if ts is not None and ts.tzinfo is None:
    ts = ts.replace(tzinfo=timezone.utc)
  assert ts == original, (
    f"re-activation must preserve existing activated_at; "
    f"got {ts}, expected {original}"
  )


def test_is_active_still_set_true_on_activation(db_session_factory):
  """Sanity: regardless of activated_at branch, is_active must always
  flip to True. Otherwise subsequent flow breaks."""
  s = db_session_factory()
  p = _make_portfolio(s, portfolio_type="real", activated_at=None)
  pid = p.id
  s.close()

  coord = _build_coord(db_session_factory, paper_trading=False)
  asyncio.run(coord.activate_portfolio(pid))

  s2 = db_session_factory()
  refreshed = s2.get(Portfolio, pid)
  assert refreshed.is_active is True


def test_activated_at_preserved_for_paper_portfolio_on_paper_engine(db_session_factory):
  """Same fix applies on the paper backend (paper_trading=True)."""
  original = datetime(2026, 4, 27, 23, 17, 14, tzinfo=timezone.utc)
  s = db_session_factory()
  p = _make_portfolio(s, portfolio_type="paper", activated_at=original)
  pid = p.id
  s.close()

  coord = _build_coord(db_session_factory, paper_trading=True)
  asyncio.run(coord.activate_portfolio(pid))

  s2 = db_session_factory()
  refreshed = s2.get(Portfolio, pid)
  ts = refreshed.activated_at
  if ts is not None and ts.tzinfo is None:
    ts = ts.replace(tzinfo=timezone.utc)
  assert ts == original


def test_activation_guard_still_blocks_mode_mismatch(db_session_factory):
  """Sanity: the Apr 23 activation guard remains intact — paper portfolio
  on a live engine is still refused (and activated_at is NOT touched)."""
  original = datetime(2026, 4, 25, 19, 13, 49, tzinfo=timezone.utc)
  s = db_session_factory()
  p = _make_portfolio(s, portfolio_type="paper", activated_at=original)
  pid = p.id
  s.close()

  coord = _build_coord(db_session_factory, paper_trading=False)
  result = asyncio.run(coord.activate_portfolio(pid))
  assert result is False, "mode-mismatched activation must return False"

  s2 = db_session_factory()
  refreshed = s2.get(Portfolio, pid)
  ts = refreshed.activated_at
  if ts is not None and ts.tzinfo is None:
    ts = ts.replace(tzinfo=timezone.utc)
  assert ts == original, "refused activation must not touch activated_at"
