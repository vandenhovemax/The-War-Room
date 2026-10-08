"""IS/OOS Data Integrity Agent — API endpoints.

On-demand validation of IS/OOS boundaries for any optimization context.
Includes both lightweight checks and comprehensive deep-audit.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.analysis.integrity_agent import (
  _build_report,
  check_date_to_present,
  check_portfolio_metrics,
  report_to_dict,
  run_integrity_audit,
)
from backend.analysis.oos_integrity import OOSIntegrityAgent
from backend.analysis.oos_integrity.audit_prompt import generate_audit_prompt
from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.report import report_to_json
from backend.db.database import get_db

router = APIRouter()


class ValidateRequest(BaseModel):
  path_type: str = "single"
  batch_id: int | None = None
  batch_ids: list[int] | None = None
  portfolio_id: int | None = None
  date_to: str | None = None
  date_from: str | None = None
  optimizer_settings: dict | None = None


@router.post("/validate")
def validate_integrity(
  req: ValidateRequest,
  db: Session = Depends(get_db),
):
  """Run IS/OOS integrity audit for a specific optimization context."""
  report = run_integrity_audit(
    path_type=req.path_type,
    batch_id=req.batch_id,
    batch_ids=req.batch_ids,
    portfolio_id=req.portfolio_id,
    date_to=req.date_to,
    date_from=req.date_from,
    optimizer_settings=req.optimizer_settings,
    db=db,
  )
  return report_to_dict(report)


@router.post("/validate-all-portfolios")
def validate_all_portfolios(db: Session = Depends(get_db)):
  """Batch-validate all saved portfolios for IS/OOS boundary compliance."""
  from backend.db.models import Portfolio
  portfolios = db.query(Portfolio).all()
  results = []
  for p in portfolios:
    checks = []
    checks.append(check_date_to_present(
      p.optimization_end_date, f"portfolio '{p.name}'",
    ))
    if p.optimization_end_date:
      checks.append(check_portfolio_metrics(p.id, db))
    report = _build_report(f"portfolio={p.id} name='{p.name}'", checks)
    results.append(report_to_dict(report))
  return {
    "total_portfolios": len(portfolios),
    "reports": results,
    "overall": "FAIL" if any(r["overall_status"] == "FAIL" for r in results)
               else "WARN" if any(r["overall_status"] == "WARN" for r in results)
               else "PASS",
  }


class DeepAuditRequest(BaseModel):
  batch_id: int | None = None
  batch_ids: list[int] | None = None
  portfolio_id: int | None = None
  date_to: str | None = None
  path_type: str = "single"
  embargo_days: int = 0


@router.post("/deep-audit")
def deep_audit(
  req: DeepAuditRequest,
  db: Session = Depends(get_db),
):
  """Run comprehensive OOS integrity audit with 13 scanners + validators.

  Returns full AuditReport with 0-100 integrity score, findings, and remediation.
  """
  import pandas as pd

  from backend.db.models import BacktestTrade, BatchBacktestRun, Portfolio

  is_trades: list = []
  oos_trades: list = []
  is_end = req.date_to
  batch_trades_map: dict | None = None

  # Load trades from batch or portfolio
  if req.portfolio_id:
    portfolio = db.query(Portfolio).filter(Portfolio.id == req.portfolio_id).first()
    if portfolio:
      is_end = is_end or portfolio.optimization_end_date

  if req.batch_id:
    from backend.db.models import BacktestRun
    batch_run = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == req.batch_id).first()
    if batch_run:
      run_ids = [r.id for r in db.query(BacktestRun).filter(
        BacktestRun.batch_id == batch_run.id, BacktestRun.status == "completed",
      ).all()]
      all_trades = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).all() if run_ids else []
      if is_end:
        boundary = pd.Timestamp(is_end)
        is_trades = [t for t in all_trades if t.entry_time and pd.Timestamp(t.entry_time) <= boundary]
        oos_trades = [t for t in all_trades if t.entry_time and pd.Timestamp(t.entry_time) > boundary]
      else:
        is_trades = all_trades

  elif req.batch_ids:
    from backend.db.models import BacktestRun
    batch_trades_map = {}
    for bid in req.batch_ids:
      batch_run = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
      if batch_run:
        run_ids = [r.id for r in db.query(BacktestRun).filter(
          BacktestRun.batch_id == batch_run.id, BacktestRun.status == "completed",
        ).all()]
        trades = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(run_ids)).all() if run_ids else []
        batch_trades_map[bid] = trades
        if is_end:
          boundary = pd.Timestamp(is_end)
          is_trades.extend(t for t in trades if t.entry_time and pd.Timestamp(t.entry_time) <= boundary)
          oos_trades.extend(t for t in trades if t.entry_time and pd.Timestamp(t.entry_time) > boundary)
        else:
          is_trades.extend(trades)

  config = AuditConfig(embargo_days=req.embargo_days, verbose=False)
  agent = OOSIntegrityAgent(config)
  report = agent.audit(
    is_trades=is_trades,
    oos_trades=oos_trades,
    is_end=is_end,
    batch_trades=batch_trades_map,
    portfolio_id=req.portfolio_id,
    path_type=req.path_type,
  )
  return report_to_json(report)


class AuditPromptRequest(BaseModel):
  target: str | None = None
  files: list[str] | None = None
  reference: str | None = None
  focus_areas: list[str] | None = None
  path_type: str | None = None


@router.post("/audit-prompt")
def get_audit_prompt(req: AuditPromptRequest):
  """Generate a filled-in OOS contamination audit prompt.

  Returns a structured prompt for manual or AI-assisted code-level
  tracing of every data pathway. Pre-filled with War Room file paths
  and known optimization pathways.
  """
  prompt = generate_audit_prompt(
    target=req.target,
    files=req.files,
    reference=req.reference,
    focus_areas=req.focus_areas,
    path_type=req.path_type,
  )
  return {"prompt": prompt}
