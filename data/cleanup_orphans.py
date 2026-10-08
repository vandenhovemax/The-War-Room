"""
One-off orphan cleanup — run ONCE with the backend stopped.

Removes:
  - 50,547 backtest_trades whose run_id was deleted
  - 29,994 backtest_trades whose run's batch (68) was deleted
  -    347 backtest_runs pointing to deleted batch 68
  -    301 orders pointing to deleted portfolio 29
  -     93 positions pointing to deleted portfolio 29
  -      2 daily_pnl pointing to deleted portfolio 29

Total: 81,284 rows.

Safety:
  - Wraps in a single BEGIN/COMMIT. On any error, rolls back cleanly.
  - Runs PRAGMA integrity_check + foreign_key_check at the end — both
    must pass before it prints DONE.
  - Does NOT run VACUUM — do that separately once you're happy.
"""
import sqlite3
import os
import sys
from pathlib import Path

DB = Path(__file__).parent / "antigravity.db"
if not DB.exists():
    print(f"ERROR: {DB} not found", file=sys.stderr)
    sys.exit(1)

conn = sqlite3.connect(str(DB), timeout=60)
conn.execute("PRAGMA busy_timeout = 60000")
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.execute("PRAGMA foreign_keys = OFF")   # we enforce the order manually
cur = conn.cursor()

print(f"DB size before: {os.path.getsize(DB) / (1024**2):.1f} MB")

conn.execute("BEGIN")
try:
    cur.execute("""
        DELETE FROM backtest_trades
        WHERE run_id NOT IN (SELECT id FROM backtest_runs)
    """)
    n1 = cur.rowcount
    print(f"  deleted {n1:,} orphan trades (missing run)")

    cur.execute("""
        DELETE FROM backtest_trades
        WHERE run_id IN (
            SELECT id FROM backtest_runs
            WHERE batch_id NOT IN (SELECT id FROM batch_backtest_runs)
        )
    """)
    n2 = cur.rowcount
    print(f"  deleted {n2:,} trades under orphan runs")

    cur.execute("""
        DELETE FROM backtest_runs
        WHERE batch_id NOT IN (SELECT id FROM batch_backtest_runs)
    """)
    n3 = cur.rowcount
    print(f"  deleted {n3:,} orphan runs (batch 68)")

    cur.execute("DELETE FROM orders    WHERE portfolio_id NOT IN (SELECT id FROM portfolios)")
    n4 = cur.rowcount
    print(f"  deleted {n4:,} orphan orders")

    cur.execute("DELETE FROM positions WHERE portfolio_id NOT IN (SELECT id FROM portfolios)")
    n5 = cur.rowcount
    print(f"  deleted {n5:,} orphan positions")

    cur.execute("DELETE FROM daily_pnl WHERE portfolio_id NOT IN (SELECT id FROM portfolios)")
    n6 = cur.rowcount
    print(f"  deleted {n6:,} orphan daily_pnl")

    cur.execute("PRAGMA foreign_key_check")
    remaining = cur.fetchall()
    if remaining:
        raise RuntimeError(f"{len(remaining)} FK violations still present — NOT committing")

    cur.execute("PRAGMA integrity_check(1)")
    integ = cur.fetchone()[0]
    if integ != "ok":
        raise RuntimeError(f"integrity_check failed: {integ} — NOT committing")

    conn.commit()
    total = n1 + n2 + n3 + n4 + n5 + n6
    print(f"\n  COMMITTED — {total:,} rows removed")
except Exception as e:
    conn.rollback()
    print(f"\n  ROLLED BACK: {e}", file=sys.stderr)
    raise
finally:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

print(f"\nDB size after (pre-VACUUM): {os.path.getsize(DB) / (1024**2):.1f} MB")
print("DONE. Optionally run VACUUM to reclaim space:")
print(f"  python -c \"import sqlite3; sqlite3.connect('{DB}').execute('VACUUM')\"")
