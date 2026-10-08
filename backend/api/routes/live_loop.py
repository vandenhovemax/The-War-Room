"""Live Loop admin endpoints (Ship 3).

Purpose:
  A single place for the user to (a) see the state of the nightly loop
  and (b) force a catch-up run if the backend was offline for a while
  and missed its scheduled jobs. Runs all four layers in order:

    L1  batch_store_refresh.refresh_all_active_batches()
    L2  hybrid_portfolio_refresh.refresh_all_hybrid_portfolios()
    L3  oos_boundary.check_and_enqueue()
    L4  parity_canary.run_parity_canary()   (optional, only on request)

Endpoints:
  GET  /api/live-loop/status       — snapshot: last reports + counters
  POST /api/live-loop/catch-up     — run L1 → L2 → L3 sequentially
                                     (pass ?include_canary=1 to also run L4)
  POST /api/live-loop/run/{layer}  — run just one layer (l1|l2|l3|l4)

Design:
  * Synchronous. A catch-up on 92 batches can take minutes, and that's
    fine — the UI shows a spinner and the call returns the aggregate
    summary when done.
  * Never raises. Per-layer failures are caught and reported in the
    response payload so the user can see exactly which layer had trouble.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import (
  BatchBacktestRun,
  ParityCanaryReport,
  Portfolio,
  RebalanceProposal,
)

log = logging.getLogger(__name__)
router = APIRouter()


# ── Status ──────────────────────────────────────────────────────

@router.get("/status")
def get_status(db: Session = Depends(get_db)):
  """Snapshot used by the UI to show the loop's current state."""
  active_batches = (
    db.query(BatchBacktestRun)
    .filter(
      BatchBacktestRun.is_deleted == False,  # noqa: E712
      BatchBacktestRun.is_archived == False,  # noqa: E712
      BatchBacktestRun.is_compacted == False,  # noqa: E712
    )
    .count()
  )
  hybrid_portfolios = (
    db.query(Portfolio)
    .filter(
      Portfolio.mode == "hybrid",
      Portfolio.is_active == True,  # noqa: E712
      Portfolio.is_deleted == False,  # noqa: E712
    )
    .count()
  )
  active_portfolios = (
    db.query(Portfolio)
    .filter(
      Portfolio.is_active == True,  # noqa: E712
      Portfolio.is_deleted == False,  # noqa: E712
      Portfolio.portfolio_type.in_(["paper", "real"]),
    )
    .count()
  )
  pending_proposals = (
    db.query(RebalanceProposal)
    .filter(RebalanceProposal.status == "pending")
    .count()
  )
  latest_canary = (
    db.query(ParityCanaryReport)
    .order_by(ParityCanaryReport.id.desc())
    .first()
  )

  return {
    "active_batches": active_batches,
    "active_live_portfolios": active_portfolios,
    "hybrid_portfolios": hybrid_portfolios,
    "pending_proposals": pending_proposals,
    "schedule": {
      "l1_batch_store_refresh": "daily 00:15 NYT",
      "l2_hybrid_portfolio_refresh": "daily 00:30 NYT",
      "l3_oos_boundary": "daily 01:00 NYT",
      "l4_parity_canary": "monthly 1st 02:00 NYT",
    },
    "last_canary": None if latest_canary is None else {
      "id": latest_canary.id,
      "run_date": latest_canary.run_date,
      "total_combos": latest_canary.total_combos,
      "combos_with_diff": latest_canary.combos_with_diff,
      "created_at": latest_canary.created_at.isoformat()
        if latest_canary.created_at else None,
    },
  }


# ── Catch-up ────────────────────────────────────────────────────

def _run_layer(name: str, fn, *args, **kwargs) -> dict:
  """Invoke one layer and return a uniform {layer, ok, result, error} dict."""
  started = datetime.now(timezone.utc)
  try:
    result = fn(*args, **kwargs)
    return {
      "layer": name,
      "ok": True,
      "started_at": started.isoformat(),
      "ended_at": datetime.now(timezone.utc).isoformat(),
      "result": result,
    }
  except Exception as e:
    log.exception("live_loop catch-up: %s failed", name)
    return {
      "layer": name,
      "ok": False,
      "started_at": started.isoformat(),
      "ended_at": datetime.now(timezone.utc).isoformat(),
      "error": f"{type(e).__name__}: {e}",
    }


@router.post("/catch-up")
def catch_up(
  include_canary: bool = False,
  download_candles: bool = False,
):
  """Run L1 → L2 → L3 in order. Optionally include L4 (parity canary).

  Safe to invoke any time — each layer is idempotent:
    * L0 (optional) candle download is append-only via upsert (no duplicates)
    * L1 no-ops on runs with no new bars
    * L2 writes a new KellyAllocation row per hybrid portfolio (history preserved)
    * L3 skips portfolios that already have a pending proposal
    * L4 is pure audit (never mutates state)
  """
  # Lazy imports keep the module import cheap.
  from backend.services.batch_store_refresh import refresh_all_active_batches
  from backend.services.hybrid_portfolio_refresh import refresh_all_hybrid_portfolios
  from backend.services.oos_boundary import check_and_enqueue as check_oos_boundaries

  layers: list[dict] = []
  if download_candles:
    from backend.services.candle_catchup import catchup_candles
    layers.append(_run_layer("l0_candle_catchup", catchup_candles))
  layers.append(_run_layer("l1_batch_store_refresh", refresh_all_active_batches))
  layers.append(_run_layer("l2_hybrid_portfolio_refresh", refresh_all_hybrid_portfolios))
  layers.append(_run_layer("l3_oos_boundary", check_oos_boundaries))

  if include_canary:
    from backend.services.parity_canary import run_parity_canary
    layers.append(_run_layer("l4_parity_canary", run_parity_canary))

  ok = all(layer["ok"] for layer in layers)
  return {"ok": ok, "layers": layers}


@router.post("/run/{layer}")
def run_one(layer: str):
  """Ad-hoc single-layer runner. Accepts l0 | l1 | l2 | l3 | l4."""
  layer = (layer or "").strip().lower()

  if layer in ("l0", "candles", "download"):
    from backend.services.candle_catchup import catchup_candles
    return _run_layer("l0_candle_catchup", catchup_candles)
  if layer in ("l1", "batch"):
    from backend.services.batch_store_refresh import refresh_all_active_batches
    return _run_layer("l1_batch_store_refresh", refresh_all_active_batches)
  if layer in ("l2", "hybrid"):
    from backend.services.hybrid_portfolio_refresh import refresh_all_hybrid_portfolios
    return _run_layer("l2_hybrid_portfolio_refresh", refresh_all_hybrid_portfolios)
  if layer in ("l3", "boundary", "oos"):
    from backend.services.oos_boundary import check_and_enqueue as check_oos_boundaries
    return _run_layer("l3_oos_boundary", check_oos_boundaries)
  if layer in ("l4", "canary", "parity"):
    from backend.services.parity_canary import run_parity_canary
    return _run_layer("l4_parity_canary", run_parity_canary)

  raise HTTPException(status_code=400, detail=f"unknown layer: {layer}")


# ── Stale-batch rebuild (one-off, for pre-Live-Loop batches) ────

@router.get("/stale-batches")
def list_stale_batches(db: Session = Depends(get_db)):
  """List active batches whose runs don't yet have strategy_state captured.

  Those batches are invisible to L1 until rebuilt. UI uses this to show
  "X of Y batches need a one-off rebuild before nightly L1 can touch them".
  """
  from backend.services.batch_rebuild import find_stale_batches
  stale = find_stale_batches(db)
  return {
    "batches": stale,
    "batch_count": len(stale),
    "run_count": sum(b["stale_runs"] for b in stale),
  }


@router.post("/rebuild-stale-batches")
def start_rebuild_stale_batches(
  background: BackgroundTasks,
  download_candles: bool = False,
):
  """Kick off the bulk rebuild in the background. Returns immediately.

  Set `download_candles=1` to run the L0 candle catch-up first — useful
  when batches have been idle for a while and the DB is missing recent
  bars. L0 is append-only (upsert), so it never re-downloads existing
  candles. Market Structure dashboard continuity preserved.

  The job updates an in-memory progress dict that the UI polls via
  GET /rebuild-status. Only one rebuild can run at a time — a second
  POST while one is in progress is a no-op (returns current status).
  """
  from backend.services.batch_rebuild import get_progress, rebuild_all_stale

  snap = get_progress()
  if snap["status"] == "running":
    return {"started": False, "reason": "already_running", "progress": snap}

  background.add_task(rebuild_all_stale, download_candles_first=download_candles)
  return {"started": True, "progress": get_progress()}


@router.get("/rebuild-status")
def get_rebuild_status():
  """Poll endpoint for the rebuild progress bar."""
  from backend.services.batch_rebuild import get_progress
  return get_progress()
