"""CS-019: cross_strategy helpers must not issue one query per batch_id.

Before this fix, ``_load_multi_batch_trades``, ``_strategy_map``,
``_asset_strategy_lookup`` and ``_build_cross_daily_pnl`` ran a fresh
``db.query(...).filter(batch_id == bid)`` inside a ``for bid in batch_ids:``
loop. For N batches that meant 2N–3N database queries. With the real DB
(~3M trades), this was the dominant cost of every cross-strategy route.

The fix collapses each helper into a bounded number of IN()-clause queries.
This test pins the contract by counting `after_cursor_execute` events while
running each helper with N=1 and N=5 batches, then asserting that the
query count is bounded — it does NOT grow linearly with N.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.api.routes.cross_strategy import (
  _asset_strategy_lookup,
  _build_cross_daily_pnl,
  _load_multi_batch_trades,
  _strategy_map,
)
from backend.db.models import (
  Base,
  BacktestRun,
  BacktestTrade,
  BatchBacktestRun,
  Strategy,
)


@pytest.fixture()
def seeded_db():
  engine = create_engine("sqlite:///:memory:")
  Base.metadata.create_all(engine)
  Session = sessionmaker(bind=engine)
  db = Session()

  # One strategy, 5 batches, 3 runs/batch, 4 trades/run.
  strat = Strategy(name="TestStrat", python_source="pass")
  db.add(strat)
  db.commit()

  batches: list[BatchBacktestRun] = []
  for i in range(5):
    b = BatchBacktestRun(
      strategy_id=strat.id,
      timeframe="30",
      status="completed",
      name=f"TestStrat — Cfg{i}",
    )
    db.add(b)
    db.commit()
    batches.append(b)

    for j in range(3):
      r = BacktestRun(
        strategy_id=strat.id,
        batch_id=b.id,
        symbol=f"SYM{j}",
        timeframe="30",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 2, 1, tzinfo=timezone.utc),
        status="completed",
      )
      db.add(r)
      db.commit()
      for k in range(4):
        t = BacktestTrade(
          run_id=r.id,
          trade_num=k,
          symbol=r.symbol,
          side="Long" if k % 2 == 0 else "Short",
          entry_time=datetime(2024, 1, 10 + k, tzinfo=timezone.utc),
          exit_time=datetime(2024, 1, 11 + k, tzinfo=timezone.utc),
          entry_price=100.0 + k,
          exit_price=101.0 + k,
          quantity=1.0,
          net_profit=1.0,
        )
        db.add(t)
      db.commit()

  yield db, engine, [b.id for b in batches]
  db.close()


def _count_queries(engine, fn):
  count = {"n": 0}

  def _handler(conn, cursor, statement, parameters, context, executemany):
    # Only count SELECTs against our tables — PRAGMA / metadata probes are noise.
    if statement.strip().upper().startswith("SELECT"):
      count["n"] += 1

  event.listen(engine, "after_cursor_execute", _handler)
  try:
    fn()
  finally:
    event.remove(engine, "after_cursor_execute", _handler)
  return count["n"]


# Query count must stay roughly constant as batch count grows.
# Allow some headroom: our bulk path issues ~3 queries; allow up to 8 to
# absorb lazy-loaded relationships (strategy for _strategy_map).
BOUND = 8


def test_load_multi_batch_trades_bounded(seeded_db):
  db, engine, batch_ids = seeded_db
  q1 = _count_queries(engine, lambda: _load_multi_batch_trades(batch_ids[:1], db))
  qN = _count_queries(engine, lambda: _load_multi_batch_trades(batch_ids, db))
  assert qN <= BOUND, f"CS-019: N+1 regression — {qN} queries for 5 batches"
  # Contract: queries should not grow linearly with N batches.
  assert qN <= q1 + 2, (
    f"CS-019: {qN} queries for 5 batches vs {q1} for 1 — grew with N"
  )


def test_strategy_map_bounded(seeded_db):
  db, engine, batch_ids = seeded_db
  q1 = _count_queries(engine, lambda: _strategy_map(batch_ids[:1], db))
  qN = _count_queries(engine, lambda: _strategy_map(batch_ids, db))
  assert qN <= BOUND, f"CS-019: N+1 regression — {qN} queries for 5 batches"
  assert qN <= q1 + 2, (
    f"CS-019: {qN} queries for 5 batches vs {q1} for 1 — grew with N"
  )


def test_asset_strategy_lookup_bounded(seeded_db):
  db, engine, batch_ids = seeded_db
  q1 = _count_queries(engine, lambda: _asset_strategy_lookup(batch_ids[:1], db))
  qN = _count_queries(engine, lambda: _asset_strategy_lookup(batch_ids, db))
  assert qN <= BOUND, f"CS-019: N+1 regression — {qN} queries for 5 batches"
  assert qN <= q1 + 2, (
    f"CS-019: {qN} queries for 5 batches vs {q1} for 1 — grew with N"
  )


def test_build_cross_daily_pnl_bounded(seeded_db):
  db, engine, batch_ids = seeded_db
  q1 = _count_queries(engine, lambda: _build_cross_daily_pnl(batch_ids[:1], db))
  qN = _count_queries(engine, lambda: _build_cross_daily_pnl(batch_ids, db))
  assert qN <= BOUND, f"CS-019: N+1 regression — {qN} queries for 5 batches"
  assert qN <= q1 + 2, (
    f"CS-019: {qN} queries for 5 batches vs {q1} for 1 — grew with N"
  )


def test_load_multi_batch_trades_preserves_counts(seeded_db):
  """Correctness: all 60 trades (5 batches × 3 runs × 4 trades) are returned."""
  db, _, batch_ids = seeded_db
  trades, rbm = _load_multi_batch_trades(batch_ids, db)
  assert len(trades) == 5 * 3 * 4
  assert len(rbm) == 5 * 3  # all 15 runs mapped


def test_build_cross_daily_pnl_preserves_batch_tagging(seeded_db):
  db, _, batch_ids = seeded_db
  daily, trades, rbm = _build_cross_daily_pnl(batch_ids, db)
  assert len(trades) == 60
  # Every column must have a batch tag ("~B<id>" suffix) so same-symbol
  # assets from different batches stay separate.
  for col in daily.columns:
    assert "~B" in col, f"CS-019: column {col!r} missing batch tag"


def test_asset_strategy_lookup_tagged_covers_all_symbols(seeded_db):
  db, _, batch_ids = seeded_db
  tagged = _asset_strategy_lookup(batch_ids, db, tagged=True)
  # 5 batches × 3 symbols = 15 unique tagged keys.
  assert len(tagged) == 15
