"""Live trading API routes.

Provides REST endpoints for the frontend to control and monitor
the live trading engine.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config.settings import get_settings
from backend.db.database import get_db
from backend.db.models import (
  BatchBacktestRun,
  DailyPnL,
  LiveEvent,
  LiveSignal,
  Order,
  Portfolio,
  PortfolioAsset,
  Position,
  Strategy,
  Trade,
)
from backend.exchange.engine_coordinator import get_engine

router = APIRouter()


# ── Paper-backend forwarding (additive, opt-in) ─────────────────────
#
# When this LIVE backend (paper_trading=False) gets an engine-state request
# for a paper-type portfolio AND settings.paper_backend_url is set, the
# request is forwarded to the paper backend instead of refused. This lets
# the user run BOTH a live engine (this process) and a paper engine (the
# paper_backend_url process) at the same time, with each handling only its
# own portfolio type. The is_paper migration on `trades` keeps martingale
# state isolated even though both backends share one DB.
#
# Forwarding is one-directional only: live → paper. The paper backend has
# paper_trading=True and `_should_forward_to_paper` always returns False
# there, so loops are impossible.

def _should_forward_to_paper(portfolio_type: str | None) -> bool:
  """Return True iff this is the live backend AND a paper portfolio AND
  forwarding is configured."""
  s = get_settings()
  if s.paper_trading:
    return False  # We're the paper backend — handle locally
  if portfolio_type != "paper":
    return False  # Real portfolio — handle locally on the live backend
  if not s.paper_backend_url:
    return False  # Forwarding not configured — fall through to existing guard
  return True


async def _forward_to_paper(
  path: str, json_body: dict | None = None, *, method: str = "POST",
  timeout: float = 600.0,
) -> dict:
  """Send to the paper backend and return its JSON response.

  Default timeout is 600s (10 min) because /api/live/start on the paper
  backend warms up every active paper portfolio's strategy executor —
  potentially 30 portfolios × 50 symbols × 70k+ bars each. A short timeout
  here would falsely report 'not reachable' while the engine is still
  warming up correctly. Status / activate / deactivate finish in seconds
  but they reuse the same generous ceiling rather than have per-route
  timeouts; the cost of an actually-hung backend is just a 10-min wait.

  Errors:
    * ConnectError → backend isn't running (open the window)
    * ReadTimeout (only after 10 min) → backend is hung; investigate logs"""
  s = get_settings()
  if not s.paper_backend_url:
    raise HTTPException(
      status_code=503,
      detail="Paper backend not configured (PAPER_BACKEND_URL is empty).",
    )
  url = f"{s.paper_backend_url.rstrip('/')}{path}"
  try:
    async with httpx.AsyncClient(timeout=timeout) as client:
      if method.upper() == "GET":
        resp = await client.get(url)
      else:
        resp = await client.post(url, json=json_body or {})
  except httpx.ConnectError:
    raise HTTPException(
      status_code=503,
      detail=(
        f"Paper backend not reachable at {s.paper_backend_url}. "
        f"Open the [WAR ROOM - PAPER BACKEND] window."
      ),
    )
  except httpx.ReadTimeout:
    raise HTTPException(
      status_code=504,
      detail=(
        f"Paper backend at {s.paper_backend_url} did not respond within "
        f"{timeout:.0f}s. The engine may still be warming up — check the "
        f"paper backend window logs and try /paper/status in a minute."
      ),
    )
  if resp.status_code >= 400:
    # Surface the paper backend's error verbatim
    try:
      detail = resp.json().get("detail", resp.text)
    except Exception:
      detail = resp.text
    raise HTTPException(status_code=resp.status_code, detail=detail)
  return resp.json()


def _refuse_if_on_paper_backend() -> None:
  """The /api/paper/* routes only make sense on the LIVE backend (port 8055)
  because they forward TO the paper backend (port 8056). If somebody hits
  these endpoints on the paper backend itself we'd recurse forever. Refuse
  with a clear message."""
  if get_settings().paper_trading:
    raise HTTPException(
      status_code=400,
      detail=(
        "/api/paper/* routes are only valid on the LIVE backend. "
        "You're already on the paper backend — call /api/live/* directly."
      ),
    )


# ── Paper-engine remote-control routes ──────────────────────────────
#
# These three endpoints exist on the LIVE backend only and forward their
# request to the paper backend's standard /api/live/* endpoints. They give
# the frontend (which only knows about port 8055) a way to start/stop and
# inspect the paper engine without code-duplicating the existing UI.

@router.get("/paper/status")
async def paper_engine_status():
  """Status of the paper engine (forwards to paper backend)."""
  _refuse_if_on_paper_backend()
  return await _forward_to_paper("/api/live/status", method="GET")


@router.post("/paper/start")
async def paper_engine_start():
  """Start the paper engine (forwards to paper backend)."""
  _refuse_if_on_paper_backend()
  return await _forward_to_paper("/api/live/start")


@router.post("/paper/stop")
async def paper_engine_stop():
  """Stop the paper engine (forwards to paper backend)."""
  _refuse_if_on_paper_backend()
  return await _forward_to_paper("/api/live/stop")


# ── Request/Response Models ─────────────────────────────────────────

class ActivateRequest(BaseModel):
  portfolio_id: int


class DeactivateRequest(BaseModel):
  portfolio_id: int
  close_positions: bool = False


class ClosePositionRequest(BaseModel):
  position_id: int


# ── Engine Control ──────────────────────────────────────────────────

@router.get("/live/status")
def get_status():
  """Get current engine status."""
  engine = get_engine()
  return engine.get_status()


@router.post("/live/start")
async def start_engine():
  """Start the live trading engine."""
  engine = get_engine()
  success = await engine.start()
  if not success:
    raise HTTPException(status_code=400, detail="Failed to start engine. Check API keys and settings.")
  return {"status": "started"}


@router.post("/live/stop")
async def stop_engine():
  """Graceful engine shutdown — cancels entries, keeps exits."""
  engine = get_engine()
  await engine.shutdown()
  return {"status": "stopped"}


@router.post("/live/monitor")
async def start_monitoring(req: ActivateRequest, db: Session = Depends(get_db)):
  """Start monitoring a portfolio (candle gathering only, no trading).

  Sets is_active=True so candle_sync picks up this portfolio's symbols.
  Does NOT start the engine or execute any strategy signals.
  """
  portfolio = db.get(Portfolio, req.portfolio_id)
  if not portfolio:
    raise HTTPException(status_code=404, detail="Portfolio not found")
  if portfolio.is_active:
    raise HTTPException(status_code=400, detail="Portfolio is already active")
  portfolio.is_active = True
  # Preserve portfolio_type (paper/real) — don't overwrite to "monitoring"
  portfolio.activated_at = datetime.now(timezone.utc)
  portfolio.deactivated_at = None
  db.commit()
  return {"status": "monitoring", "portfolio_id": req.portfolio_id}


@router.post("/live/stop-monitoring")
async def stop_monitoring(req: ActivateRequest, db: Session = Depends(get_db)):
  """Stop monitoring a portfolio (back to saved state).

  Only works for portfolios in monitoring state (not live-engine ones).
  """
  portfolio = db.get(Portfolio, req.portfolio_id)
  if not portfolio:
    raise HTTPException(status_code=404, detail="Portfolio not found")
  # Safety: don't stop monitoring if it's in the live engine
  engine = get_engine()
  status = engine.get_status()
  if req.portfolio_id in (status.get("active_portfolios") or []):
    raise HTTPException(
      status_code=400,
      detail="Portfolio is live trading — stop trading from engine first",
    )
  portfolio.is_active = False
  # Preserve portfolio_type (paper/real) — don't reset to "saved"
  portfolio.deactivated_at = datetime.now(timezone.utc)
  db.commit()
  return {"status": "stopped", "portfolio_id": req.portfolio_id}


@router.post("/live/activate")
async def activate_portfolio(req: ActivateRequest, db: Session = Depends(get_db)):
  """Activate a monitoring portfolio for live trading (Go Live).

  Portfolio must already be in monitoring state (is_active=True).
  This starts the engine's strategy execution and WebSocket subscriptions.
  """
  portfolio = db.get(Portfolio, req.portfolio_id)
  if not portfolio:
    raise HTTPException(status_code=404, detail="Portfolio not found")
  if not portfolio.is_active:
    raise HTTPException(
      status_code=400,
      detail="Portfolio must be in monitoring state first. Click Activate first.",
    )
  # Dual-backend: paper portfolios on the live backend → forward to paper backend.
  if _should_forward_to_paper(portfolio.portfolio_type):
    return await _forward_to_paper(
      "/api/live/activate", {"portfolio_id": req.portfolio_id},
    )
  engine = get_engine()
  success = await engine.activate_portfolio(req.portfolio_id)
  if not success:
    raise HTTPException(status_code=400, detail="Failed to activate portfolio")
  return {"status": "activated", "portfolio_id": req.portfolio_id}


@router.post("/live/deactivate")
async def deactivate_portfolio(req: DeactivateRequest, db: Session = Depends(get_db)):
  """Deactivate a portfolio (optionally close positions).

  Stops live trading and returns portfolio to saved state.
  """
  # Dual-backend: paper portfolios on the live backend → forward to paper backend.
  portfolio = db.get(Portfolio, req.portfolio_id)
  if portfolio and _should_forward_to_paper(portfolio.portfolio_type):
    return await _forward_to_paper(
      "/api/live/deactivate",
      {"portfolio_id": req.portfolio_id, "close_positions": req.close_positions},
    )
  engine = get_engine()
  success = await engine.deactivate_portfolio(
    req.portfolio_id, close_positions=req.close_positions,
  )
  if not success:
    raise HTTPException(status_code=400, detail="Failed to deactivate portfolio")
  return {
    "status": "deactivated",
    "portfolio_id": req.portfolio_id,
    "positions_closed": req.close_positions,
  }


class RefreshRequest(BaseModel):
  portfolio_id: int


@router.post("/live/refresh-portfolio")
async def refresh_portfolio(req: RefreshRequest, db: Session = Depends(get_db)):
  """Refresh portfolio asset list without killing open positions.

  Re-reads active assets from DB. New symbols get subscribed and warmed up.
  Removed symbols get unsubscribed (only if no open positions remain).
  """
  # Dual-backend: paper portfolios on the live backend → forward to paper backend.
  portfolio = db.get(Portfolio, req.portfolio_id)
  if portfolio and _should_forward_to_paper(portfolio.portfolio_type):
    return await _forward_to_paper(
      "/api/live/refresh-portfolio", {"portfolio_id": req.portfolio_id},
    )
  engine = get_engine()
  result = await engine.refresh_portfolio(req.portfolio_id)
  if "error" in result:
    raise HTTPException(status_code=400, detail=result["error"])
  return result


@router.post("/live/close-position")
async def close_position(req: ClosePositionRequest):
  """Manually close a single position."""
  engine = get_engine()
  success = await engine.close_position(req.position_id)
  if not success:
    raise HTTPException(status_code=400, detail="Failed to close position")
  return {"status": "closing", "position_id": req.position_id}


@router.post("/live/close-all")
async def close_all_positions():
  """Close ALL open positions (emergency)."""
  engine = get_engine()
  count = await engine.close_all_positions()
  return {"status": "closing_all", "positions": count}


@router.post("/live/emergency-stop")
async def emergency_stop():
  """Kill switch — close all positions and shut down engine."""
  engine = get_engine()
  await engine.emergency_stop()
  return {"status": "emergency_stopped"}


@router.post("/live/reset-safety")
def reset_safety():
  """Reset safety monitor counters (daily PnL, circuit breaker, trade count).

  Useful for running multiple tests in one session without restarting.
  """
  engine = get_engine()
  if engine._safety_monitor:
    engine._safety_monitor.reset_counters()
    return {"status": "reset", "message": "Safety counters reset to zero, circuit breaker OFF"}
  return {"status": "no_monitor", "message": "Safety monitor not initialized"}


# ── Data Queries ────────────────────────────────────────────────────

@router.get("/live/positions")
def get_positions(db: Session = Depends(get_db)):
  """Get all open live positions."""
  positions = db.query(Position).filter(Position.is_open == True).all()
  return [
    {
      "id": p.id,
      "portfolio_id": p.portfolio_id,
      "symbol": p.symbol,
      "side": p.side,
      "strategy_name": p.strategy_name or "",
      "entry_price": p.entry_price,
      "quantity": p.quantity,
      "sl_price": p.sl_price,
      "tp_price": p.tp_price,
      "unrealized_pnl": p.unrealized_pnl,
      "leverage": p.leverage,
      "margin_mode": p.margin_mode,
      "trailing_stop_distance": p.trailing_stop_distance,
      "trailing_extreme": p.trailing_extreme,
      "funding_accumulated": p.funding_accumulated,
      "entry_commission": p.entry_commission,
      "exit_commission": p.exit_commission,
      "risk_removed": p.risk_removed,
      "opened_at": p.opened_at.isoformat() if p.opened_at else None,
    }
    for p in positions
  ]


@router.get("/live/orders")
def get_orders(
  status: str = "all",
  limit: int = 50,
  db: Session = Depends(get_db),
):
  """Get live orders (filtered by status)."""
  query = db.query(Order).filter(Order.role != "")  # Only live orders (have a role)
  if status != "all":
    query = query.filter(Order.status == status)
  query = query.order_by(Order.created_at.desc()).limit(limit)

  return [
    {
      "id": o.id,
      "symbol": o.symbol,
      "side": o.side,
      "order_type": o.order_type,
      "role": o.role,
      "status": o.status,
      "intended_qty": o.intended_qty,
      "filled_qty": o.filled_qty,
      "price": o.price,
      "avg_fill_price": o.avg_fill_price,
      "sl_price": o.sl_price,
      "tp_price": o.tp_price,
      "commission": o.commission,
      "cancel_reason": o.cancel_reason,
      "order_link_id": o.order_link_id,
      "portfolio_id": o.portfolio_id,
      "created_at": o.created_at.isoformat() if o.created_at else None,
    }
    for o in query.all()
  ]


@router.get("/live/trades")
def get_live_trades(limit: int = 50, db: Session = Depends(get_db)):
  """Get closed live trades with full fee breakdown."""
  trades = (
    db.query(Trade)
    .order_by(Trade.closed_at.desc())
    .limit(limit)
    .all()
  )
  return [
    {
      "id": t.id,
      "portfolio_id": t.portfolio_id,
      "symbol": t.symbol,
      "side": t.side,
      "strategy_name": t.strategy_name,
      "entry_price": t.entry_price,
      "exit_price": t.exit_price,
      "quantity": t.quantity,
      "realized_pnl": t.realized_pnl,
      "commission": t.commission,
      "entry_commission": t.entry_commission,
      "exit_commission": t.exit_commission,
      "funding_fee": t.funding_fee,
      "close_reason": t.close_reason,
      "opened_at": t.opened_at.isoformat() if t.opened_at else None,
      "closed_at": t.closed_at.isoformat() if t.closed_at else None,
    }
    for t in trades
  ]


@router.get("/live/equity")
def get_equity():
  """Get current exchange equity snapshot."""
  engine = get_engine()
  status = engine.get_status()
  return {
    "equity": status.get("equity", 0),
    "available_balance": status.get("available_balance", 0),
    "testnet": status.get("testnet", True),
  }


@router.get("/live/events")
def get_events(limit: int = 100, db: Session = Depends(get_db)):
  """Get recent live trading events (audit log)."""
  events = (
    db.query(LiveEvent)
    .order_by(LiveEvent.created_at.desc())
    .limit(limit)
    .all()
  )
  return [
    {
      "id": e.id,
      "portfolio_id": e.portfolio_id,
      "event_type": e.event_type,
      "symbol": e.symbol,
      "details": e.details,
      "created_at": e.created_at.isoformat() if e.created_at else None,
    }
    for e in events
  ]


@router.get("/live/tsl-status")
def get_tsl_status():
  """Get trailing stop tracker status."""
  engine = get_engine()
  if engine._tsl_tracker:
    return engine._tsl_tracker.get_status()
  return []


@router.get("/live/account-summary")
def get_account_summary(db: Session = Depends(get_db)):
  """Enhanced account info for the dashboard.

  Returns equity breakdown, position counts, daily PnL,
  and circuit breaker status.
  """
  engine = get_engine()
  status = engine.get_status()
  settings = get_settings()

  # Open positions breakdown
  positions = db.query(Position).filter(Position.is_open == True).all()
  total_unrealized = sum(p.unrealized_pnl or 0 for p in positions)
  total_notional = sum(p.quantity * p.entry_price for p in positions)
  long_count = sum(1 for p in positions if p.side == "Buy")
  short_count = sum(1 for p in positions if p.side == "Sell")

  equity = status.get("equity", 0)
  available = status.get("available_balance", 0)

  # Today's PnL from DailyPnL table
  today = date.today()
  daily = db.query(DailyPnL).filter(DailyPnL.date == today).all()
  daily_pnl = sum(d.realized_pnl or 0 for d in daily)
  daily_trades = sum(d.trade_count or 0 for d in daily)
  daily_wins = sum(d.win_count or 0 for d in daily)
  daily_losses = sum(d.loss_count or 0 for d in daily)
  daily_commission = sum(d.total_commission or 0 for d in daily)

  # Circuit breaker: daily loss vs limit
  circuit_breaker = abs(daily_pnl) >= settings.max_daily_loss_usd if daily_pnl < 0 else False

  # Risk mode info
  risk_mode = settings.risk_mode
  risk_display = (
    f"${settings.risk_per_trade_usd:.0f}" if risk_mode == "fixed"
    else f"{settings.risk_per_trade_pct}%"
  )

  return {
    "equity": equity,
    "available_balance": available,
    "margin_used": max(0, equity - available),
    "unrealized_pnl": total_unrealized,
    "total_notional": total_notional,
    "position_count": len(positions),
    "long_count": long_count,
    "short_count": short_count,
    "daily_pnl": daily_pnl,
    "daily_trades": daily_trades,
    "daily_wins": daily_wins,
    "daily_losses": daily_losses,
    "daily_commission": daily_commission,
    "circuit_breaker": circuit_breaker,
    "risk_mode": risk_mode,
    "risk_display": risk_display,
    "testnet": status.get("testnet", True),
    "paper_trading": status.get("paper_trading", True),
  }


@router.get("/live/pnl-summary")
def get_pnl_summary(db: Session = Depends(get_db)):
  """PnL summary across time periods.

  Returns realized PnL for today, this week, this month, and all-time,
  plus overall win rate.
  """
  today = date.today()
  week_start = today - timedelta(days=today.weekday())
  month_start = today.replace(day=1)

  # All daily PnL records
  all_daily = db.query(DailyPnL).all()

  # All-time trades for win rate
  all_trades = db.query(Trade).all()

  # Aggregate by period
  today_pnl = sum(d.realized_pnl or 0 for d in all_daily if d.date == today)
  week_pnl = sum(d.realized_pnl or 0 for d in all_daily if d.date and d.date >= week_start)
  month_pnl = sum(d.realized_pnl or 0 for d in all_daily if d.date and d.date >= month_start)
  all_time_pnl = sum(d.realized_pnl or 0 for d in all_daily)

  # Win rate from trades
  total_trades = len(all_trades)
  winning_trades = sum(1 for t in all_trades if (t.realized_pnl or 0) > 0)
  win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

  # Average win/loss
  wins = [t.realized_pnl for t in all_trades if (t.realized_pnl or 0) > 0]
  losses = [t.realized_pnl for t in all_trades if (t.realized_pnl or 0) < 0]
  avg_win = sum(wins) / len(wins) if wins else 0
  avg_loss = sum(losses) / len(losses) if losses else 0

  return {
    "today": today_pnl,
    "week": week_pnl,
    "month": month_pnl,
    "all_time": all_time_pnl,
    "total_trades": total_trades,
    "winning_trades": winning_trades,
    "win_rate": win_rate,
    "avg_win": avg_win,
    "avg_loss": avg_loss,
  }


# ── Signal Viewer ──────────────────────────────────────────────────


@router.get("/live/signals")
def list_signals(
  portfolio_id: int | None = None,
  status: str | None = None,
  symbol: str | None = None,
  limit: int = 100,
  db: Session = Depends(get_db),
):
  """List scanner signals with full context: portfolio, strategy, batch,
  timeframe, execution window check, and trade outcome."""
  import json

  q = db.query(LiveSignal)
  if portfolio_id is not None:
    q = q.filter(LiveSignal.portfolio_id == portfolio_id)
  if status:
    q = q.filter(LiveSignal.status == status)
  if symbol:
    q = q.filter(LiveSignal.symbol == symbol)
  q = q.order_by(LiveSignal.created_at.desc()).limit(limit)
  rows = q.all()

  # Pre-load portfolio info and execution window configs
  portfolio_ids = {r.portfolio_id for r in rows}
  portfolio_cache: dict[int, dict] = {}
  for pid in portfolio_ids:
    p = db.get(Portfolio, pid)
    if not p:
      continue
    source = json.loads(p.source_strategies or "{}")
    # Build execution window: excluded hours + excluded days (NYT)
    exclude_hours = set(source.get("session_filter_exclude_hours", []))
    exclude_days = set(source.get("session_filter_exclude_days", []))
    session_enabled = source.get("session_filter_enabled", False)
    portfolio_cache[pid] = {
      "name": p.name,
      "source": source,
      "exclude_hours": exclude_hours,
      "exclude_days": exclude_days,
      "session_enabled": session_enabled,
    }

  results = []
  for r in rows:
    pinfo = portfolio_cache.get(r.portfolio_id, {})
    portfolio_name = pinfo.get("name", f"Portfolio #{r.portfolio_id}")

    # Resolve strategy + batch context from PortfolioAsset
    strategy_name = ""
    batch_name = ""
    timeframe = ""
    base_symbol = r.symbol.split("_")[0] if "_" in r.symbol else r.symbol
    asset = db.query(PortfolioAsset).filter(
      PortfolioAsset.portfolio_id == r.portfolio_id,
      PortfolioAsset.symbol.contains(base_symbol),
      PortfolioAsset.is_active == True,
    ).first()
    if asset and asset.batch_id:
      batch = db.get(BatchBacktestRun, asset.batch_id)
      if batch:
        batch_name = batch.name or f"Batch #{batch.id}"
        timeframe = batch.timeframe or ""
        if batch.strategy_id:
          strat = db.get(Strategy, batch.strategy_id)
          if strat:
            strategy_name = strat.name
    if not strategy_name:
      source = pinfo.get("source", {})
      if isinstance(source, dict) and source.get("strategy_name"):
        strategy_name = source["strategy_name"]

    # Execution window check (bar_timestamp is NYT)
    in_window: bool | None = None  # None = no filter configured
    if pinfo.get("session_enabled") and r.bar_timestamp:
      ts = r.bar_timestamp
      if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
      # dayofweek: Monday=0, Sunday=6
      day = ts.weekday()
      hour = ts.hour
      day_ok = day not in pinfo["exclude_days"]
      hour_ok = hour not in pinfo["exclude_hours"]
      in_window = day_ok and hour_ok

    # Find matching filled Order on Bybit
    filled_at = None
    if r.action == "entry" and r.created_at:
      window_end = r.created_at + timedelta(minutes=10)
      match = db.query(Order).filter(
        Order.portfolio_id == r.portfolio_id,
        Order.symbol == r.symbol,
        Order.role == "entry",
        Order.status == "filled",
        Order.created_at >= r.created_at,
        Order.created_at <= window_end,
      ).order_by(Order.created_at.asc()).first()
      if match:
        filled_at = match.updated_at

    # Find trade outcome (if a matching trade exists)
    trade_pnl: float | None = None
    trade_status: str | None = None
    if r.action == "entry" and r.created_at:
      # Look for a trade on this symbol+portfolio opened around signal time
      trade = db.query(Trade).filter(
        Trade.portfolio_id == r.portfolio_id,
        Trade.symbol == r.symbol,
        Trade.side == r.direction,
      ).order_by(Trade.opened_at.desc()).first()
      if trade and trade.opened_at:
        opened = trade.opened_at
        if isinstance(opened, str):
          opened = datetime.fromisoformat(opened)
        # Match if trade opened within 30min of signal
        sig_time = r.bar_timestamp or r.created_at
        if isinstance(sig_time, str):
          sig_time = datetime.fromisoformat(sig_time)
        if abs((opened - sig_time).total_seconds()) < 1800:
          trade_pnl = trade.realized_pnl
          trade_status = trade.status or ("open" if trade.closed_at is None else "closed")

    results.append({
      "id": r.id,
      "portfolio_id": r.portfolio_id,
      "portfolio_name": portfolio_name,
      "strategy_name": strategy_name,
      "batch_name": batch_name,
      "timeframe": timeframe,
      "symbol": r.symbol,
      "action": r.action,
      "direction": r.direction,
      "entry_id": r.entry_id,
      "bar_timestamp": r.bar_timestamp.isoformat() if r.bar_timestamp else None,
      "close_price": r.close_price,
      "sl_price": r.sl_price,
      "tp_price": r.tp_price,
      "trail_points": r.trail_points,
      "comment": r.comment,
      "status": r.status,
      "in_window": in_window,
      "trade_pnl": trade_pnl,
      "trade_status": trade_status,
      "created_at": r.created_at.isoformat() if r.created_at else None,
      "filled_at": filled_at.isoformat() if filled_at else None,
    })
  return results


class UpdateSignalStatusRequest(BaseModel):
  status: str


@router.patch("/live/signals/{signal_id}/status")
def update_signal_status(
  signal_id: int,
  req: UpdateSignalStatusRequest,
  db: Session = Depends(get_db),
):
  """Update a signal's status (new -> seen -> acted)."""
  if req.status not in ("new", "seen", "acted"):
    raise HTTPException(status_code=400, detail="Status must be new, seen, or acted")
  signal = db.get(LiveSignal, signal_id)
  if not signal:
    raise HTTPException(status_code=404, detail="Signal not found")
  signal.status = req.status
  db.commit()
  return {"id": signal.id, "status": signal.status}
