"""Strategy replay helper for the trade-chart popup.

Runs a strategy class bar-by-bar over a candle window to capture:
  - Entry triggers (where the strategy's conditions fired)
  - Structural state per bar (fib range, pivots, trend)

Does NOT touch the production backtest engine — this is additive.
Positions and pending orders are cleared each bar so every signal surfaces,
rather than only those that would fit under the engine's max_trades cap.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backend.db.database import SessionLocal
from backend.db.models import BacktestRun, Strategy
from backend.utils.candle_loader import load_candles_df, to_nyt
from backend.utils.safe_exec import safe_exec
from backend.utils.logger import get_logger

logger = get_logger(__name__)

NYT = ZoneInfo("America/New_York")

# Strategy attributes we snapshot each bar (if present as scalar floats/ints).
# Anything else is ignored — keeps the output clean.
STRUCTURAL_ATTRS = [
    "last_hh", "last_hl", "last_ll", "last_lh",
    "fib_range_high", "fib_range_low",
    "local_swing_high", "local_swing_low",
]

# User-facing TF label → engine TF string (matches strategy._resolve_tf).
HTF_LABEL_MAP = {
    "Current": None, "1h": "60", "4h": "240",
    "12h": "720", "1D": "D", "3D": "3D",
}

# Which input-keys point to HTFs the strategy might reference via request_security.
HTF_INPUT_KEYS = ["RSI Timeframe", "MA Filter Timeframe"]


def _utc_to_nyt_chart_unix(ts: pd.Timestamp) -> int:
    """NYT-naive pd.Timestamp → fake-UTC unix second for lightweight-charts."""
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    fake_utc = datetime(
        ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second,
        tzinfo=timezone.utc,
    )
    return int(fake_utc.timestamp())


def _fetch_htfs(symbol: str, inputs: dict) -> dict[str, pd.DataFrame]:
    """Return HTF DataFrames keyed by engine tf string, for filters like RSI on 1D."""
    needed: set[str] = set()
    for key in HTF_INPUT_KEYS:
        mapped = HTF_LABEL_MAP.get(inputs.get(key), None)
        if mapped:
            needed.add(mapped)
    if not needed:
        return {}
    out: dict[str, pd.DataFrame] = {}
    db = SessionLocal()
    try:
        for tf in needed:
            try:
                out[tf] = to_nyt(load_candles_df(db, symbol, tf))
            except Exception as e:
                logger.warning("Replay: HTF %s load failed for %s: %s", tf, symbol, e)
    finally:
        db.close()
    return out


def _init_strategy(strategy, df: pd.DataFrame, inputs: dict, tf_data: dict):
    """Mirror BacktestEngine's strategy setup (no trading, just state)."""
    strategy._df = df
    strategy._initial_capital = 10_000.0
    strategy._cash = 10_000.0
    strategy._positions = {}
    strategy._positions_by_side = {"long": 0, "short": 0}  # CS-050 counter
    strategy._exit_orders = {}
    strategy._trades = []
    strategy._pending_orders = []
    strategy._equity_curve = []
    strategy._inputs = inputs or {}
    strategy._plot_data = {}
    strategy._max_pyramiding = 1
    strategy._consumed_exits = {}
    strategy._tf_data = {tf: d.copy() for tf, d in tf_data.items()}

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
        from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError
        if isinstance(e, (AmbiguousTimeError, NonExistentTimeError)):
            from backend.backtester.portfolio_engine import _sanitize_dst_timestamps
            _sanitize_dst_timestamps(strategy._tf_data)
            strategy.init()
        else:
            raise

    strategy._convert_indicators_to_arrays()


def replay_run(
    run_id: int,
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
    times_unix: list[int],
    df_visible: pd.DataFrame,
) -> dict:
    """Replay the strategy for a given backtest_run over the visible window.

    Args:
        run_id: backtest_runs.id — source of strategy source + input config.
        start_dt, end_dt: visible window bounds (UTC datetimes).
        times_unix: chart-unix times for each bar in df_visible (same order).
        df_visible: OHLCV DataFrame (NYT-naive index) covering the visible window.

    Returns:
        {triggers: [...], structural_overlays: [...], trigger_count: {...}}
        or {error: "..."} on failure.
    """
    db = SessionLocal()
    try:
        run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
        if not run:
            return {"error": f"Run {run_id} not found"}
        symbol = run.symbol
        timeframe = run.timeframe
        params = json.loads(run.parameters or "{}")
        inputs = params.get("inputs", {})

        strat_row = db.query(Strategy).filter(Strategy.id == run.strategy_id).first()
        if not strat_row or not strat_row.python_source:
            return {"error": f"Strategy {run.strategy_id} has no source"}
        python_source = strat_row.python_source
    finally:
        db.close()

    # Load a WIDER window than visible so warmup/state stabilizes
    db2 = SessionLocal()
    try:
        full_df = to_nyt(load_candles_df(db2, symbol, timeframe))
    finally:
        db2.close()

    if full_df.empty:
        return {"error": f"No candles for {symbol} {timeframe}"}

    # Clip: need ~500 bars of warmup before the visible window start
    visible_start_nyt = df_visible.index[0]
    visible_end_nyt = df_visible.index[-1]
    tf_min = int(timeframe) if timeframe.isdigit() else (1440 if timeframe == "D" else 60)
    warmup_td = pd.Timedelta(minutes=tf_min * 500)

    replay_start = visible_start_nyt - warmup_td
    df = full_df[(full_df.index >= replay_start) & (full_df.index <= visible_end_nyt)].copy()
    if len(df) < 20:
        return {"error": "Insufficient candles for replay"}

    tf_data = _fetch_htfs(symbol, inputs)

    try:
        strategy_cls = safe_exec(python_source, label=f"replay_{run.strategy_id}")
    except Exception as e:
        return {"error": f"safe_exec failed: {e}"}

    strategy = strategy_cls()
    try:
        _init_strategy(strategy, df, inputs, tf_data)
    except Exception as e:
        logger.warning("Replay init failed for run %s: %s", run_id, e)
        return {"error": f"strategy init failed: {e}"}

    # Run bars — clear position state per-bar so every signal fires
    triggers: list[dict] = []
    state_series: dict[str, list] = {k: [] for k in STRUCTURAL_ATTRS}
    visible_times: list[pd.Timestamp] = []

    trig_count = {"long": 0, "short": 0}

    for bar_idx in range(len(df)):
        strategy._positions = {}
        strategy._pending_orders = []
        strategy._bar = bar_idx

        try:
            strategy.next()
        except Exception as e:
            # One bar's failure shouldn't kill the whole replay
            logger.debug("Replay bar %d failed: %s", bar_idx, e)
            continue

        bar_time = df.index[bar_idx]
        if bar_time < visible_start_nyt or bar_time > visible_end_nyt:
            continue

        visible_times.append(bar_time)

        for k in STRUCTURAL_ATTRS:
            v = getattr(strategy, k, None)
            if isinstance(v, (int, float)) and not (isinstance(v, float) and np.isnan(v)):
                state_series[k].append(float(v))
            else:
                state_series[k].append(None)

        for order in strategy._pending_orders:
            if order.get("action") != "entry":
                continue
            direction = order.get("direction", "long")
            if direction not in ("long", "short"):
                continue
            triggers.append({
                "time": _utc_to_nyt_chart_unix(bar_time),
                "direction": direction,
                "price": round(float(strategy._closes[bar_idx]), 8),
                "comment": order.get("comment", "") or "",
            })
            trig_count[direction] += 1

    # Build structural overlays keyed by time_unix
    # Map visible_times → chart-unix (same calculation as candle endpoint)
    time_unix_map = [_utc_to_nyt_chart_unix(t) for t in visible_times]

    overlays: list[dict] = []

    # Raw structural scalar series — each as its own overlay
    DISPLAY_NAMES = {
        "last_hh": "Last HH", "last_hl": "Last HL",
        "last_ll": "Last LL", "last_lh": "Last LH",
        "fib_range_high": "Range High", "fib_range_low": "Range Low",
        "local_swing_high": "Swing High", "local_swing_low": "Swing Low",
    }
    COLORS = {
        "last_hh": "#22c55e", "last_hl": "#16a34a",
        "last_ll": "#ef4444", "last_lh": "#dc2626",
        "fib_range_high": "#f59e0b", "fib_range_low": "#f59e0b",
        "local_swing_high": "#60a5fa", "local_swing_low": "#60a5fa",
    }
    for attr in STRUCTURAL_ATTRS:
        vs = state_series[attr]
        data = [
            {"time": t, "value": round(v, 8)}
            for t, v in zip(time_unix_map, vs)
            if v is not None
        ]
        if not data:
            continue
        overlays.append({
            "name": DISPLAY_NAMES.get(attr, attr),
            "color": COLORS.get(attr, "#94a3b8"),
            "data": data,
        })

    # Fib level series — computed from fib_range_high/low × fib_pcts
    fib_pcts = getattr(strategy, "fib_pcts", None) or []
    if fib_pcts:
        fh = state_series["fib_range_high"]
        fl = state_series["fib_range_low"]
        FIB_COLORS = ["#a78bfa", "#c084fc", "#e879f9", "#f472b6", "#fb7185"]
        for i, pct in enumerate(fib_pcts):
            data = []
            for t, h, l in zip(time_unix_map, fh, fl):
                if h is None or l is None:
                    continue
                data.append({"time": t, "value": round(l + (h - l) * pct, 8)})
            if not data:
                continue
            overlays.append({
                "name": f"Fib {pct*100:.1f}%",
                "color": FIB_COLORS[i % len(FIB_COLORS)],
                "data": data,
            })

    return {
        "triggers": triggers,
        "structural_overlays": overlays,
        "trigger_count": trig_count,
    }
