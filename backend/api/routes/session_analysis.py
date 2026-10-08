"""Session Intelligence — time-of-day / day-of-week edge analysis."""
from __future__ import annotations

import statistics
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.db.database import get_db
from backend.db.models import BacktestRun, BacktestTrade, BatchBacktestRun

router = APIRouter()

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Session annotations for NYT hours
SESSION_LABELS: dict[int, str] = {
    0: "Asia", 1: "Asia", 2: "Asia", 3: "Asia", 4: "Asia",
    5: "Asia/London", 6: "London", 7: "London", 8: "London Pre-Market",
    9: "US Open", 10: "US Morning", 11: "US Morning",
    12: "US Midday", 13: "US Afternoon", 14: "US Afternoon",
    15: "US Afternoon", 16: "US Close", 17: "US Post-Market",
    18: "US Post-Market", 19: "Evening", 20: "Evening",
    21: "Asia Pre-Open", 22: "Asia Pre-Open", 23: "Asia Pre-Open",
}


def _cell_metrics(trades: list[dict], min_trades: int) -> dict:
    """Compute full metrics for a group of trades."""
    n = len(trades)
    if n == 0:
        return {"trade_count": 0, "is_confident": False}

    pnls = [t["net_profit"] for t in trades]
    total_pnl = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / n * 100 if n else 0
    avg_win = statistics.mean(wins) if wins else 0
    avg_loss = abs(statistics.mean(losses)) if losses else 0
    loss_rate = 100 - win_rate

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0)

    expectancy = (win_rate / 100 * avg_win) - (loss_rate / 100 * avg_loss) if n else 0
    payoff = avg_win / avg_loss if avg_loss > 0 else (999.0 if avg_win > 0 else 0)

    maes = [t["mae"] for t in trades if t["mae"] is not None]
    mfes = [t["mfe"] for t in trades if t["mfe"] is not None]
    avg_mae = statistics.mean(maes) if maes else 0
    avg_mfe = statistics.mean(mfes) if mfes else 0
    mfe_mae_ratio = avg_mfe / abs(avg_mae) if avg_mae != 0 else (999.0 if avg_mfe > 0 else 0)

    symbols = set(t["symbol"] for t in trades)
    symbol_count = len(symbols)
    # Per-symbol profitability
    sym_pnl: dict[str, float] = {}
    for t in trades:
        sym_pnl[t["symbol"]] = sym_pnl.get(t["symbol"], 0) + t["net_profit"]
    profitable_syms = sum(1 for v in sym_pnl.values() if v > 0)
    profitable_symbol_pct = profitable_syms / symbol_count * 100 if symbol_count > 0 else 0

    return {
        "trade_count": n,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / n, 2) if n else 0,
        "median_pnl": round(statistics.median(pnls), 2) if pnls else 0,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(min(pf, 999), 2),
        "expectancy": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": round(min(payoff, 999), 2),
        "best_trade": round(max(pnls), 2) if pnls else 0,
        "worst_trade": round(min(pnls), 2) if pnls else 0,
        "avg_mae": round(avg_mae, 2),
        "avg_mfe": round(avg_mfe, 2),
        "mfe_mae_ratio": round(min(mfe_mae_ratio, 999), 2),
        "symbol_count": symbol_count,
        "profitable_symbol_pct": round(profitable_symbol_pct, 1),
        "is_confident": n >= min_trades,
    }


@router.get("/session-intel/{batch_id}")
def session_intel(
    batch_id: int,
    start_date: str = Query(..., description="ISO date: only trades with entry_time >= this"),
    end_date: str = Query(..., description="ISO date: only trades with entry_time <= this"),
    granularity: Literal["hour", "half_hour"] = Query("hour"),
    group_by: Literal["entry", "exit"] = Query("entry"),
    side_filter: Literal["both", "long", "short"] = Query("both"),
    min_trades: int = Query(20, ge=1),
    db: Session = Depends(get_db),
):
    """Session intelligence — hour×day edge analysis for a batch."""
    # Validate batch exists
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "Batch not found")

    # Parse dates
    try:
        dt_start = datetime.strptime(start_date, "%Y-%m-%d")
        dt_end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(400, "Invalid date format — use YYYY-MM-DD")

    # Load all trades for this batch
    run_ids = [
        r.id for r in
        db.query(BacktestRun.id)
        .filter(BacktestRun.batch_id == batch_id, BacktestRun.status == "completed")
        .all()
    ]
    if not run_ids:
        raise HTTPException(400, "No completed runs in this batch")

    raw_trades = (
        db.query(BacktestTrade)
        .filter(BacktestTrade.run_id.in_(run_ids))
        .all()
    )

    # Pick timestamp field
    ts_field = "entry_time" if group_by == "entry" else "exit_time"

    # Filter and convert to dicts
    trades: list[dict] = []
    for t in raw_trades:
        ts = getattr(t, ts_field)
        if ts is None or ts < dt_start or ts > dt_end:
            continue
        if side_filter == "long" and t.side.lower() != "long":
            continue
        if side_filter == "short" and t.side.lower() != "short":
            continue
        trades.append({
            "net_profit": t.net_profit or 0,
            "symbol": t.symbol,
            "side": t.side,
            "mae": t.mae,
            "mfe": t.mfe,
            "hour": ts.hour,
            "minute": ts.minute,
            "weekday": ts.weekday(),  # 0=Mon
            "date": ts.date(),
        })

    if not trades:
        raise HTTPException(400, "No trades found in the specified date range")

    # Bucket function
    def bucket_key(t: dict) -> int:
        if granularity == "half_hour":
            return t["hour"] * 2 + (1 if t["minute"] >= 30 else 0)
        return t["hour"]

    n_time_slots = 48 if granularity == "half_hour" else 24

    # ---- Section 1: Hour×Day Grid ----
    grid: dict[tuple[int, int], list[dict]] = {}
    for slot in range(n_time_slots):
        for day in range(7):
            grid[(slot, day)] = []

    for t in trades:
        key = (bucket_key(t), t["weekday"])
        grid[key].append(t)

    heatmap = []
    for slot in range(n_time_slots):
        row = []
        for day in range(7):
            cell_trades = grid[(slot, day)]
            metrics = _cell_metrics(cell_trades, min_trades)
            row.append(metrics)
        heatmap.append(row)

    # ---- Section 2: Hour Profile ----
    hour_buckets: dict[int, list[dict]] = {s: [] for s in range(n_time_slots)}
    for t in trades:
        hour_buckets[bucket_key(t)].append(t)

    total_pnl_all = sum(t["net_profit"] for t in trades)
    total_trades_all = len(trades)

    hour_profile = []
    for slot in range(n_time_slots):
        bucket_trades = hour_buckets[slot]
        metrics = _cell_metrics(bucket_trades, min_trades)
        bucket_pnl = metrics.get("total_pnl", 0)
        metrics["pnl_contribution_pct"] = round(
            bucket_pnl / total_pnl_all * 100, 1
        ) if total_pnl_all != 0 else 0
        metrics["trade_share_pct"] = round(
            len(bucket_trades) / total_trades_all * 100, 1
        ) if total_trades_all else 0
        metrics["edge_density"] = round(
            bucket_pnl / len(bucket_trades), 2
        ) if bucket_trades else 0

        # Label
        if granularity == "half_hour":
            h, half = divmod(slot, 2)
            label = f"{h:02d}:{30 if half else '00'}"
        else:
            label = f"{slot:02d}:00"
        metrics["label"] = label
        metrics["session"] = SESSION_LABELS.get(slot if granularity == "hour" else slot // 2, "")
        hour_profile.append(metrics)

    # ---- Section 3: Day-of-Week Profile ----
    day_buckets: dict[int, list[dict]] = {d: [] for d in range(7)}
    for t in trades:
        day_buckets[t["weekday"]].append(t)

    # Count occurrences of each weekday in date range
    from datetime import timedelta
    weekday_counts: dict[int, int] = {d: 0 for d in range(7)}
    current = dt_start.date()
    end_d = dt_end.date()
    while current <= end_d:
        weekday_counts[current.weekday()] += 1
        current += timedelta(days=1)

    day_profile = []
    for day in range(7):
        day_trades = day_buckets[day]
        metrics = _cell_metrics(day_trades, min_trades)
        day_pnl = metrics.get("total_pnl", 0)
        metrics["pnl_contribution_pct"] = round(
            day_pnl / total_pnl_all * 100, 1
        ) if total_pnl_all != 0 else 0
        metrics["trade_share_pct"] = round(
            len(day_trades) / total_trades_all * 100, 1
        ) if total_trades_all else 0

        wk_count = weekday_counts[day]
        metrics["avg_trades_per_session"] = round(
            len(day_trades) / wk_count, 1
        ) if wk_count > 0 else 0

        # Consistency: % of individual occurrences of this weekday that were net profitable
        day_pnl_by_date: dict[date, float] = {}
        for t in day_trades:
            day_pnl_by_date[t["date"]] = day_pnl_by_date.get(t["date"], 0) + t["net_profit"]
        profitable_days = sum(1 for v in day_pnl_by_date.values() if v > 0)
        total_days_with_trades = len(day_pnl_by_date)
        metrics["consistency"] = round(
            profitable_days / total_days_with_trades * 100, 1
        ) if total_days_with_trades > 0 else 0

        pnl_values = list(day_pnl_by_date.values())
        metrics["best_day_pnl"] = round(max(pnl_values), 2) if pnl_values else 0
        metrics["worst_day_pnl"] = round(min(pnl_values), 2) if pnl_values else 0

        metrics["label"] = DAY_NAMES[day]
        metrics["is_weekend"] = day >= 5
        day_profile.append(metrics)

    # ---- Section 4: Summary ----
    summary = {
        "total_trades_in_range": total_trades_all,
        "total_pnl_in_range": round(total_pnl_all, 2),
        "date_range_days": (dt_end.date() - dt_start.date()).days + 1,
        "symbols_in_batch": len(set(t["symbol"] for t in trades)),
    }

    # Time slot labels for frontend
    time_labels = []
    for slot in range(n_time_slots):
        if granularity == "half_hour":
            h, half = divmod(slot, 2)
            time_labels.append(f"{h:02d}:{30 if half else '00'}")
        else:
            time_labels.append(f"{slot:02d}:00")

    return {
        "heatmap": heatmap,
        "hour_profile": hour_profile,
        "day_profile": day_profile,
        "summary": summary,
        "time_labels": time_labels,
        "day_labels": DAY_NAMES,
        "session_labels": {str(k): v for k, v in SESSION_LABELS.items()},
        "granularity": granularity,
    }
