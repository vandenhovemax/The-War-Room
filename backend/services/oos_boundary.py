"""L3.1 — OOS boundary detector (Ship 2, daily).

Purpose:
  Every day at 01:00 NYT, scan active portfolios. For each one past its
  OOS end date with no pending RebalanceProposal, enqueue a Step-1 re-run
  (step1_refresh_worker.rebuild_and_propose). The worker writes a
  proposal row — never auto-applies.

OOS end is computed as:
    oos_end = portfolio.optimization_end_date
              + timedelta(days=source_strategies.oos_length_days or 30)

Ship 2 scope (plan §L3):
  * Applies to BOTH hybrid and non_hybrid portfolios. At OOS boundary,
    even a hybrid portfolio needs a fresh Step-1 because the survivor
    basket universe itself may have shifted.
  * Never auto-applies — proposals always require human approval.
  * Idempotent: a portfolio with a pending proposal is skipped until
    that proposal is approved/rejected/expired.

Additive-only (RULE 9): reads Portfolio, writes RebalanceProposal.
Does not mutate any Portfolio / PortfolioAsset / KellyAllocation row.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from backend.db.database import SessionLocal
from backend.db.models import Portfolio, RebalanceProposal

log = logging.getLogger(__name__)


# Default when source_strategies.oos_length_days is missing. Matches the
# plan's Decision Point #2 fallback.
_DEFAULT_OOS_DAYS = 30


def _oos_length_days(src: dict) -> int:
  """Pull OOS length from source_strategies, falling back to 30 days."""
  try:
    val = int(src.get("oos_length_days") or _DEFAULT_OOS_DAYS)
    return val if val > 0 else _DEFAULT_OOS_DAYS
  except (TypeError, ValueError):
    return _DEFAULT_OOS_DAYS


def _parse_source(p: Portfolio) -> dict:
  try:
    return json.loads(p.source_strategies or "{}") or {}
  except Exception:
    return {}


def compute_oos_end(p: Portfolio) -> datetime | None:
  """Return the portfolio's OOS end datetime (naive, UTC-ish) or None if
  we can't determine it. Callers treat None as 'not a boundary candidate'.
  """
  if p.optimization_end_date is None:
    return None
  src = _parse_source(p)
  days = _oos_length_days(src)
  base = p.optimization_end_date
  # Strip tz if present — DB stores naive UTC.
  if base.tzinfo is not None:
    base = base.replace(tzinfo=None)
  return base + timedelta(days=days)


def _has_pending_proposal(db, portfolio_id: int) -> bool:
  q = (
    db.query(RebalanceProposal)
    .filter(
      RebalanceProposal.portfolio_id == portfolio_id,
      RebalanceProposal.status == "pending",
    )
  )
  return db.query(q.exists()).scalar() is True


def _eligible_portfolios(db) -> list[Portfolio]:
  """Active, non-deleted portfolios of any mode. Paper + real only —
  saved-but-inactive portfolios never need boundary rotation since they
  aren't live-trading."""
  return (
    db.query(Portfolio)
    .filter(
      Portfolio.is_active == True,  # noqa: E712
      Portfolio.is_deleted == False,  # noqa: E712
      Portfolio.portfolio_type.in_(["paper", "real"]),
    )
    .all()
  )


def check_and_enqueue(now: datetime | None = None) -> dict:
  """Daily boundary scan.

  Args:
    now: Override for testing. Defaults to utcnow().

  Returns: {
    scanned: int,        # total portfolios considered
    past_boundary: int,  # how many were past oos_end
    enqueued: int,       # how many proposals were actually created
    skipped_pending: int,  # already have a pending proposal
    results: [{portfolio_id, oos_end, proposal_id|None, reason}, ...],
  }

  Never raises — logs and moves on for each portfolio.
  """
  when = now or datetime.now(timezone.utc).replace(tzinfo=None)
  report: dict = {
    "scanned": 0,
    "past_boundary": 0,
    "enqueued": 0,
    "skipped_pending": 0,
    "results": [],
  }

  db = SessionLocal()
  try:
    portfolios = _eligible_portfolios(db)
    report["scanned"] = len(portfolios)

    # Defer import to break a circular dep — worker imports this module.
    from backend.services.step1_refresh_worker import rebuild_and_propose

    for p in portfolios:
      try:
        oos_end = compute_oos_end(p)
        if oos_end is None or when <= oos_end:
          continue
        report["past_boundary"] += 1

        if _has_pending_proposal(db, p.id):
          report["skipped_pending"] += 1
          report["results"].append({
            "portfolio_id": p.id,
            "oos_end": oos_end.isoformat(),
            "proposal_id": None,
            "reason": "already_pending",
          })
          continue

        res = rebuild_and_propose(p.id, trigger_type="oos_boundary", db=db)
        if res.get("ok") and res.get("proposal_id") is not None:
          report["enqueued"] += 1
          report["results"].append({
            "portfolio_id": p.id,
            "oos_end": oos_end.isoformat(),
            "proposal_id": res.get("proposal_id"),
            "reason": "proposed",
          })
        else:
          report["results"].append({
            "portfolio_id": p.id,
            "oos_end": oos_end.isoformat(),
            "proposal_id": None,
            "reason": res.get("reason", "worker_failed"),
          })
      except Exception as e:
        log.warning("oos_boundary: portfolio %d crashed: %s", p.id, e)
        report["results"].append({
          "portfolio_id": p.id,
          "oos_end": None,
          "proposal_id": None,
          "reason": f"exc_{type(e).__name__}",
        })

    log.info(
      "oos_boundary: scanned=%d past_boundary=%d enqueued=%d skipped_pending=%d",
      report["scanned"], report["past_boundary"],
      report["enqueued"], report["skipped_pending"],
    )
    return report
  finally:
    db.close()
