"""Data collection routes — downloads, watchlist, progress tracking, backup."""
import json as _json
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.data.bybit_downloader import BybitDownloader
from backend.data.data_manager import DataManager
from backend.db.database import get_db
from backend.db.models import (
  CustomTemplate,
  JournalNote,
  JournalTemplate,
  JournalTrade,
  MonthlyObjective,
  OHLCVData,
  Portfolio,
  PortfolioAsset,
  Strategy,
  StrategyConfig,
  SymbolCategory,
  Watchlist,
)
from backend.services.category_service import get_all_categories
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Overridable so the Docker deployment can point at the worker container;
# the default keeps the Windows multi-terminal setup working unchanged.
import os as _os
WORKER_URL = _os.getenv("WORKER_URL", "http://localhost:8081")


class DownloadRequest(BaseModel):
  symbol: str
  timeframe: str = "30"
  start_date: Optional[str] = None  # ISO date string for historical backfill


class BatchDownloadRequest(BaseModel):
  symbols: list[str]
  timeframe: str = "30"


class WatchlistAddRequest(BaseModel):
  symbols: list[str]


# --- Symbols ---

@router.get("/symbols")
async def list_bybit_symbols():
  """Fetch all USDT perpetual symbols from Bybit V5 public API."""
  downloader = BybitDownloader()
  symbols = await downloader.get_usdt_perpetuals()
  return {"count": len(symbols), "symbols": symbols}


# --- Data Status ---

@router.get("/status")
def data_status():
  """Show how many candles are stored per symbol, plus avg daily volume ($)."""
  from backend.db.database import ohlcv_router

  result = []
  for tf in ohlcv_router.list_timeframes():
    ohlcv_db = ohlcv_router.get_session(tf)
    try:
      rows = (
        ohlcv_db.query(
          OHLCVData.symbol,
          OHLCVData.timeframe,
          func.count(OHLCVData.id).label("candle_count"),
          func.min(OHLCVData.timestamp).label("earliest"),
          func.max(OHLCVData.timestamp).label("latest"),
          func.sum(OHLCVData.close * OHLCVData.volume).label("total_turnover"),
        )
        .group_by(OHLCVData.symbol, OHLCVData.timeframe)
        .all()
      )
      for r in rows:
        adv = 0.0
        earliest = r.earliest
        latest = r.latest
        turnover = r.total_turnover
        try:
          if isinstance(earliest, str):
            earliest = datetime.fromisoformat(earliest)
          if isinstance(latest, str):
            latest = datetime.fromisoformat(latest)
          if earliest and latest and turnover:
            days = (latest - earliest).days + 1
            adv = float(turnover) / max(days, 1)
        except Exception as e:
          logger.warning("ADV calc failed for %s: %s", r.symbol, e)
          adv = 0.0
        result.append({
          "symbol": r.symbol,
          "timeframe": r.timeframe,
          "candle_count": r.candle_count,
          "earliest": earliest.isoformat() if hasattr(earliest, 'isoformat') else str(earliest) if earliest else None,
          "latest": latest.isoformat() if hasattr(latest, 'isoformat') else str(latest) if latest else None,
          "avg_daily_volume": round(adv, 2),
        })
    finally:
      ohlcv_db.close()
  return result


@router.get("/debug-adv")
def debug_adv():
  """Debug endpoint — shows raw ADV calculation values."""
  from backend.db.database import ohlcv_router

  debug = []
  for tf in ohlcv_router.list_timeframes():
    ohlcv_db = ohlcv_router.get_session(tf)
    try:
      rows = (
        ohlcv_db.query(
          OHLCVData.symbol,
          OHLCVData.timeframe,
          func.count(OHLCVData.id).label("candle_count"),
          func.min(OHLCVData.timestamp).label("earliest"),
          func.max(OHLCVData.timestamp).label("latest"),
          func.sum(OHLCVData.close * OHLCVData.volume).label("total_turnover"),
        )
        .group_by(OHLCVData.symbol, OHLCVData.timeframe)
        .all()
      )
      for r in rows:
        debug.append({
          "symbol": r.symbol,
          "timeframe": r.timeframe,
          "candle_count": r.candle_count,
          "earliest_raw": str(r.earliest),
          "earliest_type": type(r.earliest).__name__,
          "latest_raw": str(r.latest),
          "latest_type": type(r.latest).__name__,
          "turnover_raw": str(r.total_turnover),
          "turnover_type": type(r.total_turnover).__name__,
        })
    finally:
      ohlcv_db.close()
  return debug


# --- Download (proxied to download worker on port 8081) ---

@router.post("/download")
async def download_full_history(req: DownloadRequest):
  """Download ALL available history for a symbol. Proxied to download worker."""
  try:
    async with httpx.AsyncClient(timeout=30) as client:
      resp = await client.post(f"{WORKER_URL}/download", json=req.model_dump())
      return resp.json()
  except httpx.ConnectError:
    raise HTTPException(503, "Download worker not running (port 8081). Check CMD window [2/4].")


@router.post("/download/batch")
async def download_batch(req: BatchDownloadRequest):
  """Download full history for multiple symbols. Proxied to download worker."""
  try:
    async with httpx.AsyncClient(timeout=30) as client:
      resp = await client.post(f"{WORKER_URL}/download/batch", json=req.model_dump())
      return resp.json()
  except httpx.ConnectError:
    raise HTTPException(503, "Download worker not running (port 8081). Check CMD window [2/4].")


@router.get("/download/progress")
async def download_progress():
  """Get current download progress. Proxied to download worker."""
  try:
    async with httpx.AsyncClient(timeout=10) as client:
      resp = await client.get(f"{WORKER_URL}/download/progress")
      return resp.json()
  except httpx.ConnectError:
    return {}


@router.post("/download/cancel")
async def cancel_downloads():
  """Cancel all active downloads. Proxied to download worker."""
  try:
    async with httpx.AsyncClient(timeout=10) as client:
      resp = await client.post(f"{WORKER_URL}/download/cancel")
      return resp.json()
  except httpx.ConnectError:
    raise HTTPException(status_code=503, detail="Download worker not available")


# --- Watchlist ---

@router.get("/watchlist")
def get_watchlist(db: Session = Depends(get_db)):
  """Get the custom watchlist."""
  items = db.query(Watchlist).order_by(Watchlist.symbol).all()
  return [{"symbol": w.symbol, "added_at": w.added_at.isoformat()} for w in items]


@router.post("/watchlist")
def add_to_watchlist(req: WatchlistAddRequest, db: Session = Depends(get_db)):
  """Add symbols to watchlist (ignores duplicates)."""
  added = []
  for symbol in req.symbols:
    exists = db.query(Watchlist).filter(Watchlist.symbol == symbol).first()
    if not exists:
      db.add(Watchlist(symbol=symbol))
      added.append(symbol)
  db.commit()
  return {"added": len(added), "symbols": added}


@router.delete("/watchlist/{symbol}")
def remove_from_watchlist(symbol: str, db: Session = Depends(get_db)):
  """Remove a symbol from watchlist."""
  item = db.query(Watchlist).filter(Watchlist.symbol == symbol).first()
  if not item:
    raise HTTPException(status_code=404, detail="Symbol not in watchlist")
  db.delete(item)
  db.commit()
  return {"removed": symbol}


@router.post("/watchlist/download-all")
async def download_watchlist(
  timeframe: str = Query("30"),
  db: Session = Depends(get_db),
):
  """Download full history for every symbol in the watchlist. Proxied to worker."""
  items = db.query(Watchlist).all()
  symbols = [w.symbol for w in items]

  if not symbols:
    raise HTTPException(status_code=400, detail="Watchlist is empty")

  try:
    async with httpx.AsyncClient(timeout=30) as client:
      resp = await client.post(
        f"{WORKER_URL}/download/batch",
        json={"symbols": symbols, "timeframe": timeframe},
      )
      return resp.json()
  except httpx.ConnectError:
    raise HTTPException(503, "Download worker not running (port 8081). Check CMD window [2/4].")


# --- Asset Categories (manually maintained) ---

@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
  """Return all manually-maintained asset categories from DB."""
  return get_all_categories(db)


class CategoryUpdateRequest(BaseModel):
  categories: list[str]


@router.put("/categories/{symbol}")
def update_symbol_categories(
  symbol: str, req: CategoryUpdateRequest, db: Session = Depends(get_db),
):
  """Manually set categories for a symbol. Creates or updates.
  Marks as manual_override=1 so auto-refresh never wipes it."""
  import json
  row = db.query(SymbolCategory).filter(SymbolCategory.symbol == symbol).first()
  if row:
    row.categories = json.dumps(req.categories)
    row.manual_override = 1
    row.last_updated = datetime.now(timezone.utc)
  else:
    db.add(SymbolCategory(
      symbol=symbol,
      categories=json.dumps(req.categories),
      manual_override=1,
      last_updated=datetime.now(timezone.utc),
    ))
  db.commit()
  return {"symbol": symbol, "categories": req.categories}


@router.delete("/ohlcv/{symbol}")
def delete_ohlcv_data(
  symbol: str,
  timeframe: Optional[str] = Query(default=None),
):
  """Delete stored OHLCV data for a symbol. Optionally filter by timeframe."""
  from backend.db.database import ohlcv_router

  total_deleted = 0
  tfs_to_check = [timeframe] if timeframe else ohlcv_router.list_timeframes()
  for tf in tfs_to_check:
    ohlcv_db = ohlcv_router.get_session(tf)
    try:
      q = ohlcv_db.query(OHLCVData).filter(OHLCVData.symbol == symbol)
      if timeframe:
        q = q.filter(OHLCVData.timeframe == timeframe)
      count = q.delete(synchronize_session=False)
      ohlcv_db.commit()
      total_deleted += count
    finally:
      ohlcv_db.close()
  if total_deleted == 0:
    raise HTTPException(status_code=404, detail=f"No data found for {symbol}")
  return {"deleted": total_deleted, "symbol": symbol, "timeframe": timeframe or "all"}


# --- Derived Timeframes (3D etc.) ---

class BuildDerivedRequest(BaseModel):
  symbol: str | None = None


@router.post("/build-derived")
def build_derived(req: BuildDerivedRequest | None = None):
  """Build derived timeframes (3D) from daily data.

  If symbol provided, builds for that symbol only.
  Otherwise builds for ALL symbols with daily data.
  """
  from backend.data.derived_candles import rebuild_3d_all_symbols, rebuild_3d_candles

  if req and req.symbol:
    count = rebuild_3d_candles(req.symbol)
    return {"built": {req.symbol: count}}
  results = rebuild_3d_all_symbols()
  return {"built": results, "total_symbols": len(results)}


# --- Backup & Restore ---

def _dt_str(val) -> str | None:
  """Safely convert datetime/date to ISO string."""
  if val is None:
    return None
  if hasattr(val, "isoformat"):
    return val.isoformat()
  return str(val)


@router.get("/backup")
def backup_export(db: Session = Depends(get_db)):
  """Export all user data (categories, journal, strategies, portfolios, watchlist) as JSON."""
  # Categories
  cats = db.query(SymbolCategory).all()
  categories = [
    {"symbol": c.symbol, "categories": _json.loads(c.categories) if c.categories else []}
    for c in cats
  ]

  # Watchlist
  watchlist = [w.symbol for w in db.query(Watchlist).all()]

  # Strategies + configs
  strats = db.query(Strategy).filter(Strategy.is_deleted == False).all()
  strategies = []
  for s in strats:
    configs = db.query(StrategyConfig).filter(StrategyConfig.strategy_id == s.id).all()
    strategies.append({
      "name": s.name,
      "description": s.description or "",
      "source_type": s.source_type or "manual",
      "pine_source": s.pine_source or "",
      "python_source": s.python_source or "",
      "parameters": s.parameters or "{}",
      "configs": [
        {"name": c.name, "description": c.description or "", "parameter_values": c.parameter_values or "{}"}
        for c in configs
      ],
    })

  # Portfolios + assets
  portfolios_db = db.query(Portfolio).all()
  portfolios = []
  for p in portfolios_db:
    assets = db.query(PortfolioAsset).filter(PortfolioAsset.portfolio_id == p.id).all()
    portfolios.append({
      "name": p.name,
      "portfolio_type": p.portfolio_type or "saved",
      "is_cross_strat": p.is_cross_strat,
      "source_strategies": p.source_strategies or "{}",
      "metrics_snapshot": p.metrics_snapshot or "{}",
      "optimization_end_date": _dt_str(p.optimization_end_date),
      "is_start_date": _dt_str(p.is_start_date),
      "portfolio_source": p.portfolio_source or "optimizer",
      "description": p.description or "",
      "swap_history": _json.loads(p.swap_history) if p.swap_history else [],
      "assets": [
        {"symbol": a.symbol, "trade_side": a.trade_side, "strategy_name": a.strategy_name or "",
         "is_active": a.is_active, "raw_name": a.raw_name or "", "batch_id": a.batch_id,
         "config_name": a.config_name, "risk_mode": a.risk_mode}
        for a in assets
      ],
    })

  # Journal notes
  notes = db.query(JournalNote).all()
  journal_notes = [
    {"note_type": n.note_type, "title": n.title, "subject": n.subject or "",
     "content": n.content or "", "note_date": _dt_str(n.note_date),
     "period_start": _dt_str(n.period_start), "period_end": _dt_str(n.period_end),
     "color": n.color or "indigo", "images": n.images or "[]"}
    for n in notes
  ]

  # Journal trades
  jtrades = db.query(JournalTrade).all()
  journal_trades = [
    {"symbol": t.symbol, "side": t.side, "status": t.status or "closed",
     "source": t.source or "manual", "entry_price": t.entry_price,
     "exit_price": t.exit_price, "quantity": t.quantity,
     "net_profit": t.net_profit, "entry_time": _dt_str(t.entry_time),
     "exit_time": _dt_str(t.exit_time), "notes": t.notes or "",
     "tags": t.tags or "[]", "rating": t.rating}
    for t in jtrades
  ]

  # Journal templates
  templates = db.query(JournalTemplate).all()
  journal_templates = [
    {"template_type": t.template_type, "content": t.content or ""}
    for t in templates
  ]

  # Custom templates
  ctemplates = db.query(CustomTemplate).all()
  custom_templates = [
    {"name": t.name, "content": t.content or ""}
    for t in ctemplates
  ]

  # Monthly objectives
  objectives = db.query(MonthlyObjective).all()
  monthly_objectives = [
    {"title": o.title, "content": o.content or "", "checklist": o.checklist or "[]",
     "period": o.period or "monthly", "obj_year": o.obj_year, "obj_month": o.obj_month,
     "is_active": o.is_active, "sort_order": o.sort_order}
    for o in objectives
  ]

  backup = {
    "exported_at": datetime.now(timezone.utc).isoformat(),
    "version": 1,
    "categories": categories,
    "watchlist": watchlist,
    "strategies": strategies,
    "portfolios": portfolios,
    "journal_notes": journal_notes,
    "journal_trades": journal_trades,
    "journal_templates": journal_templates,
    "custom_templates": custom_templates,
    "monthly_objectives": monthly_objectives,
  }

  content = _json.dumps(backup, indent=2, default=str)
  date_str = datetime.now(timezone.utc).strftime("%Y%m%d")

  return StreamingResponse(
    iter([content]),
    media_type="application/json",
    headers={"Content-Disposition": f'attachment; filename="warroom_backup_{date_str}.json"'},
  )


@router.post("/restore")
async def backup_restore(file: UploadFile = File(...), db: Session = Depends(get_db)):
  """Restore user data from a backup JSON file. Upserts by unique keys."""
  raw = await file.read()
  try:
    data = _json.loads(raw)
  except _json.JSONDecodeError:
    raise HTTPException(status_code=400, detail="Invalid JSON file")

  counts: dict[str, int] = {}

  # Categories: upsert by symbol
  for cat in data.get("categories", []):
    row = db.query(SymbolCategory).filter(SymbolCategory.symbol == cat["symbol"]).first()
    cats_json = _json.dumps(cat.get("categories", []))
    if row:
      row.categories = cats_json
      row.last_updated = datetime.now(timezone.utc)
    else:
      db.add(SymbolCategory(symbol=cat["symbol"], categories=cats_json, last_updated=datetime.now(timezone.utc)))
  counts["categories"] = len(data.get("categories", []))

  # Watchlist: merge
  existing_wl = {w.symbol for w in db.query(Watchlist).all()}
  added_wl = 0
  for sym in data.get("watchlist", []):
    if sym not in existing_wl:
      db.add(Watchlist(symbol=sym))
      added_wl += 1
  counts["watchlist_added"] = added_wl

  # Strategies: upsert by name
  for s in data.get("strategies", []):
    existing = db.query(Strategy).filter(Strategy.name == s["name"]).first()
    if existing:
      existing.pine_source = s.get("pine_source", existing.pine_source)
      existing.python_source = s.get("python_source", existing.python_source)
      existing.parameters = s.get("parameters", existing.parameters)
      existing.description = s.get("description", existing.description)
    else:
      new_strat = Strategy(
        name=s["name"], description=s.get("description", ""),
        source_type=s.get("source_type", "manual"),
        pine_source=s.get("pine_source", ""), python_source=s.get("python_source", ""),
        parameters=s.get("parameters", "{}"),
      )
      db.add(new_strat)
      db.flush()
      # Restore configs
      for c in s.get("configs", []):
        db.add(StrategyConfig(
          strategy_id=new_strat.id, name=c["name"],
          description=c.get("description", ""),
          parameter_values=c.get("parameter_values", "{}"),
        ))
  counts["strategies"] = len(data.get("strategies", []))

  # Portfolios: upsert by name
  for p in data.get("portfolios", []):
    existing = db.query(Portfolio).filter(Portfolio.name == p["name"]).first()
    if not existing:
      new_port = Portfolio(
        name=p["name"], portfolio_type=p.get("portfolio_type", "saved"),
        is_cross_strat=p.get("is_cross_strat", False),
        source_strategies=p.get("source_strategies", "{}"),
        metrics_snapshot=p.get("metrics_snapshot", "{}"),
        portfolio_source=p.get("portfolio_source", "optimizer"),
        description=p.get("description", ""),
        swap_history=_json.dumps(p.get("swap_history", [])),
      )
      db.add(new_port)
      db.flush()
      for a in p.get("assets", []):
        db.add(PortfolioAsset(
          portfolio_id=new_port.id, symbol=a["symbol"],
          trade_side=a["trade_side"], strategy_name=a.get("strategy_name", ""),
          is_active=a.get("is_active", True), raw_name=a.get("raw_name", ""),
          batch_id=a.get("batch_id"), config_name=a.get("config_name"),
          risk_mode=a.get("risk_mode"),
        ))
  counts["portfolios"] = len(data.get("portfolios", []))

  # Journal notes: insert if not duplicate (match by title + date)
  restored_notes = 0
  for n in data.get("journal_notes", []):
    note_date = n.get("note_date")
    if note_date and isinstance(note_date, str):
      try:
        note_date = datetime.fromisoformat(note_date).date()
      except ValueError:
        continue
    exists = db.query(JournalNote).filter(
      JournalNote.title == n["title"], JournalNote.note_date == note_date,
    ).first()
    if not exists:
      db.add(JournalNote(
        note_type=n.get("note_type", "journal"), title=n["title"],
        subject=n.get("subject", ""), content=n.get("content", ""),
        note_date=note_date, color=n.get("color", "indigo"),
        images=n.get("images", "[]"),
      ))
      restored_notes += 1
  counts["journal_notes"] = restored_notes

  # Journal trades: insert if not duplicate (match by symbol + entry_time)
  restored_jtrades = 0
  for t in data.get("journal_trades", []):
    entry_time = t.get("entry_time")
    exists = db.query(JournalTrade).filter(
      JournalTrade.symbol == t["symbol"],
      JournalTrade.entry_time == entry_time,
    ).first()
    if not exists:
      db.add(JournalTrade(
        symbol=t["symbol"], side=t["side"], status=t.get("status", "closed"),
        source=t.get("source", "manual"), entry_price=t.get("entry_price", 0),
        exit_price=t.get("exit_price"), quantity=t.get("quantity", 0),
        net_profit=t.get("net_profit"), entry_time=entry_time,
        exit_time=t.get("exit_time"), notes=t.get("notes", ""),
        tags=t.get("tags", "[]"), rating=t.get("rating"),
      ))
      restored_jtrades += 1
  counts["journal_trades"] = restored_jtrades

  # Journal templates: upsert by type
  for t in data.get("journal_templates", []):
    existing = db.query(JournalTemplate).filter(
      JournalTemplate.template_type == t["template_type"]
    ).first()
    if existing:
      existing.content = t.get("content", "")
    else:
      db.add(JournalTemplate(template_type=t["template_type"], content=t.get("content", "")))
  counts["journal_templates"] = len(data.get("journal_templates", []))

  # Custom templates: upsert by name
  for t in data.get("custom_templates", []):
    existing = db.query(CustomTemplate).filter(CustomTemplate.name == t["name"]).first()
    if existing:
      existing.content = t.get("content", "")
    else:
      db.add(CustomTemplate(name=t["name"], content=t.get("content", "")))
  counts["custom_templates"] = len(data.get("custom_templates", []))

  # Monthly objectives: upsert by title + year + month
  for o in data.get("monthly_objectives", []):
    existing = db.query(MonthlyObjective).filter(
      MonthlyObjective.title == o["title"],
      MonthlyObjective.obj_year == o.get("obj_year"),
      MonthlyObjective.obj_month == o.get("obj_month"),
    ).first()
    if not existing:
      db.add(MonthlyObjective(
        title=o["title"], content=o.get("content", ""),
        checklist=o.get("checklist", "[]"), period=o.get("period", "monthly"),
        obj_year=o.get("obj_year"), obj_month=o.get("obj_month"),
        is_active=o.get("is_active", True), sort_order=o.get("sort_order", 0),
      ))
  counts["monthly_objectives"] = len(data.get("monthly_objectives", []))

  db.commit()
  return {"status": "restored", "counts": counts}


# --- Category Performance Dashboard ---

def _get_symbols_for_source(source: str, db: Session) -> list[str]:
  """Get symbol list based on source filter (watchlist or all)."""
  if source == "watchlist":
    return [w.symbol for w in db.query(Watchlist).all()]
  # "all" = all symbols we have categories for
  return [c.symbol for c in db.query(SymbolCategory).filter(
    func.length(SymbolCategory.categories) > 2  # not "[]"
  ).all()]


def _build_category_map(symbols: list[str], db: Session) -> dict[str, list[str]]:
  """Build {category: [symbols]} from DB. Only includes provided symbols."""
  symbol_set = set(symbols)
  rows = db.query(SymbolCategory).filter(SymbolCategory.symbol.in_(symbols)).all()
  cat_map: dict[str, list[str]] = {}
  for row in rows:
    try:
      cats = _json.loads(row.categories) if row.categories else []
    except (_json.JSONDecodeError, TypeError):
      continue
    for cat in cats:
      if row.symbol in symbol_set:
        cat_map.setdefault(cat, []).append(row.symbol)
  return cat_map


@router.get("/category-returns")
async def category_returns(
  source: str = Query("watchlist"),
  db: Session = Depends(get_db),
):
  """Market returns (%) by category for D/W/M/Y periods.

  Daily: Bybit 24h % from tickers API.
  W/M/Y: computed from DB OHLCV (30m candles aggregated to daily closes).
  """
  import httpx

  symbols = _get_symbols_for_source(source, db)
  if not symbols:
    return {"categories": {}, "source": source, "updated_at": datetime.now(timezone.utc).isoformat()}

  cat_map = _build_category_map(symbols, db)

  # Fetch 24h change from Bybit tickers (one call, all symbols)
  daily_pct: dict[str, float] = {}
  try:
    from backend.config.settings import get_settings
    base_url = get_settings().bybit_base_url
    async with httpx.AsyncClient(timeout=15) as client:
      resp = await client.get(f"{base_url}/v5/market/tickers", params={"category": "linear"})
      resp.raise_for_status()
      for item in resp.json().get("result", {}).get("list", []):
        sym = item.get("symbol", "")
        pct = float(item.get("price24hPcnt", 0)) * 100
        daily_pct[sym] = pct
  except Exception as e:
    logger.warning("Failed to fetch Bybit tickers: %s", e)

  # W/M/Y from OHLCV: get last close and close N bars ago (30m candles)
  # 1W = 336 bars, 1M = 1440, 1Y = 17520 (30m bars)
  from backend.db.database import ohlcv_router
  periods = {"w": 336, "m": 1440, "y": 17520}
  period_pct: dict[str, dict[str, float]] = {}  # {symbol: {w: %, m: %, y: %}}

  symbol_set = set(symbols)
  ohlcv_db = ohlcv_router.get_session("30")
  try:
    for period_key, bar_count in periods.items():
      for sym in symbol_set:
        latest = ohlcv_db.query(OHLCVData.close).filter(
          OHLCVData.symbol == sym, OHLCVData.timeframe == "30",
        ).order_by(OHLCVData.timestamp.desc()).first()

        old = ohlcv_db.query(OHLCVData.close).filter(
          OHLCVData.symbol == sym, OHLCVData.timeframe == "30",
        ).order_by(OHLCVData.timestamp.desc()).offset(bar_count).first()

        if latest and old and old[0] > 0:
          pct = ((latest[0] - old[0]) / old[0]) * 100
          period_pct.setdefault(sym, {})[period_key] = round(pct, 2)
  finally:
    ohlcv_db.close()

  # Aggregate by category
  result: dict[str, dict] = {}
  for cat, cat_symbols in cat_map.items():
    d_vals = [daily_pct.get(s, 0) for s in cat_symbols if s in daily_pct]
    w_vals = [period_pct.get(s, {}).get("w", 0) for s in cat_symbols if "w" in period_pct.get(s, {})]
    m_vals = [period_pct.get(s, {}).get("m", 0) for s in cat_symbols if "m" in period_pct.get(s, {})]
    y_vals = [period_pct.get(s, {}).get("y", 0) for s in cat_symbols if "y" in period_pct.get(s, {})]

    result[cat] = {
      "d": round(sum(d_vals) / max(len(d_vals), 1), 2),
      "w": round(sum(w_vals) / max(len(w_vals), 1), 2),
      "m": round(sum(m_vals) / max(len(m_vals), 1), 2),
      "y": round(sum(y_vals) / max(len(y_vals), 1), 2),
      "asset_count": len(cat_symbols),
      "symbols": cat_symbols,
    }

  return {"categories": result, "source": source, "updated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/category-chart")
def category_chart(
  categories: str = Query(""),
  timeframe: str = Query("1M"),
  source: str = Query("watchlist"),
  db: Session = Depends(get_db),
):
  """Cumulative return line chart data for selected categories.

  Returns daily data points for recharts LineChart.
  """
  from datetime import timedelta

  cat_list = [c.strip() for c in categories.split(",") if c.strip()]
  if not cat_list:
    return {"dates": [], "series": {}}

  symbols = _get_symbols_for_source(source, db)
  cat_map = _build_category_map(symbols, db)

  # Timeframe to days mapping
  tf_days = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365, "ALL": 3650}
  days = tf_days.get(timeframe, 30)
  cutoff = datetime.now(timezone.utc) - timedelta(days=days)

  # For each requested category, get daily closes for its symbols
  from backend.db.database import ohlcv_router
  all_dates: set[str] = set()
  cat_daily: dict[str, dict[str, list[float]]] = {}  # {cat: {date_str: [returns]}}

  ohlcv_db = ohlcv_router.get_session("30")
  try:
    for cat in cat_list:
      cat_symbols = cat_map.get(cat, [])
      if not cat_symbols:
        continue
      cat_daily[cat] = {}

      for sym in cat_symbols:
        # Get 30m candles, take daily close (last bar of each day)
        candles = ohlcv_db.query(OHLCVData.timestamp, OHLCVData.close).filter(
          OHLCVData.symbol == sym,
          OHLCVData.timeframe == "30",
          OHLCVData.timestamp >= cutoff,
        ).order_by(OHLCVData.timestamp).all()

        if len(candles) < 2:
          continue

        # Group by date, take last close
        daily_closes: dict[str, float] = {}
        for ts, close in candles:
          if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
          date_str = ts.strftime("%Y-%m-%d")
          daily_closes[date_str] = close

        # Compute daily returns as % from first day
        sorted_dates = sorted(daily_closes.keys())
        if not sorted_dates:
          continue
        base_price = daily_closes[sorted_dates[0]]
        if base_price <= 0:
          continue

        for d in sorted_dates:
          pct = ((daily_closes[d] - base_price) / base_price) * 100
          cat_daily[cat].setdefault(d, []).append(pct)
          all_dates.add(d)
  finally:
    ohlcv_db.close()

  # Average returns per category per date
  sorted_all_dates = sorted(all_dates)
  series: dict[str, list[float]] = {}
  for cat in cat_list:
    cat_data = cat_daily.get(cat, {})
    series[cat] = []
    for d in sorted_all_dates:
      vals = cat_data.get(d, [])
      avg = round(sum(vals) / max(len(vals), 1), 2) if vals else 0
      series[cat].append(avg)

  return {"dates": sorted_all_dates, "series": series}


@router.get("/category-trading-pnl")
def category_trading_pnl(
  source: str = Query("watchlist"),
  db: Session = Depends(get_db),
):
  """Trading PnL by category for D/W/M/Y periods.

  Only returns categories that have actual closed trades.
  """
  from datetime import timedelta

  from backend.db.models import Trade

  symbols = _get_symbols_for_source(source, db)
  cat_map = _build_category_map(symbols, db)

  now = datetime.now(timezone.utc)
  cutoffs = {
    "d": now - timedelta(days=1),
    "w": now - timedelta(weeks=1),
    "m": now - timedelta(days=30),
    "y": now - timedelta(days=365),
  }

  # Get all closed trades for these symbols
  trades = db.query(Trade).filter(
    Trade.symbol.in_(symbols),
    Trade.closed_at.isnot(None),
  ).all()

  # Map symbol -> categories
  sym_cats: dict[str, list[str]] = {}
  for cat, cat_symbols in cat_map.items():
    for sym in cat_symbols:
      sym_cats.setdefault(sym, []).append(cat)

  # Accumulate PnL by category and period
  result: dict[str, dict[str, float]] = {}
  cat_trade_counts: dict[str, int] = {}

  for trade in trades:
    cats = sym_cats.get(trade.symbol, [])
    if not cats:
      continue
    pnl = trade.realized_pnl or 0
    closed = trade.closed_at
    if isinstance(closed, str):
      try:
        closed = datetime.fromisoformat(closed)
      except ValueError:
        continue

    for cat in cats:
      if cat not in result:
        result[cat] = {"d": 0, "w": 0, "m": 0, "y": 0}
        cat_trade_counts[cat] = 0
      cat_trade_counts[cat] += 1
      for period_key, cutoff in cutoffs.items():
        if closed and closed >= cutoff:
          result[cat][period_key] += pnl

  # Round and add trade count
  final: dict[str, dict] = {}
  for cat, pnls in result.items():
    final[cat] = {
      "d": round(pnls["d"], 2),
      "w": round(pnls["w"], 2),
      "m": round(pnls["m"], 2),
      "y": round(pnls["y"], 2),
      "trade_count": cat_trade_counts.get(cat, 0),
    }

  return {"categories": final, "source": source}


@router.get("/filtered-asset-returns")
async def filtered_asset_returns(
  include_cats: str = Query(""),
  exclude_cats: str = Query(""),
  timeframe: str = Query("1M"),
  source: str = Query("watchlist"),
  db: Session = Depends(get_db),
):
  """Returns D/W/M/Y for assets matching included categories but NOT in excluded.

  Logic: union of included category assets MINUS any asset in excluded categories.
  Also returns cumulative chart data for the filtered asset set.
  """
  from datetime import timedelta

  import httpx

  inc_list = [c.strip() for c in include_cats.split(",") if c.strip()]
  exc_list = [c.strip() for c in exclude_cats.split(",") if c.strip()]
  if not inc_list:
    return {"d": 0, "w": 0, "m": 0, "y": 0, "asset_count": 0, "symbols": [],
            "chart": {"dates": [], "values": []}}

  symbols = _get_symbols_for_source(source, db)
  cat_map = _build_category_map(symbols, db)

  # Build filtered asset set: union of included MINUS excluded
  included_assets: set[str] = set()
  for cat in inc_list:
    for sym in cat_map.get(cat, []):
      included_assets.add(sym)
  excluded_assets: set[str] = set()
  for cat in exc_list:
    for sym in cat_map.get(cat, []):
      excluded_assets.add(sym)
  filtered = sorted(included_assets - excluded_assets)

  if not filtered:
    return {"d": 0, "w": 0, "m": 0, "y": 0, "asset_count": 0, "symbols": [],
            "chart": {"dates": [], "values": []}}

  # Daily % from Bybit tickers
  daily_pct: dict[str, float] = {}
  try:
    from backend.config.settings import get_settings
    base_url = get_settings().bybit_base_url
    async with httpx.AsyncClient(timeout=15) as client:
      resp = await client.get(f"{base_url}/v5/market/tickers", params={"category": "linear"})
      resp.raise_for_status()
      for item in resp.json().get("result", {}).get("list", []):
        sym = item.get("symbol", "")
        if sym in included_assets:
          daily_pct[sym] = float(item.get("price24hPcnt", 0)) * 100
  except Exception as _exc:
    logger.debug("swallowed: %s", _exc)

  # W/M/Y from OHLCV
  from backend.db.database import ohlcv_router
  periods = {"w": 336, "m": 1440, "y": 17520}
  asset_pct: dict[str, dict[str, float]] = {}
  ohlcv_db = ohlcv_router.get_session("30")
  try:
    for pk, bc in periods.items():
      for sym in filtered:
        latest = ohlcv_db.query(OHLCVData.close).filter(
          OHLCVData.symbol == sym, OHLCVData.timeframe == "30",
        ).order_by(OHLCVData.timestamp.desc()).first()
        old = ohlcv_db.query(OHLCVData.close).filter(
          OHLCVData.symbol == sym, OHLCVData.timeframe == "30",
        ).order_by(OHLCVData.timestamp.desc()).offset(bc).first()
        if latest and old and old[0] > 0:
          asset_pct.setdefault(sym, {})[pk] = round(((latest[0] - old[0]) / old[0]) * 100, 2)
  finally:
    ohlcv_db.close()

  # Averages
  d_vals = [daily_pct[s] for s in filtered if s in daily_pct]
  w_vals = [asset_pct.get(s, {}).get("w", 0) for s in filtered if "w" in asset_pct.get(s, {})]
  m_vals = [asset_pct.get(s, {}).get("m", 0) for s in filtered if "m" in asset_pct.get(s, {})]
  y_vals = [asset_pct.get(s, {}).get("y", 0) for s in filtered if "y" in asset_pct.get(s, {})]

  avg_d = round(sum(d_vals) / max(len(d_vals), 1), 2)
  avg_w = round(sum(w_vals) / max(len(w_vals), 1), 2)
  avg_m = round(sum(m_vals) / max(len(m_vals), 1), 2)
  avg_y = round(sum(y_vals) / max(len(y_vals), 1), 2)

  # Chart: cumulative returns for the filtered group
  tf_days = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365, "ALL": 3650}
  days = tf_days.get(timeframe, 30)
  cutoff = datetime.now(timezone.utc) - timedelta(days=days)

  all_dates: set[str] = set()
  daily_returns: dict[str, list[float]] = {}  # {date: [pct_from_base per asset]}

  from backend.db.database import ohlcv_router
  chart_db = ohlcv_router.get_session("30")
  try:
    for sym in filtered:
      candles = chart_db.query(OHLCVData.timestamp, OHLCVData.close).filter(
        OHLCVData.symbol == sym, OHLCVData.timeframe == "30",
        OHLCVData.timestamp >= cutoff,
      ).order_by(OHLCVData.timestamp).all()
      if len(candles) < 2:
        continue
      closes: dict[str, float] = {}
      for ts, close in candles:
        if isinstance(ts, str):
          ts = datetime.fromisoformat(ts)
        closes[ts.strftime("%Y-%m-%d")] = close
      sdates = sorted(closes.keys())
      if not sdates:
        continue
      base = closes[sdates[0]]
      if base <= 0:
        continue
      for d in sdates:
        pct = ((closes[d] - base) / base) * 100
        daily_returns.setdefault(d, []).append(pct)
        all_dates.add(d)
  finally:
    chart_db.close()

  sorted_dates = sorted(all_dates)
  chart_values = []
  for d in sorted_dates:
    vals = daily_returns.get(d, [])
    chart_values.append(round(sum(vals) / max(len(vals), 1), 2) if vals else 0)

  return {
    "d": avg_d, "w": avg_w, "m": avg_m, "y": avg_y,
    "asset_count": len(filtered), "symbols": filtered,
    "chart": {"dates": sorted_dates, "values": chart_values},
  }
