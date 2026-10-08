"""Strategy config routes — parameter definitions + daughter configs."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import Strategy, StrategyConfig

router = APIRouter()


# --- Pydantic schemas ---

class ParameterDef(BaseModel):
  name: str
  type: str  # "int", "float", "bool", "string"
  default: int | float | bool | str
  min: float | None = None
  max: float | None = None
  step: float | None = None
  options: list[str] | None = None


class ParameterDefsPayload(BaseModel):
  parameters: list[ParameterDef]


class ConfigCreate(BaseModel):
  name: str
  description: str = ""
  tag: str | None = None
  parameter_values: dict[str, int | float | bool | str]


class ConfigUpdate(BaseModel):
  name: str | None = None
  description: str | None = None
  tag: str | None = None
  parameter_values: dict[str, int | float | bool | str] | None = None


# --- Parameter definitions (on the mother strategy) ---

@router.get("/{strategy_id}/parameters")
def get_parameters(strategy_id: int, db: Session = Depends(get_db)):
  s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  raw = json.loads(s.parameters) if s.parameters else []
  # Handle legacy format (empty dict vs empty list)
  if isinstance(raw, dict):
    return []
  return raw


@router.put("/{strategy_id}/parameters")
def update_parameters(
  strategy_id: int, payload: ParameterDefsPayload,
  db: Session = Depends(get_db),
):
  s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  valid_types = {"int", "float", "bool", "string"}
  for p in payload.parameters:
    if p.type not in valid_types:
      raise HTTPException(400, f"Invalid type '{p.type}' for '{p.name}'")
  s.parameters = json.dumps([p.model_dump() for p in payload.parameters])
  db.commit()
  return {"updated": True}


# --- Daughter configs ---

@router.get("/{strategy_id}/configs")
def list_configs(strategy_id: int, db: Session = Depends(get_db)):
  s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  configs = (
    db.query(StrategyConfig)
    .filter(StrategyConfig.strategy_id == strategy_id)
    .order_by(StrategyConfig.created_at.desc())
    .all()
  )
  return [_config_to_dict(c) for c in configs]


@router.post("/{strategy_id}/configs")
def create_config(
  strategy_id: int, req: ConfigCreate,
  db: Session = Depends(get_db),
):
  s = db.query(Strategy).filter(Strategy.id == strategy_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  # Check name uniqueness
  exists = (
    db.query(StrategyConfig)
    .filter(StrategyConfig.strategy_id == strategy_id, StrategyConfig.name == req.name)
    .first()
  )
  if exists:
    raise HTTPException(400, f"Config '{req.name}' already exists")
  config = StrategyConfig(
    strategy_id=strategy_id,
    name=req.name,
    description=req.description,
    tag=req.tag,
    parameter_values=json.dumps(req.parameter_values),
  )
  db.add(config)
  db.commit()
  db.refresh(config)
  return _config_to_dict(config)


@router.put("/{strategy_id}/configs/{config_id}")
def update_config(
  strategy_id: int, config_id: int, req: ConfigUpdate,
  db: Session = Depends(get_db),
):
  c = (
    db.query(StrategyConfig)
    .filter(StrategyConfig.id == config_id, StrategyConfig.strategy_id == strategy_id)
    .first()
  )
  if not c:
    raise HTTPException(status_code=404, detail="Config not found")
  if req.name is not None:
    c.name = req.name
  if req.description is not None:
    c.description = req.description
  if req.tag is not None:
    c.tag = req.tag if req.tag else None
  if req.parameter_values is not None:
    c.parameter_values = json.dumps(req.parameter_values)
  db.commit()
  db.refresh(c)
  return _config_to_dict(c)


@router.delete("/{strategy_id}/configs/{config_id}")
def delete_config(
  strategy_id: int, config_id: int,
  db: Session = Depends(get_db),
):
  c = (
    db.query(StrategyConfig)
    .filter(StrategyConfig.id == config_id, StrategyConfig.strategy_id == strategy_id)
    .first()
  )
  if not c:
    raise HTTPException(status_code=404, detail="Config not found")
  db.delete(c)
  db.commit()
  return {"deleted": True}


def _config_to_dict(c: StrategyConfig) -> dict:
  return {
    "id": c.id,
    "strategy_id": c.strategy_id,
    "name": c.name,
    "description": c.description,
    "tag": c.tag,
    "parameter_values": json.loads(c.parameter_values) if c.parameter_values else {},
    "created_at": c.created_at.isoformat() if c.created_at else None,
    "updated_at": c.updated_at.isoformat() if c.updated_at else None,
  }
