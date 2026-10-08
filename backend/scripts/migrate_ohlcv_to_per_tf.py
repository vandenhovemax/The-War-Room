"""
One-time migration: Move OHLCV data from monolithic antigravity.db
into per-timeframe SQLite files under data/ohlcv/.

Run from The_War_Room/ with backend STOPPED:
  python -m backend.scripts.migrate_ohlcv_to_per_tf

Uses ATTACH DATABASE so SQLite copies data internally (no Python round-trips).
"""
import sqlite3
import sys
import time
from pathlib import Path


def main():
  # Locate the main DB
  script_dir = Path(__file__).resolve().parent
  backend_dir = script_dir.parent
  project_root = backend_dir.parent  # The_War_Room/
  data_dir = project_root / "data"
  main_db = data_dir / "antigravity.db"

  if not main_db.exists():
    print(f"ERROR: Main DB not found at {main_db}")
    sys.exit(1)

  ohlcv_dir = data_dir / "ohlcv"
  ohlcv_dir.mkdir(parents=True, exist_ok=True)

  print(f"Main DB: {main_db}")
  print(f"OHLCV dir: {ohlcv_dir}")
  print()

  # Connect to main DB
  conn = sqlite3.connect(str(main_db), timeout=600)
  conn.execute("PRAGMA journal_mode=WAL")
  conn.execute("PRAGMA busy_timeout=30000")
  conn.execute("PRAGMA cache_size=-64000")

  # Check if ohlcv_data table exists
  tables = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='ohlcv_data'"
  ).fetchall()
  if not tables:
    print("No ohlcv_data table found in main DB - nothing to migrate.")
    conn.close()
    return

  # Get distinct timeframes
  tfs = conn.execute(
    "SELECT DISTINCT timeframe FROM ohlcv_data ORDER BY timeframe"
  ).fetchall()
  tfs = [r[0] for r in tfs]

  if not tfs:
    print("No OHLCV data found in main DB - nothing to migrate.")
    conn.close()
    return

  print(f"Found {len(tfs)} timeframes: {tfs}")

  # Get row counts per timeframe
  total_main = 0
  tf_counts: dict[str, int] = {}
  for tf in tfs:
    count = conn.execute(
      "SELECT COUNT(*) FROM ohlcv_data WHERE timeframe = ?", (tf,)
    ).fetchone()[0]
    tf_counts[tf] = count
    total_main += count
    print(f"  {tf}: {count:,} rows")
  print(f"  Total: {total_main:,} rows")
  print()

  conn.close()

  # Migrate each timeframe using ATTACH DATABASE (all inside SQLite engine)
  total_migrated = 0
  for tf in tfs:
    tf_db_path = ohlcv_dir / f"ohlcv_{tf}.db"
    expected = tf_counts[tf]
    print(f"Migrating TF={tf} ({expected:,} rows) -> {tf_db_path.name}...", end=" ", flush=True)
    start = time.time()

    # First, create the per-TF database with the table schema
    tf_conn = sqlite3.connect(str(tf_db_path), timeout=600)
    tf_conn.execute("PRAGMA journal_mode=WAL")
    tf_conn.execute("PRAGMA synchronous=NORMAL")
    tf_conn.execute("PRAGMA cache_size=-64000")
    tf_conn.execute("PRAGMA temp_store=MEMORY")
    tf_conn.execute("""
      CREATE TABLE IF NOT EXISTS ohlcv_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol VARCHAR NOT NULL,
        timeframe VARCHAR NOT NULL,
        timestamp DATETIME NOT NULL,
        open FLOAT NOT NULL,
        high FLOAT NOT NULL,
        low FLOAT NOT NULL,
        close FLOAT NOT NULL,
        volume FLOAT NOT NULL,
        UNIQUE (symbol, timeframe, timestamp)
      )
    """)

    # Check how many rows already exist (in case of partial previous run)
    existing = tf_conn.execute("SELECT COUNT(*) FROM ohlcv_data").fetchone()[0]
    if existing > 0:
      print(f"already has {existing:,} rows", end=" ")
      if existing == expected:
        print("- SKIP (already migrated)")
        tf_conn.close()
        total_migrated += existing
        continue
      else:
        # Partial - clear and redo
        print("- clearing partial...", end=" ", flush=True)
        tf_conn.execute("DELETE FROM ohlcv_data")
        tf_conn.commit()

    tf_conn.close()

    # Now open the MAIN DB and ATTACH the per-TF DB to it
    # This lets SQLite do INSERT...SELECT entirely inside its engine
    mconn = sqlite3.connect(str(main_db), timeout=600)
    mconn.execute("PRAGMA busy_timeout=30000")
    mconn.execute("PRAGMA cache_size=-64000")

    # Attach the target per-TF database
    mconn.execute("ATTACH DATABASE ? AS tf_db", (str(tf_db_path),))

    # Set WAL + performance pragmas on the attached DB
    mconn.execute("PRAGMA tf_db.journal_mode=WAL")
    mconn.execute("PRAGMA tf_db.synchronous=NORMAL")
    mconn.execute("PRAGMA tf_db.cache_size=-64000")

    # Copy all rows for this timeframe in one shot (inside SQLite engine)
    mconn.execute("""
      INSERT OR IGNORE INTO tf_db.ohlcv_data
        (symbol, timeframe, timestamp, open, high, low, close, volume)
      SELECT symbol, timeframe, timestamp, open, high, low, close, volume
      FROM main.ohlcv_data
      WHERE timeframe = ?
    """, (tf,))
    mconn.commit()

    # Verify row count
    actual = mconn.execute("SELECT COUNT(*) FROM tf_db.ohlcv_data").fetchone()[0]
    elapsed = time.time() - start

    # Checkpoint and detach
    mconn.execute("PRAGMA tf_db.wal_checkpoint(TRUNCATE)")
    mconn.execute("DETACH DATABASE tf_db")
    mconn.close()

    if actual == expected:
      print(f"OK ({actual:,} rows, {elapsed:.1f}s)")
      total_migrated += actual
    else:
      print(f"MISMATCH! Expected {expected:,}, got {actual:,}")
      print("Aborting migration - per-TF files kept for inspection.")
      sys.exit(1)

  print()
  print(f"Migration complete: {total_migrated:,} / {total_main:,} rows copied")

  if total_migrated != total_main:
    print("WARNING: Row count mismatch - NOT dropping ohlcv_data from main DB")
    sys.exit(1)

  # Drop ohlcv_data table from main DB
  print("Dropping ohlcv_data table from main DB...", end=" ", flush=True)
  conn = sqlite3.connect(str(main_db), timeout=600)
  conn.execute("DROP TABLE IF EXISTS ohlcv_data")
  conn.commit()
  conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
  conn.close()
  print("done")

  # VACUUM main DB
  print("Vacuuming main DB...", end=" ", flush=True)
  start = time.time()
  vconn = sqlite3.connect(str(main_db), isolation_level=None, timeout=600)
  vconn.execute("VACUUM")
  vconn.close()
  elapsed = time.time() - start
  print(f"done ({elapsed:.1f}s)")

  # Final sizes
  main_size = main_db.stat().st_size
  ohlcv_total = sum(
    f.stat().st_size for f in ohlcv_dir.iterdir()
    if f.is_file() and not f.name.endswith(("-wal", "-shm"))
  )
  print()
  print(f"Main DB: {main_size / (1024*1024):.1f} MB")
  print(f"OHLCV dir: {ohlcv_total / (1024*1024):.1f} MB")
  print(f"Total: {(main_size + ohlcv_total) / (1024*1024):.1f} MB")
  print()
  print("Migration successful! You can now start the backend.")


if __name__ == "__main__":
  main()
