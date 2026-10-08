"""Boundary clipping: force-close trades at IS/OOS period boundaries.

When clip_boundaries=True (default), trades that straddle a period
boundary are force-closed at the boundary date's candle close price.
This prevents a trade entering in IS from counting its full TP profit
that would only be realized in OOS.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from backend.db.models import OHLCVData
from backend.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ClippedTrade:
    """Lightweight duck-type of BacktestTrade for clipped trades.

    Not an ORM model. Not persisted. Works with trades_from_db()
    and build_daily_pnl_matrix() which read the same attributes.
    """

    id: int = 0
    run_id: int = 0
    trade_num: int = 0
    position_id: str | None = None
    symbol: str = ""
    side: str = ""
    entry_time: Any = None
    exit_time: Any = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: float = 0.0
    net_profit: float = 0.0
    net_profit_pct: float = 0.0
    commission: float = 0.0
    mae: float = 0.0
    mfe: float = 0.0
    sl_price: float | None = None
    tp_price: float | None = None
    exit_reason: str = "boundary_clip"


def get_boundary_close_prices(
    symbols: set[str],
    boundary_date: str,
    timeframe: str,
    db: Session,
) -> tuple[dict[str, float], dict[str, datetime]]:
    """Look up close price & timestamp of last candle on *boundary_date* per symbol.

    Returns (prices, times) where:
      prices: symbol -> close price
      times:  symbol -> candle timestamp (actual last candle, not cosmetic 23:59:59)
    Symbols with no candle data are omitted (caller falls back to entry_price).
    """
    cutoff = datetime.strptime(boundary_date, "%Y-%m-%d") + timedelta(days=1)
    prices: dict[str, float] = {}
    times: dict[str, datetime] = {}

    from backend.db.database import ohlcv_router
    ohlcv_db = ohlcv_router.get_session(timeframe)
    try:
        for sym in symbols:
            row = (
                ohlcv_db.query(OHLCVData.close, OHLCVData.timestamp)
                .filter(
                    OHLCVData.symbol == sym,
                    OHLCVData.timeframe == timeframe,
                    OHLCVData.timestamp < cutoff,
                )
                .order_by(OHLCVData.timestamp.desc())
                .first()
            )
            if row:
                prices[sym] = float(row[0])
                ts = row[1]
                if isinstance(ts, str):
                    ts = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                times[sym] = ts
            else:
                logger.warning(
                    "No OHLCV candle for %s at boundary %s (tf=%s)",
                    sym, boundary_date, timeframe,
                )
    finally:
        ohlcv_db.close()

    return prices, times


def _to_dt(val: Any) -> datetime:
    """Coerce entry_time / exit_time to datetime for comparison."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        return datetime.strptime(val[:19], "%Y-%m-%d %H:%M:%S")
    return datetime.min


def _make_clipped(
    trade: Any,
    boundary_close: float,
    boundary_exit_time: datetime,
) -> ClippedTrade:
    """Create a ClippedTrade from an original, re-priced at boundary."""
    entry_price = float(trade.entry_price)
    quantity = float(trade.quantity)
    side = (trade.side or "").lower()

    if side == "long":
        gross_pnl = (boundary_close - entry_price) * quantity
    else:
        gross_pnl = (entry_price - boundary_close) * quantity

    # Scale commission proportionally to price movement
    orig_exit = float(trade.exit_price) if trade.exit_price else entry_price
    orig_comm = abs(float(trade.commission)) if trade.commission else 0.0
    denom = abs(entry_price) + abs(orig_exit)
    if denom > 0 and orig_comm > 0:
        scaled_comm = orig_comm * (abs(entry_price) + abs(boundary_close)) / denom
    else:
        scaled_comm = orig_comm

    net = gross_pnl - scaled_comm
    entry_value = entry_price * quantity
    net_pct = (net / entry_value * 100) if entry_value else 0.0

    return ClippedTrade(
        id=trade.id,
        run_id=trade.run_id,
        trade_num=trade.trade_num,
        position_id=getattr(trade, "position_id", None),
        symbol=trade.symbol,
        side=trade.side,
        entry_time=trade.entry_time,
        exit_time=boundary_exit_time,
        entry_price=entry_price,
        exit_price=boundary_close,
        quantity=quantity,
        net_profit=net,
        net_profit_pct=net_pct,
        commission=scaled_comm,
        mae=0.0,
        mfe=0.0,
        sl_price=getattr(trade, "sl_price", None),
        tp_price=getattr(trade, "tp_price", None),
        exit_reason="boundary_clip",
    )


def clip_trades_at_boundary(
    trades: list,
    boundary_date: str,
    boundary_prices: dict[str, float],
    boundary_times: dict[str, datetime] | None = None,
) -> list:
    """Clip trades that straddle *boundary_date*.

    - Trade fully within boundary: passed through unchanged
    - Trade entry <= boundary AND exit > boundary: clipped
    - Trade entry > boundary: passed through unchanged
    - Straddling trade whose symbol has NO boundary price: passed through
      unchanged with a warning (never silently repriced at entry — that
      would fabricate a zero-PnL exit)

    boundary_times: optional per-symbol last candle timestamp. When provided,
    the clipped trade's exit_time is set to the actual candle time instead of
    a cosmetic 23:59:59.
    """
    boundary_dt = datetime.strptime(boundary_date, "%Y-%m-%d")
    cutoff = boundary_dt + timedelta(days=1)
    fallback_exit = boundary_dt.replace(hour=23, minute=59, second=59)

    result = []
    unresolved: set[str] = set()
    for t in trades:
        entry = _to_dt(t.entry_time)
        exit_ = _to_dt(t.exit_time)

        if entry < cutoff and exit_ >= cutoff:
            close_price = boundary_prices.get(t.symbol)
            if close_price is None:
                # No boundary candle for this symbol — fabricating
                # exit=entry would silently zero the trade's PnL. Pass the
                # trade through unclipped and surface the gap instead.
                unresolved.add(t.symbol)
                result.append(t)
                continue
            clip_exit = (boundary_times or {}).get(t.symbol, fallback_exit)
            result.append(_make_clipped(t, close_price, clip_exit))
        else:
            result.append(t)

    if unresolved:
        logger.warning(
            "Boundary clip: %d symbol(s) have no candle at %s — "
            "%s passed through unclipped (PnL preserved, not zeroed)",
            len(unresolved), boundary_date, sorted(unresolved),
        )

    return result


def clip_is_trades(
    trades: list, is_end: str, timeframe: str, db: Session,
) -> list:
    """Clip trades straddling the IS end boundary.

    Fast-paths if no trades straddle. Looks up close prices only
    for symbols that actually need clipping.
    """
    if not trades or not is_end:
        return trades

    boundary_dt = datetime.strptime(is_end, "%Y-%m-%d")
    cutoff = boundary_dt + timedelta(days=1)

    straddling: set[str] = set()
    for t in trades:
        if _to_dt(t.entry_time) < cutoff and _to_dt(t.exit_time) >= cutoff:
            straddling.add(t.symbol)

    if not straddling:
        return trades

    try:
        prices, times = get_boundary_close_prices(straddling, is_end, timeframe, db)
    except Exception as e:
        logger.warning("Boundary clip skipped (OHLCV unavailable): %s", e)
        return trades
    return clip_trades_at_boundary(trades, is_end, prices, times)


def clip_oos_trades(
    trades: list, oos_end: str, timeframe: str, db: Session,
) -> list:
    """Clip trades straddling the OOS end boundary."""
    return clip_is_trades(trades, oos_end, timeframe, db)


def clip_df_at_boundary(
    df: "pd.DataFrame",
    boundary_date: str,
    timeframe: str,
    db: Session,
    return_details: bool = False,
) -> "pd.DataFrame | tuple[pd.DataFrame, list[dict]]":
    """Clip DataFrame rows that straddle *boundary_date*.

    Works on the standardized DataFrame produced by trades_from_db().
    Adjusts Exit Time, Exit Price, Net Profit for straddling rows.

    When return_details=True, returns (df, clip_details) where clip_details
    is a list of dicts describing each clipped trade (asset, original/clipped
    PnL, prices, etc.).
    """
    import re
    import pandas as pd

    _empty_details: list[dict] = []

    if df.empty:
        return (df, _empty_details) if return_details else df

    boundary = pd.Timestamp(boundary_date)
    cutoff = boundary + pd.Timedelta(days=1)
    entry_col = pd.to_datetime(df["Entry Time"])
    exit_col = pd.to_datetime(df["Exit Time"])
    mask = (entry_col < cutoff) & (exit_col >= cutoff)

    if not mask.any():
        return (df, _empty_details) if return_details else df

    # Extract base symbols (strip _Long/_Short suffix)
    symbols = set()
    for asset in df.loc[mask, "Asset"].unique():
        symbols.add(re.sub(r"_(Long|Short)$", "", asset))

    prices, candle_times = get_boundary_close_prices(symbols, boundary_date, timeframe, db)
    if not prices:
        return (df, _empty_details) if return_details else df

    df = df.copy()
    fallback_exit = boundary.replace(hour=23, minute=59, second=59)
    clip_details: list[dict] = []

    for idx in df[mask].index:
        row = df.loc[idx]
        base_sym = re.sub(r"_(Long|Short)$", "", row["Asset"])
        if base_sym not in prices:
            continue

        bnd_close = prices[base_sym]
        entry_price = float(row["Entry Price"])
        exit_price = float(row["Exit Price"]) if row["Exit Price"] else entry_price
        original_pnl = float(row["Net Profit"])
        commission = abs(float(row.get("Commission", 0)))

        # Derive quantity from original PnL
        price_diff = exit_price - entry_price
        if abs(price_diff) > 1e-10:
            side = row.get("Side", "Long")
            if side == "Short":
                qty = (original_pnl + commission) / (entry_price - exit_price)
            else:
                qty = (original_pnl + commission) / price_diff
            qty = abs(qty)
        else:
            qty = 0.0

        # Recalculate PnL at boundary close
        side = row.get("Side", "Long")
        if side == "Short":
            gross = (entry_price - bnd_close) * qty
        else:
            gross = (bnd_close - entry_price) * qty

        # Scale commission
        denom = abs(entry_price) + abs(exit_price)
        if denom > 0 and commission > 0:
            scaled_comm = commission * (abs(entry_price) + abs(bnd_close)) / denom
        else:
            scaled_comm = commission

        clipped_pnl = gross - scaled_comm

        clip_exit = candle_times.get(base_sym, fallback_exit)
        df.at[idx, "Exit Time"] = clip_exit
        df.at[idx, "Exit Price"] = bnd_close
        df.at[idx, "Net Profit"] = clipped_pnl
        if "Exit Reason" in df.columns:
            df.at[idx, "Exit Reason"] = "boundary_clip"

        if return_details:
            clip_details.append({
                "asset": row["Asset"],
                "entry_time": str(row["Entry Time"]),
                "original_exit_time": str(row["Exit Time"]),
                "clipped_exit_time": str(clip_exit),
                "original_pnl": round(original_pnl, 2),
                "clipped_pnl": round(clipped_pnl, 2),
                "entry_price": round(entry_price, 4),
                "boundary_close_price": round(bnd_close, 4),
                "original_exit_price": round(exit_price, 4),
                "status": "clipped",
            })

    return (df, clip_details) if return_details else df
