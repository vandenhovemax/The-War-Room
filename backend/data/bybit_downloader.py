"""
Bybit V5 public API client for downloading OHLCV candle data.
No authentication required — uses public market endpoints only.
"""
import asyncio
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from backend.config import get_settings
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Bybit V5 interval mapping: our label -> Bybit API value
INTERVAL_MAP = {
  "1": "1",
  "3": "3",
  "5": "5",
  "15": "15",
  "30": "30",
  "60": "60",
  "120": "120",
  "240": "240",
  "360": "360",
  "720": "720",
  "D": "D",
  "W": "W",
  "M": "M",
}


class BybitDownloader:
  """Fetches market data from Bybit V5 public API."""

  def __init__(self):
    settings = get_settings()
    self.base_url = settings.bybit_base_url
    self._client: httpx.AsyncClient | None = None

  async def _get_client(self) -> httpx.AsyncClient:
    """Return a shared httpx client (reuses TCP connections)."""
    if self._client is None or self._client.is_closed:
      self._client = httpx.AsyncClient(timeout=30)
    return self._client

  async def close(self):
    """Close the shared HTTP client."""
    if self._client and not self._client.is_closed:
      await self._client.aclose()
      self._client = None

  async def get_usdt_perpetuals(self) -> list[str]:
    """Return all active USDT perpetual symbols."""
    url = f"{self.base_url}/v5/market/instruments-info"
    params = {"category": "linear", "limit": "1000"}

    client = await self._get_client()
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    symbols = []
    for item in data.get("result", {}).get("list", []):
      if item.get("quoteCoin") == "USDT" and item.get("status") == "Trading":
        symbols.append(item["symbol"])

    symbols.sort()
    logger.info("Found %d USDT perpetual symbols on Bybit", len(symbols))
    return symbols

  async def get_all_instrument_specs(self) -> dict[str, dict]:
    """Fetch lot_size/min_qty/max_qty for all USDT perpetuals.

    Returns: { "BTCUSDT": { "min_qty": 0.001, "max_qty": 100.0,
               "lot_size": 0.001 }, ... }
    """
    url = f"{self.base_url}/v5/market/instruments-info"
    params = {"category": "linear", "limit": "1000"}

    client = await self._get_client()
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    specs: dict[str, dict] = {}
    for item in data.get("result", {}).get("list", []):
      if item.get("quoteCoin") != "USDT" or item.get("status") != "Trading":
        continue
      lot_filter = item.get("lotSizeFilter", {})
      lev_filter = item.get("leverageFilter", {})
      specs[item["symbol"]] = {
        "min_qty": float(lot_filter.get("minOrderQty", "0.001")),
        "max_qty": float(lot_filter.get("maxOrderQty", "1000")),
        "lot_size": float(lot_filter.get("qtyStep", "0.001")),
        "max_leverage": float(lev_filter.get("maxLeverage", "100")),
      }

    logger.info("Fetched instrument specs for %d symbols", len(specs))
    return specs

  async def fetch_klines(
    self,
    symbol: str,
    interval: str = "30",
    limit: int = 1000,
    start_ms: int | None = None,
    end_ms: int | None = None,
  ) -> list[dict]:
    """
    Fetch OHLCV klines from Bybit V5.

    Returns list of dicts with keys:
      timestamp, open, high, low, close, volume
    """
    if interval not in INTERVAL_MAP:
      raise ValueError(f"Invalid interval '{interval}'. Use one of: {list(INTERVAL_MAP.keys())}")

    url = f"{self.base_url}/v5/market/kline"
    params = {
      "category": "linear",
      "symbol": symbol,
      "interval": INTERVAL_MAP[interval],
      "limit": str(min(limit, 1000)),
    }
    if start_ms is not None:
      params["start"] = str(start_ms)
    if end_ms is not None:
      params["end"] = str(end_ms)

    client = await self._get_client()
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    if data.get("retCode") != 0:
      raise RuntimeError(f"Bybit API error: {data.get('retMsg', 'unknown')}")

    raw_list = data.get("result", {}).get("list", [])
    candles = []
    for row in raw_list:
      # Bybit V5 kline: [startTime, open, high, low, close, volume, turnover]
      candles.append({
        "timestamp": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
      })

    # Bybit returns newest first — reverse to chronological order
    candles.sort(key=lambda c: c["timestamp"])
    logger.info("Fetched %d candles for %s/%s", len(candles), symbol, interval)
    return candles

  async def fetch_all_klines(
    self,
    symbol: str,
    interval: str = "30",
    since_ms: int | None = None,
    end_ms: int | None = None,
    progress_callback: Callable | None = None,
  ) -> list[dict]:
    """
    Paginate through Bybit API to fetch all available candles.
    If since_ms is provided, only fetches candles after that timestamp.
    If end_ms is provided, only fetches candles before that timestamp.
    progress_callback(pages_done, total_candles) is called after each page.
    """
    all_candles: list[dict] = []
    pages = 0

    for _ in range(500):  # safety limit: 500 pages * 1000 = 500k candles
      candles = await self.fetch_klines(
        symbol, interval, limit=1000, start_ms=since_ms, end_ms=end_ms,
      )
      if not candles:
        break

      all_candles.extend(candles)
      pages += 1

      if progress_callback:
        progress_callback(pages, len(all_candles))

      # Move the window back (Bybit paginates by end time)
      oldest_ts = int(candles[0]["timestamp"].timestamp() * 1000)
      if end_ms is not None and oldest_ts >= end_ms:
        break
      end_ms = oldest_ts - 1

      if len(candles) < 1000:
        break

      # Small delay to respect rate limits
      await asyncio.sleep(0.1)

    # Deduplicate and sort
    seen = set()
    unique = []
    for c in all_candles:
      key = c["timestamp"]
      if key not in seen:
        seen.add(key)
        unique.append(c)

    unique.sort(key=lambda c: c["timestamp"])
    logger.info(
      "Total fetched: %d unique candles for %s/%s", len(unique), symbol, interval
    )
    return unique
