"""T-001b backfill: populate portfolios.optimization_end_date for hybrid
rows where it is NULL, using source_strategies.wfo_windows_meta[-1].oos_end
as the source of truth.

Background:
  Hybrid portfolios saved before /save-track and /save-phase2-track started
  auto-populating optimization_end_date from the last window's OOS end
  ended up with a NULL column value. Downstream views (Monthly PnL,
  /portfolios/{id}/wfo-timeline, IS/OOS cutoff filters) need this value
  to segment trades correctly — without it, IS and OOS data bleed together.

  The wfo_windows_meta JSON array inside source_strategies carries the
  window schedule locked at save time; its final element's oos_end is
  the correct OOS boundary for the portfolio.

Usage:
  python -m backend.db.backfill_optimization_end_date --dry-run
  python -m backend.db.backfill_optimization_end_date --apply
  python -m backend.db.backfill_optimization_end_date --dry-run --ids 22,23,24
  python -m backend.db.backfill_optimization_end_date --apply   --ids 22,23,24

  --dry-run : prints the list of affected portfolios + computed dates, writes nothing.
  --apply   : runs the idempotent UPDATE (only for rows that would flip).
  --ids     : optional comma-separated list of portfolio IDs. When
              provided, only rows with matching id are considered
              (intersected with the safety predicate below).

The UPDATE is safe to re-run — it only fills NULL optimization_end_date
on hybrid rows whose wfo_windows_meta[-1].oos_end is parseable.

Safety predicate (NEVER widened by --ids):
  - is_deleted = 0
  - json_valid(source_strategies) = 1
  - source_strategies.hybrid_mode = 1  (the single source of truth for
    "this portfolio is meant to be hybrid"; matches both mode='hybrid' and
    T-001 victims whose mode column is still 'non_hybrid')
  - optimization_end_date IS NULL
  - source_strategies.wfo_windows_meta is non-null (so we have a source)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.sql import bindparam

from backend.db.database import SessionLocal


_BASE_WHERE = (
  "is_deleted = 0 "
  "AND json_valid(source_strategies) = 1 "
  "AND json_extract(source_strategies, '$.hybrid_mode') = 1 "
  "AND optimization_end_date IS NULL "
  "AND json_extract(source_strategies, '$.wfo_windows_meta') IS NOT NULL"
)


def _select_stmt(ids: list[int] | None):
  sql = (
    "SELECT id, name, portfolio_source, mode, "
    "       json_extract(source_strategies, '$.hybrid_mode') AS hybrid_mode_flag, "
    "       json_extract(source_strategies, '$.wfo_windows_meta[#-1].oos_end') AS oos_end_last "
    "  FROM portfolios "
    f" WHERE {_BASE_WHERE}"
  )
  if ids:
    stmt = text(sql + " AND id IN :ids ORDER BY id").bindparams(
      bindparam("ids", expanding=True)
    )
    return stmt, {"ids": ids}
  return text(sql + " ORDER BY id"), {}


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


def _parse_oos_end(raw: str | None) -> datetime | None:
  if not raw:
    return None
  try:
    return datetime.fromisoformat(raw)
  except (ValueError, TypeError):
    return None


def _list_affected(db, ids: list[int] | None) -> list[dict]:
  stmt, params = _select_stmt(ids)
  rows = db.execute(stmt, params).mappings().all()
  out: list[dict] = []
  for r in rows:
    d = dict(r)
    d["parsed_oos_end"] = _parse_oos_end(d.get("oos_end_last"))
    out.append(d)
  return out


def _print_table(rows: list[dict]) -> None:
  if not rows:
    print("(no affected portfolios)")
    return
  header = f"{'id':>4}  {'name':<40}  {'source':<14}  {'mode':<10}  {'oos_end (raw)':<14}  would_set_optimization_end_date"
  print(header)
  print("-" * len(header))
  for r in rows:
    name = str(r.get("name") or "")[:40]
    src = str(r.get("portfolio_source") or "")[:14]
    mode = str(r.get("mode") or "")[:10]
    raw = str(r.get("oos_end_last") or "")[:14]
    parsed = r.get("parsed_oos_end")
    parsed_str = parsed.isoformat() if parsed else "(UNPARSEABLE — row will be SKIPPED)"
    print(f"{r['id']:>4}  {name:<40}  {src:<14}  {mode:<10}  {raw:<14}  {parsed_str}")


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Backfill portfolios.optimization_end_date for hybrid rows where NULL."
  )
  group = parser.add_mutually_exclusive_group(required=True)
  group.add_argument("--dry-run", action="store_true",
                     help="List affected portfolios + computed dates, no writes.")
  group.add_argument("--apply", action="store_true",
                     help="Run the UPDATE on rows with parseable oos_end.")
  parser.add_argument("--ids", type=str, default=None,
                      help="Comma-separated portfolio IDs to limit scope "
                           "(e.g. --ids 22,23,24). Intersects with the safety "
                           "predicate; never widens it.")
  args = parser.parse_args(argv)

  ids = _parse_ids(args.ids)
  scope_label = f"ids={ids}" if ids else "all NULL hybrid rows"
  db = SessionLocal()
  try:
    before = _list_affected(db, ids)
    print(f"Affected portfolios ({scope_label}): {len(before)}")
    _print_table(before)

    if args.dry_run:
      print("\n--dry-run: no changes written.")
      return 0

    if args.apply:
      writable = [r for r in before if r.get("parsed_oos_end")]
      skipped = [r for r in before if not r.get("parsed_oos_end")]
      if not writable:
        print("\nNothing to update (all rows have unparseable oos_end).")
        return 0
      print(f"\nApplying UPDATE to {len(writable)} row(s); skipping {len(skipped)} unparseable...")
      updated = 0
      for r in writable:
        stmt = text(
          "UPDATE portfolios "
          "   SET optimization_end_date = :oos_end "
          " WHERE id = :pid "
          f"   AND {_BASE_WHERE}"
        )
        result = db.execute(stmt, {"oos_end": r["parsed_oos_end"], "pid": r["id"]})
        updated += result.rowcount or 0
      db.commit()
      print(f"UPDATE complete. rows affected: {updated}")

      after = _list_affected(db, ids)
      print(f"\nRemaining affected after UPDATE: {len(after)}")
      if after:
        remaining_parseable = [r for r in after if r.get("parsed_oos_end")]
        if remaining_parseable:
          print("(unexpected — parseable rows still NULL)")
          _print_table(remaining_parseable)
          return 1
        print("(all remaining rows have unparseable oos_end — skipped by design)")
        _print_table(after)
      return 0
  finally:
    db.close()

  return 0


if __name__ == "__main__":
  sys.exit(main())
