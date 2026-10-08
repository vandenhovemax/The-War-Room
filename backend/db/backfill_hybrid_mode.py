"""T-001 backfill: flip portfolios.mode to 'hybrid' for rows where
source_strategies.hybrid_mode=1 but mode is still stuck at 'non_hybrid'.

Background:
  The one-shot migration in migrate_live_loop.py runs this same UPDATE
  the first time the `mode` column is added to the DB — but it is guarded
  on column creation and never re-runs. Any WFAS portfolio saved AFTER
  the migration landed, while /wfo/save-track and /wfo/save-phase2-track
  were still missing the `mode=` kwarg, got the column default
  ('non_hybrid') regardless of the user's hybrid selection. Those rows
  bypass the nightly hybrid refresh and their baskets freeze.

Usage:
  python -m backend.db.backfill_hybrid_mode --dry-run
  python -m backend.db.backfill_hybrid_mode --apply
  python -m backend.db.backfill_hybrid_mode --dry-run --ids 23,24
  python -m backend.db.backfill_hybrid_mode --apply   --ids 23,24

  --dry-run : prints the list of affected portfolios, writes nothing.
  --apply   : runs the idempotent UPDATE.
  --ids     : optional comma-separated list of portfolio IDs. When
              provided, only rows with matching id are considered
              (intersected with the safety predicate below).

The UPDATE is safe to re-run — it only flips rows still stuck at
non_hybrid with hybrid_mode=1.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.sql import bindparam

from backend.db.database import SessionLocal


_BASE_WHERE = (
  "json_valid(source_strategies) = 1 "
  "AND json_extract(source_strategies, '$.hybrid_mode') = 1 "
  "AND mode = 'non_hybrid' "
  "AND is_deleted = 0"
)


def _select_stmt(ids: list[int] | None):
  sql = (
    "SELECT id, name, portfolio_source, mode, "
    "       json_extract(source_strategies, '$.hybrid_mode') AS hybrid_mode_flag "
    "  FROM portfolios "
    f" WHERE {_BASE_WHERE}"
  )
  if ids:
    stmt = text(sql + " AND id IN :ids ORDER BY id").bindparams(
      bindparam("ids", expanding=True)
    )
    return stmt, {"ids": ids}
  return text(sql + " ORDER BY id"), {}


def _update_stmt(ids: list[int] | None):
  sql = f"UPDATE portfolios SET mode = 'hybrid' WHERE {_BASE_WHERE}"
  if ids:
    stmt = text(sql + " AND id IN :ids").bindparams(
      bindparam("ids", expanding=True)
    )
    return stmt, {"ids": ids}
  return text(sql), {}


def _parse_ids(raw: str | None) -> list[int] | None:
  if not raw:
    return None
  out: list[int] = []
  for part in raw.split(","):
    part = part.strip()
    if not part:
      continue
    out.append(int(part))
  return out or None


def _list_affected(db, ids: list[int] | None) -> list[dict]:
  stmt, params = _select_stmt(ids)
  rows = db.execute(stmt, params).mappings().all()
  return [dict(r) for r in rows]


def _print_table(rows: list[dict]) -> None:
  if not rows:
    print("(no affected portfolios)")
    return
  header = f"{'id':>4}  {'name':<40}  {'source':<12}  {'mode':<10}  hybrid_mode"
  print(header)
  print("-" * len(header))
  for r in rows:
    name = str(r.get("name") or "")[:40]
    src = str(r.get("portfolio_source") or "")[:12]
    mode = str(r.get("mode") or "")[:10]
    hm = r.get("hybrid_mode_flag")
    print(f"{r['id']:>4}  {name:<40}  {src:<12}  {mode:<10}  {hm}")


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Backfill portfolios.mode = 'hybrid' for T-001 victims."
  )
  group = parser.add_mutually_exclusive_group(required=True)
  group.add_argument("--dry-run", action="store_true",
                     help="List affected portfolios without writing.")
  group.add_argument("--apply", action="store_true",
                     help="Run the UPDATE.")
  parser.add_argument("--ids", type=str, default=None,
                      help="Comma-separated portfolio IDs to limit scope "
                           "(e.g. --ids 23,24). Intersects with the safety "
                           "predicate; never widens it.")
  parser.add_argument("--require-ids", action="store_true",
                      help="Refuse to run --apply without an explicit --ids "
                           "allow-list. R4.1 safety flag: prevents a bare "
                           "`--apply` from sweeping every hybrid_mode=1 row "
                           "when the operator just wanted 23,24.")
  args = parser.parse_args(argv)

  ids = _parse_ids(args.ids)
  if args.apply and args.require_ids and not ids:
    print("ERROR: --require-ids set but --ids was not provided. Refusing to "
          "run a bare --apply that could sweep all hybrid_mode=1 portfolios.")
    return 2
  scope_label = f"ids={ids}" if ids else "all victims"
  db = SessionLocal()
  try:
    before = _list_affected(db, ids)
    print(f"Affected portfolios ({scope_label}): {len(before)}")
    _print_table(before)

    if args.dry_run:
      print("\n--dry-run: no changes written.")
      return 0

    if args.apply:
      if not before:
        print("\nNothing to update.")
        return 0
      print(f"\nApplying UPDATE to {len(before)} row(s)...")
      stmt, params = _update_stmt(ids)
      result = db.execute(stmt, params)
      db.commit()
      rowcount = result.rowcount if result.rowcount is not None else len(before)
      print(f"UPDATE complete. rows affected: {rowcount}")

      after = _list_affected(db, ids)
      print(f"\nRemaining affected after UPDATE: {len(after)}")
      if after:
        print("(unexpected — should be 0)")
        _print_table(after)
        return 1
      return 0
  finally:
    db.close()

  return 0


if __name__ == "__main__":
  sys.exit(main())
