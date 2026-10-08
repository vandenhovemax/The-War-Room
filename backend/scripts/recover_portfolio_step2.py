"""One-shot recovery script — restore save-time step2_result on portfolios
damaged by the pre-A3 overwrite bug.

Before A3 (2026-04-24), `hybrid_portfolio_refresh.py` wholesale-overwrote
`source_strategies.step2_result` on every nightly refresh. When A2 unblocked
persistence, the very next refresh on P23/P24 replaced their 214-record
multi-year save-time matrix with a 1-record single-month cold-start output.

Recovery source: `wfo_runs.optimizer_settings.step2_result` — the WFAS run
that originally produced the portfolio preserves its full step2 output
untouched. We copy that blob back into `portfolios.source_strategies.step2_result`.

This script is additive only — it does NOT touch `refresh_step2_result`.
After recovery, the next L2 refresh will populate `refresh_step2_result`
and `get_effective_step2()` (in `backend/analysis/portfolio_wfo_replay.py`)
will merge both on read so downstream views see the full union.

Usage:
  # Dry run (default): prints what would be written, does not commit.
  python -m backend.scripts.recover_portfolio_step2 --ids 23,24 --dry-run

  # Live run: commits the restore.
  python -m backend.scripts.recover_portfolio_step2 --ids 23,24

  # Explicit --run-ids override (use when portfolio name doesn't match
  # "Run <N>" — e.g. renamed portfolios).
  python -m backend.scripts.recover_portfolio_step2 --ids 23,24 --run-ids 366,360

Idempotent: running twice produces the same state — the save-time blob
from wfo_runs is the deterministic source of truth.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys

from backend.db.database import SessionLocal
from backend.db.models import Portfolio, WFORun


log = logging.getLogger(__name__)


_RUN_NAME_RE = re.compile(r"^Run (\d+)$")


class RecoveryError(Exception):
  pass


def _parse_run_id_from_name(name: str | None) -> int | None:
  if not name:
    return None
  m = _RUN_NAME_RE.match(name.strip())
  if not m:
    return None
  return int(m.group(1))


def _resolve_run_id(portfolio: Portfolio, run_override: int | None) -> int:
  """Resolve the WFORun.id for a portfolio.

  Primary: parse "Run <N>" from portfolio.name.
  Fallback: explicit --run-ids override (same index as --ids).
  Error when neither works — recovery must never silently guess.
  """
  if run_override is not None:
    return run_override
  parsed = _parse_run_id_from_name(portfolio.name)
  if parsed is not None:
    return parsed
  raise RecoveryError(
    f"Portfolio {portfolio.id} name={portfolio.name!r} does not match "
    f"'Run <N>' pattern. Pass --run-ids to specify the source WFORun "
    f"explicitly."
  )


def _extract_save_time_step2(wfo_run: WFORun) -> dict:
  """Pull the save-time step2_result blob from wfo_runs.optimizer_settings.

  Mirrors the exact keys copied by portfolio_analysis.backfill_step2...
  so recovery output matches the legacy backfill endpoint byte-for-byte.
  """
  opt_settings = json.loads(wfo_run.optimizer_settings or "{}")
  step2 = opt_settings.get("step2_result")
  if not isinstance(step2, dict):
    raise RecoveryError(
      f"WFORun {wfo_run.id}: optimizer_settings.step2_result is not a dict "
      f"(got {type(step2).__name__}) — cannot recover from this run."
    )
  recs = step2.get("selected_records") or []
  if not recs:
    raise RecoveryError(
      f"WFORun {wfo_run.id}: step2_result.selected_records is empty — "
      f"nothing to restore. This run may pre-date step2 persistence."
    )
  restored = {
    k: step2[k] for k in (
      "selected_records",
      "aggregate_monthly_matrix",
      "aggregate_equity_curve",
      "aggregate_breakdown",
      "cumulative_metrics",
    ) if step2.get(k) is not None
  }
  return restored


def recover_one(
  db, portfolio_id: int, run_override: int | None, *, dry_run: bool,
) -> dict:
  """Recover a single portfolio. Returns a report dict for the caller to
  print. Does NOT touch `refresh_step2_result` — post-A3 nightly refresh
  writes there and the read path merges both.
  """
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise RecoveryError(f"Portfolio {portfolio_id} not found")

  run_id = _resolve_run_id(p, run_override)
  wfo_run = db.query(WFORun).filter(WFORun.id == run_id).first()
  if not wfo_run:
    raise RecoveryError(
      f"WFORun {run_id} (for portfolio {portfolio_id} name={p.name!r}) "
      f"not found"
    )

  restored = _extract_save_time_step2(wfo_run)
  restored_records = len(restored.get("selected_records") or [])
  restored_years = sorted(
    ((restored.get("aggregate_monthly_matrix") or {}).get("matrix") or {}).keys()
  )

  # Inspect current portfolio state for the report.
  cur_settings = json.loads(p.source_strategies) if p.source_strategies else {}
  cur_step2 = cur_settings.get("step2_result") or {}
  cur_records = len(cur_step2.get("selected_records") or [])
  cur_years = sorted(
    ((cur_step2.get("aggregate_monthly_matrix") or {}).get("matrix") or {}).keys()
  )

  report = {
    "portfolio_id": p.id,
    "portfolio_name": p.name,
    "source_wfo_run_id": run_id,
    "current_records": cur_records,
    "current_years": cur_years,
    "restored_records": restored_records,
    "restored_years": restored_years,
    "refresh_step2_result_present": "refresh_step2_result" in cur_settings,
    "dry_run": dry_run,
    "committed": False,
  }

  if dry_run:
    return report

  cur_settings["step2_result"] = restored
  p.source_strategies = json.dumps(cur_settings)
  db.commit()
  report["committed"] = True
  return report


def _parse_comma_ints(s: str, field_name: str) -> list[int]:
  try:
    return [int(x.strip()) for x in s.split(",") if x.strip()]
  except ValueError as e:
    raise RecoveryError(f"{field_name}: could not parse as comma-separated ints: {e}")


def _run_cli(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Recover save-time step2_result for portfolios damaged by the "
                "pre-A3 overwrite bug.",
  )
  parser.add_argument(
    "--ids", required=True,
    help="Comma-separated portfolio IDs to recover (e.g. '23,24')",
  )
  parser.add_argument(
    "--run-ids", default=None,
    help="Optional comma-separated WFORun IDs, same length and order as --ids. "
         "Use when portfolio name does not match 'Run <N>'.",
  )
  parser.add_argument(
    "--dry-run", action="store_true",
    help="Print what would be written without committing. Recommended first pass.",
  )
  args = parser.parse_args(argv)

  portfolio_ids = _parse_comma_ints(args.ids, "--ids")
  run_overrides: list[int | None]
  if args.run_ids:
    runs = _parse_comma_ints(args.run_ids, "--run-ids")
    if len(runs) != len(portfolio_ids):
      raise RecoveryError(
        f"--run-ids has {len(runs)} entries but --ids has {len(portfolio_ids)}; "
        f"they must be the same length"
      )
    run_overrides = list(runs)
  else:
    run_overrides = [None] * len(portfolio_ids)

  logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

  db = SessionLocal()
  try:
    any_fail = False
    for pid, run_override in zip(portfolio_ids, run_overrides):
      try:
        report = recover_one(db, pid, run_override, dry_run=args.dry_run)
      except RecoveryError as e:
        any_fail = True
        print(f"[FAIL] portfolio_id={pid}: {e}", file=sys.stderr)
        continue
      tag = "DRY-RUN" if report["dry_run"] else "RESTORED"
      print(
        f"[{tag}] portfolio_id={report['portfolio_id']} "
        f"name={report['portfolio_name']!r} "
        f"run_id={report['source_wfo_run_id']} "
        f"records {report['current_records']} -> {report['restored_records']} "
        f"years {report['current_years']} -> {report['restored_years']} "
        f"refresh_field_present={report['refresh_step2_result_present']}"
      )
    return 1 if any_fail else 0
  finally:
    db.close()


if __name__ == "__main__":
  sys.exit(_run_cli())
