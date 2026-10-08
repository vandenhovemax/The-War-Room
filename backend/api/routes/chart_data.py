"""Chart data routes — candle retrieval + indicator overlay for trade chart."""
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query

from backend.data.data_manager import DataManager
from backend.backtester.indicators import ta
from backend.utils.indicator_extractor import (
    extract_indicators,
    OVERLAY_COLORS,
    OSCILLATOR_COLORS,
)
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

NYT = ZoneInfo("America/New_York")


def _utc_to_nyt_chart_unix(ts: datetime) -> int:
    """Convert a UTC datetime to a 'fake UTC' unix second shifted to NYT.

    lightweight-charts has no timezone support — it displays raw unix seconds
    as-is.  To make the x-axis show NYT, we convert UTC → NYT, then build a
    fake-UTC timestamp from the NYT components.  The result is a unix second
    that *displays* as NYT on the chart.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    nyt_dt = ts.astimezone(NYT)
    fake_utc = datetime(
        nyt_dt.year, nyt_dt.month, nyt_dt.day,
        nyt_dt.hour, nyt_dt.minute, nyt_dt.second,
        tzinfo=timezone.utc,
    )
    return int(fake_utc.timestamp())


# ── Indicator computation ────────────────────────────────────────────

def _compute_indicators(
    df: pd.DataFrame,
    specs: list[dict],
    times: list[int],
) -> dict:
    """Compute indicators on OHLCV DataFrame, return categorized results.

    Args:
        df: DataFrame with open/high/low/close/volume columns
        specs: indicator specs from extract_indicators()
        times: NYT-shifted unix seconds (same order as df rows)

    Returns:
        {"overlays": [...], "oscillators": [...]}
    """
    overlays: list[dict] = []
    oscillators: list[dict] = []
    overlay_idx = 0
    osc_idx = 0

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    for spec in specs:
        name = spec["name"]
        args = spec["args"]
        src_col = spec.get("source", "close")
        source = df[src_col] if src_col in df.columns else close
        category = spec["category"]

        try:
            series_list = _compute_single(
                name, args, source, close, high, low, volume,
            )
        except Exception as e:
            logger.warning("Indicator %s(%s) failed: %s", name, args, e)
            continue

        if not series_list:
            continue

        for label, values in series_list:
            # Build time/value pairs, skip NaN
            data = []
            for i, v in enumerate(values):
                if i < len(times) and not (np.isnan(v) if isinstance(v, float) else False):
                    data.append({"time": times[i], "value": round(float(v), 6)})

            if not data:
                continue

            entry: dict = {"name": label, "data": data}

            if category == "oscillator":
                entry["color"] = OSCILLATOR_COLORS[osc_idx % len(OSCILLATOR_COLORS)]
                # Add reference levels for known oscillators
                if name == "rsi":
                    entry["levels"] = [30, 70]
                elif name in ("stoch", "mfi"):
                    entry["levels"] = [20, 80]
                elif name == "cci":
                    entry["levels"] = [-100, 100]
                oscillators.append(entry)
                osc_idx += 1
            else:
                entry["color"] = OVERLAY_COLORS[overlay_idx % len(OVERLAY_COLORS)]
                overlays.append(entry)
                overlay_idx += 1

    return {"overlays": overlays, "oscillators": oscillators}


def _compute_single(
    name: str,
    args: list[float],
    source: pd.Series,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
) -> list[tuple[str, np.ndarray]]:
    """Compute a single indicator, return list of (label, values) tuples.

    Multi-output indicators (BB, MACD) return multiple tuples.
    """
    # ── Moving Averages (single line) ──
    if name == "sma":
        length = int(args[0]) if args else 20
        return [(f"SMA {length}", ta.sma(source, length).values)]

    if name == "ema":
        length = int(args[0]) if args else 20
        return [(f"EMA {length}", ta.ema(source, length).values)]

    if name == "wma":
        length = int(args[0]) if args else 20
        return [(f"WMA {length}", ta.wma(source, length).values)]

    if name == "hma":
        length = int(args[0]) if args else 20
        return [(f"HMA {length}", ta.hma(source, length).values)]

    if name == "rma":
        length = int(args[0]) if args else 14
        return [(f"RMA {length}", ta.rma(source, length).values)]

    if name == "alma":
        length = int(args[0]) if args else 20
        offset = args[1] if len(args) > 1 else 0.85
        sigma = args[2] if len(args) > 2 else 6.0
        return [(f"ALMA {length}", ta.alma(source, length, offset, sigma).values)]

    if name == "vwma":
        length = int(args[0]) if args else 20
        return [(f"VWMA {length}", ta.vwma(source, volume, length).values)]

    # ── Bands (multi-line) ──
    if name == "bb":
        length = int(args[0]) if args else 20
        mult = args[1] if len(args) > 1 else 2.0
        upper, basis, lower = ta.bb(source, length, mult)
        return [
            (f"BB Upper {length}", upper.values),
            (f"BB Basis {length}", basis.values),
            (f"BB Lower {length}", lower.values),
        ]

    if name == "kc":
        length = int(args[0]) if args else 20
        mult = args[1] if len(args) > 1 else 1.5
        upper, basis, lower = ta.kc(high, low, close, length, mult)
        return [
            (f"KC Upper {length}", upper.values),
            (f"KC Basis {length}", basis.values),
            (f"KC Lower {length}", lower.values),
        ]

    if name == "donchian":
        length = int(args[0]) if args else 20
        upper, basis, lower = ta.donchian(high, low, length)
        return [
            (f"DC Upper {length}", upper.values),
            (f"DC Basis {length}", basis.values),
            (f"DC Lower {length}", lower.values),
        ]

    # ── Trend overlays ──
    if name == "supertrend":
        length = int(args[0]) if args else 10
        mult = args[1] if len(args) > 1 else 3.0
        st_line, _direction = ta.supertrend(high, low, close, length, mult)
        return [(f"SuperTrend {length}/{mult}", st_line.values)]

    if name == "sar":
        start = args[0] if args else 0.02
        inc = args[1] if len(args) > 1 else 0.02
        max_val = args[2] if len(args) > 2 else 0.2
        return [("SAR", ta.sar(high, low, start, inc, max_val).values)]

    if name == "vwap":
        return [("VWAP", ta.vwap(high, low, close, volume).values)]

    if name == "ichimoku":
        conv = int(args[0]) if args else 9
        base = int(args[1]) if len(args) > 1 else 26
        span_b = int(args[2]) if len(args) > 2 else 52
        tenkan, kijun, senkou_a, senkou_b = ta.ichimoku(
            high, low, conv, base, span_b,
        )
        return [
            ("Tenkan", tenkan.values),
            ("Kijun", kijun.values),
            ("Senkou A", senkou_a.values),
            ("Senkou B", senkou_b.values),
        ]

    # ── Oscillators ──
    if name == "rsi":
        length = int(args[0]) if args else 14
        return [(f"RSI {length}", ta.rsi(source, length).values)]

    if name == "macd":
        fast = int(args[0]) if args else 12
        slow = int(args[1]) if len(args) > 1 else 26
        signal = int(args[2]) if len(args) > 2 else 9
        macd_line, signal_line, histogram = ta.macd(source, fast, slow, signal)
        return [
            (f"MACD {fast}/{slow}", macd_line.values),
            (f"Signal {signal}", signal_line.values),
            ("Histogram", histogram.values),
        ]

    if name == "stoch":
        length = int(args[0]) if args else 14
        return [(f"Stoch %K {length}", ta.stoch(high, low, close, length).values)]

    if name == "cci":
        length = int(args[0]) if args else 20
        return [(f"CCI {length}", ta.cci(high, low, close, length).values)]

    if name == "mfi":
        length = int(args[0]) if args else 14
        return [(f"MFI {length}", ta.mfi(high, low, close, volume, length).values)]

    if name == "dmi":
        length = int(args[0]) if args else 14
        plus_di, minus_di, adx = ta.dmi(high, low, close, length)
        return [
            (f"+DI {length}", plus_di.values),
            (f"-DI {length}", minus_di.values),
            (f"ADX {length}", adx.values),
        ]

    if name == "atr":
        length = int(args[0]) if args else 14
        return [(f"ATR {length}", ta.atr(high, low, close, length).values)]

    return []


# ── Main endpoint ────────────────────────────────────────────────────

@router.get("/candles")
def get_chart_candles(
    symbol: str = Query(...),
    timeframe: str = Query(...),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(500, le=2000),
    strategy_id: Optional[int] = Query(None),
    run_id: Optional[int] = Query(None),
):
    """Retrieve OHLCV candles + strategy indicators for chart rendering.

    Returns candles in lightweight-charts format with timestamps shifted to
    America/New_York.  When strategy_id is provided, auto-detects the
    strategy's indicator calls and returns computed values alongside candles.

    When run_id is provided, additionally replays the strategy over the
    visible window to surface entry triggers and structural state (fib
    ranges, pivots) — matches the exact daughter-config from that run.
    """
    dm = DataManager()
    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None

    candles = dm.get_candles(
        symbol=symbol,
        timeframe=timeframe,
        start=start_dt,
        end=end_dt,
        limit=limit,
    )

    # Build candle list + NYT-shifted times
    result = []
    times: list[int] = []
    for c in candles:
        ts = c["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        t = _utc_to_nyt_chart_unix(ts)
        times.append(t)
        result.append({
            "time": t,
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "volume": c["volume"],
        })

    # Compute indicators if strategy_id provided
    indicators: dict = {"overlays": [], "oscillators": []}

    if strategy_id and len(candles) > 0:
        try:
            indicators = _load_and_compute_indicators(
                strategy_id, candles, times,
            )
        except Exception as e:
            logger.warning("Indicator computation failed: %s", e)

    # Strategy replay: triggers + structural overlays for this exact run
    replay: Optional[dict] = None
    if run_id and len(candles) > 0:
        try:
            from backend.api.routes.chart_replay import replay_run

            # Build a NYT-naive DataFrame from the raw candles (preserves UTC ts)
            raw_rows = []
            for c in candles:
                ts = c["timestamp"]
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                raw_rows.append({
                    "timestamp": ts,
                    "open": c["open"], "high": c["high"],
                    "low": c["low"], "close": c["close"], "volume": c["volume"],
                })
            df_visible = pd.DataFrame(raw_rows)
            df_visible["timestamp"] = (
                pd.to_datetime(df_visible["timestamp"], utc=True)
                .dt.tz_convert("America/New_York")
                .dt.tz_localize(None)
            )
            df_visible = df_visible.set_index("timestamp").sort_index()
            df_visible = df_visible[~df_visible.index.duplicated(keep="first")]

            replay = replay_run(
                run_id=run_id,
                start_dt=start_dt,
                end_dt=end_dt,
                times_unix=times,
                df_visible=df_visible,
            )
        except Exception as e:
            logger.warning("Strategy replay failed for run %s: %s", run_id, e)
            replay = {"error": str(e)}

    logger.info(
        "Chart candles: %s %s — %d candles, %d overlays, %d oscillators, replay=%s",
        symbol, timeframe, len(result),
        len(indicators.get("overlays", [])),
        len(indicators.get("oscillators", [])),
        "yes" if replay and "error" not in replay else ("error" if replay else "no"),
    )
    response: dict = {
        "candles": result,
        "count": len(result),
        "indicators": indicators,
    }
    if replay is not None:
        response["replay"] = replay
    return response


def _load_and_compute_indicators(
    strategy_id: int,
    candles: list[dict],
    times: list[int],
) -> dict:
    """Load strategy source, extract indicators, compute on candle data."""
    from backend.db.database import SessionLocal
    from backend.db.models import Strategy

    with SessionLocal() as session:
        strategy = session.query(Strategy).filter(
            Strategy.id == strategy_id,
        ).first()

    if not strategy or not strategy.python_source:
        return {"overlays": [], "oscillators": []}

    specs = extract_indicators(strategy.python_source)
    if not specs:
        return {"overlays": [], "oscillators": []}

    # Build DataFrame from candles
    df = pd.DataFrame(candles)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return _compute_indicators(df, specs, times)
