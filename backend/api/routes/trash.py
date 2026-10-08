"""
Trash / Deleted Items API — list, restore, and permanently delete soft-deleted items.
Covers: strategies, backtest runs, batch runs, WFO runs.
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import (
  BacktestRun,
  BatchBacktestRun,
  Strategy,
  WFORun,
)

router = APIRouter()


# ── List deleted items ─────────────────────────────────────────

@router.get("/trash/strategies")
def list_deleted_strategies(db: Session = Depends(get_db)):
  """List all soft-deleted strategies."""
  items = (
    db.query(Strategy)
    .filter(Strategy.is_deleted == True)
    .order_by(Strategy.deleted_at.desc())
    .all()
  )
  return [
    {
      "id": s.id,
      "name": s.name,
      "description": s.description,
      "source_type": s.source_type,
      "created_at": s.created_at.isoformat() if s.created_at else None,
      "deleted_at": s.deleted_at.isoformat() if s.deleted_at else None,
    }
    for s in items
  ]


@router.get("/trash/backtests")
def list_deleted_backtests(db: Session = Depends(get_db)):
  """List all soft-deleted individual backtest runs."""
  items = (
    db.query(BacktestRun)
    .filter(BacktestRun.is_deleted == True)
    .order_by(BacktestRun.deleted_at.desc())
    .all()
  )
  return [
    {
      "id": r.id,
      "strategy_id": r.strategy_id,
      "symbol": r.symbol,
      "timeframe": r.timeframe,
      "status": r.status,
      "created_at": r.created_at.isoformat() if r.created_at else None,
      "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
      "metrics": json.loads(r.metrics) if r.metrics else {},
    }
    for r in items
  ]


@router.get("/trash/batches")
def list_deleted_batches(db: Session = Depends(get_db)):
  """List all soft-deleted batch backtest runs."""
  items = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.is_deleted == True)
    .order_by(BatchBacktestRun.deleted_at.desc())
    .all()
  )
  return [
    {
      "id": b.id,
      "name": b.name or f"Batch #{b.id}",
      "strategy_id": b.strategy_id,
      "timeframe": b.timeframe,
      "total_symbols": b.total_symbols,
      "status": b.status,
      "created_at": b.created_at.isoformat() if b.created_at else None,
      "deleted_at": b.deleted_at.isoformat() if b.deleted_at else None,
    }
    for b in items
  ]


@router.get("/trash/wfo")
def list_deleted_wfo(db: Session = Depends(get_db)):
  """List all soft-deleted WFO runs."""
  items = (
    db.query(WFORun)
    .filter(WFORun.is_deleted == True)
    .order_by(WFORun.deleted_at.desc())
    .all()
  )
  return [
    {
      "id": w.id,
      "batch_id": w.batch_id,
      "is_months": w.is_months,
      "oos_months": w.oos_months,
      "status": w.status,
      "created_at": w.created_at.isoformat() if w.created_at else None,
      "deleted_at": w.deleted_at.isoformat() if w.deleted_at else None,
    }
    for w in items
  ]


# ── Restore deleted items ─────────────────────────────────────

@router.post("/trash/strategies/{item_id}/restore")
def restore_strategy(item_id: int, db: Session = Depends(get_db)):
  s = db.query(Strategy).filter(Strategy.id == item_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  s.is_deleted = False
  s.deleted_at = None
  db.commit()
  return {"restored": True, "id": item_id}


@router.post("/trash/backtests/{item_id}/restore")
def restore_backtest(item_id: int, db: Session = Depends(get_db)):
  r = db.query(BacktestRun).filter(BacktestRun.id == item_id).first()
  if not r:
    raise HTTPException(status_code=404, detail="Backtest run not found")
  r.is_deleted = False
  r.deleted_at = None
  db.commit()
  return {"restored": True, "id": item_id}


@router.post("/trash/batches/{item_id}/restore")
def restore_batch(item_id: int, db: Session = Depends(get_db)):
  b = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == item_id).first()
  if not b:
    raise HTTPException(status_code=404, detail="Batch not found")
  b.is_deleted = False
  b.deleted_at = None
  # Also restore child runs
  child_runs = db.query(BacktestRun).filter(BacktestRun.batch_id == item_id).all()
  for run in child_runs:
    run.is_deleted = False
    run.deleted_at = None
  db.commit()
  return {"restored": True, "id": item_id, "child_runs_restored": len(child_runs)}


@router.post("/trash/wfo/{item_id}/restore")
def restore_wfo(item_id: int, db: Session = Depends(get_db)):
  w = db.query(WFORun).filter(WFORun.id == item_id).first()
  if not w:
    raise HTTPException(status_code=404, detail="WFO run not found")
  w.is_deleted = False
  w.deleted_at = None
  db.commit()
  return {"restored": True, "id": item_id}


# ── Permanent delete ──────────────────────────────────────────

@router.delete("/trash/strategies/{item_id}")
def permanent_delete_strategy(item_id: int, db: Session = Depends(get_db)):
  s = db.query(Strategy).filter(Strategy.id == item_id).first()
  if not s:
    raise HTTPException(status_code=404, detail="Strategy not found")
  db.delete(s)
  db.commit()
  return {"permanently_deleted": True, "id": item_id}


@router.delete("/trash/backtests/{item_id}")
def permanent_delete_backtest(item_id: int, db: Session = Depends(get_db)):
  r = db.query(BacktestRun).filter(BacktestRun.id == item_id).first()
  if not r:
    raise HTTPException(status_code=404, detail="Backtest run not found")
  db.delete(r)
  db.commit()
  return {"permanently_deleted": True, "id": item_id}


@router.delete("/trash/batches/{item_id}")
def permanent_delete_batch(item_id: int, db: Session = Depends(get_db)):
  b = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == item_id).first()
  if not b:
    raise HTTPException(status_code=404, detail="Batch not found")

  # Block if any portfolio references this batch
  from backend.api.routes.batch_backtest import _find_portfolios_using_batch
  refs = _find_portfolios_using_batch(db, item_id)
  if refs:
    names = ", ".join(p["name"] for p in refs)
    raise HTTPException(
      status_code=409,
      detail=f"Cannot delete batch #{item_id} — used by portfolio(s): {names}",
    )

  child_runs = db.query(BacktestRun).filter(BacktestRun.batch_id == item_id).all()
  for run in child_runs:
    db.delete(run)

  # Clean up orphan references (no FK constraints on these tables)
  from backend.db.models import LiveStrategyState, PortfolioAsset
  from sqlalchemy import text as sa_text

  # NULL out batch_id on PortfolioAssets that reference this batch
  db.execute(
    sa_text("UPDATE portfolio_assets SET batch_id = NULL WHERE batch_id = :bid"),
    {"bid": item_id},
  )
  # NULL out batch_id on LiveStrategyState rows
  db.execute(
    sa_text("UPDATE live_strategy_states SET batch_id = NULL WHERE batch_id = :bid"),
    {"bid": item_id},
  )
  # Clean up WFO runs: delete any that reference this batch as their primary
  orphan_wfos = db.query(WFORun).filter(WFORun.batch_id == item_id).all()
  for w in orphan_wfos:
    db.delete(w)

  db.delete(b)
  db.commit()
  return {"permanently_deleted": True, "id": item_id}


@router.delete("/trash/wfo/{item_id}")
def permanent_delete_wfo(item_id: int, db: Session = Depends(get_db)):
  w = db.query(WFORun).filter(WFORun.id == item_id).first()
  if not w:
    raise HTTPException(status_code=404, detail="WFO run not found")
  db.delete(w)
  db.commit()
  return {"permanently_deleted": True, "id": item_id}


# ── Empty trash (all or per-tab) ──────────────────────────────

def _safe_empty_trash(db: Session, types: list[str]) -> dict:
  """Safely empty trash items using raw SQL in correct cascade order.

  Disables FK checks to avoid cascade surprises, deletes children first.
  Protects batches referenced by portfolios.
  Protects strategies that still have active (non-deleted) backtest_runs.
  """
  from sqlalchemy import text as sa_text

  db.execute(sa_text("PRAGMA foreign_keys=OFF"))

  counts: dict[str, int] = {}
  batches_skipped: list[dict] = []

  # 1. Trades from deleted runs/batches
  if "backtests" in types or "batches" in types:
    r = db.execute(sa_text("""
      DELETE FROM backtest_trades WHERE run_id IN (
        SELECT id FROM backtest_runs WHERE is_deleted = 1
      )
    """))
    counts["trades"] = r.rowcount

  # 2. Backtest runs
  if "backtests" in types:
    r = db.execute(sa_text("DELETE FROM backtest_runs WHERE is_deleted = 1"))
    counts["backtests"] = r.rowcount

  # 3. Batches — protect those referenced by portfolios
  if "batches" in types:
    deleted_batches = db.execute(sa_text(
      "SELECT id, name FROM batch_backtest_runs WHERE is_deleted = 1"
    )).fetchall()

    from backend.api.routes.batch_backtest import _find_portfolios_using_batch

    skip_ids = set()
    for row in deleted_batches:
      refs = _find_portfolios_using_batch(db, row[0])
      if refs:
        names = ", ".join(p["name"] for p in refs)
        batches_skipped.append({"batch_id": row[0], "name": row[1], "portfolios": names})
        skip_ids.add(row[0])

    # Delete child runs of deletable batches first
    deletable_ids = [row[0] for row in deleted_batches if row[0] not in skip_ids]
    if deletable_ids:
      ids_str = ",".join(str(i) for i in deletable_ids)
      db.execute(sa_text(f"""
        DELETE FROM backtest_trades WHERE run_id IN (
          SELECT id FROM backtest_runs WHERE batch_id IN ({ids_str})
        )
      """))
      db.execute(sa_text(f"DELETE FROM backtest_runs WHERE batch_id IN ({ids_str})"))
      db.execute(sa_text(f"DELETE FROM batch_backtest_runs WHERE id IN ({ids_str})"))
    counts["batches"] = len(deletable_ids)

  # 4. Strategies — only if they have no remaining active runs
  if "strategies" in types:
    r = db.execute(sa_text("""
      DELETE FROM strategies WHERE is_deleted = 1
      AND id NOT IN (SELECT DISTINCT strategy_id FROM backtest_runs)
    """))
    counts["strategies"] = r.rowcount

  # 5. WFO runs (wfo_windows will be orphaned since FK is OFF, so delete them too)
  if "wfo" in types:
    db.execute(sa_text("""
      DELETE FROM wfo_windows WHERE wfo_run_id IN (
        SELECT id FROM wfo_runs WHERE is_deleted = 1
      )
    """))
    r = db.execute(sa_text("DELETE FROM wfo_runs WHERE is_deleted = 1"))
    counts["wfo"] = r.rowcount

  db.commit()
  db.execute(sa_text("PRAGMA foreign_keys=ON"))

  result: dict = {"emptied": True, "counts": counts}
  if batches_skipped:
    result["batches_skipped"] = batches_skipped
  return result


@router.delete("/trash/empty")
def empty_all_trash(db: Session = Depends(get_db)):
  """Permanently delete ALL soft-deleted items across all types."""
  return _safe_empty_trash(db, ["backtests", "batches", "strategies", "wfo"])


@router.delete("/trash/empty/{tab}")
def empty_trash_tab(tab: str, db: Session = Depends(get_db)):
  """Permanently delete all soft-deleted items in a specific tab."""
  valid = {"strategies", "backtests", "batches", "wfo"}
  if tab not in valid:
    raise HTTPException(status_code=400, detail=f"Invalid tab: {tab}. Valid: {sorted(valid)}")
  return _safe_empty_trash(db, [tab])
