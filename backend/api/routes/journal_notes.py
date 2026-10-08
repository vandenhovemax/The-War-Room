"""Personal journal notes — CRUD, calendar summary, templates, image upload."""
import json
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.db.database import get_db
from backend.db.models import CustomTemplate, JournalNote, JournalTemplate, MonthlyObjective

router = APIRouter()
settings = get_settings()

ALLOWED_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB

DEFAULT_TEMPLATES: dict[str, str] = {
  "journal": "## What happened today\n\n## Observations\n\n## Notes\n",
  "recap": (
    "## Overview\n\n## What went well\n\n## Key trades\n\n"
    "## Lessons learned\n\n## System/code progress\n\n## Goals for next period\n"
  ),
  "todo": '[\n  {"text": "Task 1", "checked": false},\n  {"text": "Task 2", "checked": false}\n]',
}


# --- Request Schemas ---

class NoteCreate(BaseModel):
  note_type: str = "journal"
  title: str
  subject: str = ""
  content: str = ""
  note_date: str  # "YYYY-MM-DD"
  period_start: str | None = None
  period_end: str | None = None
  color: str = "indigo"
  images: list[str] = []
  tags: list[str] = []


class NoteUpdate(BaseModel):
  title: str | None = None
  subject: str | None = None
  content: str | None = None
  note_date: str | None = None
  note_type: str | None = None
  period_start: str | None = None
  period_end: str | None = None
  color: str | None = None
  images: list[str] | None = None
  tags: list[str] | None = None


class TemplateSave(BaseModel):
  content: str


# --- Helpers ---

def _note_to_dict(n: JournalNote) -> dict:
  return {
    "id": n.id,
    "note_type": n.note_type,
    "title": n.title,
    "subject": n.subject,
    "content": n.content,
    "note_date": n.note_date.isoformat() if n.note_date else None,
    "period_start": n.period_start.isoformat() if n.period_start else None,
    "period_end": n.period_end.isoformat() if n.period_end else None,
    "color": getattr(n, "color", "indigo") or "indigo",
    "images": json.loads(n.images) if n.images else [],
    "tags": json.loads(n.tags) if n.tags else [],
    "created_at": n.created_at.isoformat() if n.created_at else None,
    "updated_at": n.updated_at.isoformat() if n.updated_at else None,
  }


def _parse_date(s: str) -> date:
  return date.fromisoformat(s)


def _upload_dir() -> Path:
  p = Path(settings.upload_dir) / "journal"
  p.mkdir(parents=True, exist_ok=True)
  return p


# --- Notes CRUD ---

@router.get("/list")
def list_notes(
  year: int = Query(...),
  month: int = Query(...),
  db: Session = Depends(get_db),
):
  """List all notes for a given month."""
  start = date(year, month, 1)
  end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
  notes = (
    db.query(JournalNote)
    .filter(JournalNote.note_date >= start, JournalNote.note_date < end)
    .order_by(JournalNote.note_date.asc())
    .all()
  )
  return [_note_to_dict(n) for n in notes]


@router.get("/calendar-summary")
def calendar_summary(
  year: int = Query(...),
  month: int = Query(...),
  db: Session = Depends(get_db),
):
  """Day-level summary for calendar cells (note count + types per day)."""
  start = date(year, month, 1)
  end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
  notes = (
    db.query(JournalNote)
    .filter(JournalNote.note_date >= start, JournalNote.note_date < end)
    .all()
  )
  daily: dict[str, dict] = defaultdict(lambda: {"note_count": 0, "types": [], "colors": []})
  for n in notes:
    key = n.note_date.isoformat()
    daily[key]["note_count"] += 1
    daily[key]["types"].append(n.note_type)
    daily[key]["colors"].append(getattr(n, "color", "indigo") or "indigo")
  return [
    {"date": k, "note_count": v["note_count"], "types": v["types"], "colors": v["colors"]}
    for k, v in sorted(daily.items())
  ]


@router.get("/detail/{note_id}")
def get_note(note_id: int, db: Session = Depends(get_db)):
  """Get a single note."""
  note = db.query(JournalNote).filter(JournalNote.id == note_id).first()
  if not note:
    raise HTTPException(status_code=404, detail="Note not found")
  return _note_to_dict(note)


@router.post("")
def create_note(req: NoteCreate, db: Session = Depends(get_db)):
  """Create a new journal note."""
  note = JournalNote(
    note_type=req.note_type,
    title=req.title,
    subject=req.subject,
    content=req.content,
    note_date=_parse_date(req.note_date),
    period_start=_parse_date(req.period_start) if req.period_start else None,
    period_end=_parse_date(req.period_end) if req.period_end else None,
    color=req.color,
    images=json.dumps(req.images),
    tags=json.dumps(req.tags),
  )
  db.add(note)
  db.commit()
  db.refresh(note)
  return _note_to_dict(note)


@router.put("/{note_id}")
def update_note(note_id: int, req: NoteUpdate, db: Session = Depends(get_db)):
  """Update an existing note."""
  note = db.query(JournalNote).filter(JournalNote.id == note_id).first()
  if not note:
    raise HTTPException(status_code=404, detail="Note not found")
  if req.title is not None:
    note.title = req.title
  if req.subject is not None:
    note.subject = req.subject
  if req.content is not None:
    note.content = req.content
  if req.note_date is not None:
    note.note_date = _parse_date(req.note_date)
  if req.note_type is not None:
    note.note_type = req.note_type
  if req.period_start is not None:
    note.period_start = _parse_date(req.period_start)
  if req.period_end is not None:
    note.period_end = _parse_date(req.period_end)
  if req.color is not None:
    note.color = req.color
  if req.images is not None:
    note.images = json.dumps(req.images)
  if req.tags is not None:
    note.tags = json.dumps(req.tags)
  db.commit()
  db.refresh(note)
  return _note_to_dict(note)


@router.delete("/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
  """Delete a note."""
  note = db.query(JournalNote).filter(JournalNote.id == note_id).first()
  if not note:
    raise HTTPException(status_code=404, detail="Note not found")
  db.delete(note)
  db.commit()
  return {"deleted": True, "id": note_id}


# --- Templates ---

@router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
  """List all templates (returns defaults for missing types)."""
  saved = db.query(JournalTemplate).all()
  saved_map = {t.template_type: t.content for t in saved}
  result = []
  for ttype, default_content in DEFAULT_TEMPLATES.items():
    result.append({
      "template_type": ttype,
      "content": saved_map.get(ttype, default_content),
      "is_default": ttype not in saved_map,
    })
  return result


@router.get("/templates/{template_type}")
def get_template(template_type: str, db: Session = Depends(get_db)):
  """Get a single template (returns default if not saved)."""
  saved = (
    db.query(JournalTemplate)
    .filter(JournalTemplate.template_type == template_type)
    .first()
  )
  if saved:
    return {"template_type": template_type, "content": saved.content, "is_default": False}
  default = DEFAULT_TEMPLATES.get(template_type)
  if default is None:
    raise HTTPException(status_code=404, detail="Unknown template type")
  return {"template_type": template_type, "content": default, "is_default": True}


@router.put("/templates/{template_type}")
def save_template(
  template_type: str, req: TemplateSave, db: Session = Depends(get_db),
):
  """Create or update a template (upsert)."""
  if template_type not in DEFAULT_TEMPLATES:
    raise HTTPException(status_code=400, detail="Unknown template type")
  existing = (
    db.query(JournalTemplate)
    .filter(JournalTemplate.template_type == template_type)
    .first()
  )
  if existing:
    existing.content = req.content
  else:
    db.add(JournalTemplate(template_type=template_type, content=req.content))
  db.commit()
  return {"template_type": template_type, "saved": True}


# --- Image Upload ---

@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
  """Upload an image for journal notes. Max 5MB, image types only."""
  if not file.filename:
    raise HTTPException(status_code=400, detail="No filename provided")
  ext = Path(file.filename).suffix.lower()
  if ext not in ALLOWED_IMAGE_TYPES:
    raise HTTPException(status_code=400, detail=f"Invalid file type: {ext}")
  data = await file.read()
  if len(data) > MAX_IMAGE_SIZE:
    raise HTTPException(status_code=400, detail="File too large (max 5MB)")
  filename = f"{date.today().isoformat()}_{uuid.uuid4().hex[:8]}{ext}"
  dest = _upload_dir() / filename
  dest.write_bytes(data)
  return {"path": f"uploads/journal/{filename}", "filename": filename}


@router.get("/images/{filename}")
def serve_image(filename: str):
  """Serve an uploaded journal image."""
  path = _upload_dir() / filename
  if not path.exists():
    raise HTTPException(status_code=404, detail="Image not found")
  return FileResponse(path)


# --- Monthly Objectives ---

class ObjectiveCreate(BaseModel):
  title: str = "Objectives"
  content: str = ""
  checklist: list[dict] = []  # [{"text": "...", "checked": false, "completed_at": null}]
  period: str = "monthly"     # "daily" | "weekly" | "monthly"
  year: int | None = None     # Which month these objectives belong to
  month: int | None = None


class ObjectiveUpdate(BaseModel):
  title: str | None = None
  content: str | None = None
  checklist: list[dict] | None = None
  period: str | None = None
  is_active: bool | None = None
  sort_order: int | None = None


def _objective_to_dict(o: MonthlyObjective) -> dict:
  return {
    "id": o.id,
    "title": o.title,
    "content": o.content,
    "checklist": json.loads(o.checklist) if o.checklist else [],
    "period": getattr(o, "period", "monthly") or "monthly",
    "year": getattr(o, "obj_year", None),
    "month": getattr(o, "obj_month", None),
    "is_active": o.is_active,
    "sort_order": o.sort_order,
    "created_at": o.created_at.isoformat() if o.created_at else None,
    "updated_at": o.updated_at.isoformat() if o.updated_at else None,
  }


@router.get("/monthly-objectives")
def list_objectives(
  year: int = Query(...),
  month: int = Query(...),
  db: Session = Depends(get_db),
):
  """List active objectives for a specific month (also shows legacy rows with NULL year/month)."""
  from sqlalchemy import or_
  objectives = (
    db.query(MonthlyObjective)
    .filter(
      MonthlyObjective.is_active == True,
      or_(
        (MonthlyObjective.obj_year == year) & (MonthlyObjective.obj_month == month),
        (MonthlyObjective.obj_year == None) & (MonthlyObjective.obj_month == None),
      ),
    )
    .order_by(MonthlyObjective.sort_order.asc(), MonthlyObjective.id.asc())
    .all()
  )
  return [_objective_to_dict(o) for o in objectives]


@router.post("/monthly-objectives")
def create_objective(req: ObjectiveCreate, db: Session = Depends(get_db)):
  """Create a new objective (daily/weekly/monthly) for a specific month."""
  obj = MonthlyObjective(
    title=req.title,
    content=req.content,
    checklist=json.dumps(req.checklist),
    period=req.period,
    obj_year=req.year,
    obj_month=req.month,
  )
  db.add(obj)
  db.commit()
  db.refresh(obj)
  return _objective_to_dict(obj)


@router.put("/monthly-objectives/{obj_id}")
def update_objective(obj_id: int, req: ObjectiveUpdate, db: Session = Depends(get_db)):
  """Update an objective."""
  obj = db.query(MonthlyObjective).filter(MonthlyObjective.id == obj_id).first()
  if not obj:
    raise HTTPException(status_code=404, detail="Objective not found")
  if req.title is not None:
    obj.title = req.title
  if req.content is not None:
    obj.content = req.content
  if req.checklist is not None:
    obj.checklist = json.dumps(req.checklist)
  if req.period is not None:
    obj.period = req.period
  if req.is_active is not None:
    obj.is_active = req.is_active
  if req.sort_order is not None:
    obj.sort_order = req.sort_order
  db.commit()
  db.refresh(obj)
  return _objective_to_dict(obj)


@router.delete("/monthly-objectives/{obj_id}")
def delete_objective(obj_id: int, db: Session = Depends(get_db)):
  """Delete an objective."""
  obj = db.query(MonthlyObjective).filter(MonthlyObjective.id == obj_id).first()
  if not obj:
    raise HTTPException(status_code=404, detail="Objective not found")
  db.delete(obj)
  db.commit()
  return {"deleted": True, "id": obj_id}


# --- Custom Templates ---

class CustomTemplateCreate(BaseModel):
  name: str
  content: str = ""
  color: str = "indigo"


class CustomTemplateUpdate(BaseModel):
  name: str | None = None
  content: str | None = None
  color: str | None = None


def _custom_template_to_dict(t: CustomTemplate) -> dict:
  return {
    "id": t.id,
    "name": t.name,
    "content": t.content,
    "color": t.color or "indigo",
    "created_at": t.created_at.isoformat() if t.created_at else None,
    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
  }


@router.get("/custom-templates")
def list_custom_templates(db: Session = Depends(get_db)):
  """List all user-created templates."""
  templates = db.query(CustomTemplate).order_by(CustomTemplate.name.asc()).all()
  return [_custom_template_to_dict(t) for t in templates]


@router.post("/custom-templates")
def create_custom_template(req: CustomTemplateCreate, db: Session = Depends(get_db)):
  """Create a new custom template."""
  t = CustomTemplate(name=req.name, content=req.content, color=req.color)
  db.add(t)
  db.commit()
  db.refresh(t)
  return _custom_template_to_dict(t)


@router.put("/custom-templates/{template_id}")
def update_custom_template(
  template_id: int, req: CustomTemplateUpdate, db: Session = Depends(get_db),
):
  """Update a custom template."""
  t = db.query(CustomTemplate).filter(CustomTemplate.id == template_id).first()
  if not t:
    raise HTTPException(status_code=404, detail="Template not found")
  if req.name is not None:
    t.name = req.name
  if req.content is not None:
    t.content = req.content
  if req.color is not None:
    t.color = req.color
  db.commit()
  db.refresh(t)
  return _custom_template_to_dict(t)


@router.delete("/custom-templates/{template_id}")
def delete_custom_template(template_id: int, db: Session = Depends(get_db)):
  """Delete a custom template."""
  t = db.query(CustomTemplate).filter(CustomTemplate.id == template_id).first()
  if not t:
    raise HTTPException(status_code=404, detail="Template not found")
  db.delete(t)
  db.commit()
  return {"deleted": True, "id": template_id}
