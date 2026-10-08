"""CS-033: ORM ``__table_args__`` must mirror the raw-SQL composite indexes.

Before this fix, the raw SQL in ``database.py`` created 9 composite indexes at
runtime, but the ORM models had no matching ``Index(...)`` declarations. Fresh
in-memory SQLite fixtures built via ``Base.metadata.create_all()`` — which most
tests and tooling use — therefore never had these indexes, so queries that
depend on them ran a full scan. Perf-sensitive tests silently drifted from
production characteristics.

This test pins the contract: every index declared at ``database.py:326-340``
must exist in ``Base.metadata`` so ``create_all()`` creates it.
"""
from __future__ import annotations

from sqlalchemy import create_engine, inspect

from backend.db.models import Base


# Mirror of database.py:327-340 — if this diverges, CS-033 regressed.
EXPECTED = {
  "backtest_trades": [("idx_bt_trades_run", ["run_id"])],
  "backtest_runs": [("idx_bt_runs_batch", ["batch_id", "status"])],
  "positions": [("idx_positions_portfolio_open", ["portfolio_id", "is_open"])],
  "orders": [("idx_orders_portfolio_status", ["portfolio_id", "status"])],
  "trades": [("idx_trades_portfolio", ["portfolio_id"])],
  "daily_pnl": [("idx_daily_pnl_portfolio_date", ["portfolio_id", "date"])],
  "portfolio_assets": [("idx_portfolio_assets_portfolio", ["portfolio_id"])],
  "wfo_runs": [("idx_wfo_runs_batch", ["batch_id"])],
  "signals": [("idx_signals_symbol_status", ["symbol", "status"])],
}


def _fresh_in_memory_schema():
  engine = create_engine("sqlite:///:memory:")
  Base.metadata.create_all(engine)
  return engine


def test_all_expected_indexes_created_by_metadata():
  """create_all() on a fresh engine must materialize every CS-033 index."""
  engine = _fresh_in_memory_schema()
  insp = inspect(engine)
  missing: list[str] = []
  wrong_cols: list[str] = []
  for table, idx_specs in EXPECTED.items():
    got = {i["name"]: i["column_names"] for i in insp.get_indexes(table)}
    for name, cols in idx_specs:
      if name not in got:
        missing.append(f"{table}.{name}")
      elif got[name] != cols:
        wrong_cols.append(f"{table}.{name}: expected {cols}, got {got[name]}")
  assert not missing, f"CS-033: indexes missing from ORM metadata: {missing}"
  assert not wrong_cols, f"CS-033: indexes with wrong columns: {wrong_cols}"


def test_every_raw_sql_index_has_an_orm_mirror():
  """Regression: if someone adds a new raw-SQL index to database.py,
  they must also add the ORM declaration here. We read the raw-SQL source
  and assert every name appears in EXPECTED.
  """
  import re
  from pathlib import Path

  here = Path(__file__).resolve()
  for parent in [here, *here.parents]:
    if (parent / "backend").is_dir():
      root = parent
      break
  else:
    raise RuntimeError("Cannot locate repo root")

  src = (root / "backend/db/database.py").read_text(encoding="utf-8")
  # Scan CREATE INDEX IF NOT EXISTS <name> ON <table>(...)
  pattern = re.compile(
    r"CREATE INDEX IF NOT EXISTS\s+(\w+)\s+ON\s+(\w+)", re.IGNORECASE
  )
  declared = {(m.group(2), m.group(1)) for m in pattern.finditer(src)}
  expected_pairs = {
    (table, name) for table, specs in EXPECTED.items() for name, _ in specs
  }
  # Every raw-SQL index must have a matching ORM mirror entry
  missing_mirror = declared - expected_pairs
  assert not missing_mirror, (
    "CS-033: raw-SQL indexes in database.py have no ORM mirror in EXPECTED. "
    f"Add Index(...) to the corresponding model: {missing_mirror}"
  )
