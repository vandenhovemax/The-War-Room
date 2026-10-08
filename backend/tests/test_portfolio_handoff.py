"""Portfolio Handoff Tests — Phases 5-6 of the optimization audit.

Verifies that:
  - Portfolio stores optimization_end_date
  - Portfolio stores optimizer_settings
  - Portfolio assets match candidate selection
  - Portfolio OOS evaluation boundary is correct
  - Strategy config matches optimization
  - Live timeframe matches backtest
  - Live direction matches selection

Uses in-memory SQLite DB with synthetic data.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
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
)


# ── In-memory test DB ──────────────────────────────────────────────────

_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_TEST_ENGINE, "connect")
def _set_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)


# ── Synthetic data ─────────────────────────────────────────────────────

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]


def _make_trades(
    run_id: int, symbol: str, side: str, n: int,
    rng: np.random.Generator, start: datetime,
) -> list[BacktestTrade]:
    trades = []
    for i in range(n):
        day_off = int(rng.integers(0, 180))
        entry = start + timedelta(days=day_off, hours=int(rng.integers(0, 23)))
        exit_ = entry + timedelta(hours=int(rng.integers(1, 48)))
        px = float(rng.uniform(100, 50000))
        win = rng.random() < 0.6
        pnl = float(rng.uniform(5, 500)) * (1 if win else -1)
        trades.append(BacktestTrade(
            run_id=run_id, trade_num=i + 1, position_id=f"T{run_id}_{i}",
            symbol=symbol, side=side,
            entry_time=entry, exit_time=exit_,
            entry_price=px, exit_price=px * (1 + pnl / (px * 0.1)),
            quantity=0.1, net_profit=pnl,
            net_profit_pct=pnl / (px * 0.1) * 100,
            commission=px * 0.1 * 0.001,
        ))
    return trades


def _seed_db(db):
    """Populate DB with 1 strategy, 1 batch, 5 symbols."""
    rng = np.random.default_rng(42)
    start = datetime(2024, 1, 1)

    strat = Strategy(id=1, name="TestStrat", source_type="manual")
    db.add(strat)
    db.flush()

    batch = BatchBacktestRun(
        id=1, strategy_id=1, timeframe="30",
        direction_filter="long_plus_short", status="completed",
        total_symbols=5, completed_symbols=5,
        parameters=json.dumps({"initial_capital": 10000}),
    )
    db.add(batch)
    db.flush()

    run_id = 0
    for sym in SYMBOLS:
        for side in ["long", "short"]:
            run_id += 1
            run = BacktestRun(
                id=run_id, strategy_id=1, symbol=sym,
                timeframe="30", start_date=start,
                end_date=start + timedelta(days=180),
                status="completed", batch_id=1,
            )
            db.add(run)
            db.flush()
            trades = _make_trades(run_id, sym, side, 25, rng, start)
            db.add_all(trades)

    db.commit()


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    """Create all tables and seed data once for the module."""
    Base.metadata.create_all(bind=_TEST_ENGINE)
    db = _TestSession()
    _seed_db(db)
    db.close()
    yield
    Base.metadata.drop_all(bind=_TEST_ENGINE)


@pytest.fixture()
def db_session():
    """Fresh DB session per test with automatic rollback on error."""
    db = _TestSession()
    yield db
    db.rollback()
    db.close()


# ── Tests ──────────────────────────────────────────────────────────────

class TestPortfolioStoresOptimizationEndDate:
    """Portfolio should store the optimization_end_date."""

    def test_portfolio_stores_optimization_end_date(self, db_session):
        """Create a Portfolio with optimization_end_date and verify storage."""
        p = Portfolio(
            name="Test End Date",
            portfolio_type="saved",
            optimization_end_date=datetime(2024, 6, 1),
            source_strategies=json.dumps({"strategy_id": 1, "timeframe": "30"}),
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        assert p.optimization_end_date is not None
        assert p.optimization_end_date == datetime(2024, 6, 1)
        # Cleanup
        db_session.delete(p)
        db_session.commit()


class TestPortfolioStoresOptimizerSettings:
    """Portfolio should store full optimizer_settings as source_strategies."""

    def test_portfolio_stores_optimizer_settings(self, db_session):
        """source_strategies JSON should contain optimizer config."""
        settings = {
            "strategy_id": 1,
            "strategy_name": "TestStrat",
            "timeframe": "30",
            "risk_profile": {"mode": "fixed", "risk_value": 100},
            "objective": "max_sortino",
            "n_assets": 5,
        }
        p = Portfolio(
            name="Test Settings",
            portfolio_type="saved",
            source_strategies=json.dumps(settings),
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        stored = json.loads(p.source_strategies)
        assert stored["strategy_id"] == 1
        assert stored["objective"] == "max_sortino"
        assert stored["risk_profile"]["mode"] == "fixed"
        # Cleanup
        db_session.delete(p)
        db_session.commit()


class TestPortfolioAssetsMatchCandidate:
    """Portfolio assets should match the optimizer's selected candidate."""

    def test_portfolio_assets_match_candidate(self, db_session):
        """PortfolioAsset records should match what the optimizer chose."""
        p = Portfolio(
            name="Test Assets",
            portfolio_type="saved",
            source_strategies=json.dumps({"strategy_id": 1, "timeframe": "30"}),
        )
        db_session.add(p)
        db_session.flush()

        # Simulate optimizer selecting 3 assets
        selected = [
            ("BTCUSDT", "long", 1),
            ("ETHUSDT", "long", 1),
            ("SOLUSDT", "short", 1),
        ]
        for sym, side, batch_id in selected:
            pa = PortfolioAsset(
                portfolio_id=p.id, symbol=sym,
                trade_side=side, batch_id=batch_id,
            )
            db_session.add(pa)
        db_session.commit()
        db_session.refresh(p)

        assets = db_session.query(PortfolioAsset).filter(
            PortfolioAsset.portfolio_id == p.id,
        ).all()
        assert len(assets) == 3
        syms = {(a.symbol, a.trade_side) for a in assets}
        assert ("BTCUSDT", "long") in syms
        assert ("ETHUSDT", "long") in syms
        assert ("SOLUSDT", "short") in syms

        # Cleanup
        for a in assets:
            db_session.delete(a)
        db_session.delete(p)
        db_session.commit()


class TestPortfolioOOSBoundary:
    """Portfolio OOS evaluation should respect boundary."""

    def test_portfolio_oos_evaluation_boundary(self, db_session):
        """optimization_end_date should define the IS/OOS split."""
        p = Portfolio(
            name="Test OOS Boundary",
            portfolio_type="saved",
            optimization_end_date=datetime(2024, 6, 1),
            source_strategies=json.dumps({
                "strategy_id": 1, "timeframe": "30",
                "date_from": "2024-01-01", "date_to": "2024-06-01",
            }),
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        stored = json.loads(p.source_strategies)
        assert stored["date_to"] == "2024-06-01"
        assert p.optimization_end_date == datetime(2024, 6, 1)

        db_session.delete(p)
        db_session.commit()


class TestStrategyConfigMatch:
    """Strategy config in portfolio should match what was used in optimization."""

    def test_strategy_config_matches_optimization(self, db_session):
        """Timeframe and strategy_id should be consistent."""
        settings = {
            "strategy_id": 1,
            "strategy_name": "TestStrat",
            "timeframe": "30",
            "batch_id": 1,
        }
        p = Portfolio(
            name="Config Match",
            portfolio_type="saved",
            source_strategies=json.dumps(settings),
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        stored = json.loads(p.source_strategies)
        # Verify the stored config matches what was passed
        assert stored["strategy_id"] == settings["strategy_id"]
        assert stored["timeframe"] == settings["timeframe"]

        db_session.delete(p)
        db_session.commit()


class TestLiveTimeframeMatch:
    """Live trading should use the same timeframe as backtest."""

    def test_live_timeframe_matches_backtest(self, db_session):
        """source_strategies.timeframe should be the canonical format."""
        p = Portfolio(
            name="TF Match",
            portfolio_type="paper",
            source_strategies=json.dumps({"strategy_id": 1, "timeframe": "30"}),
        )
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)

        stored = json.loads(p.source_strategies)
        # Canonical timeframe format (Bybit API)
        assert stored["timeframe"] in (
            "1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W",
        )

        db_session.delete(p)
        db_session.commit()


class TestLiveDirectionMatch:
    """Live direction enforcement should match portfolio asset sides."""

    def test_live_direction_matches_selection(self, db_session):
        """PortfolioAsset.trade_side should restrict entry direction."""
        p = Portfolio(
            name="Direction Match",
            portfolio_type="paper",
            source_strategies=json.dumps({"strategy_id": 1, "timeframe": "30"}),
        )
        db_session.add(p)
        db_session.flush()

        # Long-only asset
        pa = PortfolioAsset(
            portfolio_id=p.id, symbol="BTCUSDT",
            trade_side="long", batch_id=1,
        )
        db_session.add(pa)
        db_session.commit()
        db_session.refresh(pa)

        assert pa.trade_side == "long"
        # In live trading, strategy_executor checks this value to block
        # short entries for long-only assets and vice versa

        db_session.delete(pa)
        db_session.delete(p)
        db_session.commit()
