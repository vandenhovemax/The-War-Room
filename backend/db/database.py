"""
SQLAlchemy database engine and session management.
Uses SQLite with WAL mode for portable, zero-config persistence.

OHLCV data lives in per-timeframe DB files under data/ohlcv/.
Everything else (strategies, backtests, portfolios, trades) stays in antigravity.db.
"""
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy import text as sa_text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from backend.config import get_settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)


settings = get_settings()

# Ensure the data directory exists
db_path = settings.database_url.replace("sqlite:///", "")
Path(db_path).parent.mkdir(parents=True, exist_ok=True)

_IMMUTABLE_MODE = False  # Set True when mount can't handle WAL locking


_PRAGMAS_APPLIED = False  # Guard: apply pragmas only once in immutable/StaticPool mode


def _apply_sqlite_pragmas(dbapi_conn, _connection_record=None):
  """Apply WAL mode and performance tuning to any SQLite connection."""
  global _PRAGMAS_APPLIED
  if _IMMUTABLE_MODE and _PRAGMAS_APPLIED:
    return  # StaticPool reuses one connection — pragmas already set on main thread
  cursor = dbapi_conn.cursor()
  if not _IMMUTABLE_MODE:
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA auto_vacuum=INCREMENTAL")
    cursor.execute("PRAGMA mmap_size=268435456")
  else:
    # Immutable mode: no WAL, no writes, no mmap (safe on mounted FS)
    cursor.execute("PRAGMA mmap_size=0")
  cursor.execute("PRAGMA foreign_keys=ON")
  cursor.execute("PRAGMA cache_size=-64000")
  cursor.execute("PRAGMA temp_store=MEMORY")
  cursor.execute("PRAGMA busy_timeout=30000")
  cursor.close()
  if _IMMUTABLE_MODE:
    _PRAGMAS_APPLIED = True


def _build_engine(immutable: bool = False):
  """Create SQLAlchemy engine, optionally in immutable (read-only) mode.

  Immutable mode bypasses WAL/SHM locking — safe on mounted filesystems
  where POSIX shared memory isn't supported (e.g. Co-Work sessions).
  """
  if immutable:
    # immutable=1 tells SQLite to skip WAL/SHM entirely — read-only, no locks
    # StaticPool: ONE connection shared across all threads — immutable mode
    # can only open on the main thread (mounted FS restriction), so we open
    # once and reuse everywhere with check_same_thread=False.
    url = f"sqlite:///file:{db_path}?immutable=1"
    eng = create_engine(
      url,
      connect_args={"check_same_thread": False, "timeout": 30},
      echo=settings.debug,
      poolclass=StaticPool,
      creator=lambda: __import__("sqlite3").connect(
        f"file:{db_path}?immutable=1", uri=True,
        check_same_thread=False,
      ),
    )
  else:
    eng = create_engine(
      settings.database_url,
      connect_args={"check_same_thread": False, "timeout": 30},
      echo=settings.debug,
    )
  event.listen(eng, "connect", _apply_sqlite_pragmas)
  return eng


def _probe_engine(eng) -> bool:
  """Quick probe: can the engine actually read the DB?"""
  try:
    with eng.connect() as conn:
      conn.execute(sa_text("SELECT 1"))
    return True
  except Exception as e:
    if "disk I/O error" in str(e) or "readonly" in str(e).lower():
      return False
    raise  # Re-raise unexpected errors


# Build engine — fall back to immutable mode if mount can't handle WAL
engine = _build_engine(immutable=False)
if not _probe_engine(engine):
  logger.warning(
    "Normal SQLite connection failed (mounted filesystem?) — "
    "falling back to immutable (read-only) mode"
  )
  engine.dispose()
  _IMMUTABLE_MODE = True
  engine = _build_engine(immutable=True)


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ---------------------------------------------------------------------------
# Per-timeframe OHLCV database router
# ---------------------------------------------------------------------------

from backend.utils.tf_resolver import canonical_tf as _canonical_tf


class OHLCVRouter:
  """Manages per-timeframe OHLCV SQLite databases.

  Each timeframe gets its own file: data/ohlcv/ohlcv_30.db, ohlcv_D.db, etc.
  New DB files are auto-created on first access (e.g. when downloading a new TF).
  """

  def __init__(self, data_dir: Path):
    self._data_dir = data_dir / "ohlcv"
    self._engines: dict[str, object] = {}
    self._session_factories: dict[str, sessionmaker] = {}

  def get_engine(self, timeframe: str):
    """Get or create SQLAlchemy engine for a timeframe's OHLCV database."""
    tf = _canonical_tf(timeframe)
    if tf not in self._engines:
      self._data_dir.mkdir(parents=True, exist_ok=True)
      tf_db_path = self._data_dir / f"ohlcv_{tf}.db"
      tf_url = f"sqlite:///{tf_db_path}"
      eng = create_engine(
        tf_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        echo=False,
      )
      # Apply same WAL pragmas as main DB
      event.listen(eng, "connect", _apply_sqlite_pragmas)
      # Create the ohlcv_data table if it doesn't exist
      from backend.db.models import Base
      ohlcv_table = Base.metadata.tables.get("ohlcv_data")
      if ohlcv_table is not None:
        ohlcv_table.create(eng, checkfirst=True)
      self._engines[tf] = eng
      self._session_factories[tf] = sessionmaker(
        autocommit=False, autoflush=False, bind=eng,
      )
      logger.info("OHLCV router: initialized %s -> %s", tf, tf_db_path)
    return self._engines[tf]

  def get_session(self, timeframe: str) -> Session:
    """Get a new session bound to the timeframe-specific OHLCV database."""
    tf = _canonical_tf(timeframe)
    self.get_engine(tf)  # ensure initialized
    return self._session_factories[tf]()

  def list_timeframes(self) -> list[str]:
    """List all timeframes that have OHLCV database files on disk."""
    if not self._data_dir.exists():
      return []
    return sorted(
      f.stem.replace("ohlcv_", "")
      for f in self._data_dir.glob("ohlcv_*.db")
      if not f.name.endswith(("-wal", "-shm"))
    )

  def total_size_bytes(self) -> int:
    """Return total size of all OHLCV DB files (including WAL/SHM)."""
    if not self._data_dir.exists():
      return 0
    total = 0
    for f in self._data_dir.iterdir():
      if f.is_file():
        total += f.stat().st_size
    return total

  def delete_timeframe(self, timeframe: str) -> bool:
    """Delete an entire timeframe's data by removing its DB file. Returns True if deleted."""
    tf = _canonical_tf(timeframe)
    if tf in self._engines:
      self._engines[tf].dispose()
      del self._engines[tf]
      del self._session_factories[tf]
    deleted = False
    db_path_tf = self._data_dir / f"ohlcv_{tf}.db"
    for suffix in ["", "-wal", "-shm"]:
      p = Path(str(db_path_tf) + suffix)
      if p.exists():
        p.unlink()
        deleted = True
    if deleted:
      logger.info("OHLCV router: deleted timeframe %s", tf)
    return deleted

  def dispose_all(self):
    """Dispose all OHLCV engines (for cleanup/VACUUM)."""
    for eng in self._engines.values():
      eng.dispose()


# Module-level singleton — initialized after engine setup
_data_dir = Path(db_path).parent
ohlcv_router = OHLCVRouter(_data_dir)


def get_db() -> Generator[Session, None, None]:
  """FastAPI dependency that yields a DB session."""
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()


def _add_col(conn, table: str, col: str, coltype: str, default: str) -> None:
  """Add a column to a table if it doesn't exist. Logs unexpected errors."""
  try:
    conn.execute(sa_text(
      f"ALTER TABLE {table} ADD COLUMN {col} {coltype} DEFAULT {default}"
    ))
    conn.commit()
  except Exception as e:
    if "duplicate column" not in str(e).lower():
      logger.warning("Migration failed (%s.%s): %s", table, col, e)
    conn.rollback()


def _migrate_cascade_fks() -> None:
  """One-time migration: rebuild FK-bearing tables with CASCADE/SET NULL constraints.

  SQLite cannot ALTER existing FK constraints, so we rebuild each table
  using the DDL generated from the current models (which now include ondelete).
  Runs only once — detected via PRAGMA foreign_key_list on backtest_runs.
  """
  import shutil
  from sqlalchemy.schema import CreateTable

  with engine.connect() as conn:
    # Check if migration is needed — look at backtest_runs FK on_delete action
    fk_info = conn.execute(sa_text("PRAGMA foreign_key_list(backtest_runs)")).fetchall()
    if not fk_info:
      return  # Table has no FKs yet (fresh DB — create_all will handle it)
    # SQLite PRAGMA foreign_key_list columns: id, seq, table, from, to, on_update, on_delete, match
    if any(row[6] != "NO ACTION" for row in fk_info):
      return  # Already migrated (at least one FK has CASCADE/SET NULL)

  # Back up database before migration
  backup = db_path + ".backup_cascade"
  if not Path(backup).exists():
    shutil.copy2(db_path, backup)
    logger.info("CASCADE migration: backed up DB to %s", backup)

  from backend.db.models import Base

  # Tables with ForeignKey columns that need rebuilding (order doesn't matter — FKs are OFF)
  tables_to_rebuild = [
    "strategy_configs", "backtest_trades", "backtest_runs",
    "batch_backtest_runs", "portfolio_assets", "wfo_windows",
    "orders", "positions", "trades", "daily_pnl",
    "journal_trades", "live_events", "live_strategy_states", "live_signals",
  ]

  with engine.connect() as conn:
    conn.execute(sa_text("PRAGMA foreign_keys=OFF"))

    for table_name in tables_to_rebuild:
      model_table = Base.metadata.tables.get(table_name)
      if model_table is None:
        continue

      # Get existing columns in the current DB table
      existing = conn.execute(sa_text(f"PRAGMA table_info({table_name})")).fetchall()
      if not existing:
        continue  # Table doesn't exist yet
      existing_cols = {row[1] for row in existing}

      # Get model columns — only copy columns that exist in both
      model_cols = [col.name for col in model_table.columns]
      common = [c for c in model_cols if c in existing_cols]
      col_list = ", ".join(f'"{c}"' for c in common)

      temp_name = f"_temp_cascade_{table_name}"

      # Generate DDL from model (includes CASCADE/SET NULL)
      ddl_str = str(CreateTable(model_table).compile(dialect=engine.dialect))
      ddl_str = ddl_str.replace(f"\nCREATE TABLE {table_name}", f"\nCREATE TABLE {temp_name}")
      ddl_str = ddl_str.replace(f"CREATE TABLE {table_name}", f"CREATE TABLE {temp_name}")

      conn.execute(sa_text(f"DROP TABLE IF EXISTS {temp_name}"))
      conn.execute(sa_text(ddl_str))
      conn.execute(sa_text(f'INSERT INTO {temp_name} ({col_list}) SELECT {col_list} FROM "{table_name}"'))
      conn.execute(sa_text(f'DROP TABLE "{table_name}"'))
      conn.execute(sa_text(f'ALTER TABLE {temp_name} RENAME TO "{table_name}"'))
      logger.info("CASCADE migration: rebuilt %s", table_name)

    conn.execute(sa_text("PRAGMA foreign_keys=ON"))
    conn.commit()

  logger.info("CASCADE migration complete — all FK tables rebuilt with CASCADE/SET NULL")


def init_db() -> None:
  """Create all tables. Called on app startup."""
  from backend.db.models import Base
  # Exclude ohlcv_data from main DB — it lives in per-TF databases now
  non_ohlcv_tables = [
    t for t in Base.metadata.sorted_tables if t.name != "ohlcv_data"
  ]
  Base.metadata.create_all(bind=engine, tables=non_ohlcv_tables)

  # One-time migration: add CASCADE/SET NULL to FK constraints
  _migrate_cascade_fks()

  # Performance indexes for common query patterns (NOT ohlcv — autoindex handles it)
  with engine.connect() as conn:
    for idx_sql in [
      "CREATE INDEX IF NOT EXISTS idx_bt_trades_run ON backtest_trades(run_id)",
      "CREATE INDEX IF NOT EXISTS idx_bt_runs_batch ON backtest_runs(batch_id, status)",
      # Live trading hot paths
      "CREATE INDEX IF NOT EXISTS idx_positions_portfolio_open ON positions(portfolio_id, is_open)",
      "CREATE INDEX IF NOT EXISTS idx_orders_portfolio_status ON orders(portfolio_id, status)",
      "CREATE INDEX IF NOT EXISTS idx_trades_portfolio ON trades(portfolio_id)",
      "CREATE INDEX IF NOT EXISTS idx_daily_pnl_portfolio_date ON daily_pnl(portfolio_id, date)",
      # Portfolio assets and WFO lookups
      "CREATE INDEX IF NOT EXISTS idx_portfolio_assets_portfolio ON portfolio_assets(portfolio_id)",
      "CREATE INDEX IF NOT EXISTS idx_wfo_runs_batch ON wfo_runs(batch_id)",
      # Signal scanner
      "CREATE INDEX IF NOT EXISTS idx_signals_symbol_status ON signals(symbol, status)",
    ]:
      conn.execute(sa_text(idx_sql))
    conn.commit()

  # Idempotent migrations — adds columns if they don't exist
  with engine.connect() as conn:
    # Portfolio Hub columns
    for col, coltype, default in [
      ("portfolio_type", "TEXT", "'saved'"),
      ("source_portfolio_id", "INTEGER", "NULL"),
      ("activated_at", "TEXT", "NULL"),
      ("deactivated_at", "TEXT", "NULL"),
      ("live_metrics_snapshot", "TEXT", "'{}'"),
      ("last_refreshed_at", "TEXT", "NULL"),
      ("is_hidden", "INTEGER", "0"),
      ("optimization_end_date", "TEXT", "NULL"),
      ("is_start_date", "TEXT", "NULL"),
      ("portfolio_source", "TEXT", "'optimizer'"),
      ("description", "TEXT", "NULL"),
      ("refresh_status", "TEXT", "NULL"),
    ]:
      _add_col(conn, "portfolios", col, coltype, default)

    # Portfolio assets — risk mode from auto-risk
    _add_col(conn, "portfolio_assets", "risk_mode", "TEXT", "NULL")
    # Portfolio assets — timeframe + group from WFAS batch info
    _add_col(conn, "portfolio_assets", "timeframe", "TEXT", "NULL")
    _add_col(conn, "portfolio_assets", "group_name", "TEXT", "NULL")

    # Batch naming + merge tracking + progress
    for col, coltype, default in [
      ("name", "TEXT", "NULL"),
      ("notes", "TEXT", "''"),
      ("config_group", "TEXT", "NULL"),
      ("source_batch_ids", "TEXT", "NULL"),
      ("refreshed_from_id", "INTEGER", "NULL"),
      ("progress_detail", "TEXT", "'{}'"),
      ("is_archived", "INTEGER", "0"),
      ("is_compacted", "INTEGER", "0"),
      ("personal_grade", "TEXT", "NULL"),
    ]:
      _add_col(conn, "batch_backtest_runs", col, coltype, default)

    # Min ADV (Average Daily Volume) filter used per batch
    _add_col(conn, "batch_backtest_runs", "min_adv", "REAL", "NULL")
    # Bybit position size limit and max margin per trade
    _add_col(conn, "batch_backtest_runs", "bybit_limit", "REAL", "NULL")
    _add_col(conn, "batch_backtest_runs", "max_margin_per_trade", "REAL", "NULL")

    # Backfill min_adv=1000000 for existing batches that don't have it yet
    try:
      conn.execute(
        sa_text("UPDATE batch_backtest_runs SET min_adv = 1000000.0 WHERE min_adv IS NULL")
      )
      conn.commit()
    except SQLAlchemyError as e:
      # CS-046: narrowed from bare `except Exception`. A SQLAlchemyError here
      # (table missing, column missing, DB locked) means the migration block
      # can safely roll back and move on. A non-DB error (e.g. AttributeError,
      # typo) should now surface instead of being silently swallowed.
      logger.warning("Migration backfill min_adv failed (%s: %s) — rolling back",
                     type(e).__name__, e)
      conn.rollback()

    # Backfill bybit_limit: M30 (non-Batch4) = 250, everything else = 100
    try:
      # M30 non-Batch4 → 250
      conn.execute(
        sa_text(
          "UPDATE batch_backtest_runs SET bybit_limit = 250.0 "
          "WHERE bybit_limit IS NULL AND timeframe = '30' "
          "AND (config_group IS NULL OR config_group NOT LIKE 'Batch 4%')"
        )
      )
      # All remaining (M15 + Batch 4 of any TF) → 100
      conn.execute(
        sa_text("UPDATE batch_backtest_runs SET bybit_limit = 100.0 WHERE bybit_limit IS NULL")
      )
      conn.commit()
    except SQLAlchemyError as e:
      # CS-046: see note above.
      logger.warning("Migration backfill bybit_limit failed (%s: %s) — rolling back",
                     type(e).__name__, e)
      conn.rollback()

    # Backfill max_margin_per_trade=1000 for all existing batches
    try:
      conn.execute(
        sa_text("UPDATE batch_backtest_runs SET max_margin_per_trade = 1000.0 WHERE max_margin_per_trade IS NULL")
      )
      conn.commit()
    except SQLAlchemyError as e:
      # CS-046: see note above.
      logger.warning("Migration backfill max_margin_per_trade failed (%s: %s) — rolling back",
                     type(e).__name__, e)
      conn.rollback()

    # Backfill config_group for existing batches that don't have one yet
    try:
      rows = conn.execute(
        sa_text("SELECT id, name FROM batch_backtest_runs WHERE config_group IS NULL AND name IS NOT NULL")
      ).fetchall()
      for row in rows:
        batch_name = row[1] or ""
        sep = batch_name.find(" \u2014 ")  # em-dash separator
        config = batch_name[sep + 3:] if sep > 0 else "Default"
        conn.execute(
          sa_text("UPDATE batch_backtest_runs SET config_group = :cg WHERE id = :bid"),
          {"cg": config, "bid": row[0]},
        )
      if rows:
        conn.commit()
    except Exception as e:
      logger.debug("config_group backfill skip: %s", e)
      conn.rollback()

    # Batch groups — trading parameter columns
    for col, coltype, default in [
      ("timeframe", "TEXT", "NULL"),
      ("bybit_limit", "REAL", "NULL"),
      ("max_margin_per_trade", "REAL", "NULL"),
      ("min_adv", "REAL", "NULL"),
      ("slippage_pct", "REAL", "0.0"),
      ("fee_pct", "REAL", "0.0"),
      ("pnl_multiplier", "REAL", "1.0"),
    ]:
      _add_col(conn, "batch_groups", col, coltype, default)

    # Rename backtest_base → pnl_multiplier if old column exists
    try:
      conn.execute(sa_text("SELECT backtest_base FROM batch_groups LIMIT 1"))
      # Old column exists — migrate values: multiplier = bybit_limit / backtest_base (or 1.0)
      conn.execute(sa_text(
        "UPDATE batch_groups SET pnl_multiplier = CASE "
        "WHEN backtest_base > 0 AND bybit_limit IS NOT NULL THEN bybit_limit / backtest_base "
        "ELSE 1.0 END "
        "WHERE pnl_multiplier = 1.0"
      ))
      logger.info("Migrated backtest_base → pnl_multiplier in batch_groups")
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)  # backtest_base column doesn't exist — nothing to migrate

    # Seed master groups A (M30) and B (M15) if they don't exist yet
    try:
      existing = conn.execute(
        sa_text("SELECT name FROM batch_groups WHERE name IN ('A', 'B')")
      ).fetchall()
      existing_names = {r[0] for r in existing}

      if "A" not in existing_names:
        conn.execute(sa_text(
          "INSERT INTO batch_groups (name, description, sort_order, timeframe, "
          "bybit_limit, max_margin_per_trade, min_adv, slippage_pct, fee_pct, pnl_multiplier) "
          "VALUES ('A', 'M30 batches — production sizing $200', 1, '30', "
          "250.0, 1000.0, 1000000.0, 0.0, 0.0, 2.0)"
        ))
      if "B" not in existing_names:
        conn.execute(sa_text(
          "INSERT INTO batch_groups (name, description, sort_order, timeframe, "
          "bybit_limit, max_margin_per_trade, min_adv, slippage_pct, fee_pct, pnl_multiplier) "
          "VALUES ('B', 'M15 batches — production sizing $100', 2, '15', "
          "100.0, 1000.0, 1000000.0, 0.0, 0.0, 1.0)"
        ))

      # Auto-assign existing batches to groups by timeframe (only if unassigned)
      conn.execute(sa_text(
        "UPDATE batch_backtest_runs SET group_id = ("
        "  SELECT id FROM batch_groups WHERE name = 'A'"
        ") WHERE group_id IS NULL AND timeframe = '30' AND is_deleted = 0"
      ))
      conn.execute(sa_text(
        "UPDATE batch_backtest_runs SET group_id = ("
        "  SELECT id FROM batch_groups WHERE name = 'B'"
        ") WHERE group_id IS NULL AND timeframe = '15' AND is_deleted = 0"
      ))
      conn.commit()
    except Exception as e:
      logger.debug("Batch group seeding skip: %s", e)
      conn.rollback()

    # WFO: batch_id → batch_ids (JSON list)
    _add_col(conn, "wfo_runs", "batch_ids", "TEXT", "'[]'")

    # Order table — live execution columns
    for col, coltype, default in [
      ("order_link_id", "TEXT", "''"),
      ("portfolio_id", "INTEGER", "NULL"),
      ("position_id_fk", "INTEGER", "NULL"),
      ("role", "TEXT", "'entry'"),
      ("intended_qty", "REAL", "0.0"),
      ("filled_qty", "REAL", "0.0"),
      ("avg_fill_price", "REAL", "NULL"),
      ("time_in_force", "TEXT", "'GTC'"),
      ("reduce_only", "INTEGER", "0"),
      ("ttl_seconds", "INTEGER", "0"),
      ("expired_at", "TEXT", "NULL"),
      ("cancel_reason", "TEXT", "''"),
      ("funding_fee", "REAL", "0.0"),
    ]:
      _add_col(conn, "orders", col, coltype, default)

    # Position table — live execution columns
    for col, coltype, default in [
      ("exchange_symbol", "TEXT", "''"),
      ("margin_mode", "TEXT", "'isolated'"),
      ("trailing_stop_distance", "REAL", "NULL"),
      ("trailing_extreme", "REAL", "NULL"),
      ("entry_order_link_id", "TEXT", "''"),
      ("sl_order_link_id", "TEXT", "''"),
      ("tp_order_link_id", "TEXT", "''"),
      ("funding_accumulated", "REAL", "0.0"),
      ("entry_commission", "REAL", "0.0"),
      ("exit_commission", "REAL", "0.0"),
    ]:
      _add_col(conn, "positions", col, coltype, default)

    # Trade table — live execution columns
    for col, coltype, default in [
      ("entry_commission", "REAL", "0.0"),
      ("exit_commission", "REAL", "0.0"),
      ("funding_fee", "REAL", "0.0"),
    ]:
      _add_col(conn, "trades", col, coltype, default)

    # DailyPnL table — live execution columns
    for col, coltype, default in [
      ("portfolio_id", "INTEGER", "NULL"),
      ("unrealized_pnl", "REAL", "0.0"),
      ("funding_fees", "REAL", "0.0"),
      ("total_commission", "REAL", "0.0"),
    ]:
      _add_col(conn, "daily_pnl", col, coltype, default)

    # Journal notes — color marker
    _add_col(conn, "journal_notes", "color", "TEXT", "'indigo'")

    # Monthly objectives — period + year/month scoping
    for col, coltype, default in [
      ("period", "TEXT", "'monthly'"),
      ("obj_year", "INTEGER", "NULL"),
      ("obj_month", "INTEGER", "NULL"),
    ]:
      _add_col(conn, "monthly_objectives", col, coltype, default)

    # Backtest runs — strategy state for batch refresh
    _add_col(conn, "backtest_runs", "strategy_state", "TEXT", "NULL")

    # Journal trades — live trade snapshot fields
    for col, coltype, default in [
      ("batch_id", "INTEGER", "NULL"),
      ("config_name", "TEXT", "NULL"),
      ("live_trade_id", "INTEGER", "NULL"),
    ]:
      _add_col(conn, "journal_trades", col, coltype, default)

    # Soft-delete columns for strategies, backtests, batches, WFO runs
    for table in ["strategies", "backtest_runs", "batch_backtest_runs", "wfo_runs"]:
      _add_col(conn, table, "is_deleted", "INTEGER", "0")
      _add_col(conn, table, "deleted_at", "TEXT", "NULL")

    # Custom templates — color marker
    _add_col(conn, "custom_templates", "color", "TEXT", "'indigo'")

    # Test trade flag — soft-hide from stats
    _add_col(conn, "trades", "is_test", "INTEGER", "0")
    _add_col(conn, "journal_trades", "is_test", "INTEGER", "0")

    # Per-batch martingale scope — batch_id on positions and trades
    _add_col(conn, "positions", "batch_id", "INTEGER", "NULL")
    _add_col(conn, "trades", "batch_id", "INTEGER", "NULL")

    # SL-past-BE timestamp for confirmed_tsl martingale count mode
    _add_col(conn, "backtest_trades", "sl_past_be_time", "TEXT", "NULL")

    # Tax records — journal trade dedup column
    _add_col(conn, "tax_records", "journal_trade_id", "INTEGER", "NULL")

    # One-time fix: repair portfolio_type for "Paper Trading trial" (bug corrupted it to 'real')
    try:
      fixed = conn.execute(
        sa_text("UPDATE portfolios SET portfolio_type = 'paper' WHERE name LIKE 'Paper Trading%' AND portfolio_type != 'paper'")
      ).rowcount
      if fixed:
        conn.commit()
        logger.info("Migration: fixed %d paper portfolio(s) with wrong type", fixed)
    except SQLAlchemyError as e:
      # CS-046: narrowed from bare `except Exception` — only DB-layer errors
      # should roll back silently; real bugs should surface.
      logger.warning("Migration paper portfolio repair failed (%s: %s) — rolling back",
                     type(e).__name__, e)
      conn.rollback()
