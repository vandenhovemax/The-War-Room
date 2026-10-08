"""BTC Price Context — fetches BTCUSDT daily klines from Bybit V5.

Used to show BTC performance during key strategy events
(max DD, loss streaks, etc.) via "when" captions.

Ported from market_structure_dashboard/core/btc_data.py.
Uses httpx (already in War Room deps) instead of requests.
"""
from __future__ import annotations

from functools import lru_cache

import httpx
import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=128)
def fetch_btc_daily(start_ts_ms: int, end_ts_ms: int) -> pd.DataFrame:
    """Fetch BTCUSDT daily klines from Bybit V5 public API.

    Returns DataFrame with columns: timestamp, open, close.
    Cached in-process (lru_cache) to avoid repeated API calls.
    """
    try:
        url = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "linear",
            "symbol": "BTCUSDT",
            "interval": "D",
            "start": int(start_ts_ms),
            "end": int(end_ts_ms),
            "limit": 1000,
        }
        resp = httpx.get(url, params=params, timeout=10)
        data = resp.json()

        if data.get("retCode") != 0 or not data.get("result", {}).get("list"):
            return pd.DataFrame()

        rows = []
        for candle in data["result"]["list"]:
            rows.append({
                "timestamp": pd.Timestamp(int(candle[0]), unit="ms"),
                "open": float(candle[1]),
                "close": float(candle[4]),
            })

        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    except Exception as e:
        logger.warning("Failed to fetch BTC data: %s", e)
        return pd.DataFrame()


def btc_change_between(start, end) -> float | None:
    """Calculate BTC % change between two dates.

    Returns a float (e.g. -15.3 for a 15.3% drop) or None if unavailable.
    """
    if start is None or end is None:
        return None

    try:
        start_dt = pd.Timestamp(start)
        end_dt = pd.Timestamp(end)

        if start_dt >= end_dt:
            return None

        buffer = pd.Timedelta(days=2)
        start_ms = int((start_dt - buffer).timestamp() * 1000)
        end_ms = int((end_dt + buffer).timestamp() * 1000)

        df = fetch_btc_daily(start_ms, end_ms)
        if df.empty or len(df) < 2:
            return None

        # Find closest rows to start and end dates
        start_row = df.iloc[(df["timestamp"] - start_dt).abs().argsort().iloc[0]]
        end_row = df.iloc[(df["timestamp"] - end_dt).abs().argsort().iloc[0]]

        if start_row["open"] == 0:
            return None

        pct = ((end_row["close"] - start_row["open"]) / start_row["open"]) * 100
        return round(pct, 1)
    except (KeyError, IndexError, ValueError, TypeError) as e:
        # CS-045: the old bare ``except Exception`` silently hid IndexError
        # from empty DataFrames (.iloc[0] on a filtered-to-zero row selection)
        # and KeyError from schema drift. Narrowed to the real failure
        # modes and logged, so a genuine bug surfaces instead of returning
        # a silent ``None``.
        logger.warning("btc_change_between failed (%s): %s", type(e).__name__, e)
        return None
