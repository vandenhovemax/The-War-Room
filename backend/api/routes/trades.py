"""Trade history routes."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import Trade

router = APIRouter()


@router.get("")
def list_trades(limit: int = 50, include_test: bool = False, db: Session = Depends(get_db)):
  query = db.query(Trade)
  if not include_test:
    query = query.filter(Trade.is_test != True)
  trades = query.order_by(Trade.closed_at.desc()).limit(limit).all()
  return [
    {
      "id": t.id,
      "symbol": t.symbol,
      "side": t.side,
      "entry_price": t.entry_price,
      "exit_price": t.exit_price,
      "realized_pnl": t.realized_pnl,
      "close_reason": t.close_reason,
      "closed_at": t.closed_at.isoformat() if t.closed_at else None,
      "is_test": bool(t.is_test),
    }
    for t in trades
  ]


@router.get("/stats")
def trade_stats(db: Session = Depends(get_db)):
  base = db.query(Trade).filter(Trade.is_test != True)
  total = base.count()
  total_pnl = base.with_entities(func.sum(Trade.realized_pnl)).scalar() or 0.0
  wins = base.filter(Trade.realized_pnl > 0).count()
  return {
    "total_trades": total,
    "total_pnl": round(total_pnl, 2),
    "win_count": wins,
    "loss_count": total - wins,
    "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
  }


class BulkMarkTestRequest(BaseModel):
  trade_ids: list[int] | None = None
  date_before: str | None = None
  is_test: bool = True


@router.put("/bulk-mark-test")
def bulk_mark_test(req: BulkMarkTestRequest, db: Session = Depends(get_db)):
  """Bulk mark/unmark engine trades as testing."""
  query = db.query(Trade)
  if req.trade_ids:
    query = query.filter(Trade.id.in_(req.trade_ids))
  elif req.date_before:
    try:
      cutoff = datetime.fromisoformat(req.date_before)
      query = query.filter(Trade.closed_at <= cutoff)
    except ValueError:
      raise HTTPException(400, "Invalid date_before format")
  else:
    raise HTTPException(400, "Provide trade_ids or date_before")
  trades = query.all()
  count = 0
  for t in trades:
    t.is_test = req.is_test
    count += 1
  db.commit()
  return {"updated": count, "is_test": req.is_test}


class BulkDeleteTradesRequest(BaseModel):
  trade_ids: list[int]


@router.post("/bulk-delete")
def bulk_delete_trades(req: BulkDeleteTradesRequest, db: Session = Depends(get_db)):
  """Bulk delete engine trades by IDs."""
  count = db.query(Trade).filter(Trade.id.in_(req.trade_ids)).delete(synchronize_session=False)
  db.commit()
  return {"deleted": count}
