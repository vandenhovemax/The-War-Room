"""Session Time Mask CRUD — named hour×day masks for optimizer pre-filtering."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import (
    BacktestRun,
    BacktestTrade,
    BatchBacktestRun,
    SessionTimeMask,
)

router = APIRouter()


# --- Request / Response schemas ---

class MaskCell(BaseModel):
    hour: int
    days: list[int]  # 0=Mon..6=Sun


class MaskCreateRequest(BaseModel):
    name: str
    description: str = ""
    mode: str = "include"  # "include" or "exclude"
    cells: list[MaskCell]


class MaskUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    mode: str | None = None
    cells: list[MaskCell] | None = None


class MaskFromHeatmapRequest(BaseModel):
    name: str
    description: str = ""
    batch_id: int
    start_date: str
    end_date: str
    min_profitable_symbol_pct: float = 50.0
    min_trades_per_cell: int = 20
    min_profit_factor: float = 1.0


class MaskPreviewRequest(BaseModel):
    batch_id: int
    mask_id: int
    date_to: str | None = None
    date_from: str | None = None
    n_free_params: int = 5


def _mask_to_dict(m: SessionTimeMask) -> dict:
    data = json.loads(m.mask_data)
    return {
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "mode": data.get("mode", "include"),
        "cells": data.get("cells", []),
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


# --- CRUD endpoints ---
# NOTE: Literal paths (/from-heatmap, /preview) MUST be registered before
# parameterised paths (/{mask_id}) so FastAPI matches them first.

@router.get("")
def list_masks(db: Session = Depends(get_db)):
    masks = db.query(SessionTimeMask).order_by(SessionTimeMask.created_at.desc()).all()
    return [_mask_to_dict(m) for m in masks]


@router.post("")
def create_mask(req: MaskCreateRequest, db: Session = Depends(get_db)):
    if not req.name.strip():
        raise HTTPException(400, "Mask name is required")
    existing = db.query(SessionTimeMask).filter(SessionTimeMask.name == req.name.strip()).first()
    if existing:
        raise HTTPException(400, f"A mask named '{req.name}' already exists")

    mask_data = json.dumps({
        "mode": req.mode,
        "cells": [c.model_dump() for c in req.cells],
    })
    m = SessionTimeMask(name=req.name.strip(), description=req.description, mask_data=mask_data)
    db.add(m)
    db.commit()
    db.refresh(m)
    return _mask_to_dict(m)


# --- From-heatmap convenience endpoint ---

@router.post("/from-heatmap")
def create_from_heatmap(req: MaskFromHeatmapRequest, db: Session = Depends(get_db)):
    """Auto-generate a mask from session intel thresholds."""
    from backend.api.routes.session_analysis import _cell_metrics

    # Validate batch
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == req.batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch not found")

    # Parse dates
    try:
        dt_start = datetime.strptime(req.start_date, "%Y-%m-%d")
        dt_end = datetime.strptime(req.end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(400, "Invalid date format — use YYYY-MM-DD")

    # Load trades
    run_ids = [
        r.id for r in
        db.query(BacktestRun.id)
        .filter(BacktestRun.batch_id == req.batch_id, BacktestRun.status == "completed")
        .all()
    ]
    raw_trades = (
        db.query(BacktestTrade)
        .filter(BacktestTrade.run_id.in_(run_ids))
        .all()
    )

    # Filter by date and convert
    trades: list[dict] = []
    for t in raw_trades:
        if t.entry_time is None or t.entry_time < dt_start or t.entry_time > dt_end:
            continue
        trades.append({
            "net_profit": t.net_profit or 0,
            "symbol": t.symbol,
            "side": t.side,
            "mae": t.mae,
            "mfe": t.mfe,
            "hour": t.entry_time.hour,
            "weekday": t.entry_time.weekday(),
        })

    # Group into hour×day grid and check thresholds
    grid: dict[tuple[int, int], list[dict]] = {}
    for h in range(24):
        for d in range(7):
            grid[(h, d)] = []
    for t in trades:
        grid[(t["hour"], t["weekday"])].append(t)

    included_cells: list[dict] = []
    # Group by hour
    hour_cells: dict[int, list[int]] = {}
    for h in range(24):
        for d in range(7):
            cell_trades = grid[(h, d)]
            metrics = _cell_metrics(cell_trades, req.min_trades_per_cell)
            if (
                metrics["trade_count"] >= req.min_trades_per_cell
                and metrics.get("profit_factor", 0) >= req.min_profit_factor
                and metrics.get("profitable_symbol_pct", 0) >= req.min_profitable_symbol_pct
            ):
                if h not in hour_cells:
                    hour_cells[h] = []
                hour_cells[h].append(d)

    for h, days in sorted(hour_cells.items()):
        included_cells.append({"hour": h, "days": sorted(days)})

    if not included_cells:
        raise HTTPException(400, "No cells meet the specified thresholds. Try relaxing the criteria.")

    # Check for duplicate name
    existing = db.query(SessionTimeMask).filter(SessionTimeMask.name == req.name.strip()).first()
    if existing:
        raise HTTPException(400, f"A mask named '{req.name}' already exists")

    mask_data = json.dumps({"mode": "include", "cells": included_cells})
    m = SessionTimeMask(name=req.name.strip(), description=req.description, mask_data=mask_data)
    db.add(m)
    db.commit()
    db.refresh(m)
    return _mask_to_dict(m)


# --- Preview endpoint (powers optimizer trade-count impact) ---

@router.post("/preview")
def preview_mask_impact(req: MaskPreviewRequest, db: Session = Depends(get_db)):
    """Preview how a mask would affect the trade pool for a batch."""
    from backend.analysis.session_mask_filter import apply_session_mask, validate_mask_significance

    mask = db.query(SessionTimeMask).filter(SessionTimeMask.id == req.mask_id).first()
    if not mask:
        raise HTTPException(404, "Mask not found")

    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == req.batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch not found")

    # Load trades
    run_ids = [
        r.id for r in
        db.query(BacktestRun.id)
        .filter(BacktestRun.batch_id == req.batch_id, BacktestRun.status == "completed")
        .all()
    ]
    query = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids))
    if req.date_to:
        from datetime import timedelta
        cutoff = datetime.strptime(req.date_to, "%Y-%m-%d") + timedelta(days=1)
        query = query.filter(BacktestTrade.entry_time < cutoff)
    if req.date_from:
        from_dt = datetime.strptime(req.date_from, "%Y-%m-%d")
        query = query.filter(BacktestTrade.entry_time >= from_dt)
    trades = query.all()

    mask_data = json.loads(mask.mask_data)
    filtered = apply_session_mask(trades, mask_data)
    validation = validate_mask_significance(
        trades, filtered, n_free_params=req.n_free_params,
    )

    return {
        "mask_name": mask.name,
        **validation,
    }


# --- Parameterised CRUD (MUST come after literal paths) ---

@router.get("/{mask_id}")
def get_mask(mask_id: int, db: Session = Depends(get_db)):
    m = db.query(SessionTimeMask).filter(SessionTimeMask.id == mask_id).first()
    if not m:
        raise HTTPException(404, "Mask not found")
    return _mask_to_dict(m)


@router.put("/{mask_id}")
def update_mask(mask_id: int, req: MaskUpdateRequest, db: Session = Depends(get_db)):
    m = db.query(SessionTimeMask).filter(SessionTimeMask.id == mask_id).first()
    if not m:
        raise HTTPException(404, "Mask not found")

    if req.name is not None:
        dup = db.query(SessionTimeMask).filter(
            SessionTimeMask.name == req.name.strip(), SessionTimeMask.id != mask_id
        ).first()
        if dup:
            raise HTTPException(400, f"A mask named '{req.name}' already exists")
        m.name = req.name.strip()

    if req.description is not None:
        m.description = req.description

    if req.mode is not None or req.cells is not None:
        current = json.loads(m.mask_data)
        if req.mode is not None:
            current["mode"] = req.mode
        if req.cells is not None:
            current["cells"] = [c.model_dump() for c in req.cells]
        m.mask_data = json.dumps(current)

    db.commit()
    db.refresh(m)
    return _mask_to_dict(m)


@router.delete("/{mask_id}")
def delete_mask(mask_id: int, db: Session = Depends(get_db)):
    m = db.query(SessionTimeMask).filter(SessionTimeMask.id == mask_id).first()
    if not m:
        raise HTTPException(404, "Mask not found")
    db.delete(m)
    db.commit()
    return {"ok": True}
