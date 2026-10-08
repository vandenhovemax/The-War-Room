"""
Targeted test: load_trades_lightweight must NOT include trades from
non-completed BacktestRuns (status='filtered', 'error', 'pending', etc.)
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
    Base,
    BacktestRun,
    BacktestTrade,
    BatchBacktestRun,
    BatchGroup,
    Strategy,
)


_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture
def db_session():
    Base.metadata.create_all(_TEST_ENGINE)
    Session = sessionmaker(bind=_TEST_ENGINE)
    session = Session()

    strat = Strategy(id=1, name="TestStrat")
    session.add(strat)
    session.flush()

    grp = BatchGroup(name="TestGroup", pnl_multiplier=1.0)
    session.add(grp)
    session.flush()

    batch = BatchBacktestRun(
        id=1, strategy_id=1, timeframe="30m", direction_filter="long_plus_short",
        status="completed", total_symbols=2, completed_symbols=2,
        is_deleted=False, group_id=grp.id,
        parameters=json.dumps({"initial_capital": 10000}),
    )
    session.add(batch)
    session.flush()

    start = datetime(2024, 1, 1)

    # Run 1: completed — 3 trades
    run1 = BacktestRun(
        id=1, strategy_id=1, symbol="BTCUSDT", timeframe="30m",
        start_date=start, end_date=start + timedelta(days=200),
        status="completed", batch_id=1,
    )
    session.add(run1)
    session.flush()
    for i, pnl in enumerate([100.0, -50.0, 75.0]):
        session.add(BacktestTrade(
            run_id=1, trade_num=i+1, symbol="BTCUSDT", side="long",
            net_profit=pnl, position_id=f"T1_{i}",
            entry_time=start + timedelta(days=i),
            exit_time=start + timedelta(days=i, hours=4),
            entry_price=50000.0, exit_price=50000.0 + pnl,
            quantity=1.0,
        ))

    # Run 2: filtered — 2 trades (should be EXCLUDED)
    run2 = BacktestRun(
        id=2, strategy_id=1, symbol="GHOSTUSDT", timeframe="30m",
        start_date=start, end_date=start + timedelta(days=200),
        status="filtered", batch_id=1,
    )
    session.add(run2)
    session.flush()
    for i, pnl in enumerate([200.0, -30.0]):
        session.add(BacktestTrade(
            run_id=2, trade_num=i+1, symbol="GHOSTUSDT", side="short",
            net_profit=pnl, position_id=f"T2_{i}",
            entry_time=start + timedelta(days=i + 30),
            exit_time=start + timedelta(days=i + 30, hours=4),
            entry_price=1.0, exit_price=1.0 - pnl/100,
            quantity=1.0,
        ))

    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(_TEST_ENGINE)


def test_lightweight_excludes_filtered_runs(db_session):
    """load_trades_lightweight must only return trades from completed runs."""
    from backend.analysis.batch_selection import load_trades_lightweight

    trades, rbm, batch_info = load_trades_lightweight(db_session)

    assert len(trades) == 3, (
        f"Expected 3 trades (completed only), got {len(trades)}. "
        f"Filtered-run trades are leaking through!"
    )

    symbols = set(t.symbol for t in trades)
    assert "GHOSTUSDT" not in symbols, (
        "GHOSTUSDT from filtered run should NOT appear in results"
    )
