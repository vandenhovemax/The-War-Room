"""
Base strategy class — all transpiled PineScript strategies inherit from this.
Provides series access (close, open, high, low, volume), order methods,
multi-position support, multi-timeframe data, and streak tracking.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.backtester.models import ExitOrder, OrderFill, Position


class BaseStrategy:
  """Abstract base for all backtestable strategies.

  Subclasses override:
    - init()  — called once before the first bar
    - next()  — called on every bar
  """

  def __init__(self):
    # Set by the engine before run
    self._df: pd.DataFrame = pd.DataFrame()
    self._bar: int = 0
    self._positions: dict[str, Position] = {}
    # CS-050: O(1) same-side position counter. Maintained at every
    # ``_positions`` mutation so the pyramiding check in ``_fill_entry``
    # does NOT scan every position on every entry. Parity with the old
    # ``sum(... if p.side == direction)`` is locked by the existing
    # golden-reference test + a dedicated parity test.
    self._positions_by_side: dict[str, int] = {"long": 0, "short": 0}
    self._exit_orders: dict[str, ExitOrder] = {}
    self._trades: list[OrderFill] = []
    self._pending_orders: list[dict] = []
    self._equity_curve: list[float] = []
    self._initial_capital: float = 10_000.0
    self._cash: float = 10_000.0
    self._inputs: dict = {}
    self._plot_data: dict[str, list] = {}
    self._max_pyramiding: int = 1
    # Streak tracking (martingale support)
    self._consecutive_wins: int = 0
    self._consecutive_losses: int = 0
    self._closed_trades_count: int = 0
    # Warmup
    self._warmup_bars: int = 0
    self._warmup_complete: bool = False
    # Multi-timeframe data
    self._tf_data: dict[str, pd.DataFrame] = {}
    # Consumed exit tracking — prevents re-registration of fired partial exits
    # (matches PineScript semantics: once an exit fires, it's consumed)
    # Maps exit_id → from_entry so we can clean up when position fully closes
    self._consumed_exits: dict[str, str] = {}

  # ── Backward-compat _position property ────────────────────────
  @property
  def _position(self) -> Position:
    """Returns first open position or empty (backward compat)."""
    if self._positions:
      return next(iter(self._positions.values()))
    return Position()

  @_position.setter
  def _position(self, value: Position):
    # CS-050: maintain side counter alongside every ``_positions`` mutation
    # so legacy strategies that use ``self._position = Position(...)`` still
    # get an accurate pyramiding count at the ``_fill_entry`` check.
    if value.side:
      key = value.id or value.label or "_default"
      prev = self._positions.get(key)
      if prev is not None and prev.side:
        self._positions_by_side[prev.side] = self._positions_by_side.get(prev.side, 0) - 1
      self._positions[key] = value
      self._positions_by_side[value.side] = self._positions_by_side.get(value.side, 0) + 1
    else:
      self._positions.clear()
      self._positions_by_side = {"long": 0, "short": 0}

  # ── Series access (current bar) — uses pre-extracted numpy arrays ──
  @property
  def close(self) -> float:
    return float(self._closes[self._bar])

  @property
  def open(self) -> float:
    return float(self._opens[self._bar])

  @property
  def high(self) -> float:
    return float(self._highs[self._bar])

  @property
  def low(self) -> float:
    return float(self._lows[self._bar])

  @property
  def volume(self) -> float:
    return float(self._volumes[self._bar])

  @property
  def time(self) -> pd.Timestamp:
    return self._timestamps[self._bar]

  @property
  def hour(self) -> int:
    """Current bar's hour (0-23) in New York Time."""
    return int(self._hours[self._bar])

  @property
  def minute(self) -> int:
    """Current bar's minute (0-59)."""
    return int(self._minutes[self._bar])

  @property
  def second(self) -> int:
    """Current bar's second (0-59)."""
    return int(self._seconds[self._bar])

  @property
  def dayofweek(self) -> int:
    """Day of week — PineScript convention: 1=Sun, 2=Mon, ..., 7=Sat."""
    return int(self._dayofweek_pine[self._bar])

  @property
  def dayofmonth(self) -> int:
    """Day of month (1-31)."""
    return int(self._days[self._bar])

  @property
  def month(self) -> int:
    """Month (1-12)."""
    return int(self._months[self._bar])

  @property
  def year(self) -> int:
    """Year (e.g. 2025)."""
    return int(self._years[self._bar])

  @property
  def bar_index(self) -> int:
    return self._bar

  @property
  def last_bar_index(self) -> int:
    return len(self._df) - 1

  # ── Full series access (for indicators) ────────────────────────
  @property
  def close_series(self) -> pd.Series:
    return self._df["close"]

  @property
  def open_series(self) -> pd.Series:
    return self._df["open"]

  @property
  def high_series(self) -> pd.Series:
    return self._df["high"]

  @property
  def low_series(self) -> pd.Series:
    return self._df["low"]

  @property
  def volume_series(self) -> pd.Series:
    return self._df["volume"]

  # ── History reference: close[n] — uses pre-extracted numpy arrays ──
  def close_at(self, n: int) -> float:
    idx = self._bar - n
    return np.nan if idx < 0 else float(self._closes[idx])

  def open_at(self, n: int) -> float:
    idx = self._bar - n
    return np.nan if idx < 0 else float(self._opens[idx])

  def high_at(self, n: int) -> float:
    idx = self._bar - n
    return np.nan if idx < 0 else float(self._highs[idx])

  def low_at(self, n: int) -> float:
    idx = self._bar - n
    return np.nan if idx < 0 else float(self._lows[idx])

  def volume_at(self, n: int) -> float:
    idx = self._bar - n
    return np.nan if idx < 0 else float(self._volumes[idx])

  # ── Position properties ────────────────────────────────────────
  @property
  def position_size(self) -> float:
    """Total qty across all open positions."""
    return sum(p.qty for p in self._positions.values())

  @property
  def equity(self) -> float:
    """Current equity = cash + unrealized PnL across all open positions."""
    eq = self._cash
    if self._positions:
      price = float(self._closes[self._bar])
      for pos in self._positions.values():
        if pos.side == "long":
          eq += (price - pos.entry_price) * pos.qty
        else:
          eq += (pos.entry_price - price) * pos.qty
    return eq

  @property
  def position_avg_price(self) -> float:
    """Average entry price of first position (backward compat)."""
    if self._positions:
      return next(iter(self._positions.values())).entry_price
    return 0.0

  def get_position(self, entry_id: str) -> Position | None:
    """Get a specific position by its entry ID."""
    return self._positions.get(entry_id)

  # ── Streak properties (martingale) ─────────────────────────────
  @property
  def consecutive_wins(self) -> int:
    return self._consecutive_wins

  @property
  def consecutive_losses(self) -> int:
    return self._consecutive_losses

  @property
  def closed_trades_count(self) -> int:
    return self._closed_trades_count

  # ── Multi-timeframe access ────────────────────────────────────
  def request_security(self, timeframe: str, column: str) -> float:
    """Get most recent completed higher-TF bar value at current timestamp.
    Equivalent to PineScript request.security() — returns LAST COMPLETED bar only.

    Uses bar CLOSE time (open + duration) for the comparison because
    PineScript processes indicators at bar close on historical data.
    Without this, the last M30 bar of a daily period sees yesterday's
    daily value instead of today's (one-bar lag).
    """
    htf_df = self._tf_data.get(timeframe)
    if htf_df is None or htf_df.empty:
      return float("nan")
    # Use bar close time — PineScript sees completed HTF bars at bar close
    if len(self._timestamps) > 1:
      bar_duration = self._timestamps[1] - self._timestamps[0]
    else:
      bar_duration = pd.Timedelta(minutes=30)
    current_ts = self._timestamps[self._bar] + bar_duration
    # Determine HTF bar duration so we only include COMPLETED bars
    if timeframe == 'W':
      period = pd.Timedelta(weeks=1)
    elif timeframe.endswith('D'):
      days = int(timeframe[:-1]) if len(timeframe) > 1 else 1
      period = pd.Timedelta(days=days)
    else:
      period = pd.Timedelta(minutes=int(timeframe))
    # Bar is complete when its close time (open + period) <= current bar close
    mask = (htf_df.index + period) <= current_ts
    if not mask.any():
      return float("nan")
    return float(htf_df.loc[mask, column].iloc[-1])

  # ── Order methods (Pine strategy.*) ────────────────────────────
  def entry(self, id: str = "", direction: str = "long",
            qty: float = 0, limit: float = 0, stop: float = 0,
            comment: str = "", **kwargs):
    """Queue an entry order (executes next bar at open)."""
    if qty <= 0:
      qty = self._default_qty()
    self._pending_orders.append({
      "action": "entry", "id": id, "direction": direction,
      "qty": qty, "limit": limit, "stop": stop, "comment": comment,
    })

  def exit(self, id: str = "", from_entry: str = "",
           qty: float = 0, qty_percent: float = 0,
           limit: float = 0, stop: float = 0,
           trail_points: float = 0, trail_offset: float = 0,
           comment: str = "", **kwargs):
    """Register a persistent exit order (PineScript semantics).
    If stop/limit/trail_points set: persists and is checked each bar.
    If none set: queues a market exit for next bar.
    qty_percent: 1-100 = close that % of position (e.g. 50 = close half).
    Once an exit fires (partial or full), it's consumed and can't be re-registered.
    """
    if limit or stop or trail_points:
      # Skip if this exit already fired for this position (PineScript semantics)
      if id in self._consumed_exits:
        return
      # Register persistent exit order (checked each bar by engine)
      self._exit_orders[id] = ExitOrder(
        id=id, from_entry=from_entry,
        stop=stop, limit=limit,
        trail_points=trail_points, trail_offset=trail_offset,
        qty=qty, qty_percent=qty_percent,
      )
    else:
      # Market exit — queue for next bar
      self._pending_orders.append({
        "action": "exit", "id": id, "from_entry": from_entry,
        "qty": qty, "qty_percent": qty_percent, "comment": comment,
      })

  def close_position(self, id: str = "", comment: str = "", **kwargs):
    """Close a specific position by entry label."""
    self._pending_orders.append({
      "action": "close", "id": id, "comment": comment,
    })

  def close_all(self, comment: str = "", **kwargs):
    """Close all positions."""
    self._pending_orders.append({"action": "close_all", "comment": comment})

  def order(self, id: str = "", direction: str = "long",
            qty: float = 0, limit: float = 0, stop: float = 0, **kwargs):
    """Generic order."""
    if qty <= 0:
      qty = self._default_qty()
    self._pending_orders.append({
      "action": "order", "id": id, "direction": direction,
      "qty": qty, "limit": limit, "stop": stop,
    })

  def cancel(self, id: str = "", **kwargs):
    self._pending_orders = [o for o in self._pending_orders if o.get("id") != id]
    self._exit_orders.pop(id, None)

  def cancel_all(self, **kwargs):
    self._pending_orders.clear()
    self._exit_orders.clear()

  # ── Input ──────────────────────────────────────────────────────
  def input(self, defval=None, title: str = "", **kwargs):
    """Return input value (use override dict or default)."""
    if title and title in self._inputs:
      return self._inputs[title]
    return defval

  # ── Plot (no-op in backtester, stores data for charts) ─────────
  def plot(self, value=None, title: str = "", **kwargs):
    if title not in self._plot_data:
      self._plot_data[title] = []
    self._plot_data[title].append(value)

  def plotshape(self, *args, **kwargs):
    pass

  def plotchar(self, *args, **kwargs):
    pass

  def plotarrow(self, *args, **kwargs):
    pass

  def bgcolor(self, *args, **kwargs):
    pass

  def fill(self, *args, **kwargs):
    pass

  def hline(self, *args, **kwargs):
    pass

  def alert(self, *args, **kwargs):
    pass

  # ── Lifecycle (override in subclass) ───────────────────────────
  def init(self):
    """Called once before the first bar. Override in subclass."""
    pass

  def next(self):
    """Called on every bar. Override in subclass."""
    pass

  # ── Fast indicator access (post-init conversion) ──────────────
  def _array_safe_iloc(self, arr, idx):
    """Fast array access — replaces _safe_iloc after indicator conversion."""
    if idx < 0 or idx >= len(arr):
      return np.nan
    return float(arr[idx])

  def _convert_indicators_to_arrays(self):
    """Convert all pd.Series attributes to numpy arrays for fast per-bar access.

    Called by the engine after init(). Also overrides _safe_iloc via an
    instance attribute so it shadows any class-level definition (including
    the transpiled strategy's own _safe_iloc that uses .iloc[]).
    """
    for attr_name in list(vars(self)):
      if attr_name.startswith('_'):
        continue  # skip engine internals (_df, _bar, _positions, etc.)
      val = getattr(self, attr_name)
      if isinstance(val, pd.Series):
        setattr(self, attr_name, val.values)
    # Instance attribute shadows class method — overrides transpiled _safe_iloc
    self._safe_iloc = self._array_safe_iloc

  # ── Internals ──────────────────────────────────────────────────
  def _default_qty(self) -> float:
    return 1.0
