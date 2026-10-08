"""Tax Records route — CRUD for comprehensive trade records used for tax reporting.

Supports:
  - List all tax records (with optional date/symbol filters)
  - Create manual tax records
  - Update / delete individual records
  - Bulk-import completed live trades from the trades table
  - Export as CSV
  - Tax withholding planner (Quebec/Canada brackets, USD→CAD)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc

from backend.db.database import SessionLocal
from backend.db.models import JournalTrade, Portfolio, TaxExpense, TaxRecord, Trade
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


# ── Pydantic schemas ──────────────────────────────────────────────

class TaxRecordCreate(BaseModel):
  symbol: str
  side: str
  strategy_name: str = ""
  quantity: float
  leverage: int = 1
  margin_used: float = 0.0
  entry_price: float
  entry_time: str  # ISO datetime string
  entry_order_type: str = ""
  exit_price: Optional[float] = None
  exit_time: Optional[str] = None
  exit_order_type: str = ""
  exit_reason: str = ""
  entry_commission: float = 0.0
  exit_commission: float = 0.0
  funding_fee: float = 0.0
  gross_pnl: float = 0.0
  net_pnl: float = 0.0
  net_pnl_pct: float = 0.0
  outcome: str = ""
  portfolio_name: str = ""
  notes: str = ""


class TaxRecordUpdate(BaseModel):
  symbol: Optional[str] = None
  side: Optional[str] = None
  strategy_name: Optional[str] = None
  quantity: Optional[float] = None
  leverage: Optional[int] = None
  margin_used: Optional[float] = None
  entry_price: Optional[float] = None
  entry_time: Optional[str] = None
  entry_order_type: Optional[str] = None
  exit_price: Optional[float] = None
  exit_time: Optional[str] = None
  exit_order_type: Optional[str] = None
  exit_reason: Optional[str] = None
  entry_commission: Optional[float] = None
  exit_commission: Optional[float] = None
  funding_fee: Optional[float] = None
  gross_pnl: Optional[float] = None
  net_pnl: Optional[float] = None
  net_pnl_pct: Optional[float] = None
  outcome: Optional[str] = None
  portfolio_name: Optional[str] = None
  notes: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────

def _parse_dt(s: str | None) -> datetime | None:
  if not s:
    return None
  # Accept ISO format with or without Z/timezone
  s = s.replace("Z", "+00:00")
  try:
    return datetime.fromisoformat(s)
  except ValueError:
    return None


def _row_to_dict(r: TaxRecord) -> dict:
  return {
    "id": r.id,
    "source": r.source,
    "live_trade_id": r.live_trade_id,
    "journal_trade_id": r.journal_trade_id,
    "portfolio_id": r.portfolio_id,
    "portfolio_name": r.portfolio_name or "",
    "symbol": r.symbol,
    "side": r.side,
    "strategy_name": r.strategy_name or "",
    "quantity": r.quantity,
    "leverage": r.leverage,
    "margin_used": r.margin_used,
    "entry_price": r.entry_price,
    "entry_time": r.entry_time.isoformat() if r.entry_time else None,
    "entry_order_type": r.entry_order_type or "",
    "exit_price": r.exit_price,
    "exit_time": r.exit_time.isoformat() if r.exit_time else None,
    "exit_order_type": r.exit_order_type or "",
    "exit_reason": r.exit_reason or "",
    "entry_commission": r.entry_commission,
    "exit_commission": r.exit_commission,
    "total_commission": r.total_commission,
    "funding_fee": r.funding_fee,
    "gross_pnl": r.gross_pnl,
    "net_pnl": r.net_pnl,
    "net_pnl_pct": r.net_pnl_pct,
    "outcome": r.outcome or "",
    "notes": r.notes or "",
    "created_at": r.created_at.isoformat() if r.created_at else None,
    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
  }


# ── Routes ────────────────────────────────────────────────────────

@router.get("")
def list_tax_records(
  symbol: str = Query("", description="Filter by symbol"),
  year: int = Query(0, description="Filter by year (entry_time)"),
):
  """List all tax records, newest first. Optional filters."""
  db = SessionLocal()
  try:
    q = db.query(TaxRecord).order_by(desc(TaxRecord.entry_time))
    if symbol:
      q = q.filter(TaxRecord.symbol == symbol)
    if year > 0:
      q = q.filter(
        TaxRecord.entry_time >= datetime(year, 1, 1),
        TaxRecord.entry_time < datetime(year + 1, 1, 1),
      )
    rows = q.all()
    return {"count": len(rows), "records": [_row_to_dict(r) for r in rows]}
  finally:
    db.close()


@router.post("")
def create_tax_record(data: TaxRecordCreate):
  """Create a manual tax record."""
  db = SessionLocal()
  try:
    total_comm = data.entry_commission + data.exit_commission
    rec = TaxRecord(
      source="manual",
      symbol=data.symbol.upper().strip(),
      side=data.side,
      strategy_name=data.strategy_name,
      quantity=data.quantity,
      leverage=data.leverage,
      margin_used=data.margin_used,
      entry_price=data.entry_price,
      entry_time=_parse_dt(data.entry_time),
      entry_order_type=data.entry_order_type,
      exit_price=data.exit_price,
      exit_time=_parse_dt(data.exit_time),
      exit_order_type=data.exit_order_type,
      exit_reason=data.exit_reason,
      entry_commission=data.entry_commission,
      exit_commission=data.exit_commission,
      total_commission=total_comm,
      funding_fee=data.funding_fee,
      gross_pnl=data.gross_pnl,
      net_pnl=data.net_pnl,
      net_pnl_pct=data.net_pnl_pct,
      outcome=data.outcome or ("Win" if data.net_pnl > 0 else "Loss" if data.net_pnl < 0 else "Breakeven"),
      portfolio_name=data.portfolio_name,
      notes=data.notes,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return {"status": "created", "record": _row_to_dict(rec)}
  finally:
    db.close()


@router.put("/{record_id}")
def update_tax_record(record_id: int, data: TaxRecordUpdate):
  """Update an existing tax record."""
  db = SessionLocal()
  try:
    rec = db.query(TaxRecord).filter(TaxRecord.id == record_id).first()
    if not rec:
      return {"status": "error", "detail": "Record not found"}

    changes = data.model_dump(exclude_none=True)

    # Handle datetime string → datetime conversion
    for dt_field in ("entry_time", "exit_time"):
      if dt_field in changes:
        changes[dt_field] = _parse_dt(changes[dt_field])

    for key, val in changes.items():
      setattr(rec, key, val)

    # Recalculate total commission
    rec.total_commission = (rec.entry_commission or 0) + (rec.exit_commission or 0)

    # Auto-set outcome if not explicitly provided
    if "outcome" not in changes and "net_pnl" in changes:
      rec.outcome = "Win" if rec.net_pnl > 0 else "Loss" if rec.net_pnl < 0 else "Breakeven"

    rec.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(rec)
    return {"status": "updated", "record": _row_to_dict(rec)}
  finally:
    db.close()


@router.delete("/{record_id}")
def delete_tax_record(record_id: int):
  """Delete a tax record."""
  db = SessionLocal()
  try:
    rec = db.query(TaxRecord).filter(TaxRecord.id == record_id).first()
    if not rec:
      return {"status": "error", "detail": "Record not found"}
    db.delete(rec)
    db.commit()
    return {"status": "deleted", "id": record_id}
  finally:
    db.close()


@router.post("/import-live")
def import_live_trades():
  """Import all completed live trades that aren't already in tax_records.

  Matches on live_trade_id to avoid duplicates. Returns count of new records.
  """
  db = SessionLocal()
  try:
    # Get IDs already imported
    existing_ids = {
      r[0] for r in
      db.query(TaxRecord.live_trade_id)
      .filter(TaxRecord.live_trade_id.isnot(None))
      .all()
    }

    # Get all closed live trades (not test trades)
    trades = (
      db.query(Trade)
      .filter(Trade.is_test == False)  # noqa: E712
      .order_by(Trade.closed_at)
      .all()
    )

    # Build portfolio name lookup
    portfolio_ids = {t.portfolio_id for t in trades if t.portfolio_id}
    portfolio_names = {}
    if portfolio_ids:
      portfolios = db.query(Portfolio).filter(Portfolio.id.in_(portfolio_ids)).all()
      portfolio_names = {p.id: p.name for p in portfolios}

    imported = 0
    for t in trades:
      if t.id in existing_ids:
        continue

      total_comm = (t.entry_commission or 0) + (t.exit_commission or 0)
      funding = t.funding_fee or 0
      # gross = realized_pnl + all fees (reverse the fee deduction)
      gross = (t.realized_pnl or 0) + total_comm + abs(funding)

      rec = TaxRecord(
        source="live",
        live_trade_id=t.id,
        portfolio_id=t.portfolio_id,
        portfolio_name=portfolio_names.get(t.portfolio_id, ""),
        symbol=t.symbol,
        side=t.side,
        strategy_name=t.strategy_name or "",
        quantity=t.quantity,
        leverage=1,  # not stored on Trade — will show 1
        margin_used=0.0,
        entry_price=t.entry_price,
        entry_time=t.opened_at,
        entry_order_type="",
        exit_price=t.exit_price,
        exit_time=t.closed_at,
        exit_order_type="",
        exit_reason=t.close_reason or "",
        entry_commission=t.entry_commission or 0,
        exit_commission=t.exit_commission or 0,
        total_commission=total_comm,
        funding_fee=funding,
        gross_pnl=round(gross, 4),
        net_pnl=round(t.realized_pnl or 0, 4),
        net_pnl_pct=0.0,
        outcome="Win" if (t.realized_pnl or 0) > 0 else "Loss" if (t.realized_pnl or 0) < 0 else "Breakeven",
      )
      db.add(rec)
      imported += 1

    if imported:
      db.commit()

    return {"status": "ok", "imported": imported, "already_existed": len(existing_ids)}
  finally:
    db.close()


@router.post("/import-journal")
def import_journal_trades():
  """Import closed journal trades that aren't already in tax_records.

  Matches on journal_trade_id to avoid duplicates. Returns count of new records.
  """
  db = SessionLocal()
  try:
    # Get journal IDs already imported
    existing_ids = {
      r[0] for r in
      db.query(TaxRecord.journal_trade_id)
      .filter(TaxRecord.journal_trade_id.isnot(None))
      .all()
    }

    # Get all closed, non-test journal trades
    trades = (
      db.query(JournalTrade)
      .filter(JournalTrade.status == "closed")
      .filter(JournalTrade.is_test != True)  # noqa: E712
      .order_by(JournalTrade.entry_time)
      .all()
    )

    imported = 0
    for t in trades:
      if t.id in existing_ids:
        continue

      commission = t.actual_commission or t.expected_commission or 0
      net = t.net_profit or 0
      gross = net + abs(commission)

      rec = TaxRecord(
        source="journal",
        journal_trade_id=t.id,
        portfolio_id=t.portfolio_id,
        symbol=t.symbol,
        side=t.side,
        strategy_name="",
        quantity=t.quantity,
        leverage=1,
        margin_used=0.0,
        entry_price=t.entry_price,
        entry_time=t.entry_time,
        entry_order_type="",
        exit_price=t.exit_price,
        exit_time=t.exit_time,
        exit_order_type="",
        exit_reason=t.exit_reason or "",
        entry_commission=commission / 2,
        exit_commission=commission / 2,
        total_commission=commission,
        funding_fee=0.0,
        gross_pnl=round(gross, 4),
        net_pnl=round(net, 4),
        net_pnl_pct=round(t.net_profit_pct or 0, 4),
        outcome=t.outcome or ("Win" if net > 0 else "Loss" if net < 0 else "Breakeven"),
        notes=t.notes or "",
      )
      db.add(rec)
      imported += 1

    if imported:
      db.commit()

    return {"status": "ok", "imported": imported, "already_existed": len(existing_ids)}
  finally:
    db.close()


@router.get("/export-csv")
def export_csv(year: int = Query(0)):
  """Export tax records as CSV. Optional year filter."""
  import csv
  import io

  db = SessionLocal()
  try:
    q = db.query(TaxRecord).order_by(TaxRecord.entry_time)
    if year > 0:
      q = q.filter(
        TaxRecord.entry_time >= datetime(year, 1, 1),
        TaxRecord.entry_time < datetime(year + 1, 1, 1),
      )
    rows = q.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
      "ID", "Source", "Symbol", "Side", "Strategy", "Portfolio",
      "Quantity", "Leverage", "Margin Used",
      "Entry Price", "Entry Time", "Entry Order Type",
      "Exit Price", "Exit Time", "Exit Order Type", "Exit Reason",
      "Entry Commission", "Exit Commission", "Total Commission",
      "Funding Fee", "Gross PnL", "Net PnL", "Net PnL %", "Outcome",
      "Notes",
    ])
    for r in rows:
      writer.writerow([
        r.id, r.source, r.symbol, r.side, r.strategy_name or "", r.portfolio_name or "",
        r.quantity, r.leverage, r.margin_used,
        r.entry_price, r.entry_time.isoformat() if r.entry_time else "",
        r.entry_order_type or "",
        r.exit_price or "", r.exit_time.isoformat() if r.exit_time else "",
        r.exit_order_type or "", r.exit_reason or "",
        r.entry_commission, r.exit_commission, r.total_commission,
        r.funding_fee, r.gross_pnl, r.net_pnl, r.net_pnl_pct,
        r.outcome or "", r.notes or "",
      ])

    filename = f"tax_records_{year}.csv" if year else "tax_records_all.csv"
    output.seek(0)
    return StreamingResponse(
      iter([output.getvalue()]),
      media_type="text/csv",
      headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
  finally:
    db.close()


@router.get("/summary")
def tax_summary(year: int = Query(0)):
  """Quick summary stats for the tax tab header."""
  db = SessionLocal()
  try:
    q = db.query(TaxRecord)
    if year > 0:
      q = q.filter(
        TaxRecord.entry_time >= datetime(year, 1, 1),
        TaxRecord.entry_time < datetime(year + 1, 1, 1),
      )
    rows = q.all()

    total_pnl = sum(r.net_pnl or 0 for r in rows)
    total_fees = sum(r.total_commission or 0 for r in rows)
    total_funding = sum(r.funding_fee or 0 for r in rows)
    wins = sum(1 for r in rows if (r.net_pnl or 0) > 0)
    losses = sum(1 for r in rows if (r.net_pnl or 0) < 0)

    return {
      "total_records": len(rows),
      "total_net_pnl": round(total_pnl, 2),
      "total_fees": round(total_fees, 2),
      "total_funding": round(total_funding, 2),
      "wins": wins,
      "losses": losses,
    }
  finally:
    db.close()


# ── Tax Withholding Planner (Quebec / Canada) ───────────────────

# 2026 Federal brackets (with 16.5% Quebec abatement applied)
FEDERAL_BRACKETS_2026 = [
  (58_523, 0.1169),   # 14% × 0.835
  (117_045, 0.1712),  # 20.5% × 0.835
  (181_440, 0.2171),  # 26% × 0.835
  (258_482, 0.2422),  # 29% × 0.835
  (float("inf"), 0.2756),  # 33% × 0.835
]

# 2026 Quebec provincial brackets
QUEBEC_BRACKETS_2026 = [
  (54_345, 0.14),
  (108_680, 0.19),
  (132_245, 0.24),
  (float("inf"), 0.2575),
]

MONTH_NAMES = [
  "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _calc_bracket_tax(income: float, brackets: list[tuple[float, float]]) -> float:
  """Calculate progressive tax for a given income using bracket table."""
  if income <= 0:
    return 0.0
  tax = 0.0
  prev_limit = 0.0
  for limit, rate in brackets:
    taxable = min(income, limit) - prev_limit
    if taxable <= 0:
      break
    tax += taxable * rate
    prev_limit = limit
  return tax


def _calc_combined_tax(income_cad: float) -> dict:
  """Calculate combined federal + Quebec tax on business income (CAD)."""
  federal = _calc_bracket_tax(income_cad, FEDERAL_BRACKETS_2026)
  quebec = _calc_bracket_tax(income_cad, QUEBEC_BRACKETS_2026)
  total = federal + quebec
  effective_rate = (total / income_cad * 100) if income_cad > 0 else 0.0

  # Determine current marginal bracket
  marginal = 0.0
  for limit, rate in FEDERAL_BRACKETS_2026:
    if income_cad <= limit:
      marginal = rate
      break
  for limit, rate in QUEBEC_BRACKETS_2026:
    if income_cad <= limit:
      marginal += rate
      break

  return {
    "federal": round(federal, 2),
    "quebec": round(quebec, 2),
    "total": round(total, 2),
    "effective_rate": round(effective_rate, 2),
    "marginal_rate": round(marginal * 100, 2),
  }


def _fetch_usd_cad_rate() -> float:
  """Fetch latest USD/CAD rate from Bank of Canada. Falls back to 1.36."""
  try:
    url = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json?recent=1"
    resp = httpx.get(url, timeout=5.0)
    data = resp.json()
    obs = data.get("observations", [])
    if obs:
      return float(obs[-1]["FXUSDCAD"]["v"])
  except Exception as e:
    logger.warning(f"Failed to fetch USD/CAD rate: {e}")
  return 1.36  # reasonable fallback


def _fetch_daily_rates(year: int) -> dict[str, float]:
  """Fetch all daily USD/CAD rates for a year from Bank of Canada Valet API.

  Returns dict mapping date strings ("YYYY-MM-DD") to rates.
  Only business days are published — weekends/holidays are absent.
  Falls back to empty dict on failure.
  """
  try:
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    url = (
      "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"
      f"?start_date={start}&end_date={end}"
    )
    resp = httpx.get(url, timeout=10.0)
    data = resp.json()
    rates: dict[str, float] = {}
    for obs in data.get("observations", []):
      date_str = obs.get("d", "")
      val = obs.get("FXUSDCAD", {}).get("v")
      if date_str and val:
        rates[date_str] = float(val)
    logger.info("Fetched %d daily USD/CAD rates for %d", len(rates), year)
    return rates
  except Exception as e:
    logger.warning("Failed to fetch daily USD/CAD rates for %d: %s", year, e)
    return {}


def _get_rate_for_date(daily_rates: dict[str, float], dt: datetime | None) -> float:
  """Get USD/CAD rate for a specific date.

  If the date falls on a weekend or holiday (no rate published),
  walks backwards up to 7 days to find the most recent business day rate.
  This is the CRA-accepted convention: use the Bank of Canada rate on or
  immediately preceding the transaction date.
  Falls back to 1.36 if no rate found within the window.
  """
  if not dt or not daily_rates:
    return 1.36

  from datetime import timedelta

  date = dt.date() if hasattr(dt, "date") else dt
  for i in range(8):  # today + up to 7 days back
    key = (date - timedelta(days=i)).isoformat()
    if key in daily_rates:
      return daily_rates[key]
  # Shouldn't happen unless data is missing entirely for a long period
  return 1.36


@router.get("/tax-withholding")
def tax_withholding(
  year: int = Query(0, description="Tax year (default: current year)"),
  usd_cad_rate: float = Query(0, description="USD/CAD rate. 0 = auto-fetch"),
  business_expenses: float = Query(0, description="Business expenses in CAD (year-end deduction)"),
):
  """Tax withholding planner — monthly/quarterly breakdown for Quebec/Canada.

  Calculates how much to set aside (or move back) each month based on
  cumulative YTD trading income, converted to CAD, using progressive
  federal + Quebec tax brackets for business income.
  """
  if year <= 0:
    year = datetime.now().year

  # Resolve FX rate
  rate = usd_cad_rate if usd_cad_rate > 0 else _fetch_usd_cad_rate()

  db = SessionLocal()
  try:
    rows = (
      db.query(TaxRecord)
      .filter(
        TaxRecord.exit_time >= datetime(year, 1, 1),
        TaxRecord.exit_time < datetime(year + 1, 1, 1),
      )
      .all()
    )

    # Group PnL by month (using exit_time)
    monthly_pnl_usd: dict[int, float] = defaultdict(float)
    for r in rows:
      if r.exit_time:
        m = r.exit_time.month
        monthly_pnl_usd[m] += r.net_pnl or 0

    # Build monthly breakdown with cumulative withholding
    months = []
    cumulative_cad = 0.0
    cumulative_withheld = 0.0

    for m in range(1, 13):
      pnl_usd = round(monthly_pnl_usd.get(m, 0), 2)
      pnl_cad = round(pnl_usd * rate, 2)
      cumulative_cad += pnl_cad

      # Tax on cumulative YTD income
      tax_info = _calc_combined_tax(cumulative_cad)
      tax_on_cumulative = tax_info["total"]

      # What to set aside (or move back) this month
      action_amount = round(tax_on_cumulative - cumulative_withheld, 2)
      cumulative_withheld = tax_on_cumulative

      months.append({
        "month": m,
        "month_name": MONTH_NAMES[m],
        "pnl_usd": pnl_usd,
        "pnl_cad": pnl_cad,
        "cumulative_cad": round(cumulative_cad, 2),
        "marginal_rate": tax_info["marginal_rate"],
        "effective_rate": tax_info["effective_rate"],
        "action_amount": action_amount,  # positive = set aside, negative = move back
        "action": "set_aside" if action_amount >= 0 else "move_back",
      })

    # Quarterly summaries
    quarters = []
    for qi in range(4):
      q_months = months[qi * 3: qi * 3 + 3]
      q_pnl_usd = sum(m["pnl_usd"] for m in q_months)
      q_pnl_cad = sum(m["pnl_cad"] for m in q_months)
      q_action = sum(m["action_amount"] for m in q_months)
      quarters.append({
        "quarter": f"Q{qi + 1}",
        "pnl_usd": round(q_pnl_usd, 2),
        "pnl_cad": round(q_pnl_cad, 2),
        "action_amount": round(q_action, 2),
        "action": "set_aside" if q_action >= 0 else "move_back",
      })

    # Annual totals
    total_pnl_usd = sum(m["pnl_usd"] for m in months)
    total_pnl_cad = round(total_pnl_usd * rate, 2)
    annual_tax = _calc_combined_tax(total_pnl_cad)

    # After business expenses (year-end deduction)
    expenses = max(business_expenses, 0)
    taxable_after = max(total_pnl_cad - expenses, 0)
    tax_after = _calc_combined_tax(taxable_after)
    tax_savings = round(annual_tax["total"] - tax_after["total"], 2)

    return {
      "year": year,
      "usd_cad_rate": round(rate, 4),
      "months": months,
      "quarters": quarters,
      "annual": {
        "total_pnl_usd": round(total_pnl_usd, 2),
        "total_pnl_cad": total_pnl_cad,
        "total_tax": annual_tax["total"],
        "federal_tax": annual_tax["federal"],
        "quebec_tax": annual_tax["quebec"],
        "effective_rate": annual_tax["effective_rate"],
        "marginal_rate": annual_tax["marginal_rate"],
        "side_account_balance": round(sum(m["action_amount"] for m in months), 2),
      },
      "after_expenses": {
        "business_expenses": round(expenses, 2),
        "taxable_income": round(taxable_after, 2),
        "total_tax": tax_after["total"],
        "federal_tax": tax_after["federal"],
        "quebec_tax": tax_after["quebec"],
        "effective_rate": tax_after["effective_rate"],
        "tax_savings": tax_savings,
      },
    }
  finally:
    db.close()


# ── Business Expenses CRUD ───────────────────────────────────────

EXPENSE_CATEGORIES = [
  "software", "internet", "home_office", "equipment",
  "education", "professional", "other",
]


class ExpenseCreate(BaseModel):
  year: int
  category: str
  description: str = ""
  amount_cad: float
  receipt_note: str = ""


class ExpenseUpdate(BaseModel):
  category: Optional[str] = None
  description: Optional[str] = None
  amount_cad: Optional[float] = None
  receipt_note: Optional[str] = None


@router.get("/expenses")
def list_expenses(year: int = Query(0)):
  """List business expenses for a tax year."""
  db = SessionLocal()
  try:
    q = db.query(TaxExpense).order_by(TaxExpense.created_at)
    if year > 0:
      q = q.filter(TaxExpense.year == year)
    rows = q.all()
    return {
      "count": len(rows),
      "expenses": [
        {
          "id": r.id,
          "year": r.year,
          "category": r.category,
          "description": r.description or "",
          "amount_cad": r.amount_cad,
          "receipt_note": r.receipt_note or "",
          "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
      ],
    }
  finally:
    db.close()


@router.post("/expenses")
def create_expense(data: ExpenseCreate):
  """Add a deductible business expense."""
  db = SessionLocal()
  try:
    rec = TaxExpense(
      year=data.year,
      category=data.category,
      description=data.description,
      amount_cad=data.amount_cad,
      receipt_note=data.receipt_note,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return {
      "status": "created",
      "expense": {
        "id": rec.id, "year": rec.year, "category": rec.category,
        "description": rec.description, "amount_cad": rec.amount_cad,
        "receipt_note": rec.receipt_note,
      },
    }
  finally:
    db.close()


@router.put("/expenses/{expense_id}")
def update_expense(expense_id: int, data: ExpenseUpdate):
  """Update a business expense."""
  db = SessionLocal()
  try:
    rec = db.query(TaxExpense).filter(TaxExpense.id == expense_id).first()
    if not rec:
      return {"status": "error", "detail": "Expense not found"}
    changes = data.model_dump(exclude_none=True)
    for key, val in changes.items():
      setattr(rec, key, val)
    db.commit()
    db.refresh(rec)
    return {"status": "updated", "expense": {
      "id": rec.id, "year": rec.year, "category": rec.category,
      "description": rec.description, "amount_cad": rec.amount_cad,
      "receipt_note": rec.receipt_note,
    }}
  finally:
    db.close()


@router.delete("/expenses/{expense_id}")
def delete_expense(expense_id: int):
  """Delete a business expense."""
  db = SessionLocal()
  try:
    rec = db.query(TaxExpense).filter(TaxExpense.id == expense_id).first()
    if not rec:
      return {"status": "error", "detail": "Expense not found"}
    db.delete(rec)
    db.commit()
    return {"status": "deleted", "id": expense_id}
  finally:
    db.close()


# ── Comprehensive Tax Report ─────────────────────────────────────

@router.get("/tax-report")
def tax_report(
  year: int = Query(0, description="Tax year (default: current year)"),
  usd_cad_rate: float = Query(0, description="USD/CAD rate. 0 = auto per-trade daily rates"),
):
  """Generate a comprehensive year-end tax report for CRA T2125 + Quebec TP-21.4.39-V.

  When usd_cad_rate=0 (default), fetches daily Bank of Canada rates and converts
  each trade using the rate from its exit date (nearest preceding business day
  for weekends/holidays). When usd_cad_rate>0, uses that fixed rate for all trades.
  """
  if year <= 0:
    year = datetime.now().year

  # Determine conversion mode
  fixed_rate = usd_cad_rate if usd_cad_rate > 0 else None
  daily_rates = _fetch_daily_rates(year) if not fixed_rate else {}

  # If daily rates fetch failed and no fixed rate, fall back to latest single rate
  if not fixed_rate and not daily_rates:
    fixed_rate = _fetch_usd_cad_rate()

  db = SessionLocal()
  try:
    rows = (
      db.query(TaxRecord)
      .filter(
        TaxRecord.exit_time >= datetime(year, 1, 1),
        TaxRecord.exit_time < datetime(year + 1, 1, 1),
      )
      .order_by(TaxRecord.exit_time)
      .all()
    )

    # ── Per-trade CAD conversion using daily rates ──
    trade_data = []
    for r in rows:
      t_rate = fixed_rate if fixed_rate else _get_rate_for_date(daily_rates, r.exit_time)
      trade_data.append({
        "row": r,
        "rate": t_rate,
        "gross_pnl_cad": (r.gross_pnl or 0) * t_rate,
        "net_pnl_cad": (r.net_pnl or 0) * t_rate,
        "commissions_cad": (r.total_commission or 0) * t_rate,
        "funding_cad": (r.funding_fee or 0) * t_rate,
      })

    # Rate info for response
    used_rates = [td["rate"] for td in trade_data]
    if used_rates:
      avg_rate = sum(used_rates) / len(used_rates)
      min_rate, max_rate = min(used_rates), max(used_rates)
    else:
      avg_rate = min_rate = max_rate = fixed_rate or 1.36

    rate_info = {
      "mode": "fixed" if fixed_rate else "daily",
      "avg": round(avg_rate, 4),
      "min": round(min_rate, 4),
      "max": round(max_rate, 4),
      "source": "Manual override" if usd_cad_rate > 0 else (
        "Bank of Canada daily rates" if daily_rates else "Bank of Canada latest (daily unavailable)"
      ),
    }

    # ── Income Summary (T2125) ──
    total_gross_pnl = sum(r.gross_pnl or 0 for r in rows)
    total_net_pnl = sum(r.net_pnl or 0 for r in rows)
    total_commissions = sum(r.total_commission or 0 for r in rows)
    total_funding = sum(r.funding_fee or 0 for r in rows)
    total_trading_expenses = total_commissions + abs(total_funding)

    # CAD totals — summed from individually converted trades
    total_gross_pnl_cad = sum(td["gross_pnl_cad"] for td in trade_data)
    total_net_pnl_cad = sum(td["net_pnl_cad"] for td in trade_data)
    total_commissions_cad = sum(td["commissions_cad"] for td in trade_data)
    total_funding_cad = sum(td["funding_cad"] for td in trade_data)
    total_trading_expenses_cad = total_commissions_cad + abs(total_funding_cad)

    income_summary = {
      "total_trades": len(rows),
      "gross_pnl_usd": round(total_gross_pnl, 2),
      "gross_pnl_cad": round(total_gross_pnl_cad, 2),
      "total_commissions_usd": round(total_commissions, 2),
      "total_commissions_cad": round(total_commissions_cad, 2),
      "total_funding_fees_usd": round(total_funding, 2),
      "total_funding_fees_cad": round(total_funding_cad, 2),
      "total_trading_expenses_usd": round(total_trading_expenses, 2),
      "total_trading_expenses_cad": round(total_trading_expenses_cad, 2),
      "net_pnl_usd": round(total_net_pnl, 2),
      "net_pnl_cad": round(total_net_pnl_cad, 2),
      "net_business_income_cad": round(total_net_pnl_cad, 2),
    }

    # ── Per-Asset Breakdown (TP-21.4.39-V) ──
    asset_data: dict[str, dict] = defaultdict(lambda: {
      "trade_count": 0,
      "gross_pnl": 0.0, "net_pnl": 0.0, "commissions": 0.0, "funding": 0.0,
      "gross_pnl_cad": 0.0, "net_pnl_cad": 0.0, "commissions_cad": 0.0, "funding_cad": 0.0,
      "wins": 0, "losses": 0,
      "first_trade": None, "last_trade": None,
    })
    for td in trade_data:
      r = td["row"]
      a = asset_data[r.symbol]
      a["trade_count"] += 1
      a["gross_pnl"] += r.gross_pnl or 0
      a["net_pnl"] += r.net_pnl or 0
      a["commissions"] += r.total_commission or 0
      a["funding"] += r.funding_fee or 0
      a["gross_pnl_cad"] += td["gross_pnl_cad"]
      a["net_pnl_cad"] += td["net_pnl_cad"]
      a["commissions_cad"] += td["commissions_cad"]
      a["funding_cad"] += td["funding_cad"]
      if (r.net_pnl or 0) > 0:
        a["wins"] += 1
      elif (r.net_pnl or 0) < 0:
        a["losses"] += 1
      if r.exit_time:
        if a["first_trade"] is None or r.exit_time < a["first_trade"]:
          a["first_trade"] = r.exit_time
        if a["last_trade"] is None or r.exit_time > a["last_trade"]:
          a["last_trade"] = r.exit_time

    per_asset = sorted([
      {
        "symbol": sym,
        "trade_count": d["trade_count"],
        "gross_pnl_usd": round(d["gross_pnl"], 2),
        "gross_pnl_cad": round(d["gross_pnl_cad"], 2),
        "commissions_usd": round(d["commissions"], 2),
        "commissions_cad": round(d["commissions_cad"], 2),
        "funding_usd": round(d["funding"], 2),
        "funding_cad": round(d["funding_cad"], 2),
        "net_pnl_usd": round(d["net_pnl"], 2),
        "net_pnl_cad": round(d["net_pnl_cad"], 2),
        "wins": d["wins"],
        "losses": d["losses"],
        "win_rate": round(d["wins"] / d["trade_count"] * 100, 1) if d["trade_count"] > 0 else 0,
        "first_trade_date": d["first_trade"].isoformat() if d["first_trade"] else None,
        "last_trade_date": d["last_trade"].isoformat() if d["last_trade"] else None,
      }
      for sym, d in asset_data.items()
    ], key=lambda x: x["net_pnl_cad"], reverse=True)

    # ── Monthly Breakdown ──
    monthly_data: dict[int, dict] = defaultdict(lambda: {
      "trade_count": 0,
      "gross_pnl": 0.0, "net_pnl": 0.0, "commissions": 0.0, "funding": 0.0,
      "gross_pnl_cad": 0.0, "net_pnl_cad": 0.0, "commissions_cad": 0.0, "funding_cad": 0.0,
      "wins": 0, "losses": 0,
    })
    for td in trade_data:
      r = td["row"]
      if r.exit_time:
        m = r.exit_time.month
        md = monthly_data[m]
        md["trade_count"] += 1
        md["gross_pnl"] += r.gross_pnl or 0
        md["net_pnl"] += r.net_pnl or 0
        md["commissions"] += r.total_commission or 0
        md["funding"] += r.funding_fee or 0
        md["gross_pnl_cad"] += td["gross_pnl_cad"]
        md["net_pnl_cad"] += td["net_pnl_cad"]
        md["commissions_cad"] += td["commissions_cad"]
        md["funding_cad"] += td["funding_cad"]
        if (r.net_pnl or 0) > 0:
          md["wins"] += 1
        elif (r.net_pnl or 0) < 0:
          md["losses"] += 1

    empty_month = {
      "trade_count": 0,
      "gross_pnl": 0.0, "net_pnl": 0.0, "commissions": 0.0, "funding": 0.0,
      "gross_pnl_cad": 0.0, "net_pnl_cad": 0.0, "commissions_cad": 0.0, "funding_cad": 0.0,
      "wins": 0, "losses": 0,
    }
    monthly = []
    for m in range(1, 13):
      d = monthly_data.get(m, empty_month)
      monthly.append({
        "month": m,
        "month_name": MONTH_NAMES[m],
        "trade_count": d["trade_count"],
        "gross_pnl_usd": round(d["gross_pnl"], 2),
        "gross_pnl_cad": round(d["gross_pnl_cad"], 2),
        "commissions_usd": round(d["commissions"], 2),
        "commissions_cad": round(d["commissions_cad"], 2),
        "funding_usd": round(d["funding"], 2),
        "funding_cad": round(d["funding_cad"], 2),
        "net_pnl_usd": round(d["net_pnl"], 2),
        "net_pnl_cad": round(d["net_pnl_cad"], 2),
        "wins": d["wins"],
        "losses": d["losses"],
      })

    # ── Performance Summary ──
    wins_list = [r.net_pnl for r in rows if (r.net_pnl or 0) > 0]
    losses_list = [r.net_pnl for r in rows if (r.net_pnl or 0) < 0]
    long_trades = [r for r in rows if r.side == "Long"]
    short_trades = [r for r in rows if r.side == "Short"]

    hold_times = []
    for r in rows:
      if r.entry_time and r.exit_time:
        delta = (r.exit_time - r.entry_time).total_seconds() / 3600
        hold_times.append(delta)

    performance = {
      "total_wins": len(wins_list),
      "total_losses": len(losses_list),
      "total_breakeven": len(rows) - len(wins_list) - len(losses_list),
      "win_rate": round(len(wins_list) / len(rows) * 100, 1) if rows else 0,
      "avg_win_usd": round(sum(wins_list) / len(wins_list), 2) if wins_list else 0,
      "avg_loss_usd": round(sum(losses_list) / len(losses_list), 2) if losses_list else 0,
      "largest_win_usd": round(max(wins_list), 2) if wins_list else 0,
      "largest_loss_usd": round(min(losses_list), 2) if losses_list else 0,
      "avg_hold_time_hours": round(sum(hold_times) / len(hold_times), 1) if hold_times else 0,
      "long_trades": len(long_trades),
      "short_trades": len(short_trades),
      "long_pnl_usd": round(sum(r.net_pnl or 0 for r in long_trades), 2),
      "short_pnl_usd": round(sum(r.net_pnl or 0 for r in short_trades), 2),
    }

    # ── Tax Calculation ──
    net_business_income_cad = income_summary["net_business_income_cad"]
    tax_info = _calc_combined_tax(max(net_business_income_cad, 0))

    # ── Data Quality Warnings ──
    warnings = []
    no_exit_time = sum(1 for r in rows if not r.exit_time)
    if no_exit_time:
      warnings.append(f"{no_exit_time} trade(s) missing exit time")
    no_entry_time = sum(1 for r in rows if not r.entry_time)
    if no_entry_time:
      warnings.append(f"{no_entry_time} trade(s) missing entry time")
    zero_commission = sum(1 for r in rows if (r.total_commission or 0) == 0)
    if zero_commission:
      warnings.append(f"{zero_commission} trade(s) have $0 commission — verify fees are recorded")
    no_exit_price = sum(1 for r in rows if r.exit_price is None)
    if no_exit_price:
      warnings.append(f"{no_exit_price} trade(s) missing exit price")
    zero_qty = sum(1 for r in rows if (r.quantity or 0) == 0)
    if zero_qty:
      warnings.append(f"{zero_qty} trade(s) have zero quantity")
    sources = set(r.source for r in rows)
    if rows and sources == {"manual"}:
      warnings.append("All trades are manually entered — consider importing live/journal trades")

    return {
      "year": year,
      "usd_cad_rate": round(avg_rate, 4),
      "rate_info": rate_info,
      "generated_at": datetime.now(timezone.utc).isoformat(),
      "income_summary": income_summary,
      "per_asset": per_asset,
      "monthly": monthly,
      "performance": performance,
      "tax_estimate": {
        "net_business_income_cad": round(net_business_income_cad, 2),
        "federal_tax": tax_info["federal"],
        "quebec_tax": tax_info["quebec"],
        "total_tax": tax_info["total"],
        "effective_rate": tax_info["effective_rate"],
        "marginal_rate": tax_info["marginal_rate"],
      },
      "warnings": warnings,
    }
  finally:
    db.close()


@router.get("/export-tax-report")
def export_tax_report(
  year: int = Query(0),
  usd_cad_rate: float = Query(0),
):
  """Export a comprehensive multi-CSV ZIP for accountant filing.

  Contains:
    1_T2125_Summary.csv  — Income summary for T2125
    2_Per_Asset.csv      — Per-asset breakdown for TP-21.4.39-V
    3_Monthly.csv        — Monthly income breakdown
    4_All_Trades.csv     — Every trade with CAD values
  """
  import csv
  import io
  import zipfile

  # Generate the report data
  report = tax_report(year=year, usd_cad_rate=usd_cad_rate)
  yr = report["year"]
  ri = report.get("rate_info", {})

  # Resolve per-trade rates for sheet 4
  fixed_rate = usd_cad_rate if usd_cad_rate > 0 else None
  daily_rates = _fetch_daily_rates(yr) if not fixed_rate else {}
  if not fixed_rate and not daily_rates:
    fixed_rate = _fetch_usd_cad_rate()

  buf = io.BytesIO()
  with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

    # 1 — T2125 Summary
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Tax Year", yr])
    if ri.get("mode") == "daily":
      w.writerow(["USD/CAD Rate", f"Daily Bank of Canada rates (avg {ri['avg']}, range {ri['min']}–{ri['max']})"])
    else:
      w.writerow(["USD/CAD Rate", report["usd_cad_rate"]])
    w.writerow(["Generated", report["generated_at"]])
    w.writerow([])
    w.writerow(["T2125 — Statement of Business Activities"])
    w.writerow(["Field", "USD", "CAD"])
    s = report["income_summary"]
    w.writerow(["Total Trades", s["total_trades"], ""])
    w.writerow(["Gross PnL", s["gross_pnl_usd"], s["gross_pnl_cad"]])
    w.writerow(["Trading Commissions", s["total_commissions_usd"], s["total_commissions_cad"]])
    w.writerow(["Funding Fees", s["total_funding_fees_usd"], s["total_funding_fees_cad"]])
    w.writerow(["Total Trading Expenses", s["total_trading_expenses_usd"], s["total_trading_expenses_cad"]])
    w.writerow(["Net PnL (after broker fees)", s["net_pnl_usd"], s["net_pnl_cad"]])
    w.writerow(["Net Business Income", "", s["net_business_income_cad"]])
    w.writerow([])
    t = report["tax_estimate"]
    w.writerow(["Tax Estimate"])
    w.writerow(["Federal Tax", "", t["federal_tax"]])
    w.writerow(["Quebec Tax", "", t["quebec_tax"]])
    w.writerow(["Total Tax", "", t["total_tax"]])
    w.writerow(["Effective Rate", "", f"{t['effective_rate']}%"])
    w.writerow(["Marginal Rate", "", f"{t['marginal_rate']}%"])
    zf.writestr(f"1_T2125_Summary_{yr}.csv", out.getvalue())

    # 2 — Per Asset (TP-21.4.39-V)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
      "Symbol", "Trades", "Wins", "Losses", "Win Rate %",
      "Gross PnL USD", "Gross PnL CAD",
      "Commissions USD", "Commissions CAD",
      "Funding USD", "Funding CAD",
      "Net PnL USD", "Net PnL CAD",
      "First Trade", "Last Trade",
    ])
    for a in report["per_asset"]:
      w.writerow([
        a["symbol"], a["trade_count"], a["wins"], a["losses"], a["win_rate"],
        a["gross_pnl_usd"], a["gross_pnl_cad"],
        a["commissions_usd"], a["commissions_cad"],
        a["funding_usd"], a["funding_cad"],
        a["net_pnl_usd"], a["net_pnl_cad"],
        a["first_trade_date"], a["last_trade_date"],
      ])
    zf.writestr(f"2_Per_Asset_TP21_{yr}.csv", out.getvalue())

    # 3 — Monthly
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
      "Month", "Trades", "Wins", "Losses",
      "Gross PnL USD", "Gross PnL CAD",
      "Commissions USD", "Commissions CAD",
      "Funding USD", "Funding CAD",
      "Net PnL USD", "Net PnL CAD",
    ])
    for m in report["monthly"]:
      w.writerow([
        m["month_name"], m["trade_count"], m["wins"], m["losses"],
        m["gross_pnl_usd"], m["gross_pnl_cad"],
        m["commissions_usd"], m["commissions_cad"],
        m["funding_usd"], m["funding_cad"],
        m["net_pnl_usd"], m["net_pnl_cad"],
      ])
    zf.writestr(f"3_Monthly_{yr}.csv", out.getvalue())

    # 4 — All Trades (with per-trade CAD conversion)
    db = SessionLocal()
    try:
      q = db.query(TaxRecord).order_by(TaxRecord.exit_time)
      if yr > 0:
        q = q.filter(
          TaxRecord.exit_time >= datetime(yr, 1, 1),
          TaxRecord.exit_time < datetime(yr + 1, 1, 1),
        )
      trades = q.all()

      out = io.StringIO()
      w = csv.writer(out)
      w.writerow([
        "ID", "Source", "Symbol", "Side", "Strategy", "Portfolio",
        "Quantity", "Leverage", "Margin Used",
        "Entry Price", "Entry Time", "Entry Order Type",
        "Exit Price", "Exit Time", "Exit Order Type", "Exit Reason",
        "USD/CAD Rate",
        "Entry Commission USD", "Exit Commission USD", "Total Commission USD", "Total Commission CAD",
        "Funding Fee USD", "Funding Fee CAD",
        "Gross PnL USD", "Gross PnL CAD",
        "Net PnL USD", "Net PnL CAD", "Net PnL %",
        "Outcome", "Notes",
      ])
      for r in trades:
        t_rate = fixed_rate if fixed_rate else _get_rate_for_date(daily_rates, r.exit_time)
        w.writerow([
          r.id, r.source, r.symbol, r.side, r.strategy_name or "", r.portfolio_name or "",
          r.quantity, r.leverage, r.margin_used,
          r.entry_price, r.entry_time.isoformat() if r.entry_time else "",
          r.entry_order_type or "",
          r.exit_price or "", r.exit_time.isoformat() if r.exit_time else "",
          r.exit_order_type or "", r.exit_reason or "",
          round(t_rate, 4),
          r.entry_commission, r.exit_commission, r.total_commission,
          round((r.total_commission or 0) * t_rate, 2),
          r.funding_fee, round((r.funding_fee or 0) * t_rate, 2),
          r.gross_pnl, round((r.gross_pnl or 0) * t_rate, 2),
          r.net_pnl, round((r.net_pnl or 0) * t_rate, 2),
          r.net_pnl_pct,
          r.outcome or "", r.notes or "",
        ])
      zf.writestr(f"4_All_Trades_{yr}.csv", out.getvalue())
    finally:
      db.close()

  buf.seek(0)
  filename = f"Tax_Report_{yr}.zip"
  return StreamingResponse(
    iter([buf.getvalue()]),
    media_type="application/zip",
    headers={"Content-Disposition": f"attachment; filename={filename}"},
  )
