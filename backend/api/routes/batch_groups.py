"""Batch Group CRUD — folder-style grouping for batch backtests."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import BatchBacktestRun, BatchGroup

router = APIRouter()


class GroupCreateRequest(BaseModel):
    name: str
    description: str = ""
    sort_order: int = 0
    timeframe: str | None = None
    bybit_limit: float | None = None
    max_margin_per_trade: float | None = None
    min_adv: float | None = None
    slippage_pct: float = 0.0
    fee_pct: float = 0.0
    pnl_multiplier: float = 1.0


class GroupUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    sort_order: int | None = None
    timeframe: str | None = None
    bybit_limit: float | None = None
    max_margin_per_trade: float | None = None
    min_adv: float | None = None
    slippage_pct: float | None = None
    fee_pct: float | None = None
    pnl_multiplier: float | None = None


class AssignBatchesRequest(BaseModel):
    batch_ids: list[int]
    group_id: int | None  # None = ungroup


def _group_to_dict(g: BatchGroup, batch_count: int = 0) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "sort_order": g.sort_order,
        "batch_count": batch_count,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "timeframe": getattr(g, "timeframe", None),
        "bybit_limit": getattr(g, "bybit_limit", None),
        "max_margin_per_trade": getattr(g, "max_margin_per_trade", None),
        "min_adv": getattr(g, "min_adv", None),
        "slippage_pct": getattr(g, "slippage_pct", 0.0),
        "fee_pct": getattr(g, "fee_pct", 0.0),
        "pnl_multiplier": getattr(g, "pnl_multiplier", 1.0),
    }


@router.get("")
def list_groups(db: Session = Depends(get_db)):
    groups = db.query(BatchGroup).order_by(BatchGroup.sort_order, BatchGroup.name).all()
    # Count batches per group
    counts: dict[int, int] = {}
    for g in groups:
        counts[g.id] = db.query(BatchBacktestRun).filter(
            BatchBacktestRun.group_id == g.id,
            BatchBacktestRun.is_deleted != True,
        ).count()
    return [_group_to_dict(g, batch_count=counts.get(g.id, 0)) for g in groups]


@router.post("")
def create_group(req: GroupCreateRequest, db: Session = Depends(get_db)):
    if not req.name.strip():
        raise HTTPException(400, "Group name is required")
    existing = db.query(BatchGroup).filter(BatchGroup.name == req.name.strip()).first()
    if existing:
        raise HTTPException(400, f"A group named '{req.name}' already exists")
    g = BatchGroup(
        name=req.name.strip(), description=req.description, sort_order=req.sort_order,
        timeframe=req.timeframe, bybit_limit=req.bybit_limit,
        max_margin_per_trade=req.max_margin_per_trade, min_adv=req.min_adv,
        slippage_pct=req.slippage_pct, fee_pct=req.fee_pct,
        pnl_multiplier=req.pnl_multiplier,
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return _group_to_dict(g)


# Literal path — MUST be before /{group_id} parameterised routes
@router.post("/assign")
def assign_batches(req: AssignBatchesRequest, db: Session = Depends(get_db)):
    """Assign one or more batches to a group (or ungroup with group_id=null)."""
    if req.group_id is not None:
        g = db.query(BatchGroup).filter(BatchGroup.id == req.group_id).first()
        if not g:
            raise HTTPException(404, "Group not found")
    db.query(BatchBacktestRun).filter(BatchBacktestRun.id.in_(req.batch_ids)).update(
        {"group_id": req.group_id}, synchronize_session="fetch"
    )
    db.commit()
    return {"ok": True, "updated": len(req.batch_ids)}


# --- Parameterised routes (MUST come after literal paths) ---

@router.put("/{group_id}")
def update_group(group_id: int, req: GroupUpdateRequest, db: Session = Depends(get_db)):
    g = db.query(BatchGroup).filter(BatchGroup.id == group_id).first()
    if not g:
        raise HTTPException(404, "Group not found")
    if req.name is not None:
        dup = db.query(BatchGroup).filter(
            BatchGroup.name == req.name.strip(), BatchGroup.id != group_id
        ).first()
        if dup:
            raise HTTPException(400, f"A group named '{req.name}' already exists")
        g.name = req.name.strip()
    if req.description is not None:
        g.description = req.description
    if req.sort_order is not None:
        g.sort_order = req.sort_order
    if req.timeframe is not None:
        g.timeframe = req.timeframe
    if req.bybit_limit is not None:
        g.bybit_limit = req.bybit_limit
    if req.max_margin_per_trade is not None:
        g.max_margin_per_trade = req.max_margin_per_trade
    if req.min_adv is not None:
        g.min_adv = req.min_adv
    if req.slippage_pct is not None:
        g.slippage_pct = req.slippage_pct
    if req.fee_pct is not None:
        g.fee_pct = req.fee_pct
    if req.pnl_multiplier is not None:
        g.pnl_multiplier = req.pnl_multiplier
    db.commit()
    db.refresh(g)
    return _group_to_dict(g)


@router.delete("/{group_id}")
def delete_group(group_id: int, db: Session = Depends(get_db)):
    g = db.query(BatchGroup).filter(BatchGroup.id == group_id).first()
    if not g:
        raise HTTPException(404, "Group not found")
    # Ungroup all batches in this group
    db.query(BatchBacktestRun).filter(BatchBacktestRun.group_id == group_id).update(
        {"group_id": None}, synchronize_session="fetch"
    )
    db.delete(g)
    db.commit()
    return {"ok": True}
