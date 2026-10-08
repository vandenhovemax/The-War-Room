"""L3.3 — Rebalance proposals API (Ship 2).

Endpoints:
  GET  /api/rebalance/proposals                   — list (filter by status, portfolio)
  GET  /api/rebalance/proposals/{id}              — detail
  POST /api/rebalance/proposals/{id}/approve      — approve → fork new Portfolio row
  POST /api/rebalance/proposals/{id}/reject       — reject + reason
  POST /api/rebalance/portfolios/{pid}/propose    — manual 'Force Rebalance' button

Approval semantics (plan §L3.3):
  * Writes a NEW Portfolio row with the proposed basket (not a swap in-place).
  * Archives (is_active=False) the old Portfolio but does NOT delete — the
    old KellyAllocation + PortfolioAsset rows stay intact for audit.
  * Resets optimization_end_date to now() so the OOS clock restarts.
  * The live executor picks up the new portfolio on its next scan cycle.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import (
  LiveEvent,
  Portfolio,
  PortfolioAsset,
  RebalanceProposal,
)

log = logging.getLogger(__name__)
router = APIRouter()


# ── Pydantic ────────────────────────────────────────────────────

class RejectBody(BaseModel):
  reason: str | None = None
  reviewed_by: str | None = None


class ApproveBody(BaseModel):
  reviewed_by: str | None = None


def _serialize(p: RebalanceProposal) -> dict:
  def _loadj(s: str | None, default):
    try:
      return json.loads(s or "")
    except Exception:
      return default

  return {
    "id": p.id,
    "portfolio_id": p.portfolio_id,
    "trigger_type": p.trigger_type,
    "status": p.status,
    "created_at": p.created_at.isoformat() if p.created_at else None,
    "current_basket": _loadj(p.current_basket_json, []),
    "proposed_basket": _loadj(p.proposed_basket_json, []),
    "diff": _loadj(p.diff_json, {}),
    "metrics_preview": _loadj(p.metrics_preview_json, {}),
    "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
    "reviewed_by": p.reviewed_by,
    "resulting_portfolio_id": p.resulting_portfolio_id,
  }


# ── List + detail ───────────────────────────────────────────────

@router.get("/proposals")
def list_proposals(
  status: str | None = None,
  portfolio_id: int | None = None,
  db: Session = Depends(get_db),
):
  q = db.query(RebalanceProposal)
  if status:
    q = q.filter(RebalanceProposal.status == status)
  if portfolio_id:
    q = q.filter(RebalanceProposal.portfolio_id == portfolio_id)
  rows = q.order_by(RebalanceProposal.created_at.desc()).limit(200).all()
  return {"proposals": [_serialize(r) for r in rows], "count": len(rows)}


@router.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: int, db: Session = Depends(get_db)):
  p = db.query(RebalanceProposal).filter(RebalanceProposal.id == proposal_id).first()
  if p is None:
    raise HTTPException(status_code=404, detail="proposal_not_found")
  return _serialize(p)


# ── Reject ──────────────────────────────────────────────────────

@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(
  proposal_id: int,
  body: RejectBody,
  db: Session = Depends(get_db),
):
  p = db.query(RebalanceProposal).filter(RebalanceProposal.id == proposal_id).first()
  if p is None:
    raise HTTPException(status_code=404, detail="proposal_not_found")
  if p.status != "pending":
    raise HTTPException(status_code=409, detail=f"proposal_not_pending (current={p.status})")

  p.status = "rejected"
  p.reviewed_at = datetime.now(timezone.utc)
  p.reviewed_by = body.reviewed_by or "user"
  db.commit()

  db.add(LiveEvent(
    portfolio_id=p.portfolio_id,
    event_type="rebalance_rejected",
    details=json.dumps({
      "proposal_id": p.id,
      "reason": body.reason,
      "reviewed_by": p.reviewed_by,
    }),
  ))
  db.commit()

  return {"ok": True, "proposal": _serialize(p)}


# ── Approve (forks a new portfolio) ────────────────────────────

def _parse_asset_key(key: str) -> tuple[str, str]:
  """Inverse of step1_refresh_worker._asset_key.
  '{SYMBOL}_{Long|Short}' → ('SYMBOL', 'long'|'short')
  """
  if key.endswith("_Long"):
    return key[:-5], "long"
  if key.endswith("_Short"):
    return key[:-6], "short"
  # Legacy fallback — treat as long.
  return key, "long"


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
  proposal_id: int,
  body: ApproveBody,
  db: Session = Depends(get_db),
):
  p = db.query(RebalanceProposal).filter(RebalanceProposal.id == proposal_id).first()
  if p is None:
    raise HTTPException(status_code=404, detail="proposal_not_found")
  if p.status != "pending":
    raise HTTPException(status_code=409, detail=f"proposal_not_pending (current={p.status})")

  old_portfolio = db.query(Portfolio).filter(Portfolio.id == p.portfolio_id).first()
  if old_portfolio is None:
    raise HTTPException(status_code=404, detail="source_portfolio_missing")

  try:
    proposed = json.loads(p.proposed_basket_json or "[]")
  except Exception:
    proposed = []

  # Build the new Portfolio row. Copies metadata; basket will come from
  # newly-inserted PortfolioAsset rows below.
  now = datetime.now(timezone.utc)
  new_portfolio = Portfolio(
    name=f"{old_portfolio.name} (rotated {now.strftime('%Y-%m-%d')})",
    source_file=old_portfolio.source_file,
    is_active=True,
    is_cross_strat=old_portfolio.is_cross_strat,
    source_strategies=old_portfolio.source_strategies,
    metrics_snapshot="{}",
    portfolio_type=old_portfolio.portfolio_type,
    source_portfolio_id=old_portfolio.id,
    activated_at=now,
    optimization_end_date=now,  # fresh OOS clock
    portfolio_source=old_portfolio.portfolio_source,
    description=f"Rotated from portfolio #{old_portfolio.id} via proposal #{p.id}",
    mode=old_portfolio.mode,
  )
  db.add(new_portfolio)
  db.flush()

  # Copy batch_id + config_name + timeframe from the matching old-basket
  # PortfolioAsset rows when the proposed key is "unchanged", so the
  # new portfolio points at the same source batches. For "added" keys
  # we fall back to whatever old asset shares the same symbol/side —
  # this covers the common Step-2 rotation case where the batch_id carries
  # forward. Truly new symbols not previously in the basket end up with
  # batch_id=None and the user fills it in later (or a secondary proposal
  # round addresses them).
  old_assets = {
    (a.symbol.upper(), (a.trade_side or "").lower()): a
    for a in old_portfolio.assets
  }
  for key in proposed:
    sym, side = _parse_asset_key(key)
    src = old_assets.get((sym.upper(), side.lower()))
    db.add(PortfolioAsset(
      portfolio_id=new_portfolio.id,
      symbol=sym.upper(),
      trade_side=side,
      strategy_name=src.strategy_name if src else "",
      is_active=True,
      raw_name=src.raw_name if src else f"{sym}_{side}",
      batch_id=src.batch_id if src else None,
      config_name=src.config_name if src else None,
      timeframe=src.timeframe if src else None,
      risk_mode=src.risk_mode if src else None,
    ))

  # Archive the old portfolio (not deleted — audit trail preserved).
  # Also deactivate each PortfolioAsset row so any downstream query
  # that filters on PortfolioAsset.is_active alone (without joining
  # Portfolio.is_active) doesn't silently pick up the old basket.
  old_portfolio.is_active = False
  old_portfolio.deactivated_at = now
  for _asset in old_portfolio.assets:
    _asset.is_active = False

  # Finalize proposal.
  p.status = "approved"
  p.reviewed_at = now
  p.reviewed_by = body.reviewed_by or "user"
  p.resulting_portfolio_id = new_portfolio.id

  try:
    diff_parsed = json.loads(p.diff_json or "{}")
  except Exception:
    diff_parsed = {}
  db.add(LiveEvent(
    portfolio_id=p.portfolio_id,
    event_type="rebalance_approved",
    details=json.dumps({
      "proposal_id": p.id,
      "old_portfolio_id": old_portfolio.id,
      "new_portfolio_id": new_portfolio.id,
      "added": diff_parsed.get("added", []),
      "removed": diff_parsed.get("removed", []),
      "unchanged_count": len(diff_parsed.get("unchanged", [])),
    }),
  ))

  db.commit()
  db.refresh(p)
  return {"ok": True, "proposal": _serialize(p), "new_portfolio_id": new_portfolio.id}


# ── Manual 'Force Rebalance' trigger ────────────────────────────

@router.post("/portfolios/{portfolio_id}/propose")
def force_propose(portfolio_id: int, db: Session = Depends(get_db)):
  """UI button — run the Step-1 refresh worker immediately."""
  from backend.services.step1_refresh_worker import rebuild_and_propose
  res = rebuild_and_propose(portfolio_id, trigger_type="manual", db=db)
  if not res.get("ok"):
    if res.get("reason") == "already_pending":
      raise HTTPException(status_code=409, detail="already_pending")
    raise HTTPException(status_code=400, detail=res.get("reason") or "worker_failed")
  return res
