"""CS-044: Kelly recompute scheduler survives naive ``computed_at`` reads.

``KellyAllocation.computed_at`` is a ``Column(DateTime, ...)`` — SQLAlchemy
does not attach timezone info on SQLite (no ``timezone=True``). The column
default writes ``datetime.now(timezone.utc)`` (aware), but after a round-trip
through SQLite the value comes back as a naive datetime.

The scheduler compares ``now - computed_at`` where ``now`` is aware. Before
the fix, that raised ``TypeError: can't subtract offset-naive and
offset-aware datetimes`` and the hourly loop silently swallowed it, so no
scheduled Kelly recompute ever ran.

These tests pin the contract: the scheduler must normalize naive reads to
UTC-aware and continue without raising.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.exchange.engine_coordinator import EngineCoordinator


def _make_portfolio(pid: int, ka_config: dict) -> SimpleNamespace:
  import json

  return SimpleNamespace(
    id=pid,
    is_active=True,
    source_strategies=json.dumps({"kelly_allocation_config": ka_config}),
  )


def _build_coord(db_mock) -> EngineCoordinator:
  """Build an EngineCoordinator instance bypassing __init__ dependencies."""
  coord = EngineCoordinator.__new__(EngineCoordinator)
  coord._db_factory = lambda: db_mock
  coord._running = True
  return coord


def _stub_kelly_alloc(computed_at_value):
  return SimpleNamespace(computed_at=computed_at_value)


def _setup_db_mock(portfolios, current_allocation):
  db = MagicMock()

  def query_side_effect(model):
    q = MagicMock()
    mname = getattr(model, "__name__", str(model))
    if mname == "Portfolio":
      q.filter.return_value.all.return_value = portfolios
    elif mname == "KellyAllocation":
      q.filter.return_value.first.return_value = current_allocation
    return q

  db.query.side_effect = query_side_effect
  return db


def test_naive_computed_at_does_not_raise_tzmix_error():
  """A naive computed_at (SQLite round-trip) must NOT crash the loop."""
  naive_past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=45)
  alloc = _stub_kelly_alloc(naive_past)
  portfolios = [_make_portfolio(1, {"schedule_type": "monthly"})]
  db = _setup_db_mock(portfolios, alloc)

  coord = _build_coord(db)

  with patch.object(coord, "_run_kelly_recompute") as run_mock:
    coord._check_kelly_recomputes()
    # 45 days > 30 day monthly interval → must trigger, not TypeError
    assert run_mock.called, (
      "Naive computed_at must be coerced to UTC-aware; scheduler did not run"
    )


def test_aware_computed_at_still_works():
  """Already-aware datetimes (ideal case) must remain functional."""
  aware_past = datetime.now(timezone.utc) - timedelta(days=45)
  alloc = _stub_kelly_alloc(aware_past)
  portfolios = [_make_portfolio(2, {"schedule_type": "monthly"})]
  db = _setup_db_mock(portfolios, alloc)

  coord = _build_coord(db)

  with patch.object(coord, "_run_kelly_recompute") as run_mock:
    coord._check_kelly_recomputes()
    assert run_mock.called


def test_naive_computed_at_recent_does_not_trigger():
  """Recent naive computed_at (within interval) must NOT trigger recompute."""
  naive_recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)
  alloc = _stub_kelly_alloc(naive_recent)
  portfolios = [_make_portfolio(3, {"schedule_type": "monthly"})]
  db = _setup_db_mock(portfolios, alloc)

  coord = _build_coord(db)

  with patch.object(coord, "_run_kelly_recompute") as run_mock:
    coord._check_kelly_recomputes()
    assert not run_mock.called, (
      "Recent allocation (3d old) must not trigger monthly recompute"
    )


def test_no_current_allocation_skips_silently():
  portfolios = [_make_portfolio(4, {"schedule_type": "monthly"})]
  db = _setup_db_mock(portfolios, None)

  coord = _build_coord(db)

  with patch.object(coord, "_run_kelly_recompute") as run_mock:
    coord._check_kelly_recomputes()
    assert not run_mock.called


def test_weekly_schedule_with_naive_computed_at():
  naive_past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
  alloc = _stub_kelly_alloc(naive_past)
  portfolios = [_make_portfolio(5, {"schedule_type": "weekly"})]
  db = _setup_db_mock(portfolios, alloc)

  coord = _build_coord(db)

  with patch.object(coord, "_run_kelly_recompute") as run_mock:
    coord._check_kelly_recomputes()
    assert run_mock.called, "10d > 7d weekly interval should trigger"
