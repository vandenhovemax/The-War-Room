"""Recent activity feed — aggregates events from backtests, portfolios, journal."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import BacktestRun, BatchBacktestRun, JournalTrade, Portfolio

router = APIRouter()


@router.get("/recent")
def recent_activity(limit: int = Query(default=8, ge=1, le=50), db: Session = Depends(get_db)):
  """Return the most recent events across backtests, portfolios, and journal trades."""
  events: list[dict] = []

  # Recent completed batch backtests
  batches = (
    db.query(BatchBacktestRun)
    .filter(BatchBacktestRun.status == "completed")
    .order_by(BatchBacktestRun.created_at.desc())
    .limit(limit)
    .all()
  )
  for b in batches:
    strat_name = b.strategy.name if b.strategy else f"Strategy #{b.strategy_id}"
    events.append({
      "type": "backtest",
      "timestamp": b.created_at.isoformat() if b.created_at else None,
      "title": "Batch backtest completed",
      "detail": f"{strat_name} · {b.completed_symbols} symbols · {b.timeframe}",
      "icon": "flask",
    })

  # Recent completed single backtests (only non-batch)
  singles = (
    db.query(BacktestRun)
    .filter(BacktestRun.status == "completed", BacktestRun.batch_id.is_(None))
    .order_by(BacktestRun.created_at.desc())
    .limit(limit)
    .all()
  )
  for r in singles:
    events.append({
      "type": "backtest",
      "timestamp": r.created_at.isoformat() if r.created_at else None,
      "title": "Backtest completed",
      "detail": f"{r.symbol} · {r.timeframe}",
      "icon": "flask",
    })

  # Recent portfolios (saved, promoted, activated)
  portfolios = (
    db.query(Portfolio)
    .order_by(Portfolio.created_at.desc())
    .limit(limit)
    .all()
  )
  for p in portfolios:
    ptype = getattr(p, "portfolio_type", "saved") or "saved"
    if ptype == "paper":
      title = "Paper trading started"
    elif ptype == "real":
      title = "Real trading started"
    else:
      title = "Portfolio saved"
    events.append({
      "type": "portfolio",
      "timestamp": p.created_at.isoformat() if p.created_at else None,
      "title": title,
      "detail": p.name,
      "icon": "briefcase",
    })

  # Recent journal trades
  journal = (
    db.query(JournalTrade)
    .order_by(JournalTrade.created_at.desc())
    .limit(limit)
    .all()
  )
  for t in journal:
    action = "Trade opened" if t.status == "open" else "Trade closed"
    events.append({
      "type": "journal",
      "timestamp": t.created_at.isoformat() if t.created_at else None,
      "title": action,
      "detail": f"{t.symbol} {t.side}",
      "icon": "book",
    })

  # Sort all events by timestamp descending, take top N
  events.sort(key=lambda e: e["timestamp"] or "", reverse=True)
  return {"events": events[:limit]}
