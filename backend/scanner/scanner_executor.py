"""Scanner executor — runs a strategy on new bars using saved state.

Loads full candle history, computes indicators via init(), restores saved
user-level state, then calls next() ONLY on new bars. Captures any signals
and saves updated state back to DB.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from backend.backtester.engine import BacktestEngine
from backend.backtester.models import ExitOrder as BtExitOrder
from backend.backtester.models import Position as BtPosition
from backend.db.models import LiveSignal, LiveStrategyState
from backend.utils.candle_loader import load_candles_df, to_nyt
from backend.utils.logger import get_logger

logger = get_logger("scanner_executor")

# Bump when the scanner's state format changes so previously-saved
# state files become invalid and the scanner does a one-time full
# replay to rebuild them. v1 = pre-2026-05-12 rolling-500-bar window
# (bar indices invalid against full-history df). v2 = post-2026-05-12
# full-history window (bar indices stable across runs).
_SCANNER_STATE_VERSION = 2


def execute_strategy_on_new_bars(
  db: Session,
  portfolio_id: int,
  symbol: str,
  strategy_source: str,
  base_tf: str,
  extra_tfs: list[str],
  state: LiveStrategyState,
  side_filter: str,
  inputs: dict,
) -> list[LiveSignal]:
  """Run strategy on new bars and return any signals found.

  Algorithm:
    1. Load full candle history into DataFrame
    2. Compile strategy, set _df, call init() (indicators over full data)
    3. Restore saved user state (market structure, counters)
    4. Restore ongoing positions
    5. Run next() ONLY on new bars
    6. Capture signals from _pending_orders
    7. Save updated state to DB
  """
  # 1. Load FULL candle history (no LIMIT). REVISED 2026-05-12.
  #
  # Earlier this loaded only the last 500 bars. That was efficient but
  # WRONG for strategies whose entry logic depends on accumulated
  # market structure — e.g., "Market Structure + vol" identifies BOS /
  # CHoCH from swing highs/lows that may have formed weeks or months
  # ago. With a rolling 500-bar window, that structure context is
  # silently truncated and the strategy fires far fewer (or different)
  # signals than a full-history replay would. Even worse: when state
  # was restored, saved attributes like `since_hh_low_bar=28500`
  # pointed to bar 28500 of the OLD long df — but the freshly-loaded
  # df only had 500 bars, so the index referred to a completely
  # different timestamp. That's the "rolling-window bar-index drift"
  # bug.
  #
  # Loading full history fixes both: bar indices stay stable (the DB
  # is append-only — bar 28500 is the same timestamp every cycle), and
  # the strategy has full market structure context. Steady-state cost
  # is still cheap because we restore state and only iterate the
  # NEW bars since `last_bar_timestamp`. First run per (portfolio,
  # symbol) does a full replay (one-time slow); every subsequent
  # cycle is fast.
  try:
    df = to_nyt(load_candles_df(db, symbol, base_tf))
  except ValueError:
    return []

  if df.empty:
    return []

  # Load extra TF data (full history too — same reasoning as above)
  tf_data: dict[str, pd.DataFrame] = {}
  for tf in (extra_tfs or []):
    try:
      tf_data[tf] = to_nyt(load_candles_df(db, symbol, tf))
    except ValueError:
      pass

  # 2. Compile strategy
  from backend.utils.safe_exec import safe_exec
  try:
    strategy_cls = safe_exec(strategy_source, label=f"scanner_{symbol}")
  except (ValueError, SyntaxError) as e:
    logger.error("Strategy compile failed for %s: %s", symbol, e)
    return []

  if not strategy_cls:
    logger.error("No TranspiledStrategy class for %s", symbol)
    return []

  instance = strategy_cls()
  instance._df = df
  instance._bar = 0
  instance._inputs = inputs
  instance._positions = {}
  instance._exit_orders = {}
  instance._trades = []
  instance._pending_orders = []
  instance._equity_curve = []
  instance._tf_data = tf_data

  # Pre-extract OHLCV + time components as numpy arrays for fast per-bar access.
  # BaseStrategy properties (close, open, high, etc.) use these arrays.
  instance._opens = df["open"].values
  instance._highs = df["high"].values
  instance._lows = df["low"].values
  instance._closes = df["close"].values
  instance._volumes = df["volume"].values
  instance._timestamps = df.index
  instance._hours = df.index.hour
  instance._minutes = df.index.minute
  instance._seconds = df.index.second
  _pine_map = {0: 2, 1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 1}
  instance._dayofweek_pine = np.array([_pine_map[d] for d in df.index.dayofweek])
  instance._days = df.index.day
  instance._months = df.index.month
  instance._years = df.index.year

  # init() creates indicator Series over full _df
  instance.init()

  # Convert indicator pd.Series to numpy arrays for fast per-bar access
  instance._convert_indicators_to_arrays()

  # 3. Restore saved user-level state — RE-ENABLED 2026-05-12
  #
  # Now that we load full history (step 1), bar indices in saved state
  # (e.g. `since_hh_low_bar=28500`) refer to the SAME timestamp every
  # cycle — the OHLCV table is append-only, so bar 28500 today is the
  # same bar 28500 tomorrow. The rolling-window drift bug is gone.
  #
  # CRITICAL for market-structure strategies: BOS / CHoCH signals can
  # reference swing highs/lows that formed weeks or months ago. Without
  # state restore the strategy would have to re-discover all that
  # context from scratch on every scan cycle. With state restore it
  # picks up where it left off — exactly what a continuous live run
  # would have produced.
  #
  # MIGRATION: state saved by the prior (2026-05-11 → 2026-05-12) build
  # is stale — bar indices were captured against a rolling 500-bar
  # window. We tag every NEW save with __scanner_state_version__ (below);
  # any saved state missing that tag or with an older version is
  # ignored, and the symbol gets a one-time full-history replay to
  # rebuild correct state.
  saved = json.loads(state.strategy_state) if state.strategy_state else None
  if saved and saved.get("__scanner_state_version__") == _SCANNER_STATE_VERSION:
    BacktestEngine._restore_strategy_state(instance, saved)
  else:
    if saved:
      logger.info(
        "  %s: ignoring pre-v%d saved state — doing one-time full-history replay",
        symbol, _SCANNER_STATE_VERSION,
      )
    saved = None  # treat as fresh first-run below

  # 4. Restore ongoing positions (with full SL/TP/TSL + extremes)
  ongoing = json.loads(state.ongoing_positions) if state.ongoing_positions else []
  for p in ongoing:
    pid = p["position_id"]
    instance._positions[pid] = BtPosition(
      id=pid,
      side=p["side"],
      entry_price=p["entry_price"],
      qty=p["qty"],
      entry_bar=state.last_bar_index or 0,
      extreme_high=p.get("extreme_high", p["entry_price"]),
      extreme_low=p.get("extreme_low", p["entry_price"]),
    )
    if p.get("sl_price") and p["sl_price"] > 0:
      instance._exit_orders[f"{pid}_sl"] = BtExitOrder(
        id=f"{pid}_sl", from_entry=pid, stop=p["sl_price"],
      )
    if p.get("tp_price") and p["tp_price"] > 0:
      instance._exit_orders[f"{pid}_tp"] = BtExitOrder(
        id=f"{pid}_tp", from_entry=pid, limit=p["tp_price"],
      )
    if p.get("trail_points") and p["trail_points"] > 0:
      instance._exit_orders[f"{pid}_tsl"] = BtExitOrder(
        id=f"{pid}_tsl", from_entry=pid,
        trail_points=p["trail_points"],
        trail_offset=p.get("trail_offset", 0),
        trail_extreme=p.get("trail_extreme", 0),
      )

  # 5. Determine the iteration range and the signal-emission boundary.
  #
  # Two cases:
  #   A. Steady-state (valid v2 state restored): start iteration AT the
  #      first new bar after `last_bar_timestamp`. State is already
  #      caught up to that point, so no warmup needed. Every iterated
  #      bar emits signals.
  #   B. First-run / pre-v2 stale state (saved=None): start iteration
  #      AT bar 0 to rebuild state from full history. Only the very
  #      last bar emits a signal — historical bars iterate silently
  #      so the strategy's market structure (range_history, swing
  #      H/L bar indices, trend, etc.) accumulates correctly but we
  #      don't spam old signals the user can no longer act on.
  naive_index = df.index.tz_localize(None) if df.index.tz is not None else df.index
  if saved is not None and state.last_bar_timestamp:
    ts = pd.Timestamp(state.last_bar_timestamp)
    if ts.tzinfo is not None:
      ts = ts.tz_localize(None)
    last_idx = naive_index.searchsorted(ts, side="right") - 1
    last_idx = max(0, min(last_idx, len(df) - 1))
    iterate_from = last_idx + 1
    emit_from = last_idx + 1
  else:
    # First-run path — iterate everything, emit only the last bar
    iterate_from = 0
    emit_from = max(0, len(df) - 1)

  if iterate_from >= len(df):
    return []

  total_iter = len(df) - iterate_from
  if saved is None:
    logger.info(
      "  %s: FIRST-RUN full-history replay — iterating %d bars (emitting signals only for last bar)",
      symbol, total_iter,
    )
  else:
    logger.info(
      "  %s: %d new bar(s) to process (bars %d->%d, df=%d total)",
      symbol, total_iter, iterate_from, len(df) - 1, len(df),
    )

  # 6. Iterate. Bars in [iterate_from, emit_from) run silently to warm
  # state. Bars in [emit_from, len(df)) capture signals.
  signals: list[LiveSignal] = []

  for bar_idx in range(iterate_from, len(df)):
    instance._bar = bar_idx
    instance._pending_orders.clear()

    try:
      instance.next()
    except Exception as e:
      logger.error("  %s bar %d next() error: %s", symbol, bar_idx, e)
      continue

    # Silent warmup phase — discard any orders, don't emit signals
    if bar_idx < emit_from:
      instance._pending_orders.clear()
      continue

    # Extract signals from _pending_orders
    for order in instance._pending_orders:
      action = order.get("action", "")
      direction = order.get("direction", "long")
      entry_id = order.get("id", "")

      # Side filter
      if action == "entry":
        if side_filter == "Long" and direction == "short":
          continue
        if side_filter == "Short" and direction == "long":
          continue

      # Look up SL/TP/TSL from exit orders
      sl, tp, trail = 0.0, 0.0, 0.0
      if action == "entry":
        for eo in instance._exit_orders.values():
          if hasattr(eo, "from_entry") and eo.from_entry == entry_id:
            if eo.stop > 0 and sl == 0:
              sl = eo.stop
            if eo.limit > 0 and tp == 0:
              tp = eo.limit
            if eo.trail_points > 0 and trail == 0:
              trail = eo.trail_points

      bar_ts = df.index[bar_idx]
      close_px = float(df["close"].iloc[bar_idx])

      signal = LiveSignal(
        portfolio_id=portfolio_id,
        symbol=symbol,
        action=action,
        direction=direction,
        entry_id=entry_id,
        bar_timestamp=bar_ts,
        close_price=close_px,
        sl_price=sl,
        tp_price=tp,
        trail_points=trail,
        comment=order.get("comment", ""),
      )
      signals.append(signal)

      logger.info(
        "  SIGNAL: %s %s %s @ %.4f (SL=%.4f TP=%.4f) [%s p%d]",
        action.upper(), direction.upper(), symbol,
        close_px, sl, tp, entry_id, portfolio_id,
      )

    instance._pending_orders.clear()

  # 7. Save updated state (_capture_strategy_state is effectively static)
  new_state = BacktestEngine._capture_strategy_state_static(instance)
  # Tag this state with the scanner version so future scans can detect
  # stale state from older builds (see step 3 above).
  new_state["__scanner_state_version__"] = _SCANNER_STATE_VERSION

  # Capture ongoing positions (with full SL/TP/TSL + extremes + entry_time)
  ongoing_out = []
  for pid, pos in instance._positions.items():
    pos_dict = {
      "position_id": pid,
      "side": pos.side,
      "entry_price": pos.entry_price,
      "qty": pos.qty,
      "extreme_high": pos.extreme_high,
      "extreme_low": pos.extreme_low,
    }
    # Save ALL exit orders for this position (SL, TP, TSL)
    for eo in instance._exit_orders.values():
      if hasattr(eo, "from_entry") and eo.from_entry == pid:
        if eo.stop and eo.stop > 0:
          pos_dict["sl_price"] = eo.stop
        if eo.limit and eo.limit > 0:
          pos_dict["tp_price"] = eo.limit
        if eo.trail_points and eo.trail_points > 0:
          pos_dict["trail_points"] = eo.trail_points
          pos_dict["trail_offset"] = eo.trail_offset
          pos_dict["trail_extreme"] = eo.trail_extreme
    ongoing_out.append(pos_dict)

  state.strategy_state = json.dumps(new_state)
  state.ongoing_positions = json.dumps(ongoing_out)
  # Store as naive datetime (NYT) — consistent with how we read it back
  last_ts = df.index[-1].to_pydatetime()
  if last_ts.tzinfo is not None:
    last_ts = last_ts.replace(tzinfo=None)
  state.last_bar_timestamp = last_ts
  state.last_bar_index = len(df) - 1
  # Guard: bars_processed can return as bytes BLOB from SQLite if column
  # type affinity is wrong — safely coerce to int (Bug 39)
  _prev = state.bars_processed
  if isinstance(_prev, (bytes, memoryview)):
    _prev = 0
  state.bars_processed = int(_prev or 0) + total_iter
  state.updated_at = datetime.now(timezone.utc)
  db.commit()

  return signals
