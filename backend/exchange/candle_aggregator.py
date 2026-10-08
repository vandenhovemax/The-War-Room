"""Candle Aggregator — receives per-TF kline data from WebSocket.

When subscribed directly to the base TF kline (e.g. kline.5, kline.30),
confirmed bars fire on_bar_close immediately with complete OHLCV from Bybit.
No 1-minute aggregation needed — Bybit handles the candle assembly server-side.

Legacy 1m aggregation path is kept as fallback but unused when direct TF
subscriptions are active (the dedup guard prevents duplicate fires).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Timeframe → minutes mapping
TF_MINUTES: dict[str, int] = {
  "1": 1, "3": 3, "5": 5, "15": 15, "30": 30,
  "60": 60, "120": 120, "240": 240, "360": 360, "720": 720,
  "D": 1440, "W": 10080,
}


@dataclass
class CandleBuffer:
  """In-memory buffer for building a candle from 1m ticks."""
  open: float = 0.0
  high: float = 0.0
  low: float = 0.0
  close: float = 0.0
  volume: float = 0.0
  open_time: datetime | None = None
  bar_count: int = 0  # How many 1m bars received


@dataclass
class SymbolSubscription:
  """Tracks which timeframes a symbol needs and their buffers."""
  base_tf: str  # Primary timeframe (e.g. "30")
  extra_tfs: list[str] = field(default_factory=list)  # Additional TFs (e.g. ["D"])
  buffers: dict[str, CandleBuffer] = field(default_factory=dict)
  # Historical data for strategy context (last N bars per TF)
  history: dict[str, list[dict]] = field(default_factory=dict)
  max_history: int = 600  # Keep last 600 bars per TF


# Type for bar close callback: (symbol, timeframe, bar_data_dict)
BarCloseCallback = Callable[[str, str, dict], None]


class CandleAggregator:
  """Aggregates 1-minute candles into multiple target timeframes.

  Usage:
    1. Register symbols with their required timeframes
    2. Feed 1-minute kline data via on_kline_update()
    3. When a target-TF bar completes, fires the on_bar_close callback

  The callback receives (symbol, timeframe, bar_data) where bar_data has
  open/high/low/close/volume/timestamp keys.
  """

  def __init__(
    self,
    on_bar_close: BarCloseCallback | None = None,
    on_bar_persist: BarCloseCallback | None = None,
  ):
    self._on_bar_close = on_bar_close
    self._on_bar_persist = on_bar_persist  # DB persistence callback
    # symbol → SymbolSubscription
    self._subscriptions: dict[str, SymbolSubscription] = {}
    # Dedup: last bar close timestamp per (symbol, tf) — prevents duplicate fires on WS reconnect
    self._last_bar_fired: dict[str, datetime] = {}

  # ── Registration ──────────────────────────────────────────────────

  def register_symbol(
    self,
    symbol: str,
    base_tf: str,
    extra_tfs: list[str] | None = None,
    max_history: int = 600,
  ) -> None:
    """Register a symbol for candle aggregation.

    Args:
      symbol: e.g. "BTCUSDT"
      base_tf: Primary timeframe string (e.g. "30" for 30m)
      extra_tfs: Additional timeframes needed (e.g. ["D"] for daily)
      max_history: How many bars to keep in memory per TF
    """
    if extra_tfs is None:
      extra_tfs = []

    all_tfs = [base_tf] + extra_tfs
    sub = SymbolSubscription(
      base_tf=base_tf,
      extra_tfs=extra_tfs,
      buffers={tf: CandleBuffer() for tf in all_tfs},
      history={tf: [] for tf in all_tfs},
      max_history=max_history,
    )
    self._subscriptions[symbol] = sub
    logger.info("Registered %s for candle aggregation: base=%s extra=%s", symbol, base_tf, extra_tfs)

  def unregister_symbol(self, symbol: str) -> None:
    """Stop aggregating candles for a symbol."""
    self._subscriptions.pop(symbol, None)
    logger.info("Unregistered %s from candle aggregation", symbol)

  def get_registered_symbols(self) -> list[str]:
    """Get list of all registered symbols."""
    return list(self._subscriptions.keys())

  # ── Kline Updates ─────────────────────────────────────────────────

  def on_kline_update(self, symbol: str, kline_data: dict) -> None:
    """Process a 1-minute kline update from WebSocket.

    Args:
      kline_data: {
        "start": int (ms timestamp),
        "end": int (ms timestamp),
        "open": str, "high": str, "low": str, "close": str,
        "volume": str, "confirm": bool (true = bar closed)
      }
    """
    sub = self._subscriptions.get(symbol)
    if not sub:
      return

    o = float(kline_data.get("open", 0))
    h = float(kline_data.get("high", 0))
    lo = float(kline_data.get("low", 0))
    c = float(kline_data.get("close", 0))
    v = float(kline_data.get("volume", 0))
    start_ms = int(kline_data.get("start", 0))
    confirmed = kline_data.get("confirm", False)
    interval = str(kline_data.get("interval", "1"))

    # ── Direct TF kline (non-1m) — fire bar close directly ──────────
    # When subscribed to the actual TF (e.g. kline.5, kline.30), Bybit
    # sends confirmed bars with complete OHLCV. No aggregation needed.
    if interval != "1":
      if not confirmed:
        return  # Ignore interim updates for non-1m (not needed)

      tf_key = interval  # Bybit interval matches our canonical TF key
      if tf_key not in sub.buffers:
        return  # Not a TF we're tracking

      bar_time = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
      completed_bar = {
        "timestamp": bar_time,
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
        "volume": v,
      }

      # Dedup: skip if this bar was already fired
      dedup_key = f"{symbol}_{tf_key}"
      if dedup_key in self._last_bar_fired:
        if self._last_bar_fired[dedup_key] >= bar_time:
          return
      self._last_bar_fired[dedup_key] = bar_time

      # Add to history
      sub.history[tf_key].append(completed_bar)
      if len(sub.history[tf_key]) > sub.max_history:
        sub.history[tf_key] = sub.history[tf_key][-sub.max_history:]

      # Reset any 1m aggregation buffer for this TF (prevent stale fire)
      sub.buffers[tf_key] = CandleBuffer()

      # Fire bar close callback
      if self._on_bar_close:
        try:
          self._on_bar_close(symbol, tf_key, completed_bar)
        except Exception as e:
          logger.error("on_bar_close callback error for %s %s: %s", symbol, tf_key, e)

      # Persist completed bar to DB
      if self._on_bar_persist:
        try:
          self._on_bar_persist(symbol, tf_key, completed_bar)
        except Exception as e:
          logger.error("on_bar_persist callback error for %s %s: %s", symbol, tf_key, e)

      return  # Done — don't fall through to 1m aggregation

    # ── 1m kline path (legacy fallback) ─────────────────────────────
    # Kept for backward compatibility. When direct TF subscriptions are
    # active, the dedup guard above prevents duplicate bar close fires.

    if not confirmed:
      # Interim update — update current buffer closes but don't advance
      for tf in [sub.base_tf] + sub.extra_tfs:
        buf = sub.buffers[tf]
        if buf.bar_count > 0:
          buf.close = c
          buf.high = max(buf.high, h)
          buf.low = min(buf.low, lo)
          buf.volume += v  # Approximate — will be reset on confirm
      return

    # Confirmed 1-minute bar — aggregate into each target TF
    bar_time = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)

    for tf in [sub.base_tf] + sub.extra_tfs:
      tf_minutes = TF_MINUTES.get(tf, int(tf) if tf.isdigit() else 1440)
      buf = sub.buffers[tf]
      bars_needed = tf_minutes  # How many 1m bars make one TF bar

      if buf.bar_count == 0:
        # Start a new candle
        # Align to timeframe boundary
        aligned_time = self._align_to_tf(bar_time, tf_minutes)
        buf.open_time = aligned_time
        buf.open = o
        buf.high = h
        buf.low = lo
        buf.close = c
        buf.volume = v
        buf.bar_count = 1
      else:
        # Extend current candle
        buf.high = max(buf.high, h)
        buf.low = min(buf.low, lo)
        buf.close = c
        buf.volume += v
        buf.bar_count += 1

      # Check if this TF bar is complete
      if buf.bar_count >= bars_needed:
        completed_bar = {
          "timestamp": buf.open_time,
          "open": buf.open,
          "high": buf.high,
          "low": buf.low,
          "close": buf.close,
          "volume": buf.volume,
        }

        # Add to history
        sub.history[tf].append(completed_bar)
        if len(sub.history[tf]) > sub.max_history:
          sub.history[tf] = sub.history[tf][-sub.max_history:]

        # Dedup: skip if this bar was already fired (direct TF or WS reconnect)
        dedup_key = f"{symbol}_{tf}"
        bar_ts = completed_bar["timestamp"]
        if bar_ts and dedup_key in self._last_bar_fired:
          if self._last_bar_fired[dedup_key] >= bar_ts:
            logger.debug(
              "Skipping duplicate bar close (1m agg): %s %s ts=%s",
              symbol, tf, bar_ts,
            )
            sub.buffers[tf] = CandleBuffer()
            continue
        self._last_bar_fired[dedup_key] = bar_ts

        # Fire callback
        if self._on_bar_close:
          try:
            self._on_bar_close(symbol, tf, completed_bar)
          except Exception as e:
            logger.error("on_bar_close callback error for %s %s: %s", symbol, tf, e)

        # Persist completed bar to DB (keeps DB always current)
        if self._on_bar_persist:
          try:
            self._on_bar_persist(symbol, tf, completed_bar)
          except Exception as e:
            logger.error("on_bar_persist callback error for %s %s: %s", symbol, tf, e)

        # Reset buffer for next candle
        sub.buffers[tf] = CandleBuffer()

  # ── Seed with Historical Data ─────────────────────────────────────

  def seed_history(self, symbol: str, timeframe: str, bars: list[dict]) -> None:
    """Pre-load historical bars for warmup.

    Args:
      bars: List of {timestamp, open, high, low, close, volume} dicts,
            ordered chronologically (oldest first).
    """
    sub = self._subscriptions.get(symbol)
    if not sub or timeframe not in sub.history:
      return

    sub.history[timeframe] = bars[-sub.max_history:]
    logger.info("Seeded %s %s with %d historical bars", symbol, timeframe, len(bars))

  def get_history_df(self, symbol: str, timeframe: str) -> pd.DataFrame:
    """Get historical bars as a DataFrame (for strategy warmup).

    Returns DataFrame with timestamp index and OHLCV columns.
    """
    sub = self._subscriptions.get(symbol)
    if not sub or timeframe not in sub.history or not sub.history[timeframe]:
      return pd.DataFrame()

    df = pd.DataFrame(sub.history[timeframe])
    if "timestamp" in df.columns:
      df = df.set_index("timestamp")
    return df

  def get_latest_bar(self, symbol: str, timeframe: str) -> dict | None:
    """Get the most recently completed bar for a symbol/TF."""
    sub = self._subscriptions.get(symbol)
    if not sub or timeframe not in sub.history or not sub.history[timeframe]:
      return None
    return sub.history[timeframe][-1]

  # ── Internal ──────────────────────────────────────────────────────

  @staticmethod
  def _align_to_tf(dt: datetime, tf_minutes: int) -> datetime:
    """Align a datetime to the nearest timeframe boundary."""
    if tf_minutes >= 1440:
      # Daily/weekly — align to midnight UTC
      return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    total_minutes = dt.hour * 60 + dt.minute
    aligned_minutes = (total_minutes // tf_minutes) * tf_minutes
    return dt.replace(
      hour=aligned_minutes // 60,
      minute=aligned_minutes % 60,
      second=0,
      microsecond=0,
    )
