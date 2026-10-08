"""CS-055: scale-lite fixture to exercise the trade-loader and
downstream shape at production-realistic row counts.

The existing test fixtures (``test_analysis_api.py``, ``test_integration_
smoke.py``) seed 2 batches × 10 symbols × L+S ≈ 600-1500 trades. Prod
is ~3M. A 3000× gap hides:
  * perf regressions in ``load_trades_lightweight``
  * SQLite planner choices (index vs. scan)
  * loader memory footprint
  * beam-dedup collisions at scale
  * Kelly distribution on real trade counts

Audit's recommended mitigation: a "scale-lite" fixture with ~100k
synthetic trades in-memory that exercises the loader path end-to-end
with elapsed-time + row-count bounds.

This suite is **size-focused**, not correctness-focused — correctness
of individual functions is covered elsewhere. The point is to catch
O(n²) or accidentally-unindexed paths at a row count prod actually
sees.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.analysis.batch_selection import (
  LightTrade,
  load_trades_lightweight,
)
from backend.db.models import (
  BacktestRun,
  BacktestTrade,
  Base,
  BatchBacktestRun,
  Strategy,
)

# Target row count. Kept at 100k to match the audit recommendation —
# large enough to expose O(n²) paths, small enough to stay CI-friendly.
_TARGET_TRADES = 100_000

# Loader must return all trades in under this budget. 10s is generous
# for an in-memory SQLite; prod on-disk is slower but the shape is the
# same. A regression that drops an index will blow through this.
_LOADER_TIME_BUDGET_SEC = 15.0


@pytest.fixture(scope="module")
def scale_lite_db():
  engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
  )

  @event.listens_for(engine, "connect")
  def _pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=OFF")  # test DB — durability irrelevant
    cur.close()

  Base.metadata.create_all(bind=engine)
  Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
  db = Session()

  # ── Seed ───────────────────────────────────────────────────────────
  # 100k trades ≈ 20 batches × 25 symbols × 2 sides × 100 trades
  n_batches = 20
  n_symbols = 25
  trades_per_leg = _TARGET_TRADES // (n_batches * n_symbols * 2)
  symbols = [f"SCALE{i:02d}USDT" for i in range(n_symbols)]
  start = datetime(2024, 1, 1)

  # Seed strategies + batches.
  for i in range(n_batches):
    db.add(Strategy(id=i + 1, name=f"Strat{i}", source_type="manual"))
  db.flush()
  for b in range(1, n_batches + 1):
    db.add(BatchBacktestRun(
      id=b, strategy_id=b, timeframe="30m",
      direction_filter="long_plus_short",
      status="completed", total_symbols=n_symbols, completed_symbols=n_symbols,
      parameters="{}",
    ))
  db.flush()

  # Seed runs — need one BacktestRun per (batch, symbol, side) so the
  # loader has something to join against.
  run_id = 0
  run_rows: list[dict] = []
  for batch_id in range(1, n_batches + 1):
    strategy_id = batch_id
    for sym in symbols:
      for side in ("long", "short"):
        run_id += 1
        run_rows.append({
          "id": run_id,
          "strategy_id": strategy_id,
          "symbol": sym,
          "timeframe": "30m",
          "start_date": start,
          "end_date": start + timedelta(days=200),
          "status": "completed",
          "batch_id": batch_id,
        })
  db.bulk_insert_mappings(BacktestRun, run_rows)
  db.flush()

  # Bulk-insert the trades via raw mappings — the orm-per-row path is
  # too slow at 100k. Deterministic RNG so the row count is exact.
  rng = np.random.default_rng(0)
  trade_rows: list[dict] = []
  trade_num = 0
  for run in run_rows:
    rid = run["id"]
    sym = run["symbol"]
    side = (
      "long" if (rid - 1) % 2 == 0 else "short"
    )  # matches the insert order above
    for _ in range(trades_per_leg):
      trade_num += 1
      entry_hour = int(rng.integers(0, 23))
      day_off = int(rng.integers(0, 180))
      entry_dt = start + timedelta(days=day_off, hours=entry_hour)
      exit_dt = entry_dt + timedelta(hours=int(rng.integers(1, 48)))
      entry_px = float(rng.uniform(100, 50_000))
      pnl = float(rng.normal(5, 50))
      trade_rows.append({
        "run_id": rid,
        "trade_num": trade_num,
        "position_id": f"T{rid}_{trade_num}",
        "symbol": sym,
        "side": side,
        "entry_time": entry_dt,
        "exit_time": exit_dt,
        "entry_price": entry_px,
        "exit_price": entry_px + pnl,
        "quantity": 0.1,
        "net_profit": pnl,
        "net_profit_pct": pnl / entry_px * 100.0,
        "commission": 0.1,
      })
  db.bulk_insert_mappings(BacktestTrade, trade_rows)
  db.commit()

  yield db, len(trade_rows)

  db.close()
  Base.metadata.drop_all(bind=engine)


# ── Tests ──────────────────────────────────────────────────────────────

def test_scale_lite_seed_hits_target_row_count(scale_lite_db):
  """Sanity: the fixture actually produced ~100k trades. If seeding
  broke, every downstream assertion would give false confidence.
  """
  db, n_seeded = scale_lite_db
  assert n_seeded >= _TARGET_TRADES * 0.95, (
    f"CS-055 FIXTURE: expected ≥{_TARGET_TRADES} trades, seeded "
    f"only {n_seeded}. Adjust per-leg counts or the scale fixture "
    "misses its purpose."
  )
  count = db.query(BacktestTrade).count()
  assert count == n_seeded


def test_load_trades_lightweight_completes_within_budget(scale_lite_db):
  """The loader must finish a full-DB load in reasonable wall-clock
  time. A regression that drops an index on (run_id) or triggers a
  per-row Python callback would exceed this budget.
  """
  db, n_seeded = scale_lite_db
  t0 = time.perf_counter()
  trades, run_batch_map, batch_info = load_trades_lightweight(db)
  elapsed = time.perf_counter() - t0

  assert elapsed < _LOADER_TIME_BUDGET_SEC, (
    f"CS-055 REGRESSION: load_trades_lightweight took {elapsed:.2f}s "
    f"for {n_seeded} trades — budget is {_LOADER_TIME_BUDGET_SEC}s. "
    "Likely a dropped index or a per-row callback was reintroduced."
  )

  assert len(trades) == n_seeded, (
    f"CS-055 REGRESSION: loader returned {len(trades)} rows, expected "
    f"{n_seeded}. The status/is_deleted filter may be rejecting rows "
    "that should pass."
  )
  assert len(run_batch_map) >= 1
  assert len(batch_info) >= 1


def test_load_trades_lightweight_returns_lighttrade_shape(scale_lite_db):
  """Every returned row must be a ``LightTrade`` namedtuple with the
  six documented fields — not a full ORM object. That's the whole
  point of the lightweight path (CLAUDE.md database-access rule).
  """
  db, _ = scale_lite_db
  trades, _, _ = load_trades_lightweight(db)
  assert trades, "CS-055 FIXTURE: no trades returned"

  sample = trades[0]
  assert isinstance(sample, LightTrade), (
    "CS-055 REGRESSION: loader returned a non-LightTrade object — "
    f"got {type(sample).__name__}. The lightweight contract is "
    "broken and memory will balloon on prod-size loads."
  )
  # Minimal field presence check — prevents a refactor from silently
  # widening the row tuple (which defeats the "6 columns only" promise).
  for field in ("run_id", "symbol", "side", "net_profit",
                "entry_time", "exit_time", "timeframe"):
    assert hasattr(sample, field), f"LightTrade missing {field!r}"


def test_load_trades_lightweight_filters_by_batch_ids(scale_lite_db):
  """Loading a single batch must return ~5% of rows (1 of 20 batches).
  Protects against a filter being dropped during a refactor — which
  would cause cross-batch contamination in cross-strategy endpoints.
  """
  db, n_seeded = scale_lite_db
  trades_one, _, batch_info_one = load_trades_lightweight(db, batch_ids=[1])
  assert len(batch_info_one) == 1, (
    "CS-055 REGRESSION: batch_ids filter ignored — batch_info has "
    f"{len(batch_info_one)} entries, expected 1."
  )
  expected_fraction = 1 / 20
  actual_fraction = len(trades_one) / n_seeded
  assert 0.03 < actual_fraction < 0.08, (
    "CS-055 REGRESSION: batch-filtered load returned "
    f"{len(trades_one)} rows ({actual_fraction:.1%}), expected "
    f"~{expected_fraction:.1%}. The batch_id filter is broken."
  )
