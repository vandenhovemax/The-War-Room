"""Journal routes — trade journal CRUD and strategy trade lookup."""
import json
from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.constants import COMMISSION_PCT
from backend.db.database import get_db
from backend.db.models import JournalTrade, Strategy, StrategyConfig
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


# --- Request Schemas ---

class JournalTradeCreate(BaseModel):
  symbol: str
  side: str  # "Long" or "Short"
  entry_price: float
  quantity: float
  risk_amount: float | None = None
  source: str = "manual"
  strategy_id: int | None = None
  config_id: int | None = None
  backtest_trade_id: int | None = None
  portfolio_id: int | None = None
  batch_id: int | None = None
  config_name: str | None = None
  live_trade_id: int | None = None
  sl_price: float | None = None
  tp_price: float | None = None
  tp2_price: float | None = None
  entry_time: str | None = None
  commission_rate: float = COMMISSION_PCT
  outcome: str | None = None
  expected_outcome: str | None = None
  expected_entry: float | None = None
  expected_sl: float | None = None
  expected_tp1: float | None = None
  expected_tp2: float | None = None
  expected_exit: float | None = None
  expected_commission: float | None = None
  notes: str = ""
  tags: list[str] = []
  rating: int = 0


class JournalTradeUpdate(BaseModel):
  symbol: str | None = None
  side: str | None = None
  entry_price: float | None = None
  quantity: float | None = None
  risk_amount: float | None = None
  sl_price: float | None = None
  tp_price: float | None = None
  tp2_price: float | None = None
  entry_time: str | None = None
  exit_price: float | None = None
  exit_time: str | None = None
  exit_reason: str | None = None
  status: str | None = None
  outcome: str | None = None
  expected_outcome: str | None = None
  actual_commission: float | None = None
  commission_rate: float | None = None
  notes: str | None = None
  tags: list[str] | None = None
  rating: int | None = None
  net_profit: float | None = None
  strategy_id: int | None = None
  config_id: int | None = None
  portfolio_id: int | None = None
  batch_id: int | None = None
  config_name: str | None = None
  live_trade_id: int | None = None


class LookupTradesRequest(BaseModel):
  strategy_id: int
  config_id: int | None = None
  symbol: str
  timeframe: str = "30"
  auto_download: bool = True
  n_recent: int = 20


# --- Helpers ---

def _trade_to_dict(
  t: JournalTrade,
  strategy_name: str | None = None,
  config_name: str | None = None,
) -> dict:
  """Serialize a JournalTrade to dict."""
  return {
    "id": t.id,
    "symbol": t.symbol,
    "side": t.side,
    "status": t.status,
    "source": t.source,
    "is_test": bool(t.is_test),
    "strategy_id": t.strategy_id,
    "strategy_name": strategy_name,
    "config_id": t.config_id,
    "config_name": config_name,
    "backtest_trade_id": t.backtest_trade_id,
    "portfolio_id": t.portfolio_id,
    "batch_id": t.batch_id,
    "config_name_snapshot": t.config_name,
    "live_trade_id": t.live_trade_id,
    "entry_price": t.entry_price,
    "quantity": t.quantity,
    "risk_amount": t.risk_amount,
    "sl_price": t.sl_price,
    "tp_price": t.tp_price,
    "tp2_price": t.tp2_price,
    "entry_time": t.entry_time.isoformat() if t.entry_time else None,
    "exit_price": t.exit_price,
    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
    "exit_reason": t.exit_reason,
    "outcome": t.outcome,
    "expected_outcome": t.expected_outcome,
    "expected_entry": t.expected_entry,
    "expected_sl": t.expected_sl,
    "expected_tp1": t.expected_tp1,
    "expected_tp2": t.expected_tp2,
    "expected_exit": t.expected_exit,
    "expected_pnl": t.expected_pnl,
    "net_profit": t.net_profit,
    "net_profit_pct": t.net_profit_pct,
    "commission_rate": t.commission_rate,
    "expected_commission": t.expected_commission,
    "actual_commission": t.actual_commission,
    "notes": t.notes,
    "tags": json.loads(t.tags) if t.tags else [],
    "rating": t.rating,
    "created_at": t.created_at.isoformat() if t.created_at else None,
    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
  }


def _calc_expected_commission(entry_price: float, quantity: float, rate: float) -> float:
  """Auto-calculate expected commission: rate × notional × 2 (entry + exit)."""
  notional = abs(entry_price * quantity)
  return round(notional * (rate / 100) * 2, 4)


def _calc_pnl(
  side: str, entry_price: float, exit_price: float, quantity: float,
) -> tuple[float, float]:
  """Calculate net P&L and P&L % for a closed trade."""
  if side == "Long":
    pnl = (exit_price - entry_price) * quantity
  else:
    pnl = (entry_price - exit_price) * quantity
  pct = (pnl / (entry_price * quantity)) * 100 if entry_price * quantity else 0
  return round(pnl, 4), round(pct, 4)


# --- Endpoints ---

@router.get("/calendar-summary")
def calendar_summary(
  year: int = Query(...),
  month: int = Query(...),
  db: Session = Depends(get_db),
):
  """Daily trade counts and P&L for a given month (calendar view)."""
  start = date(year, month, 1)
  if month == 12:
    end = date(year + 1, 1, 1)
  else:
    end = date(year, month + 1, 1)
  trades = (
    db.query(JournalTrade)
    .filter(JournalTrade.entry_time >= datetime(start.year, start.month, start.day))
    .filter(JournalTrade.entry_time < datetime(end.year, end.month, end.day))
    .filter(JournalTrade.is_test != True)
    .all()
  )
  daily: dict[str, dict] = defaultdict(lambda: {"trade_count": 0, "pnl": 0.0, "wins": 0, "losses": 0})
  for t in trades:
    if not t.entry_time:
      continue
    day_key = t.entry_time.strftime("%Y-%m-%d")
    daily[day_key]["trade_count"] += 1
    if t.status == "closed":
      daily[day_key]["pnl"] += t.net_profit or 0
      if (t.net_profit or 0) > 0:
        daily[day_key]["wins"] += 1
      else:
        daily[day_key]["losses"] += 1
  return [
    {"date": k, "trade_count": v["trade_count"], "pnl": round(v["pnl"], 2),
     "wins": v["wins"], "losses": v["losses"]}
    for k, v in sorted(daily.items())
  ]


@router.get("/monthly-recap")
def monthly_recap(
  year: int = Query(...),
  month: int = Query(...),
  db: Session = Depends(get_db),
):
  """Monthly KPIs and per-asset breakdown for the trading journal calendar."""
  start = date(year, month, 1)
  end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
  trades = (
    db.query(JournalTrade)
    .filter(JournalTrade.entry_time >= datetime(start.year, start.month, start.day))
    .filter(JournalTrade.entry_time < datetime(end.year, end.month, end.day))
    .filter(JournalTrade.is_test != True)
    .all()
  )
  if not trades:
    return {
      "year": year, "month": month,
      "total_trades": 0, "closed_trades": 0, "open_trades": 0,
      "total_pnl": 0, "win_count": 0, "loss_count": 0, "win_rate": 0,
      "avg_trade_pnl": 0, "best_day": None, "worst_day": None,
      "total_fees": 0, "assets": [],
    }
  closed = [t for t in trades if t.status == "closed"]
  total_pnl = sum(t.net_profit or 0 for t in closed)
  wins = sum(1 for t in closed if (t.net_profit or 0) > 0)
  losses = len(closed) - wins
  wr = (wins / len(closed) * 100) if closed else 0
  avg_pnl = (total_pnl / len(closed)) if closed else 0
  total_fees = sum(t.actual_commission or t.expected_commission or 0 for t in trades)

  # Best/worst day by PnL
  daily_pnl: dict[str, float] = defaultdict(float)
  for t in closed:
    if t.entry_time:
      daily_pnl[t.entry_time.strftime("%Y-%m-%d")] += t.net_profit or 0
  best_day = max(daily_pnl.items(), key=lambda x: x[1]) if daily_pnl else None
  worst_day = min(daily_pnl.items(), key=lambda x: x[1]) if daily_pnl else None

  # Per-asset breakdown
  asset_stats: dict[str, dict] = defaultdict(
    lambda: {"trades": 0, "pnl": 0.0, "wins": 0, "closed": 0}
  )
  for t in trades:
    a = asset_stats[t.symbol]
    a["trades"] += 1
    if t.status == "closed":
      a["closed"] += 1
      a["pnl"] += t.net_profit or 0
      if (t.net_profit or 0) > 0:
        a["wins"] += 1
  assets = [
    {
      "symbol": sym,
      "trades": d["trades"],
      "pnl": round(d["pnl"], 2),
      "win_rate": round((d["wins"] / d["closed"] * 100) if d["closed"] > 0 else 0, 1),
    }
    for sym, d in sorted(asset_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
  ]

  return {
    "year": year, "month": month,
    "total_trades": len(trades),
    "closed_trades": len(closed),
    "open_trades": len(trades) - len(closed),
    "total_pnl": round(total_pnl, 2),
    "win_count": wins,
    "loss_count": losses,
    "win_rate": round(wr, 1),
    "avg_trade_pnl": round(avg_pnl, 2),
    "best_day": {"date": best_day[0], "pnl": round(best_day[1], 2)} if best_day else None,
    "worst_day": {"date": worst_day[0], "pnl": round(worst_day[1], 2)} if worst_day else None,
    "total_fees": round(total_fees, 2),
    "assets": assets,
  }


@router.get("/list")
def list_trades(
  status: str | None = Query(None),
  symbol: str | None = Query(None),
  strategy_id: int | None = Query(None),
  portfolio_id: int | None = Query(None),
  trade_date: str | None = Query(None),
  include_test: bool = Query(False),
  limit: int = Query(100),
  db: Session = Depends(get_db),
):
  """List journal trades with optional filters."""
  query = db.query(JournalTrade)
  if not include_test:
    query = query.filter(JournalTrade.is_test != True)
  if status:
    query = query.filter(JournalTrade.status == status)
  if symbol:
    query = query.filter(JournalTrade.symbol == symbol)
  if strategy_id:
    query = query.filter(JournalTrade.strategy_id == strategy_id)
  if portfolio_id:
    query = query.filter(JournalTrade.portfolio_id == portfolio_id)
  if trade_date:
    try:
      d = date.fromisoformat(trade_date)
      day_start = datetime(d.year, d.month, d.day)
      day_end = datetime(d.year, d.month, d.day, 23, 59, 59)
      query = query.filter(JournalTrade.entry_time >= day_start, JournalTrade.entry_time <= day_end)
    except ValueError:
      pass
  trades = query.order_by(JournalTrade.created_at.desc()).limit(limit).all()
  # Resolve strategy + config names
  strat_ids = set(t.strategy_id for t in trades if t.strategy_id)
  strat_names: dict[int, str] = {}
  if strat_ids:
    strats = db.query(Strategy).filter(Strategy.id.in_(strat_ids)).all()
    strat_names = {s.id: s.name for s in strats}
  config_ids = set(t.config_id for t in trades if t.config_id)
  config_names: dict[int, str] = {}
  if config_ids:
    cfgs = db.query(StrategyConfig).filter(StrategyConfig.id.in_(config_ids)).all()
    config_names = {c.id: c.name for c in cfgs}
  return [
    _trade_to_dict(t, strat_names.get(t.strategy_id), config_names.get(t.config_id))
    for t in trades
  ]


@router.get("/stats")
def journal_stats(db: Session = Depends(get_db)):
  """Aggregate journal stats — total P&L, fees, trade counts."""
  trades = db.query(JournalTrade).filter(JournalTrade.is_test != True).all()
  if not trades:
    return {
      "total_trades": 0, "open_trades": 0, "closed_trades": 0,
      "total_pnl": 0, "win_count": 0, "loss_count": 0, "win_rate": 0,
      "total_expected_fees": 0, "total_actual_fees": 0, "fee_delta": 0,
    }
  open_count = sum(1 for t in trades if t.status == "open")
  closed = [t for t in trades if t.status == "closed"]
  total_pnl = sum(t.net_profit or 0 for t in closed)
  wins = sum(1 for t in closed if (t.net_profit or 0) > 0)
  losses = sum(1 for t in closed if (t.net_profit or 0) <= 0)
  wr = (wins / len(closed) * 100) if closed else 0
  total_expected = sum(t.expected_commission or 0 for t in trades)
  total_actual = sum(t.actual_commission or 0 for t in trades if t.actual_commission is not None)
  return {
    "total_trades": len(trades),
    "open_trades": open_count,
    "closed_trades": len(closed),
    "total_pnl": round(total_pnl, 2),
    "win_count": wins,
    "loss_count": losses,
    "win_rate": round(wr, 1),
    "total_expected_fees": round(total_expected, 2),
    "total_actual_fees": round(total_actual, 2),
    "fee_delta": round(total_actual - total_expected, 2),
  }


@router.get("/dashboard")
def journal_dashboard(
  symbol: str | None = Query(None),
  side: str | None = Query(None),
  strategy_id: int | None = Query(None),
  portfolio_id: int | None = Query(None),
  db: Session = Depends(get_db),
):
  """Trading dashboard — aggregated stats with filters."""
  query = db.query(JournalTrade).filter(JournalTrade.is_test != True)
  if symbol:
    query = query.filter(JournalTrade.symbol == symbol)
  if side:
    query = query.filter(JournalTrade.side == side)
  if strategy_id:
    query = query.filter(JournalTrade.strategy_id == strategy_id)
  if portfolio_id:
    query = query.filter(JournalTrade.portfolio_id == portfolio_id)
  trades = query.order_by(JournalTrade.entry_time.desc()).all()

  if not trades:
    return {
      "total_trades": 0, "open_trades": 0, "closed_trades": 0,
      "total_pnl": 0, "win_rate": 0,
      "total_expected_fees": 0, "total_actual_fees": 0, "fee_delta": 0,
      "by_symbol": [], "by_side": {}, "by_strategy": [],
      "monthly_pnl": {},
    }

  closed = [t for t in trades if t.status == "closed"]
  total_pnl = sum(t.net_profit or 0 for t in closed)
  wins = sum(1 for t in closed if (t.net_profit or 0) > 0)
  wr = (wins / len(closed) * 100) if closed else 0
  total_expected = sum(t.expected_commission or 0 for t in trades)
  total_actual = sum(t.actual_commission or 0 for t in trades if t.actual_commission is not None)

  # Per-symbol breakdown
  sym_stats: dict[str, dict] = {}
  for t in trades:
    s = sym_stats.setdefault(t.symbol, {"trades": 0, "pnl": 0, "wins": 0, "closed": 0, "fees_expected": 0, "fees_actual": 0})
    s["trades"] += 1
    s["fees_expected"] += t.expected_commission or 0
    if t.actual_commission is not None:
      s["fees_actual"] += t.actual_commission
    if t.status == "closed":
      s["closed"] += 1
      s["pnl"] += t.net_profit or 0
      if (t.net_profit or 0) > 0:
        s["wins"] += 1
  by_symbol = [
    {
      "symbol": sym,
      "trades": d["trades"],
      "pnl": round(d["pnl"], 2),
      "win_rate": round((d["wins"] / d["closed"] * 100) if d["closed"] > 0 else 0, 1),
      "fees_expected": round(d["fees_expected"], 2),
      "fees_actual": round(d["fees_actual"], 2),
    }
    for sym, d in sorted(sym_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
  ]

  # Per-side breakdown
  side_stats: dict[str, dict] = {}
  for t in trades:
    s = side_stats.setdefault(t.side, {"trades": 0, "pnl": 0, "wins": 0, "closed": 0})
    s["trades"] += 1
    if t.status == "closed":
      s["closed"] += 1
      s["pnl"] += t.net_profit or 0
      if (t.net_profit or 0) > 0:
        s["wins"] += 1
  by_side = {
    k: {"trades": v["trades"], "pnl": round(v["pnl"], 2),
        "win_rate": round((v["wins"] / v["closed"] * 100) if v["closed"] > 0 else 0, 1)}
    for k, v in side_stats.items()
  }

  # Per-strategy breakdown
  strat_ids = set(t.strategy_id for t in trades if t.strategy_id)
  strat_names: dict[int, str] = {}
  if strat_ids:
    strats = db.query(Strategy).filter(Strategy.id.in_(strat_ids)).all()
    strat_names = {s.id: s.name for s in strats}
  strat_stats: dict[int, dict] = {}
  for t in trades:
    sid = t.strategy_id or 0
    s = strat_stats.setdefault(sid, {"name": strat_names.get(sid, "Manual"), "trades": 0, "pnl": 0, "wins": 0, "closed": 0})
    s["trades"] += 1
    if t.status == "closed":
      s["closed"] += 1
      s["pnl"] += t.net_profit or 0
      if (t.net_profit or 0) > 0:
        s["wins"] += 1
  by_strategy = [
    {
      "strategy_id": sid,
      "name": d["name"],
      "trades": d["trades"],
      "pnl": round(d["pnl"], 2),
      "win_rate": round((d["wins"] / d["closed"] * 100) if d["closed"] > 0 else 0, 1),
    }
    for sid, d in sorted(strat_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
  ]

  # Monthly P&L
  monthly: dict[str, float] = {}
  for t in closed:
    if t.entry_time:
      key = t.entry_time.strftime("%Y-%m")
      monthly[key] = monthly.get(key, 0) + (t.net_profit or 0)
  monthly_pnl = {k: round(v, 2) for k, v in sorted(monthly.items())}

  return {
    "total_trades": len(trades),
    "open_trades": sum(1 for t in trades if t.status == "open"),
    "closed_trades": len(closed),
    "total_pnl": round(total_pnl, 2),
    "win_rate": round(wr, 1),
    "total_expected_fees": round(total_expected, 2),
    "total_actual_fees": round(total_actual, 2),
    "fee_delta": round(total_actual - total_expected, 2),
    "by_symbol": by_symbol,
    "by_side": by_side,
    "by_strategy": by_strategy,
    "monthly_pnl": monthly_pnl,
  }


@router.get("/{trade_id}")
def get_trade(trade_id: int, db: Session = Depends(get_db)):
  """Get a single journal trade."""
  trade = db.query(JournalTrade).filter(JournalTrade.id == trade_id).first()
  if not trade:
    raise HTTPException(status_code=404, detail="Trade not found")
  return _trade_to_dict(trade)


@router.post("")
def create_trade(req: JournalTradeCreate, db: Session = Depends(get_db)):
  """Create a new journal trade."""
  expected = _calc_expected_commission(req.entry_price, req.quantity, req.commission_rate)
  entry_time = None
  if req.entry_time:
    try:
      entry_time = datetime.fromisoformat(req.entry_time)
    except ValueError:
      entry_time = datetime.now(timezone.utc)

  trade = JournalTrade(
    symbol=req.symbol,
    side=req.side,
    source=req.source,
    strategy_id=req.strategy_id,
    config_id=req.config_id,
    backtest_trade_id=req.backtest_trade_id,
    portfolio_id=req.portfolio_id,
    batch_id=req.batch_id,
    config_name=req.config_name,
    live_trade_id=req.live_trade_id,
    entry_price=req.entry_price,
    quantity=req.quantity,
    risk_amount=req.risk_amount,
    sl_price=req.sl_price,
    tp_price=req.tp_price,
    tp2_price=req.tp2_price,
    entry_time=entry_time or datetime.now(timezone.utc),
    commission_rate=req.commission_rate,
    expected_commission=req.expected_commission if req.expected_commission is not None else expected,
    outcome=req.outcome,
    expected_outcome=req.expected_outcome,
    expected_entry=req.expected_entry,
    expected_sl=req.expected_sl,
    expected_tp1=req.expected_tp1,
    expected_tp2=req.expected_tp2,
    expected_exit=req.expected_exit,
    notes=req.notes,
    tags=json.dumps(req.tags),
    rating=req.rating,
  )
  db.add(trade)
  db.commit()
  db.refresh(trade)
  return _trade_to_dict(trade)


@router.put("/{trade_id}")
def update_trade(trade_id: int, req: JournalTradeUpdate, db: Session = Depends(get_db)):
  """Update a journal trade — edit fields, close trade, update commissions."""
  trade = db.query(JournalTrade).filter(JournalTrade.id == trade_id).first()
  if not trade:
    raise HTTPException(status_code=404, detail="Trade not found")

  # Update all editable fields
  if req.symbol is not None:
    trade.symbol = req.symbol
  if req.side is not None:
    trade.side = req.side
  if req.entry_price is not None:
    trade.entry_price = req.entry_price
  if req.quantity is not None:
    trade.quantity = req.quantity
  if req.risk_amount is not None:
    trade.risk_amount = req.risk_amount
  if req.sl_price is not None:
    trade.sl_price = req.sl_price
  if req.tp_price is not None:
    trade.tp_price = req.tp_price
  if req.tp2_price is not None:
    trade.tp2_price = req.tp2_price
  if req.entry_time is not None:
    try:
      trade.entry_time = datetime.fromisoformat(req.entry_time)
    except ValueError:
      pass
  if req.outcome is not None:
    trade.outcome = req.outcome
  if req.expected_outcome is not None:
    trade.expected_outcome = req.expected_outcome
  if req.actual_commission is not None:
    trade.actual_commission = req.actual_commission
  if req.commission_rate is not None:
    trade.commission_rate = req.commission_rate
  if req.notes is not None:
    trade.notes = req.notes
  if req.tags is not None:
    trade.tags = json.dumps(req.tags)
  if req.rating is not None:
    trade.rating = req.rating
  if req.strategy_id is not None:
    trade.strategy_id = req.strategy_id
  if req.config_id is not None:
    trade.config_id = req.config_id
  if req.portfolio_id is not None:
    trade.portfolio_id = req.portfolio_id
  if req.batch_id is not None:
    trade.batch_id = req.batch_id
  if req.config_name is not None:
    trade.config_name = req.config_name
  if req.live_trade_id is not None:
    trade.live_trade_id = req.live_trade_id
  # Allow direct net_profit override on already-closed trades (edit mode)
  if req.net_profit is not None and req.status != "closed":
    # Backfill expected_pnl if not yet set (legacy trades)
    if trade.expected_pnl is None and trade.exit_price is not None:
      auto_pnl, _ = _calc_pnl(trade.side, trade.entry_price, trade.exit_price, trade.quantity)
      trade.expected_pnl = auto_pnl
    trade.net_profit = req.net_profit
    trade.outcome = "Win" if req.net_profit > 0 else "Loss"

  # Close trade — calculate P&L
  if req.status == "closed" and req.exit_price is not None:
    trade.status = "closed"
    trade.exit_price = req.exit_price
    trade.exit_reason = req.exit_reason
    if req.exit_time:
      try:
        trade.exit_time = datetime.fromisoformat(req.exit_time)
      except ValueError:
        trade.exit_time = datetime.now(timezone.utc)
    else:
      trade.exit_time = datetime.now(timezone.utc)
    auto_pnl, pct = _calc_pnl(trade.side, trade.entry_price, trade.exit_price, trade.quantity)
    # Always preserve the auto-calculated P&L as expected_pnl
    trade.expected_pnl = auto_pnl
    # Use user-provided actual P&L if given, otherwise auto-calculate
    trade.net_profit = req.net_profit if req.net_profit is not None else auto_pnl
    trade.net_profit_pct = pct
    # Auto-derive outcome from final P&L
    trade.outcome = "Win" if trade.net_profit > 0 else "Loss"

  # Recalculate expected commission if price/qty/rate changed
  trade.expected_commission = _calc_expected_commission(
    trade.entry_price, trade.quantity, trade.commission_rate,
  )

  db.commit()
  db.refresh(trade)
  return _trade_to_dict(trade)


@router.delete("/{trade_id}")
def delete_trade(trade_id: int, db: Session = Depends(get_db)):
  """Delete a journal trade."""
  trade = db.query(JournalTrade).filter(JournalTrade.id == trade_id).first()
  if not trade:
    raise HTTPException(status_code=404, detail="Trade not found")
  db.delete(trade)
  db.commit()
  return {"deleted": True, "id": trade_id}


class BulkMarkTestRequest(BaseModel):
  trade_ids: list[int] | None = None
  date_before: str | None = None
  date_after: str | None = None
  is_test: bool = True


@router.put("/bulk-mark-test")
def bulk_mark_test(req: BulkMarkTestRequest, db: Session = Depends(get_db)):
  """Bulk mark/unmark trades as testing by IDs or date range."""
  query = db.query(JournalTrade)
  if req.trade_ids:
    query = query.filter(JournalTrade.id.in_(req.trade_ids))
  else:
    # Date range filter
    if req.date_before:
      try:
        cutoff = datetime.fromisoformat(req.date_before)
        query = query.filter(JournalTrade.entry_time <= cutoff)
      except ValueError:
        raise HTTPException(400, "Invalid date_before format")
    if req.date_after:
      try:
        cutoff = datetime.fromisoformat(req.date_after)
        query = query.filter(JournalTrade.entry_time >= cutoff)
      except ValueError:
        raise HTTPException(400, "Invalid date_after format")
    if not req.date_before and not req.date_after:
      raise HTTPException(400, "Provide trade_ids or date range")
  trades = query.all()
  count = 0
  for t in trades:
    t.is_test = req.is_test
    count += 1
  db.commit()
  return {"updated": count, "is_test": req.is_test}


class BulkDeleteRequest(BaseModel):
  trade_ids: list[int]


@router.post("/bulk-delete")
def bulk_delete_trades(req: BulkDeleteRequest, db: Session = Depends(get_db)):
  """Bulk delete trades by IDs."""
  count = db.query(JournalTrade).filter(JournalTrade.id.in_(req.trade_ids)).delete(synchronize_session=False)
  db.commit()
  return {"deleted": count}


@router.post("/lookup-trades")
async def lookup_trades(req: LookupTradesRequest, db: Session = Depends(get_db)):
  """Find recent strategy trades for a symbol.

  Optionally downloads missing candles, runs the strategy, returns
  recent trades for the user to pick from and add to journal.
  """
  import pandas as pd

  from backend.backtester.engine import BacktestConfig, BacktestEngine

  strategy = db.query(Strategy).filter(Strategy.id == req.strategy_id).first()
  if not strategy or not strategy.python_source:
    raise HTTPException(status_code=404, detail="Strategy not found or has no Python source")

  # Get config inputs if specified
  inputs = None
  if req.config_id:
    config = (
      db.query(StrategyConfig)
      .filter(StrategyConfig.id == req.config_id, StrategyConfig.strategy_id == req.strategy_id)
      .first()
    )
    if config and config.parameter_values:
      inputs = json.loads(config.parameter_values)

  # Auto-download missing candles
  if req.auto_download:
    try:
      from backend.data.bybit_downloader import BybitDownloader
      from backend.data.data_manager import DataManager
      downloader = BybitDownloader()
      manager = DataManager()
      latest = manager.get_latest_timestamp(req.symbol, req.timeframe)
      # Naive datetimes from DB are UTC — force tzinfo to avoid local-time shift
      if latest and latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
      since_ms = int(latest.timestamp() * 1000) + 1 if latest else None
      candles = await downloader.fetch_all_klines(req.symbol, req.timeframe, since_ms=since_ms)
      if candles:
        manager.upsert_candles(req.symbol, req.timeframe, candles)
        logger.info("Auto-downloaded %d candles for %s/%s", len(candles), req.symbol, req.timeframe)
    except Exception as e:
      logger.warning("Auto-download failed for %s: %s", req.symbol, e)

  # Load candles and run strategy
  from backend.api.routes.backtest import _load_candles_df, _to_nyt

  try:
    df = _load_candles_df(db, req.symbol, req.timeframe)
  except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e))

  df = _to_nyt(df)

  # Parse extra timeframes from strategy source
  extra_tf_dfs: dict[str, pd.DataFrame] = {}
  source = strategy.python_source
  if "request_security" in source:
    import re
    tfs = set(re.findall(r'request_security\(["\'](\d+)["\']', source))
    for tf in tfs:
      if tf != req.timeframe:
        try:
          extra_tf_dfs[tf] = _to_nyt(_load_candles_df(db, req.symbol, tf))
        except ValueError:
          logger.warning("No %s data for %s — skipping", tf, req.symbol)

  from backend.utils.safe_exec import safe_exec
  strategy_cls = safe_exec(source, label="journal_backtest")
  if not strategy_cls:
    raise HTTPException(status_code=400, detail="Strategy code does not define TranspiledStrategy")

  config = BacktestConfig(initial_capital=10000, commission_pct=COMMISSION_PCT)
  engine = BacktestEngine(config)
  result = engine.run(
    strategy_cls(), df, symbol=req.symbol, timeframe=req.timeframe,
    inputs=inputs, extra_timeframes=extra_tf_dfs,
  )

  # Pair exit fills (each has entry_price, entry_time, sl_price, tp_price, pnl)
  exit_fills = [f for f in result.trades if f.action == "exit"]
  recent = exit_fills[-req.n_recent:]
  total_exit_count = len(exit_fills)
  return {
    "symbol": req.symbol,
    "timeframe": req.timeframe,
    "total_trades": total_exit_count,
    "trades": [
      {
        "trade_num": total_exit_count - len(recent) + i + 1,
        "side": f.side.capitalize(),
        "entry_time": f.entry_time.isoformat() if f.entry_time else None,
        "exit_time": f.timestamp.isoformat() if f.timestamp else None,
        "entry_price": f.entry_price,
        "exit_price": f.price,
        "quantity": f.qty,
        "net_profit": round(f.pnl, 2),
        "exit_reason": f.label,
        "sl_price": f.sl_price if f.sl_price else None,
        "tp_price": f.tp_price if f.tp_price else None,
        "commission": round(f.commission, 4),
      }
      for i, f in enumerate(recent)
    ],
  }
