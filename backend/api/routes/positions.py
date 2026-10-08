"""Position routes."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import Position

router = APIRouter()


@router.get("")
def list_positions(db: Session = Depends(get_db)):
  positions = db.query(Position).filter(Position.is_open).all()
  return [
    {
      "id": p.id,
      "symbol": p.symbol,
      "side": p.side,
      "entry_price": p.entry_price,
      "quantity": p.quantity,
      "unrealized_pnl": p.unrealized_pnl,
      "leverage": p.leverage,
      "opened_at": p.opened_at.isoformat() if p.opened_at else None,
    }
    for p in positions
  ]


@router.get("/count")
def position_count(db: Session = Depends(get_db)):
  return {"count": db.query(Position).filter(Position.is_open).count()}
