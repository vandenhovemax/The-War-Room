"""Portfolio Paper/Real routes — promote, refresh, deactivate.

Refresh and download operations are proxied to the download worker
(port 8081) so they run in a separate process and never get interrupted.
"""
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import Portfolio
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Overridable so the Docker deployment can point at the worker container;
# the default keeps the Windows multi-terminal setup working unchanged.
import os as _os
WORKER_URL = _os.getenv("WORKER_URL", "http://localhost:8081")


class PromoteRequest(BaseModel):
  portfolio_type: str  # "paper" | "real"


def _portfolio_to_dict(p: Portfolio) -> dict:
  """Reuse serialization from portfolio.py."""
  from backend.api.routes.portfolio import _portfolio_to_dict as _base
  return _base(p)


@router.post("/{portfolio_id}/promote")
async def promote_portfolio(
  portfolio_id: int, req: PromoteRequest,
  db: Session = Depends(get_db),
):
  """Promote a portfolio to paper or real trading (in-place, no copy).

  Changes the portfolio_type on the same portfolio — preserves OOS data,
  metrics, and avoids creating duplicate portfolios.
  """
  if req.portfolio_type not in ("paper", "real"):
    raise HTTPException(400, "portfolio_type must be 'paper' or 'real'")

  portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not portfolio:
    raise HTTPException(404, "Portfolio not found")

  portfolio.portfolio_type = req.portfolio_type
  portfolio.is_active = True
  portfolio.activated_at = datetime.now(timezone.utc)
  if not portfolio.live_metrics_snapshot:
    portfolio.live_metrics_snapshot = "{}"
  db.commit()
  db.refresh(portfolio)

  # Auto-refresh via download worker (separate process)
  try:
    async with httpx.AsyncClient(timeout=10) as client:
      await client.post(
        f"{WORKER_URL}/refresh",
        json={"portfolio_id": portfolio.id},
      )
  except httpx.ConnectError:
    pass  # Worker not running — user can manually refresh later

  return _portfolio_to_dict(portfolio)


@router.post("/{portfolio_id}/deactivate")
async def deactivate_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
  """Stop a paper/real portfolio.

  Also tells the live engine to stop trading this portfolio (if active).
  Previously this only updated the DB, so the engine kept trading while
  the UI showed 'Stopped'.
  """
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(404, "Portfolio not found")

  # Tell the engine to stop trading this portfolio (if it's active)
  try:
    from backend.exchange.engine_coordinator import get_engine
    engine = get_engine()
    status = engine.get_status()
    if portfolio_id in (status.get("active_portfolios") or []):
      await engine.deactivate_portfolio(portfolio_id, close_positions=False)
  except Exception as _exc:
    logger.debug("swallowed: %s", _exc)  # Engine might not be running — still update DB below

  p.deactivated_at = datetime.now(timezone.utc)
  p.is_active = False
  # Preserve portfolio_type (paper/real) — don't reset to "saved".
  # A stopped paper portfolio is still a paper portfolio, just inactive.
  db.commit()
  return {"ok": True}


# --- Refresh (proxied to download worker on port 8081) ---

@router.post("/{portfolio_id}/refresh")
async def refresh_portfolio(
  portfolio_id: int,
  db: Session = Depends(get_db),
):
  """Trigger background refresh. Proxied to download worker."""
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not p:
    raise HTTPException(404, "Portfolio not found")
  if p.portfolio_type not in ("paper", "real", "saved"):
    raise HTTPException(400, "Only paper/real/saved portfolios can be refreshed")

  try:
    async with httpx.AsyncClient(timeout=10) as client:
      resp = await client.post(
        f"{WORKER_URL}/refresh",
        json={"portfolio_id": portfolio_id},
      )
      return resp.json()
  except httpx.ConnectError:
    raise HTTPException(503, "Download worker not running (port 8081). Check CMD window [2/4].")


@router.get("/refresh-status/{task_id}")
async def get_refresh_status(task_id: str):
  """Poll refresh progress. Proxied to download worker."""
  try:
    async with httpx.AsyncClient(timeout=10) as client:
      resp = await client.get(f"{WORKER_URL}/refresh-status/{task_id}")
      data = resp.json()
      if data.get("status") == "not_found":
        raise HTTPException(404, "Task not found")
      return data
  except httpx.ConnectError:
    raise HTTPException(503, "Download worker not running (port 8081). Check CMD window [2/4].")


# ─────────────────────────────────────────────────────────────────────
# Backfill historical basket events for hybrid portfolios
# ─────────────────────────────────────────────────────────────────────
# Fills the gap between OOS end date and first nightly refresh by walking
# Phase 2 grading day-by-day, writing one LiveEvent per simulated day. The
# basket-at-t filter in paper_refresh then has segments to match against,
# unlocking proper WFAS validation across the post-OOS period.
#
# This runs IN THE SAME PROCESS as the API (NOT proxied to the download
# worker), because:
#   - It's a pure DB op (no candle downloads)
#   - It needs the same SQLAlchemy session as the rest of the API
#   - Per-portfolio backfills are minutes-long, not hours
#
# Progress is kept in a process-local dict so the UI can poll status.

import threading
import uuid

_backfill_progress: dict[str, dict] = {}
_backfill_lock = threading.Lock()


def _make_task_id() -> str:
  return f"backfill_{uuid.uuid4().hex[:12]}"


def _update_backfill_progress(task_id: str, **kwargs) -> None:
  with _backfill_lock:
    if task_id not in _backfill_progress:
      _backfill_progress[task_id] = {}
    _backfill_progress[task_id].update(kwargs)


def _read_backfill_progress(task_id: str) -> dict | None:
  with _backfill_lock:
    return dict(_backfill_progress.get(task_id, {})) or None


@router.get("/{portfolio_id}/backfill-basket-plan")
def backfill_basket_plan(
  portfolio_id: int,
  db: Session = Depends(get_db),
):
  """Show the planned backfill range WITHOUT writing anything.

  Returns the gap window, day count, and rough time estimate so the UI can
  show a confirmation modal before the user commits to a multi-minute job.
  """
  from backend.scripts.backfill_hybrid_basket_events import plan_backfill
  plan = plan_backfill(db, portfolio_id)
  if not plan.get("ok"):
    raise HTTPException(400, plan.get("reason") or "backfill_not_applicable")
  return plan


@router.post("/{portfolio_id}/backfill-basket-history")
def backfill_basket_history(
  portfolio_id: int,
):
  """Kick off the basket-event backfill as a background thread.

  Returns immediately with a task_id. Poll /backfill-status/{task_id} for
  progress. NOT idempotent — calling twice will start two backfills which
  may interleave writes. The frontend MUST disable the button while a
  task is running.

  Why a thread (not a worker proxy): backfill is a pure DB op, doesn't
  need the candle downloader. Why a thread (not asyncio): the underlying
  refresh_portfolio uses synchronous SQLAlchemy.
  """
  # Validate portfolio exists + is hybrid BEFORE spawning the thread,
  # so the UI gets an immediate error rather than a delayed failure
  ck_db = SessionLocalForBackfill()
  try:
    p = ck_db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not p:
      raise HTTPException(404, "Portfolio not found")
    if p.mode != "hybrid":
      raise HTTPException(400, "Backfill only applies to hybrid portfolios")
  finally:
    ck_db.close()

  task_id = _make_task_id()
  _update_backfill_progress(
    task_id,
    status="starting",
    portfolio_id=portfolio_id,
    started_at=datetime.now(timezone.utc).isoformat(),
    days_done=0,
    days_total=0,
    current_day=None,
  )

  t = threading.Thread(
    target=_run_backfill_thread,
    args=(task_id, portfolio_id),
    name=f"backfill-{portfolio_id}",
    daemon=True,
  )
  t.start()
  return {"task_id": task_id, "portfolio_id": portfolio_id, "status": "started"}


def SessionLocalForBackfill() -> Session:
  """Wrapper so the route + background thread use the same SessionLocal."""
  from backend.db.database import SessionLocal as _SL
  return _SL()


def _run_backfill_thread(task_id: str, portfolio_id: int) -> None:
  """Background thread that runs the backfill + updates the progress dict."""
  from backend.scripts.backfill_hybrid_basket_events import backfill_portfolio
  db = SessionLocalForBackfill()
  try:
    _update_backfill_progress(task_id, status="running")

    def _progress(_pid, day_idx, total_days, day_result):
      _update_backfill_progress(
        task_id,
        days_done=day_idx,
        days_total=total_days,
        current_day=day_result.get("day"),
        last_day_ok=day_result.get("ok"),
        last_day_reason=day_result.get("reason"),
      )

    result = backfill_portfolio(
      db,
      portfolio_id,
      dry_run=False,
      progress_cb=_progress,
    )
    _update_backfill_progress(
      task_id,
      status="completed",
      ended_at=datetime.now(timezone.utc).isoformat(),
      result=result.as_dict(),
    )
  except Exception as e:
    logger.exception("backfill thread for p%d failed: %s", portfolio_id, e)
    _update_backfill_progress(
      task_id,
      status="error",
      error=str(e),
      ended_at=datetime.now(timezone.utc).isoformat(),
    )
  finally:
    db.close()


@router.get("/backfill-status/{task_id}")
def get_backfill_status(task_id: str):
  """Poll backfill progress + result. Process-local — only the API process
  that started the task knows about it. Returns the live progress dict;
  on completion includes the full BackfillResult.as_dict()."""
  data = _read_backfill_progress(task_id)
  if data is None:
    raise HTTPException(404, "Task not found (may have been started in a different process)")
  return data
