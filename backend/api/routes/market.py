"""Market data routes — live ticker prices from Bybit public API."""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Query

from backend.config import get_settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

# Simple in-memory cache (avoids hammering Bybit on rapid page loads)
_ticker_cache: dict[str, object] = {"data": None, "ts": 0.0}
CACHE_TTL = 10  # seconds


@router.get("/tickers")
async def market_tickers(symbols: str = Query(default="")):
  """Fetch live ticker data from Bybit public API.

  symbols: comma-separated list (e.g. "BTCUSDT,ETHUSDT"). Empty = defaults.
  """
  requested = [s.strip().upper() for s in symbols.split(",") if s.strip()] or DEFAULT_SYMBOLS
  now = time.time()

  # Return cached data if fresh enough
  if _ticker_cache["data"] and (now - _ticker_cache["ts"]) < CACHE_TTL:
    all_tickers = _ticker_cache["data"]
  else:
    all_tickers = await _fetch_all_tickers()
    if all_tickers is not None:
      _ticker_cache["data"] = all_tickers
      _ticker_cache["ts"] = now

  if all_tickers is None:
    return {"tickers": [], "error": "Failed to fetch from Bybit"}

  # Filter and format
  lookup = {t["symbol"]: t for t in all_tickers}
  tickers = []
  for sym in requested:
    t = lookup.get(sym)
    if not t:
      continue
    tickers.append({
      "symbol": sym,
      "last_price": _float(t.get("lastPrice")),
      "price_change_pct": _float(t.get("price24hPcnt")) * 100,
      "high_24h": _float(t.get("highPrice24h")),
      "low_24h": _float(t.get("lowPrice24h")),
      "volume_24h": _float(t.get("turnover24h")),
      "funding_rate": _float(t.get("fundingRate")),
    })

  return {"tickers": tickers}


async def _fetch_all_tickers() -> list[dict] | None:
  """Fetch all linear USDT perpetual tickers from Bybit."""
  s = get_settings()
  url = f"{s.bybit_base_url}/v5/market/tickers"
  try:
    async with httpx.AsyncClient(timeout=8) as client:
      resp = await client.get(url, params={"category": "linear"})
      resp.raise_for_status()
      data = resp.json()
    if data.get("retCode") != 0:
      logger.warning("Bybit tickers API error: %s", data.get("retMsg"))
      return None
    return data.get("result", {}).get("list", [])
  except Exception as e:
    logger.warning("Failed to fetch Bybit tickers: %s", e)
    return None


def _float(v: str | None) -> float:
  """Safe string → float conversion."""
  try:
    return float(v) if v else 0.0
  except (ValueError, TypeError):
    return 0.0
