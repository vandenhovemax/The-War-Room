"""Dashboard and health check routes."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import JournalTrade, Portfolio, Position, Strategy, Trade
from backend.utils import heartbeats

router = APIRouter()


# Max age (seconds) before a component's heartbeat is considered stale.
# candle_sync: wakes on bar close; largest normal TF in use is 30m, so
#   35 min covers 30m bar + probe + download + margin.
# engine_reconciliation: ticks every ~5s (reconciliation_interval_seconds);
#   60s gives 12x slack before we flag it.
HEARTBEAT_MAX_AGE = {
  "candle_sync": 35 * 60,
  "engine_reconciliation": 60,
  # download_worker beats every ~60s at the top of its auto-refresh loop.
  # 180s allows for one missed beat plus buffer before tripping degraded.
  "download_worker": 180,
}


@router.get("/health")
async def health_check():
  """Liveness + component heartbeat status.

  Shape stays backwards-compatible: `status` is still `"ok"` when all
  components are healthy (or have never beaten). `"degraded"` means at
  least one component that HAS beaten before has gone stale.

  Components that have never beaten are reported as `"unknown"` and do
  NOT trip degraded — so the endpoint stays green on a fresh install
  where candle_sync hasn't run yet.
  """
  components: dict[str, dict] = {}
  any_stale = False
  for name, max_age in HEARTBEAT_MAX_AGE.items():
    age = heartbeats.age_seconds(name)
    if age is None:
      components[name] = {"status": "unknown", "age_seconds": None, "max_age_seconds": max_age}
    elif age > max_age:
      components[name] = {"status": "stale", "age_seconds": round(age, 1), "max_age_seconds": max_age}
      any_stale = True
    else:
      components[name] = {"status": "ok", "age_seconds": round(age, 1), "max_age_seconds": max_age}

  return {
    "status": "degraded" if any_stale else "ok",
    "service": "warroom",
    "components": components,
  }


@router.get("/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db)):
  return {
    "strategies": db.query(Strategy).count(),
    "portfolios": db.query(Portfolio).count(),
    "open_positions": db.query(Position).filter(Position.is_open).count(),
    "total_trades": db.query(Trade).filter(Trade.is_test != True).count(),
  }


@router.get("/dashboard/account-equity")
def account_equity_curve(
  period: str = Query("3m", description="w, m, 3m, 6m, y, all"),
  portfolio_id: int | None = Query(None),
  db: Session = Depends(get_db),
):
  """Account equity curve from closed live trades (Trade table).

  Returns cumulative PnL over time, grouped by day.
  """
  now = datetime.now(timezone.utc)
  period_map = {"w": 7, "m": 30, "3m": 90, "6m": 180, "y": 365}
  days = period_map.get(period, 0)

  query = db.query(Trade).filter(Trade.closed_at.isnot(None), Trade.is_test != True)
  if portfolio_id:
    query = query.filter(Trade.portfolio_id == portfolio_id)
  if days > 0:
    cutoff = now - timedelta(days=days)
    query = query.filter(Trade.closed_at >= cutoff)
  trades = query.order_by(Trade.closed_at.asc()).all()

  if not trades:
    return {"data": [], "period": period}

  # Group by date and compute daily PnL
  daily: dict[str, float] = {}
  for t in trades:
    day = t.closed_at.strftime("%Y-%m-%d")
    daily[day] = daily.get(day, 0) + (t.realized_pnl or 0)

  # Build cumulative equity curve
  data = []
  cumulative = 0.0
  peak = 0.0
  for day in sorted(daily.keys()):
    cumulative += daily[day]
    peak = max(peak, cumulative)
    dd = cumulative - peak
    data.append({
      "date": day,
      "pnl": round(daily[day], 2),
      "equity": round(cumulative, 2),
      "drawdown": round(dd, 2),
    })

  return {"data": data, "period": period}


@router.get("/dashboard/account-kpis")
def account_kpis(
  portfolio_id: int | None = Query(None),
  db: Session = Depends(get_db),
):
  """Aggregate KPIs from all closed live trades (Trade table)."""
  query = db.query(Trade).filter(Trade.is_test != True)
  if portfolio_id:
    query = query.filter(Trade.portfolio_id == portfolio_id)
  trades = query.all()
  if not trades:
    return {
      "total_trades": 0, "total_pnl": 0, "win_rate": 0, "avg_trade": 0,
      "profit_factor": None, "best_trade": 0, "worst_trade": 0,
      "max_drawdown": 0, "win_count": 0, "loss_count": 0,
      "avg_win": 0, "avg_loss": 0, "open_positions": 0,
    }

  pnls = [t.realized_pnl or 0 for t in trades]
  wins = [p for p in pnls if p > 0]
  losses = [p for p in pnls if p <= 0]
  gross_profit = sum(wins)
  gross_loss = abs(sum(losses))

  # Max drawdown from cumulative equity
  cumulative = 0.0
  peak = 0.0
  max_dd = 0.0
  for p in pnls:
    cumulative += p
    peak = max(peak, cumulative)
    dd = cumulative - peak
    max_dd = min(max_dd, dd)

  open_pos = db.query(Position).filter(Position.is_open).count()

  return {
    "total_trades": len(pnls),
    "total_pnl": round(sum(pnls), 2),
    "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
    "avg_trade": round(sum(pnls) / len(pnls), 2) if pnls else 0,
    "profit_factor": round(min(gross_profit / gross_loss, 10.0), 2) if gross_loss > 0 else None,
    "best_trade": round(max(pnls), 2) if pnls else 0,
    "worst_trade": round(min(pnls), 2) if pnls else 0,
    "max_drawdown": round(max_dd, 2),
    "win_count": len(wins),
    "loss_count": len(losses),
    "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
    "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
    "open_positions": open_pos,
  }


@router.get("/dashboard/daily-pnl-heatmap")
def daily_pnl_heatmap(
  months: int = Query(3, description="Number of months to include"),
  portfolio_id: int | None = Query(None),
  db: Session = Depends(get_db),
):
  """Daily P&L heatmap data from JournalTrade for dashboard calendar."""
  now = datetime.now(timezone.utc)
  cutoff = now - timedelta(days=months * 31)

  query = db.query(JournalTrade).filter(
    JournalTrade.status == "closed",
    JournalTrade.is_test != True,
    JournalTrade.exit_time >= cutoff,
  )
  if portfolio_id:
    query = query.filter(JournalTrade.portfolio_id == portfolio_id)
  trades = query.all()

  daily: dict[str, dict] = defaultdict(
    lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0}
  )
  for t in trades:
    if not t.exit_time:
      continue
    day = t.exit_time.strftime("%Y-%m-%d")
    daily[day]["trades"] += 1
    daily[day]["pnl"] += t.net_profit or 0
    if (t.net_profit or 0) > 0:
      daily[day]["wins"] += 1
    else:
      daily[day]["losses"] += 1

  return {
    "days": [
      {
        "date": k,
        "pnl": round(v["pnl"], 2),
        "trades": v["trades"],
        "wins": v["wins"],
        "losses": v["losses"],
      }
      for k, v in sorted(daily.items())
    ],
    "months": months,
  }


@router.get("/dashboard/performance-breakdown")
def performance_breakdown(
  portfolio_id: int | None = Query(None),
  db: Session = Depends(get_db),
):
  """Performance breakdown — win/loss distribution, L/S split, streaks."""
  query = db.query(JournalTrade).filter(
    JournalTrade.status == "closed",
    JournalTrade.is_test != True,
  )
  if portfolio_id:
    query = query.filter(JournalTrade.portfolio_id == portfolio_id)
  trades = query.order_by(JournalTrade.exit_time.asc()).all()

  if not trades:
    return {
      "long_pnl": 0, "short_pnl": 0, "long_trades": 0, "short_trades": 0,
      "long_win_rate": 0, "short_win_rate": 0,
      "best_trade": None, "worst_trade": None,
      "current_streak": 0, "max_win_streak": 0, "max_loss_streak": 0,
      "win_distribution": [], "loss_distribution": [],
    }

  # Long vs Short
  long_trades = [t for t in trades if t.side == "Long"]
  short_trades = [t for t in trades if t.side == "Short"]
  long_pnl = sum(t.net_profit or 0 for t in long_trades)
  short_pnl = sum(t.net_profit or 0 for t in short_trades)
  long_wins = sum(1 for t in long_trades if (t.net_profit or 0) > 0)
  short_wins = sum(1 for t in short_trades if (t.net_profit or 0) > 0)

  # Best/worst trade
  best = max(trades, key=lambda t: t.net_profit or 0)
  worst = min(trades, key=lambda t: t.net_profit or 0)

  # Streaks
  current_streak = 0
  max_win_streak = 0
  max_loss_streak = 0
  win_streak = 0
  loss_streak = 0
  for t in trades:
    if (t.net_profit or 0) > 0:
      win_streak += 1
      loss_streak = 0
      max_win_streak = max(max_win_streak, win_streak)
    else:
      loss_streak += 1
      win_streak = 0
      max_loss_streak = max(max_loss_streak, loss_streak)
  current_streak = win_streak if win_streak > 0 else -loss_streak

  # P&L distribution buckets for histogram
  pnls = [t.net_profit or 0 for t in trades]
  win_pnls = sorted([p for p in pnls if p > 0])
  loss_pnls = sorted([p for p in pnls if p <= 0])

  def _bucket(values: list[float], n_buckets: int = 8) -> list[dict]:
    if not values:
      return []
    mn, mx = min(values), max(values)
    if mn == mx:
      return [{"min": round(mn, 2), "max": round(mx, 2), "count": len(values)}]
    step = (mx - mn) / n_buckets
    buckets = []
    for i in range(n_buckets):
      lo = mn + i * step
      hi = mn + (i + 1) * step
      cnt = sum(1 for v in values if lo <= v < hi) if i < n_buckets - 1 else sum(1 for v in values if lo <= v <= hi)
      buckets.append({"min": round(lo, 2), "max": round(hi, 2), "count": cnt})
    return buckets

  return {
    "long_pnl": round(long_pnl, 2),
    "short_pnl": round(short_pnl, 2),
    "long_trades": len(long_trades),
    "short_trades": len(short_trades),
    "long_win_rate": round(long_wins / len(long_trades) * 100, 1) if long_trades else 0,
    "short_win_rate": round(short_wins / len(short_trades) * 100, 1) if short_trades else 0,
    "best_trade": {
      "symbol": best.symbol, "pnl": round(best.net_profit or 0, 2),
      "side": best.side, "date": best.exit_time.isoformat() if best.exit_time else None,
    },
    "worst_trade": {
      "symbol": worst.symbol, "pnl": round(worst.net_profit or 0, 2),
      "side": worst.side, "date": worst.exit_time.isoformat() if worst.exit_time else None,
    },
    "current_streak": current_streak,
    "max_win_streak": max_win_streak,
    "max_loss_streak": max_loss_streak,
    "win_distribution": _bucket(win_pnls),
    "loss_distribution": _bucket(loss_pnls),
  }
