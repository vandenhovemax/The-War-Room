"""Strategy management routes — CRUD."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from backend.db.database import get_db
from backend.db.models import Strategy
from backend.utils.param_extractor import extract_from_python

router = APIRouter()


class PythonStrategyCreate(BaseModel):
  name: str
  description: str = ""
  python_source: str
  pine_source: str = ""
  parameters: list[dict] | None = None


@router.get("")
def list_strategies(db: Session = Depends(get_db)):
  strategies = db.query(Strategy).options(selectinload(Strategy.configs)).filter(Strategy.is_deleted != True).order_by(Strategy.created_at.desc()).all()
  result = []
  for s in strategies:
    raw = json.loads(s.parameters) if s.parameters else []
    param_count = len(raw) if isinstance(raw, list) else 0
    result.append({
      "id": s.id,
      "name": s.name,
      "description": s.description,
      "source_type": s.source_type,
      "has_pine": bool(s.pine_source),
      "has_python": bool(s.python_source),
      "param_count": param_count,
      "config_count": len(s.configs) if s.configs else 0,
      "created_at": s.created_at.isoformat() if s.created_at else None,
      "category": s.category,
    })
  return result


@router.get("/count")
def strategy_count(db: Session = Depends(get_db)):
  return {"count": db.query(Strategy).count()}


@router.get("/{strategy_id}")
def get_strategy(strategy_id: int, db: Session = Depends(get_db)):
  s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  return {
    "id": s.id,
    "name": s.name,
    "description": s.description,
    "source_type": s.source_type,
    "pine_source": s.pine_source,
    "python_source": s.python_source,
    "parameters": json.loads(s.parameters) if s.parameters else {},
    "created_at": s.created_at.isoformat() if s.created_at else None,
    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
  }


@router.post("/python")
def create_python_strategy(req: PythonStrategyCreate, db: Session = Depends(get_db)):
  """Save an AI-generated Python strategy directly (no transpile needed)."""
  # Use provided params, or auto-extract from Python source
  params = req.parameters or extract_from_python(req.python_source)
  strategy = Strategy(
    name=req.name,
    description=req.description,
    source_type="ai_generated",
    pine_source=req.pine_source,
    python_source=req.python_source,
    parameters=json.dumps(params),
  )
  db.add(strategy)
  db.commit()
  db.refresh(strategy)

  return {
    "id": strategy.id,
    "name": strategy.name,
  }


class PythonStrategyUpdate(BaseModel):
  python_source: str
  re_extract_params: bool = True


@router.put("/{strategy_id}/source")
def update_strategy_source(strategy_id: int, req: PythonStrategyUpdate, db: Session = Depends(get_db)):
  """Update a strategy's Python source code and re-extract parameters."""
  s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  s.python_source = req.python_source
  if req.re_extract_params:
    params = extract_from_python(req.python_source)
    s.parameters = json.dumps(params)
  from datetime import datetime, timezone
  s.updated_at = datetime.now(timezone.utc)
  db.commit()
  db.refresh(s)
  raw = json.loads(s.parameters) if s.parameters else []
  return {
    "id": s.id,
    "name": s.name,
    "param_count": len(raw) if isinstance(raw, list) else 0,
    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
  }


class CategoryUpdate(BaseModel):
  category: str | None = None


@router.patch("/{strategy_id}/category")
def update_strategy_category(strategy_id: int, req: CategoryUpdate, db: Session = Depends(get_db)):
  """Set or clear a strategy's category (folder group)."""
  s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  s.category = req.category if req.category else None
  db.commit()
  return {"id": s.id, "category": s.category}


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: int, db: Session = Depends(get_db)):
  s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  from datetime import datetime, timezone
  s.is_deleted = True
  s.deleted_at = datetime.now(timezone.utc)
  db.commit()
  return {"deleted": True}
