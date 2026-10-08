"""
Backtest engine — runs a strategy bar-by-bar over historical data.
Handles multi-position tracking, SL/TP/TSL exit orders, warmup, and equity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.metrics import calculate_metrics, compute_avg_consecutive_ranges
from backend.backtester.models import ExitOrder, OrderFill, Position
from backend.constants import COMMISSION_PCT
from backend.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BacktestConfig:
  """Configuration for a backtest run."""
  initial_capital: float = 10_000.0
  commission_pct: float = COMMISSION_PCT
  slippage_pct: float = 0.01  # kept for backward compat
  slippage_mode: str = "pct"  # "pct" | "pips" | "risk_pct"
  slippage_value: float = 0.0  # 0 = use legacy slippage_pct
  default_qty: float = 1.0
  margin_mode: str = "cross"
  leverage: int = 1
  direction_filter: str = "both"
  max_pyramiding: int = 1
  warmup_bars: int = 0
  risk_pct: float = 0.0  # 0 = disabled (use order qty), >0 = auto-size from SL
  risk_mode: str = "pct"  # "pct" = % of equity, "fixed" = fixed $ amount
  risk_value: float = 0.0  # 0 = disabled, >0 = risk amount (% or $)
  # Strategy's internal risk reference (extracted from stripped defaults).
  # Used to scale qty by ratio when engine risk controls override strategy's.
  strategy_risk_mode: str = "fixed"
  strategy_risk_value: float = 0.0  # 0 = unknown, strategy handles own sizing
  # Martingale
  martingale_enabled: bool = False
  martingale_type: str = "anti"  # "martingale" or "anti"
  martingale_multiplier: float = 2.0
  martingale_max_streak: int = 3
  # Volatility filter
  vol_filter_enabled: bool = False
  vol_filter_atr_ceiling: float = 0.0  # max ATR value to allow entry
  vol_filter_atr_length: int = 14
  # Funding rate — perpetual swap cost (deducted at position close)
  funding_rate_daily_pct: float = 0.0  # 0 = disabled, e.g. 0.03 for perps
  # Spread — bid-ask spread cost (half-spread applied on entry and exit)
  spread_pct: float = 0.0  # 0 = disabled, e.g. 0.05 for illiquid alts
  # Order fill timing — True = fill entries at bar's close (same bar as signal)
  process_orders_on_close: bool = False
  # Martingale scope — how streaks are grouped:
  #   "asset"       = per symbol (default, current behaviour)
  #   "portfolio"   = all assets share one streak
  #   "asset_batch" = per symbol per batch (independent per batch)
  #   "batch"       = per batch (all assets in batch share one streak)
  martingale_scope: str = "asset"
  # Bybit instrument limits (0 = disabled / not fetched)
  bybit_lot_size: float = 0.0
  bybit_min_qty: float = 0.0
  bybit_max_qty: float = 0.0
  # paper_refresh incremental cache (2026-05-11). When True, the end-of-
  # backtest force-close loop ALSO closes positions marked was_injected
  # — i.e. resumed via inject_positions. This is OFF by default so the
  # L1 batch refresh keeps its current behavior (where injected positions
  # span the L1 boundary and the synthetic close at boundary would be
  # a fake trade). paper_refresh.run_refresh turns it ON so the user-
  # facing trade list always shows mark-to-market synthetic exits at
  # refresh-time for currently-open positions (replacing the stale
  # synthetic from the previous cached refresh).
  force_close_injected_positions: bool = False

  def __post_init__(self):
    """Clamp parameters to sane ranges to prevent garbage backtests."""
    self.initial_capital = max(1.0, self.initial_capital)
    self.commission_pct = max(0.0, min(self.commission_pct, 5.0))
    self.slippage_pct = max(0.0, self.slippage_pct)
    self.slippage_value = max(0.0, self.slippage_value)
    self.leverage = max(1, min(self.leverage, 125))
    self.max_pyramiding = max(1, min(self.max_pyramiding, 100))
    self.warmup_bars = max(0, self.warmup_bars)
    self.risk_pct = max(0.0, min(self.risk_pct, 100.0))
    self.risk_value = max(0.0, self.risk_value)
    self.martingale_multiplier = max(1.0, min(self.martingale_multiplier, 10.0))
    self.martingale_max_streak = max(1, min(self.martingale_max_streak, 20))
    self.vol_filter_atr_length = max(1, self.vol_filter_atr_length)
    self.funding_rate_daily_pct = max(0.0, min(self.funding_rate_daily_pct, 1.0))
    self.spread_pct = max(0.0, min(self.spread_pct, 1.0))


@dataclass
class BacktestResult:
  """Complete backtest output."""
  trades: list[OrderFill] = field(default_factory=list)
  equity_curve: list[float] = field(default_factory=list)
  metrics: dict = field(default_factory=dict)
  plot_data: dict = field(default_factory=dict)
  config: BacktestConfig = field(default_factory=BacktestConfig)
  symbol: str = ""
  timeframe: str = ""
  bars_processed: int = 0
  strategy_state: dict = field(default_factory=dict)  # captured strategy variables for batch refresh
  # paper_refresh incremental cache (2026-05-11). True iff strategy.next()
  # on the last iterated bar queued an entry that didn't fill (or any
  # unbound exit order is waiting). When True, paper_refresh skips the
  # cache write — those queued orders would be silently dropped by
  # _capture_strategy_state_static (which excludes underscore-prefixed
  # attrs) and produce missed trades on the next refresh's resume.
  had_pending_at_end: bool = False


class BacktestEngine:
  """Bar-by-bar backtest engine with multi-position and SL/TP/TSL support."""

  def __init__(self, config: BacktestConfig | None = None):
    self.config = config or BacktestConfig()

  def _apply_slippage(self, price: float, side: str, is_entry: bool) -> float:
    """Apply slippage to a fill price based on configured mode.

    Entries get worse fills (long=higher, short=lower).
    Exits get worse fills (long=lower, short=higher).
    """
    mode = self.config.slippage_mode
    value = self.config.slippage_value

    # Legacy fallback: if slippage_value is 0, use old slippage_pct
    if value == 0:
      value = self.config.slippage_pct
      mode = "pct"
    if value <= 0:
      return price

    # Determine direction of slippage (worse fill)
    worse_for_buyer = is_entry if side == "long" else not is_entry

    if mode == "pips":
      # Flat price offset (e.g. 0.5 = $0.50 per unit)
      return price + value if worse_for_buyer else price - value
    elif mode == "risk_pct":
      # % of risk distance — applied same as % of price but semantically
      # means "slippage costs X% of your risk budget"
      # In practice: treated as % of price (risk sizing absorbs it)
      slip = price * (value / 100)
      return price + slip if worse_for_buyer else price - slip
    else:
      # Default: % of price
      slip = price * (value / 100)
      return price + slip if worse_for_buyer else price - slip

  def run(self, strategy: BaseStrategy, df: pd.DataFrame,
          symbol: str = "", timeframe: str = "",
          inputs: dict | None = None,
          extra_timeframes: dict[str, pd.DataFrame] | None = None,
          inject_positions: list[dict] | None = None,
          inject_strategy_state: dict | None = None,
          resume_from_bar: int = 0,
          ) -> BacktestResult:
    """Execute backtest over the dataframe."""
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
      raise ValueError(f"DataFrame missing columns: {missing}")

    # Initialize strategy — keep DatetimeIndex for timestamp access + multi-TF
    strategy._df = df
    strategy._initial_capital = self.config.initial_capital
    strategy._cash = self.config.initial_capital
    strategy._positions = {}
    strategy._positions_by_side = {"long": 0, "short": 0}  # CS-050 counter
    strategy._exit_orders = {}
    strategy._trades = []
    strategy._pending_orders = []
    strategy._equity_curve = []
    strategy._inputs = inputs or {}
    strategy._plot_data = {}
    strategy._max_pyramiding = self.config.max_pyramiding
    strategy._consecutive_wins = 0
    strategy._consecutive_losses = 0
    strategy._closed_trades_count = 0
    strategy._tf_data = {tf: df.copy() for tf, df in (extra_timeframes or {}).items()}
    strategy._consumed_exits = {}
    self._rejected_below_min = 0  # trades skipped: qty < bybit min_qty

    # Pre-extract OHLCV + time components as numpy arrays for fast per-bar access.
    # Properties like strategy.close use these instead of df["close"].iloc[bar].
    strategy._opens = df["open"].values
    strategy._highs = df["high"].values
    strategy._lows = df["low"].values
    strategy._closes = df["close"].values
    strategy._volumes = df["volume"].values
    strategy._timestamps = df.index
    strategy._hours = df.index.hour
    strategy._minutes = df.index.minute
    strategy._seconds = df.index.second
    _pine_map = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 1}
    strategy._dayofweek_pine = np.array([_pine_map[d] for d in df.index.dayofweek])
    strategy._days = df.index.day
    strategy._months = df.index.month
    strategy._years = df.index.year

    try:
      strategy.init()
    except Exception as e:
      # DST ambiguity/gap during indicator tz-localize — match by
      # exception type instead of substring. pytz raises these as
      # canonical classes; relying on str(e) breaks on message changes.
      from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError
      if isinstance(e, (AmbiguousTimeError, NonExistentTimeError)):
        from backend.backtester.portfolio_engine import _sanitize_dst_timestamps
        _sanitize_dst_timestamps(strategy._tf_data)
        strategy.init()
      else:
        raise

    # Convert indicator pd.Series to numpy arrays for fast per-bar access.
    # Also overrides _safe_iloc to use array indexing instead of .iloc[].
    strategy._convert_indicators_to_arrays()

    # Resolve warmup: take the LARGER of strategy-level vs config-level.
    # Refresh jobs (batch_store_refresh.replay_run_forward) pass an inflated
    # config.warmup_bars so the engine skips bars before the boundary instead
    # of re-emitting historical trades. If we used `or` here, a truthy small
    # strategy._warmup_bars (e.g. 50) would shadow the larger config value
    # (e.g. 800) and the refresh would duplicate ~750 bars of past trades.
    warmup = max(strategy._warmup_bars or 0, self.config.warmup_bars or 0)

    # Pre-compute ATR array for volatility filter (avoids per-entry recalc)
    self._atr_cache = None
    if self.config.vol_filter_enabled and self.config.vol_filter_atr_ceiling > 0:
      atr_len = self.config.vol_filter_atr_length
      highs = df["high"].values
      lows = df["low"].values
      closes = df["close"].values
      n = len(df)
      atr_arr = np.full(n, np.nan)
      if n > 1:
        # True range: max(H-L, |H-prevC|, |L-prevC|)
        tr = np.maximum(
          highs[1:] - lows[1:],
          np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
          ),
        )
        # First bar TR = H-L (no previous close)
        tr_full = np.empty(n)
        tr_full[0] = highs[0] - lows[0]
        tr_full[1:] = tr
        # CS-065: Wilder RMA (Pine's ``ta.atr`` = ``ta.rma(ta.tr, len)``).
        # The trailing simple mean we used before diverges from the
        # TradingView chart after regime shifts — exactly when the vol
        # filter gate matters. Seed with SMA of the first ``atr_len``
        # TRs at index ``atr_len - 1``, then recurse: ``rma[i] =
        # (rma[i-1] * (len-1) + tr[i]) / len``.
        if n >= atr_len:
          seed_idx = atr_len - 1
          atr_arr[seed_idx] = float(np.mean(tr_full[:atr_len]))
          inv_len = 1.0 / atr_len
          for i in range(atr_len, n):
            atr_arr[i] = (atr_arr[i - 1] * (atr_len - 1) + tr_full[i]) * inv_len
      self._atr_cache = atr_arr

    # Bar-by-bar loop. `resume_from_bar` (paper_refresh incremental cache,
    # 2026-05-11) lets callers skip iteration of bars already processed
    # in a prior run — when set, state + positions are injected at that
    # boundary instead of at `warmup`, and the loop starts there.
    loop_start = max(int(resume_from_bar or 0), 0)
    if loop_start >= len(df):
      loop_start = max(0, len(df) - 1)
    inject_at = warmup if loop_start == 0 else loop_start

    for bar_idx in range(loop_start, len(df)):
      strategy._bar = bar_idx
      is_warmup = bar_idx < warmup

      # 0. At first non-warmup bar (or at resume boundary): inject saved
      #    state + positions from prior batch / prior refresh.
      if bar_idx == inject_at:
        if inject_strategy_state:
          self._restore_strategy_state(strategy, inject_strategy_state)
        if inject_positions:
          self._inject_positions(strategy, inject_positions, bar_idx)

      # 1. Process pending entry/close orders from previous bar (open-fill mode only)
      if not is_warmup and not self.config.process_orders_on_close:
        self._process_pending_orders(strategy, bar_idx)

      # 2. Check persistent exit orders (SL/TP/TSL) against current bar
      if not is_warmup and bar_idx > 0:
        self._process_exit_orders(strategy, bar_idx)

      # 3. Update position extremes for MAE/MFE tracking
      if not is_warmup:
        self._update_position_extremes(strategy, bar_idx)

      # 4. Run strategy logic
      strategy.next()

      # 4a. Process_orders_on_close: fill entries at this bar's close
      if not is_warmup and self.config.process_orders_on_close:
        self._process_close_orders(strategy, bar_idx)
        # 4a.5 Register exit orders for just-filled positions so SL is
        # active when bar N+1 opens. Also update extremes (Step 3 ran
        # before position existed).
        self._register_exits_for_close_fills(strategy, bar_idx)
        self._update_position_extremes(strategy, bar_idx)

      # 4b. Post-next risk resize: now that strategy registered exit orders
      # (including SL for entries filled this bar), re-size those positions
      # using engine risk controls instead of strategy's internal sizing.
      if not is_warmup and (self.config.risk_value > 0 or self.config.risk_pct > 0):
        self._post_next_risk_resize(strategy, bar_idx)

      # 4c. Suppress orders generated during warmup
      if is_warmup:
        strategy._pending_orders.clear()
        strategy._exit_orders.clear()

      # 5. Track equity
      strategy._equity_curve.append(self._calculate_equity(strategy))

    # Detect pending orders queued during the last iterated bar — these
    # fill at "next bar's open" in a continuous run but would be silently
    # dropped if the caller persists state across a boundary cut here.
    # paper_refresh checks `BacktestResult.had_pending_at_end` and skips
    # writing the cache when True (full rerun on next refresh).
    #
    # We also check for "unbound" exit orders — exit orders whose
    # `from_entry` doesn't reference a currently open position. These
    # are typically registered alongside a not-yet-filled entry; if
    # dropped, the entry would fill on resume but no SL/TP would trigger.
    has_pending_entries = bool(getattr(strategy, "_pending_orders", None))
    has_unbound_exits = False
    _open_pids = set(strategy._positions.keys())
    for _eo in getattr(strategy, "_exit_orders", {}).values():
      _from = getattr(_eo, "from_entry", None)
      if _from is not None and _from not in _open_pids:
        has_unbound_exits = True
        break
    _had_pending_at_end = has_pending_entries or has_unbound_exits

    # Capture strategy state BEFORE closing end-of-backtest positions.
    # The scanner uses this to seed strategy state for live signal detection.
    strategy_state = BacktestEngine._capture_strategy_state_static(strategy)

    # Serialize open positions so replay_run_forward / scanner can re-inject
    # them. Capture runs BEFORE the end-of-backtest close loop, so
    # strategy._positions still holds legs that were open at the last bar.
    # _capture_strategy_state_static skips underscore-prefixed attrs — without
    # this, open positions would be silently dropped on resume.
    open_positions_state = []
    for pid, pos in strategy._positions.items():
      # Capture EVERY exit order attached to this position, preserving the
      # original exit order IDs. A position can have multiple exits live at
      # once (e.g. TP1 + TP2 + TSL for scaled-out strategies). Capturing
      # only the first of each type (old behavior) silently dropped the
      # extras on resume, so partial-exit ladders diverged.
      exits = []
      for eid, eo in strategy._exit_orders.items():
        if eo.from_entry != pid:
          continue
        exits.append({
          "id": eid,
          "stop": float(eo.stop),
          "limit": float(eo.limit),
          "trail_points": float(eo.trail_points),
          "trail_offset": float(eo.trail_offset),
          "trail_extreme": float(eo.trail_extreme),
          "qty": float(eo.qty),
          "qty_percent": float(eo.qty_percent),
        })

      # Legacy fields (kept so older in-flight states / callers don't break).
      # Pick the first-match of each type — same semantics as the old code.
      sl_price = next((e["stop"] for e in exits if e["stop"] > 0), 0.0)
      tp_price = next((e["limit"] for e in exits if e["limit"] > 0), 0.0)
      first_trail = next((e for e in exits if e["trail_points"] > 0), None)
      trail_points = first_trail["trail_points"] if first_trail else 0.0
      trail_offset = first_trail["trail_offset"] if first_trail else 0.0
      trail_extreme = first_trail["trail_extreme"] if first_trail else 0.0

      entry_ts = strategy._timestamps[pos.entry_bar]
      entry_iso = entry_ts.isoformat() if hasattr(entry_ts, "isoformat") else str(entry_ts)
      open_positions_state.append({
        "position_id": pid,
        "side": pos.side,
        "entry_price": float(pos.entry_price),
        "qty": float(pos.qty),
        "extreme_high": float(pos.extreme_high),
        "extreme_low": float(pos.extreme_low),
        "sl_price": sl_price,
        "tp_price": tp_price,
        "trail_points": trail_points,
        "trail_offset": trail_offset,
        "trail_extreme": trail_extreme,
        "entry_time": entry_iso,
        "exits": exits,
      })
    strategy_state["_open_positions"] = open_positions_state

    # Close all open positions at last bar — but skip positions that were
    # resumed via _inject_positions (L1 catch-up). For injected positions the
    # entry_bar field points at the FIRST bar of the new L1 slice, not the
    # original entry, so the resulting trade row would carry a fake entry_time
    # paired with the original entry_price, producing fictional huge PnL on the
    # Analysis page. The position is still physically open across the L1
    # boundary; emitting a synthetic close for it is wrong. Cold backtests
    # (where was_injected=False) keep the original behavior and still emit a
    # backtest_end row for any position genuinely open at end-of-data.
    #
    # `force_close_injected_positions` (added 2026-05-11 for paper_refresh
    # incremental cache) overrides the skip so the engine emits a fresh
    # mark-to-market synthetic close at the current last bar even for
    # injected positions. paper_refresh strips stale backtest_end fills
    # from its cache on each refresh and relies on the engine to re-emit
    # current ones; without this flag those positions silently disappear
    # from the user-facing trade list across refresh boundaries.
    _force_close_injected = bool(getattr(
      self.config, "force_close_injected_positions", False,
    ))
    for pid in list(strategy._positions.keys()):
      pos = strategy._positions[pid]
      if getattr(pos, "was_injected", False) and not _force_close_injected:
        continue
      self._close_position_by_id(strategy, len(df) - 1, pid, "backtest_end")

    metrics = calculate_metrics(
      strategy._trades, strategy._equity_curve, self.config.initial_capital,
      total_bars=len(df),
      first_price=float(df["open"].iloc[0]),
      last_price=float(df["close"].iloc[-1]),
      commission_pct=self.config.commission_pct,
      timeframe=timeframe or "1d",
    )

    # Bybit instrument limit info in metrics
    if self.config.bybit_min_qty > 0 or self.config.bybit_max_qty > 0:
      metrics["bybit_min_qty"] = self.config.bybit_min_qty
      metrics["bybit_max_qty"] = self.config.bybit_max_qty
      metrics["bybit_lot_size"] = self.config.bybit_lot_size
      metrics["rejected_below_min_qty"] = self._rejected_below_min

    # Avg consecutive same-sided ranges (market structure strategies only)
    if hasattr(strategy, 'range_history') and strategy.range_history:
      metrics.update(compute_avg_consecutive_ranges(strategy.range_history))

    return BacktestResult(
      trades=strategy._trades,
      equity_curve=strategy._equity_curve,
      metrics=metrics,
      plot_data=strategy._plot_data,
      config=self.config,
      symbol=symbol,
      timeframe=timeframe,
      bars_processed=len(df),
      strategy_state=strategy_state,
      had_pending_at_end=_had_pending_at_end,
    )

  # ── Pending orders (entries + market exits) ──────────────────────
  def _process_pending_orders(self, strategy: BaseStrategy, bar: int):
    """Execute pending orders against current bar's open price."""
    orders = strategy._pending_orders.copy()
    strategy._pending_orders.clear()

    for order in orders:
      action = order["action"]
      if action == "entry":
        self._execute_entry(strategy, bar, order)
      elif action == "exit":
        self._execute_market_exit(strategy, bar, order)
      elif action == "close":
        entry_id = order.get("id", "")
        if entry_id and entry_id in strategy._positions:
          self._close_position_by_id(strategy, bar, entry_id, order.get("comment", ""))
        elif strategy._positions:
          # Close first position (backward compat)
          pid = next(iter(strategy._positions))
          self._close_position_by_id(strategy, bar, pid, order.get("comment", ""))
      elif action == "close_all":
        for pid in list(strategy._positions.keys()):
          self._close_position_by_id(strategy, bar, pid, order.get("comment", ""))

  def _process_close_orders(self, strategy: BaseStrategy, bar: int):
    """Execute pending orders at current bar's CLOSE price.

    Used when process_orders_on_close=True (matches TradingView behavior).
    Entries fill at closes[bar] instead of opens[bar+1].
    """
    orders = strategy._pending_orders.copy()
    strategy._pending_orders.clear()

    for order in orders:
      action = order["action"]
      if action == "entry":
        self._execute_entry_at_close(strategy, bar, order)
      elif action == "exit":
        self._execute_market_exit(strategy, bar, order)
      elif action == "close":
        entry_id = order.get("id", "")
        if entry_id and entry_id in strategy._positions:
          self._close_position_by_id(strategy, bar, entry_id, order.get("comment", ""))
        elif strategy._positions:
          pid = next(iter(strategy._positions))
          self._close_position_by_id(strategy, bar, pid, order.get("comment", ""))
      elif action == "close_all":
        for pid in list(strategy._positions.keys()):
          self._close_position_by_id(strategy, bar, pid, order.get("comment", ""))

  def _register_exits_for_close_fills(self, strategy: BaseStrategy, bar: int):
    """Register SL/TP exit orders for positions just filled at this bar's close.

    When process_orders_on_close=True, entries fill at bar N's close. But the
    strategy's next() placed exit orders for EXISTING trades before creating new
    ones, so new trades have no exit orders until bar N+1's next(). This method
    bridges that gap by duck-typing the strategy's tracking arrays and calling
    strategy.exit() immediately after the close-fill, ensuring SL is active when
    bar N+1 opens.

    Safe no-op for strategies without tracking arrays.
    """
    t_id = getattr(strategy, "t_id", None)
    if t_id is None or len(t_id) == 0:
      return

    t_sl = getattr(strategy, "t_sl", None)
    t_tp1 = getattr(strategy, "t_tp1", None)
    t_tsl = getattr(strategy, "t_tsl", None)
    t_dir = getattr(strategy, "t_dir", None)
    t_tp1_done = getattr(strategy, "t_tp1_done", None)
    t_tp2 = getattr(strategy, "t_tp2", None)
    p_tp1_pct = getattr(strategy, "p_tp1_pct", 0)
    p_tp2_mode = getattr(strategy, "p_tp2_mode", "TSL")

    if t_sl is None or t_dir is None:
      return  # Not enough info to register exits

    for k in range(len(t_id)):
      tid = t_id[k]
      pos = strategy._positions.get(tid)
      if not pos or pos.entry_bar != bar:
        continue  # Not a just-filled position

      # Skip if exit orders already registered for this position
      has_exit = any(
        eo.from_entry == tid for eo in strategy._exit_orders.values()
      )
      if has_exit:
        continue

      d = t_dir[k] if t_dir else 1
      sl_val = t_sl[k] if t_sl and k < len(t_sl) else 0.0
      tsl_val = t_tsl[k] if t_tsl and k < len(t_tsl) else sl_val
      tp1_val = t_tp1[k] if t_tp1 and k < len(t_tp1) else 0.0
      tp1_done_val = t_tp1_done[k] if t_tp1_done and k < len(t_tp1_done) else False

      # Effective stop = best of SL and TSL
      if d == 1:
        eff_stop = max(sl_val, tsl_val)
      else:
        eff_stop = min(sl_val, tsl_val) if (sl_val > 0 and tsl_val > 0) else max(sl_val, tsl_val)

      if eff_stop <= 0 and tp1_val <= 0:
        continue  # No exit levels to register

      # TP1: partial close with limit + stop (skip if already filled)
      if p_tp1_pct > 0 and not tp1_done_val and tp1_val > 0:
        strategy.exit(
          id="TP1_" + tid, from_entry=tid,
          qty_percent=p_tp1_pct, limit=tp1_val, stop=eff_stop)

      # TP2 / TSL for remaining position
      if p_tp2_mode == "TSL":
        if eff_stop > 0:
          strategy.exit(id="TSL_" + tid, from_entry=tid, stop=eff_stop)
      else:
        tp2_val = t_tp2[k] if t_tp2 and k < len(t_tp2) else np.nan
        if not np.isnan(tp2_val) and tp2_val > 0:
          strategy.exit(
            id="TP2_" + tid, from_entry=tid,
            limit=tp2_val, stop=eff_stop)
        elif eff_stop > 0:
          # Fallback to TSL if no TP2 target
          strategy.exit(id="TSL_" + tid, from_entry=tid, stop=eff_stop)

  def _execute_entry(self, strategy: BaseStrategy, bar: int, order: dict):
    """Fill an entry order at bar's open."""
    self._fill_entry(strategy, bar, order, strategy._opens[bar])

  def _execute_entry_at_close(self, strategy: BaseStrategy, bar: int, order: dict):
    """Fill an entry order at bar's close (process_orders_on_close mode)."""
    self._fill_entry(strategy, bar, order, strategy._closes[bar], at_close=True)

  def _fill_entry(self, strategy: BaseStrategy, bar: int, order: dict,
                  fill_price: float, at_close: bool = False):
    """Shared entry fill logic. fill_price is opens[bar] or closes[bar]."""
    direction = order.get("direction", "long")
    # CS-013: two same-bar entries with empty ``id`` previously both hashed
    # to ``_entry_{bar}`` and silently overwrote each other in _positions,
    # corrupting SL/TP attachment. Seed a per-position counter so every
    # auto-generated id is unique even within one bar.
    raw_id = order.get("id", "")
    if raw_id:
      entry_id = raw_id
      if entry_id in strategy._positions:
        # Surface the collision instead of silently overwriting a live position.
        logger.warning(
          "Duplicate entry id '%s' at bar %d — strategy emitted the same id twice",
          entry_id, bar,
        )
    else:
      counter = getattr(strategy, "_auto_entry_counter", 0)
      entry_id = f"_entry_{bar}_{counter}"
      strategy._auto_entry_counter = counter + 1

    # Direction filter
    if self.config.direction_filter == "long" and direction != "long":
      return
    if self.config.direction_filter == "short" and direction != "short":
      return

    # Check pyramiding limit
    # CS-050: O(1) counter lookup instead of scanning _positions every
    # entry. The counter is maintained at every add/remove of a position
    # (see ``_fill_entry`` below + ``_close_position_by_id`` + state
    # restore in ``_inject_positions``). ``.get(..., 0)`` is defensive
    # against legacy strategies / replay paths that built _positions
    # without going through the counter-aware init.
    same_dir = strategy._positions_by_side.get(direction, 0)
    if same_dir >= self.config.max_pyramiding:
      return

    # If max_pyramiding=1 and opposite position exists, close it
    if self.config.max_pyramiding == 1:
      for pid in list(strategy._positions.keys()):
        if strategy._positions[pid].side != direction:
          self._close_position_by_id(strategy, bar, pid, "reverse")

    # Volatility filter — skip entry if ATR exceeds ceiling (uses pre-computed cache)
    if self._atr_cache is not None and bar >= self.config.vol_filter_atr_length:
      atr = self._atr_cache[bar]
      if not np.isnan(atr) and atr > self.config.vol_filter_atr_ceiling:
        return  # ATR too high — block entry

    price = fill_price
    qty = order.get("qty", self.config.default_qty)

    # Slippage (worse fill for entries)
    price = self._apply_slippage(price, direction, is_entry=True)

    # Spread cost — half-spread on entry (always worse fill)
    if self.config.spread_pct > 0:
      half = self.config.spread_pct / 200
      price *= (1 + half) if direction == "long" else (1 - half)

    # Risk-based position sizing.
    # When ratio-based scaling is available (strategy_risk_value > 0),
    # skip SL-based sizing here — _post_next_risk_resize will apply
    # the ratio after strategy.next() registers the real SL.
    # This avoids double-sizing when _find_sl_for_entry finds stale SL.
    order_qty = qty  # preserve strategy's calculated qty as fallback
    has_ratio_ref = self.config.strategy_risk_value > 0
    if self.config.risk_value > 0 and not has_ratio_ref:
      risk_qty = self._calculate_risk_qty_v2(strategy, price, direction, entry_id)
      if risk_qty > 0:
        qty = risk_qty
      elif order_qty > 0:
        qty = order_qty
      else:
        return
    elif self.config.risk_pct > 0 and not has_ratio_ref:
      risk_qty = self._calculate_risk_qty(strategy, price, direction, entry_id)
      if risk_qty > 0:
        qty = risk_qty
      elif order_qty > 0:
        qty = order_qty
      else:
        return

    # Martingale — multiply qty by streak-based factor.
    # Skip when has_ratio_ref: _post_next_risk_resize will compute the
    # final qty from scratch (including martingale), so applying it here
    # would double-count — the strategy may have already applied its own
    # internal martingale too.
    if self.config.martingale_enabled and not has_ratio_ref:
      streak = (strategy._consecutive_losses
                if self.config.martingale_type in ("martingale", "inv_martingale")
                else strategy._consecutive_wins)
      capped = min(streak, self.config.martingale_max_streak)
      if capped > 0:
        if self.config.martingale_type in ("inv_martingale", "inv_anti"):
          qty *= (1.0 / self.config.martingale_multiplier) ** capped
        else:
          qty *= self.config.martingale_multiplier ** capped

    # Margin cap — after all qty adjustments, ensure position fits
    qty = self._cap_qty_to_margin(strategy, qty, price)
    if qty <= 0:
      return  # Not enough margin for this position

    # Bybit instrument limits — round to lot_size, enforce min/max qty
    if self.config.bybit_lot_size > 0:
      step = self.config.bybit_lot_size
      qty = math.floor(qty / step) * step
      # Fix floating point artifacts
      if step >= 1:
        qty = float(int(qty))
      else:
        decimals = len(str(step).rstrip("0").split(".")[-1])
        qty = round(qty, decimals)
    if self.config.bybit_min_qty > 0 and qty < self.config.bybit_min_qty:
      self._rejected_below_min += 1
      return  # Below Bybit minimum order quantity
    if self.config.bybit_max_qty > 0 and qty > self.config.bybit_max_qty:
      qty = self.config.bybit_max_qty

    # Commission
    commission = price * qty * (self.config.commission_pct / 100)
    strategy._cash -= commission

    # CS-050: maintain side counter on every add. If entry_id ALREADY
    # existed (duplicate-id warning path above overwrites the Position),
    # decrement the prior side first so the counter reflects the dict's
    # post-assignment state — not an incremented one.
    if not hasattr(strategy, "_positions_by_side") or strategy._positions_by_side is None:
      strategy._positions_by_side = {"long": 0, "short": 0}
    prev_pos = strategy._positions.get(entry_id)
    if prev_pos is not None and prev_pos.side:
      strategy._positions_by_side[prev_pos.side] = (
        strategy._positions_by_side.get(prev_pos.side, 0) - 1
      )
    # Open position
    strategy._positions[entry_id] = Position(
      id=entry_id, side=direction, entry_price=price,
      qty=qty, entry_bar=bar, label=entry_id,
      extreme_high=price, extreme_low=price,
    )
    strategy._positions_by_side[direction] = strategy._positions_by_side.get(direction, 0) + 1

    # Timestamp: for close-fill, use next bar's open time (= current bar's close)
    if isinstance(strategy._timestamps, pd.DatetimeIndex):
      if at_close and bar + 1 < len(strategy._timestamps):
        ts = strategy._timestamps[bar + 1]
      else:
        ts = strategy._timestamps[bar]
    else:
      ts = pd.Timestamp.now()

    strategy._trades.append(OrderFill(
      bar_index=bar,
      timestamp=ts,
      side=direction, action="entry", price=price,
      qty=qty, label=entry_id, position_id=entry_id,
      commission=commission,
    ))

  def _find_sl_for_entry(self, strategy: BaseStrategy, entry_id: str) -> float | None:
    """Find stop loss price from exit orders matching this entry."""
    for eo in strategy._exit_orders.values():
      if eo.from_entry == entry_id and eo.stop > 0:
        return eo.stop
    return None

  def _used_margin(self, strategy: BaseStrategy) -> float:
    """Margin consumed by existing positions = sum(notional / leverage)."""
    lev = max(self.config.leverage, 1)
    return sum(
      abs(p.qty * p.entry_price) / lev
      for p in strategy._positions.values()
    )

  def _cap_qty_to_margin(
    self, strategy: BaseStrategy, ideal_qty: float, entry_price: float,
  ) -> float:
    """Cap qty so the new position fits within available margin * leverage.

    This enforces realistic position limits — you can't open a $95k BTC
    position with $10k equity at 1x leverage.  When risk sizing is active
    the leverage field acts as *max* leverage: the engine uses only what's
    needed, capped at this ceiling.
    """
    equity = self._calculate_equity(strategy)
    lev = max(self.config.leverage, 1)
    available_margin = max(0.0, equity - self._used_margin(strategy))
    max_notional = available_margin * lev
    if entry_price <= 0:
      return ideal_qty
    max_qty = max_notional / entry_price
    return min(ideal_qty, max_qty)

  def _calculate_risk_qty(
    self, strategy: BaseStrategy, entry_price: float,
    direction: str, entry_id: str,
  ) -> float:
    """Calculate qty so that SL hit = risk_pct% of current equity."""
    equity = self._calculate_equity(strategy)
    risk_amount = equity * (self.config.risk_pct / 100)
    sl_price = self._find_sl_for_entry(strategy, entry_id)
    if sl_price is None or sl_price <= 0:
      return 0.0  # No SL — block trade
    sl_distance = abs(entry_price - sl_price)
    if sl_distance <= 0:
      return 0.0
    ideal_qty = risk_amount / sl_distance
    return self._cap_qty_to_margin(strategy, ideal_qty, entry_price)

  def _calculate_risk_qty_v2(
    self, strategy: BaseStrategy, entry_price: float,
    direction: str, entry_id: str,
  ) -> float:
    """Calculate qty using risk_mode (pct or fixed $) and risk_value."""
    equity = self._calculate_equity(strategy)
    if self.config.risk_mode == "fixed":
      risk_amount = self.config.risk_value
    else:
      risk_amount = equity * (self.config.risk_value / 100)
    sl_price = self._find_sl_for_entry(strategy, entry_id)
    if sl_price is None or sl_price <= 0:
      return 0.0
    sl_distance = abs(entry_price - sl_price)
    if sl_distance <= 0:
      return 0.0
    ideal_qty = risk_amount / sl_distance
    return self._cap_qty_to_margin(strategy, ideal_qty, entry_price)

  def _post_next_risk_resize(self, strategy: BaseStrategy, bar: int):
    """Re-size positions filled this bar using engine risk controls.

    Computes the ABSOLUTE correct qty from engine config and SL distance,
    completely overriding whatever the strategy calculated internally.
    This makes the UI the single source of truth for position sizing
    when engine risk controls are active (risk_value > 0 or risk_pct > 0).
    """
    for entry_id, pos in list(strategy._positions.items()):
      if pos.entry_bar != bar:
        continue  # not filled this bar

      old_qty = pos.qty
      price = pos.entry_price

      # Find SL from exit orders registered by strategy during next()
      sl = self._find_sl_for_entry(strategy, entry_id)
      if sl is None or sl <= 0:
        continue  # no SL found — can't compute risk-based qty
      sl_distance = abs(price - sl)
      if sl_distance <= 0:
        continue

      # Compute engine's target risk amount from UI settings
      equity = self._calculate_equity(strategy)
      if self.config.risk_value > 0:
        risk_amt = (self.config.risk_value if self.config.risk_mode == "fixed"
                    else equity * (self.config.risk_value / 100))
      elif self.config.risk_pct > 0:
        risk_amt = equity * (self.config.risk_pct / 100)
      else:
        continue

      new_qty = risk_amt / sl_distance

      # Martingale multiplier (engine-level only — strategy's internal
      # martingale is irrelevant since we computed qty from scratch)
      if self.config.martingale_enabled:
        streak = (strategy._consecutive_losses
                  if self.config.martingale_type in ("martingale", "inv_martingale")
                  else strategy._consecutive_wins)
        capped = min(streak, self.config.martingale_max_streak)
        if capped > 0:
          if self.config.martingale_type in ("inv_martingale", "inv_anti"):
            new_qty *= (1.0 / self.config.martingale_multiplier) ** capped
          else:
            new_qty *= self.config.martingale_multiplier ** capped

      if new_qty <= 0:
        continue

      # Early exit: if new qty is negligibly different from old qty,
      # skip resize to avoid floating-point churn
      if abs(new_qty - old_qty) / max(old_qty, 1e-10) < 0.001:
        continue

      # Cap to available margin (release current position's margin first)
      lev = max(self.config.leverage, 1)
      current_notional = abs(old_qty * price) / lev
      available = max(0.0, self._calculate_equity(strategy)
                      - self._used_margin(strategy) + current_notional)
      max_notional = available * lev
      max_qty_margin = max_notional / price if price > 0 else new_qty
      new_qty = min(new_qty, max_qty_margin)

      if new_qty <= 0:
        continue

      # Bybit instrument limits on resized qty
      if self.config.bybit_lot_size > 0:
        step = self.config.bybit_lot_size
        new_qty = math.floor(new_qty / step) * step
        if step >= 1:
          new_qty = float(int(new_qty))
        else:
          decimals = len(str(step).rstrip("0").split(".")[-1])
          new_qty = round(new_qty, decimals)
      if self.config.bybit_max_qty > 0 and new_qty > self.config.bybit_max_qty:
        new_qty = self.config.bybit_max_qty
      if self.config.bybit_min_qty > 0 and new_qty < self.config.bybit_min_qty:
        # Close the position — too small after resize
        self._rejected_below_min += 1
        self._close_position_by_id(strategy, bar, entry_id, "below_min_qty")
        continue

      # Update position qty
      pos.qty = new_qty

      # Adjust commission for qty change
      comm_rate = self.config.commission_pct / 100
      old_comm = price * old_qty * comm_rate
      new_comm = price * new_qty * comm_rate
      strategy._cash += old_comm - new_comm  # refund old, charge new

      # Update the entry OrderFill to match (qty + commission)
      for trade in strategy._trades:
        if (trade.position_id == entry_id and trade.action == "entry"
            and trade.bar_index == bar):
          trade.qty = new_qty
          trade.commission = new_comm
          break

  def _execute_market_exit(self, strategy: BaseStrategy, bar: int, order: dict):
    """Fill a market exit order."""
    from_entry = order.get("from_entry", "")
    close_qty = self._resolve_exit_qty(
      strategy, from_entry, order.get("qty", 0), order.get("qty_percent", 0),
    )
    if from_entry and from_entry in strategy._positions:
      self._close_position_by_id(
        strategy, bar, from_entry, order.get("id", ""), close_qty=close_qty,
      )
    elif not from_entry and strategy._positions:
      # No from_entry specified — close first position (backward compat)
      pid = next(iter(strategy._positions))
      self._close_position_by_id(
        strategy, bar, pid, order.get("id", ""), close_qty=close_qty,
      )

  # ── Persistent exit orders (SL/TP/TSL) ──────────────────────────
  def _process_exit_orders(self, strategy: BaseStrategy, bar: int):
    """Check all registered exit orders against current bar's OHLC."""
    bar_high = strategy._highs[bar]
    bar_low = strategy._lows[bar]
    bar_open = strategy._opens[bar]
    orders_to_remove: list[str] = []

    for exit_id, eo in list(strategy._exit_orders.items()):
      pos = strategy._positions.get(eo.from_entry)
      if not pos:
        orders_to_remove.append(exit_id)
        continue

      fill_price = self._check_exit_trigger(eo, pos, bar_high, bar_low, bar_open)
      if fill_price is not None:
        close_qty = self._resolve_exit_qty(
          strategy, eo.from_entry, eo.qty, eo.qty_percent,
        )
        self._close_position_by_id(
          strategy, bar, pos.id, exit_id,
          exit_price=fill_price, close_qty=close_qty,
        )
        orders_to_remove.append(exit_id)
        # Mark as consumed so strategy can't re-register it (PineScript semantics)
        strategy._consumed_exits[exit_id] = eo.from_entry
      else:
        # Additive: detect when the stop first moves PAST breakeven
        if not getattr(pos, 'sl_past_be_time', None):
          past_be = False
          if eo.trail_points > 0 and eo.trail_extreme > 0:
            # Engine trailing stop: compute trail stop from extreme
            if pos.side == "long":
              trail_stop = eo.trail_extreme - eo.trail_points
              past_be = trail_stop > pos.entry_price
            else:
              trail_stop = eo.trail_extreme + eo.trail_points
              past_be = 0 < trail_stop < pos.entry_price
          elif eo.stop > 0:
            # Strategy-managed stop (re-registered each bar)
            if pos.side == "long":
              past_be = eo.stop > pos.entry_price
            else:
              past_be = eo.stop < pos.entry_price
          if past_be and isinstance(strategy._timestamps, pd.DatetimeIndex):
            pos.sl_past_be_time = strategy._timestamps[bar]

    for oid in orders_to_remove:
      strategy._exit_orders.pop(oid, None)

  def _update_position_extremes(self, strategy: BaseStrategy, bar: int):
    """Update extreme_high/extreme_low for all open positions (MAE/MFE)."""
    bar_high = strategy._highs[bar]
    bar_low = strategy._lows[bar]
    for pos in strategy._positions.values():
      if bar_high > pos.extreme_high:
        pos.extreme_high = bar_high
      if bar_low < pos.extreme_low:
        pos.extreme_low = bar_low

  def _check_exit_trigger(
    self, eo: ExitOrder, pos: Position,
    bar_high: float, bar_low: float, bar_open: float,
  ) -> float | None:
    """Returns fill price if exit triggered, None otherwise."""
    # Check gap through (open already past stop/limit)
    if pos.side == "long":
      if eo.stop and bar_open <= eo.stop:
        return bar_open
      if eo.limit and bar_open >= eo.limit:
        return bar_open
    else:
      if eo.stop and bar_open >= eo.stop:
        return bar_open
      if eo.limit and bar_open <= eo.limit:
        return bar_open

    # Check stop loss first (conservative — worst case)
    if eo.stop:
      if pos.side == "long" and bar_low <= eo.stop:
        return eo.stop
      if pos.side == "short" and bar_high >= eo.stop:
        return eo.stop

    # Check take profit
    if eo.limit:
      if pos.side == "long" and bar_high >= eo.limit:
        return eo.limit
      if pos.side == "short" and bar_low <= eo.limit:
        return eo.limit

    # Check trailing stop
    if eo.trail_points:
      return self._check_trailing_stop(eo, pos, bar_high, bar_low)

    return None

  def _check_trailing_stop(
    self, eo: ExitOrder, pos: Position,
    bar_high: float, bar_low: float,
  ) -> float | None:
    """Check trailing stop trigger. Returns fill price or None."""
    if pos.side == "long":
      # Update extreme (highest price since entry)
      if bar_high > eo.trail_extreme:
        eo.trail_extreme = bar_high
      # Check activation offset (min profit before trailing starts)
      if eo.trail_offset and eo.trail_extreme < pos.entry_price + eo.trail_offset:
        return None
      # Trail stop level
      if eo.trail_extreme > 0:
        trail_stop = eo.trail_extreme - eo.trail_points
        if bar_low <= trail_stop:
          return trail_stop
    else:
      # Short: track lowest price
      if eo.trail_extreme == 0 or bar_low < eo.trail_extreme:
        eo.trail_extreme = bar_low
      if eo.trail_offset and eo.trail_extreme > pos.entry_price - eo.trail_offset:
        return None
      if eo.trail_extreme > 0:
        trail_stop = eo.trail_extreme + eo.trail_points
        if bar_high >= trail_stop:
          return trail_stop
    return None

  # ── Qty resolution ───────────────────────────────────────────────
  def _resolve_exit_qty(
    self, strategy: BaseStrategy, from_entry: str,
    qty: float, qty_percent: float,
  ) -> float:
    """Resolve exit qty from qty/qty_percent. Returns 0 = close full position."""
    if qty_percent > 0:
      pos = strategy._positions.get(from_entry)
      if pos:
        return pos.qty * (qty_percent / 100.0)
    return qty  # 0 = full close

  # ── Position closing ─────────────────────────────────────────────
  def _close_position_by_id(
    self, strategy: BaseStrategy, bar: int,
    position_id: str, label: str = "",
    exit_price: float | None = None,
    close_qty: float = 0,
  ):
    """Close a position (full or partial), record PnL, update streaks.
    close_qty: 0 = close full position, >0 = close that many units.
    """
    pos = strategy._positions.get(position_id)
    if not pos:
      return

    # Determine actual qty to close
    actual_qty = pos.qty if close_qty <= 0 else min(close_qty, pos.qty)
    is_partial = actual_qty < pos.qty

    if exit_price is None:
      exit_price = strategy._closes[bar]

    # Slippage (worse fill for exits)
    exit_price = self._apply_slippage(exit_price, pos.side, is_entry=False)

    # Spread cost — half-spread on exit (always worse fill)
    if self.config.spread_pct > 0:
      half = self.config.spread_pct / 200
      exit_price *= (1 - half) if pos.side == "long" else (1 + half)

    # PnL on closed portion
    if pos.side == "long":
      pnl = (exit_price - pos.entry_price) * actual_qty
    else:
      pnl = (pos.entry_price - exit_price) * actual_qty

    commission = exit_price * actual_qty * (self.config.commission_pct / 100)
    pnl -= commission

    # Funding rate cost — deducted at position close based on holding days
    if self.config.funding_rate_daily_pct > 0:
      if isinstance(strategy._timestamps, pd.DatetimeIndex) and pos.entry_bar < len(strategy._timestamps):
        hold_td = strategy._timestamps[bar] - strategy._timestamps[pos.entry_bar]
        hold_days = hold_td.total_seconds() / 86400
      else:
        hold_days = max(1, bar - pos.entry_bar)  # fallback
      pos_value = exit_price * actual_qty
      funding_cost = pos_value * (self.config.funding_rate_daily_pct / 100) * hold_days
      pnl -= funding_cost

    strategy._cash += pnl

    # Capture entry timestamp for the exit fill
    entry_ts = None
    if isinstance(strategy._timestamps, pd.DatetimeIndex) and pos.entry_bar < len(strategy._timestamps):
      entry_ts = strategy._timestamps[pos.entry_bar]

    # Capture SL/TP from exit orders linked to this position
    fill_sl = 0.0
    fill_tp = 0.0
    for eo in strategy._exit_orders.values():
      if eo.from_entry == position_id:
        if eo.stop > 0:
          fill_sl = eo.stop
        if eo.limit > 0:
          fill_tp = eo.limit

    # Compute MAE/MFE (Max Adverse/Favorable Excursion) in USDT
    if pos.side == "long":
      mae = (pos.extreme_low - pos.entry_price) * actual_qty   # negative = adverse
      mfe = (pos.extreme_high - pos.entry_price) * actual_qty  # positive = favorable
    else:
      mae = (pos.entry_price - pos.extreme_high) * actual_qty  # negative = adverse
      mfe = (pos.entry_price - pos.extreme_low) * actual_qty   # positive = favorable

    strategy._trades.append(OrderFill(
      bar_index=bar,
      timestamp=strategy._timestamps[bar] if isinstance(strategy._timestamps, pd.DatetimeIndex) else pd.Timestamp.now(),
      side=pos.side, action="exit", price=exit_price,
      qty=actual_qty, label=label, pnl=pnl, position_id=position_id,
      entry_price=pos.entry_price, entry_time=entry_ts,
      sl_price=fill_sl, tp_price=fill_tp,
      mae=mae, mfe=mfe, commission=commission,
      sl_past_be_time=getattr(pos, 'sl_past_be_time', None),
    ))

    if is_partial:
      # Reduce position qty — position stays open
      pos.qty -= actual_qty
    else:
      # Full close — remove position
      # CS-050: maintain side counter on every full close
      if hasattr(strategy, "_positions_by_side") and strategy._positions_by_side is not None:
        strategy._positions_by_side[pos.side] = strategy._positions_by_side.get(pos.side, 0) - 1
      del strategy._positions[position_id]
      # Update streak counters (martingale) only on full close.
      strategy._closed_trades_count += 1
      # CS-048: previously a ±$0.01 "breakeven" window caused genuine
      # tiny-notional wins/losses (e.g. $0.005) to silently not break a
      # martingale streak, which let streaks grow unbounded on small-
      # capital tests. Tolerance is now at float-noise level so only a
      # truly zero PnL is treated as breakeven. Engine and live are
      # kept in lock-step — change both sites together.
      _BE_EPS = 1e-9
      if pnl > _BE_EPS:
        strategy._consecutive_wins += 1
        strategy._consecutive_losses = 0
      elif pnl < -_BE_EPS:
        strategy._consecutive_losses += 1
        strategy._consecutive_wins = 0
      # Clean up ALL exit orders linked to this position
      to_remove = [oid for oid, eo in strategy._exit_orders.items()
                   if eo.from_entry == position_id]
      for oid in to_remove:
        del strategy._exit_orders[oid]
      # Clear consumed exits for this position so IDs can be reused
      consumed_for_pos = [eid for eid, fentry in strategy._consumed_exits.items()
                          if fentry == position_id]
      for eid in consumed_for_pos:
        strategy._consumed_exits.pop(eid, None)

  # ── Equity ───────────────────────────────────────────────────────
  def _calculate_equity(self, strategy: BaseStrategy) -> float:
    """Current equity = cash + unrealized PnL across all positions."""
    equity = strategy._cash
    if strategy._positions:
      current_price = strategy._closes[strategy._bar]
      for pos in strategy._positions.values():
        if pos.side == "long":
          equity += (current_price - pos.entry_price) * pos.qty
        else:
          equity += (pos.entry_price - current_price) * pos.qty
    return equity

  # ── State capture & injection (batch refresh) ──────────────────

  @staticmethod
  def _to_json_safe(v):
    """Convert value to JSON-serializable form. NaN/Inf → sentinel strings."""
    if v is None:
      return None
    if isinstance(v, bool):
      return v
    if isinstance(v, float):
      if math.isnan(v):
        return "__NaN__"
      if math.isinf(v):
        return "__Inf__" if v > 0 else "__-Inf__"
      return v
    if isinstance(v, int):
      return v
    if isinstance(v, str):
      return v
    if isinstance(v, (np.floating,)):
      f = float(v)
      if math.isnan(f):
        return "__NaN__"
      if math.isinf(f):
        return "__Inf__" if f > 0 else "__-Inf__"
      return f
    if isinstance(v, (np.integer,)):
      return int(v)
    if isinstance(v, (np.bool_,)):
      return bool(v)
    if isinstance(v, list):
      return [BacktestEngine._to_json_safe(x) for x in v]
    if isinstance(v, dict):
      return {str(k2): BacktestEngine._to_json_safe(v2) for k2, v2 in v.items()}
    if isinstance(v, pd.Timestamp):
      return {"__type__": "Timestamp", "value": v.isoformat()}
    if isinstance(v, tuple):
      return {"__type__": "tuple", "value": [BacktestEngine._to_json_safe(x) for x in v]}
    if isinstance(v, set):
      return {"__type__": "set", "value": [BacktestEngine._to_json_safe(x) for x in v]}
    if isinstance(v, np.ndarray):
      # Indicators are derived from candles — init() recomputes them on resume.
      # Persisting would waste space and risk stale-array overwrites. Drop.
      return None
    return None  # Skip unsupported types (callables, custom objects, etc.)

  @staticmethod
  def _from_json_safe(v):
    """Restore JSON-safe values back to Python types."""
    if v == "__NaN__":
      return float('nan')
    if v == "__Inf__":
      return float('inf')
    if v == "__-Inf__":
      return float('-inf')
    if isinstance(v, dict) and "__type__" in v:
      t = v["__type__"]
      if t == "Timestamp":
        return pd.Timestamp(v["value"])
      if t == "tuple":
        return tuple(BacktestEngine._from_json_safe(x) for x in v["value"])
      if t == "set":
        return set(BacktestEngine._from_json_safe(x) for x in v["value"])
      # NOTE: "ndarray" restore intentionally removed — the capture path
      # (_to_json_safe + _capture_strategy_state_static) drops ndarrays now,
      # so no captured state should ever contain this marker. Left here as a
      # comment to document the old behavior.
    if isinstance(v, list):
      return [BacktestEngine._from_json_safe(x) for x in v]
    if isinstance(v, dict):
      return {k2: BacktestEngine._from_json_safe(v2) for k2, v2 in v.items()}
    return v

  @staticmethod
  def _capture_strategy_state_static(strategy: BaseStrategy) -> dict:
    """Static version of _capture_strategy_state for external callers (scanner)."""
    state = {}
    for k, v in strategy.__dict__.items():
      if k.startswith('_'):
        continue
      # Skip indicator containers: they're recomputed from scratch in init()
      # during warmup on resume. Saving the full historical arrays both wastes
      # space (MB per run) AND causes a stale-state bug — restore overwrites
      # the freshly-computed arrays with the shorter captured ones.
      if isinstance(v, (pd.Series, pd.DataFrame, np.ndarray)):
        continue
      safe = BacktestEngine._to_json_safe(v)
      if safe is not None or v is None:
        state[k] = safe
    # Persist engine-private state that must survive across cycles
    state["__consumed_exits__"] = {str(k2): str(v2) for k2, v2 in strategy._consumed_exits.items()}
    state["__consecutive_wins__"] = strategy._consecutive_wins
    state["__consecutive_losses__"] = strategy._consecutive_losses
    state["__closed_trades_count__"] = strategy._closed_trades_count
    return state

  @staticmethod
  def _restore_strategy_state(strategy: BaseStrategy, state: dict):
    """Restore captured strategy variables (market structure, trade arrays, etc.)."""
    for k, v in state.items():
      if k.startswith("__"):
        continue  # Handle engine-private keys separately below
      setattr(strategy, k, BacktestEngine._from_json_safe(v))
    # Restore engine-private state
    if "__consumed_exits__" in state:
      strategy._consumed_exits = state["__consumed_exits__"]
    if "__consecutive_wins__" in state:
      strategy._consecutive_wins = state["__consecutive_wins__"]
    if "__consecutive_losses__" in state:
      strategy._consecutive_losses = state["__consecutive_losses__"]
    if "__closed_trades_count__" in state:
      strategy._closed_trades_count = state["__closed_trades_count__"]

  def _inject_positions(self, strategy: BaseStrategy, positions: list[dict], bar_idx: int):
    """Re-open positions that were ongoing at end of original batch.

    Creates Position objects, registers exit orders (SL/TP), deducts
    commission, and records entry fills for proper exit pairing.
    """
    for p in positions:
      pos_id = p["position_id"]
      side = p["side"]
      entry_price = p["entry_price"]
      qty = p["qty"]

      strategy._positions[pos_id] = Position(
        id=pos_id, side=side, entry_price=entry_price,
        qty=qty, entry_bar=bar_idx,
        extreme_high=p.get("extreme_high", entry_price),
        extreme_low=p.get("extreme_low", entry_price),
        was_injected=True,
      )
      # CS-050: maintain side counter on state restore
      if not hasattr(strategy, "_positions_by_side") or strategy._positions_by_side is None:
        strategy._positions_by_side = {"long": 0, "short": 0}
      strategy._positions_by_side[side] = strategy._positions_by_side.get(side, 0) + 1

      # Preferred path: re-register every captured exit with its ORIGINAL id.
      # Original ids avoid collisions with ids the strategy may re-issue
      # through strategy.exit() on the next bar (which would overwrite in a
      # dict keyed by id anyway, so matching the id means "refresh" rather
      # than "add a duplicate").
      exits = p.get("exits") or []
      if exits:
        for e in exits:
          eid = e.get("id") or f"{pos_id}_exit_{len(strategy._exit_orders)}"
          strategy._exit_orders[eid] = ExitOrder(
            id=eid, from_entry=pos_id,
            stop=float(e.get("stop") or 0),
            limit=float(e.get("limit") or 0),
            trail_points=float(e.get("trail_points") or 0),
            trail_offset=float(e.get("trail_offset") or 0),
            trail_extreme=float(e.get("trail_extreme") or 0),
            qty=float(e.get("qty") or 0),
            qty_percent=float(e.get("qty_percent") or 0),
          )
      else:
        # Legacy fallback: older states in the DB were captured before the
        # "exits" list existed. Use the single sl/tp/trail fields with
        # synthetic ids. Same behavior as the pre-fix code path.
        if p.get("sl_price") and p["sl_price"] > 0:
          strategy._exit_orders[f"{pos_id}_sl"] = ExitOrder(
            id=f"{pos_id}_sl", from_entry=pos_id,
            stop=p["sl_price"],
          )
        if p.get("tp_price") and p["tp_price"] > 0:
          strategy._exit_orders[f"{pos_id}_tp"] = ExitOrder(
            id=f"{pos_id}_tp", from_entry=pos_id,
            limit=p["tp_price"],
          )
        if p.get("trail_points") and p["trail_points"] > 0:
          strategy._exit_orders[f"{pos_id}_tsl"] = ExitOrder(
            id=f"{pos_id}_tsl", from_entry=pos_id,
            trail_points=p["trail_points"],
            trail_offset=p.get("trail_offset", 0),
            trail_extreme=p.get("trail_extreme", 0),
          )

      # Deduct entry commission (position was already open, but we need
      # consistent accounting for the refreshed segment)
      commission = entry_price * qty * (self.config.commission_pct / 100)
      strategy._cash -= commission

      # Record entry fill so exit fills can pair correctly
      entry_time = pd.Timestamp(p["entry_time"]) if p.get("entry_time") else strategy._timestamps[bar_idx]
      strategy._trades.append(OrderFill(
        bar_index=bar_idx, timestamp=entry_time,
        side=side, action="entry", price=entry_price,
        qty=qty, label=pos_id, position_id=pos_id,
        commission=commission,
      ))
