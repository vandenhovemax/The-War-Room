"""Per-asset detail routes — list assets with summary, individual trades, correlation stats."""
from __future__ import annotations

import json
from collections import defaultdict

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.analysis.correlation import correlation_stats, get_correlation_matrix
from backend.db.database import get_db
from backend.db.models import BacktestRun, BacktestTrade, BatchBacktestRun, Portfolio, Strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _get_portfolio(pid: int, db: Session) -> Portfolio:
    p = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not p:
        raise HTTPException(404, "Portfolio not found")
    return p


def _parse_batch_ids(p: Portfolio) -> list[int]:
    """Safely extract batch_ids from portfolio source_strategies JSON."""
    settings = json.loads(p.source_strategies) if p.source_strategies else {}
    return settings.get("batch_ids") or (
        [settings["batch_id"]] if settings.get("batch_id") else []
    )


def _asset_col_name(a) -> str:
    """Build asset column name from a PortfolioAsset."""
    if a.trade_side in ("Long", "Short"):
        sfx = f"_{a.trade_side}"
        return a.symbol if a.symbol.endswith(sfx) else f"{a.symbol}{sfx}"
    return a.symbol


def _load_trades(p: Portfolio, db: Session) -> pd.DataFrame:
    """Load standardized trades for a portfolio (with stored costs/risk applied).

    Delegates to portfolio_analysis._load_portfolio_trades which handles
    batch-scoped loading, extra cost application, and risk profile scaling.
    clip_is=False here because callers want ALL trades (dashboard, robustness).
    """
    from backend.api.routes.portfolio_analysis import _load_portfolio_trades
    return _load_portfolio_trades(p, db, clip_is=False)


@router.get("/{portfolio_id}/assets")
def portfolio_assets(portfolio_id: int, db: Session = Depends(get_db)):
    """List all assets with summary metrics and strategy/direction info."""
    p = _get_portfolio(portfolio_id, db)
    df = _load_trades(p, db)
    if df.empty:
        return {"assets": []}

    # Build strategy/config/direction lookup — keys must match suffixed asset names
    # Strategy = actual name from Strategy table (via batch.strategy_id)
    # Config = config_group or cleaned batch name (without direction suffix)
    strat_map: dict[str, str] = {}
    config_map: dict[str, list[str]] = defaultdict(list)
    dir_map: dict[str, str] = {}

    # Build batch_id → (strategy_name, config_name) from DB lookups
    batch_ids = _parse_batch_ids(p)
    batch_strat_prefix: dict[int, str] = {}
    batch_config_suffix: dict[int, str] = {}
    if batch_ids:
        batches = db.query(BatchBacktestRun).filter(
            BatchBacktestRun.id.in_(batch_ids)
        ).all()
        for batch in batches:
            # Strategy name: from Strategy table (actual strategy, not batch name)
            strat = db.query(Strategy).filter(Strategy.id == batch.strategy_id).first()
            batch_strat_prefix[batch.id] = strat.name if strat else "Unknown"

            # Config name: prefer config_group (clean, no direction suffix)
            cfg_group = getattr(batch, "config_group", None) or ""
            if cfg_group and cfg_group != "Default":
                batch_config_suffix[batch.id] = cfg_group
            else:
                # Fallback: use batch name, strip trailing direction suffix
                bn = batch.name or ""
                for dir_sfx in (" — Long", " — Short"):
                    if bn.endswith(dir_sfx):
                        bn = bn[:-len(dir_sfx)]
                        break
                batch_config_suffix[batch.id] = bn

    # Derive config from each PortfolioAsset's batch_id (not from batch runs)
    for a in p.assets:
        if not a.is_active:
            continue
        # Derive config: prefer stored config_name, else batch's config
        cfg = a.config_name or ""
        if not cfg and a.batch_id and a.batch_id in batch_config_suffix:
            cfg = batch_config_suffix[a.batch_id]

        if a.trade_side in ("Long", "Short"):
            key = f"{a.symbol}_{a.trade_side}" if not a.symbol.endswith(f"_{a.trade_side}") else a.symbol
            # Use batch-derived strategy name (before " — "), fall back to stored name
            strat_name = batch_strat_prefix.get(a.batch_id, "") if a.batch_id else ""
            if not strat_name:
                raw = a.strategy_name or "Unknown"
                strat_name = raw.split(" — ", 1)[0] if " — " in raw else raw
            strat_map[key] = strat_name
            dir_map[key] = a.trade_side
            if cfg and cfg not in config_map[key]:
                config_map[key].append(cfg)
        else:
            for side in ("Long", "Short"):
                key = f"{a.symbol}_{side}"
                strat_name = batch_strat_prefix.get(a.batch_id, "") if a.batch_id else ""
                if not strat_name:
                    raw = a.strategy_name or "Unknown"
                    strat_name = raw.split(" — ", 1)[0] if " — " in raw else raw
                strat_map[key] = strat_name
                dir_map[key] = side
                if cfg and cfg not in config_map[key]:
                    config_map[key].append(cfg)

    # Detect trades straddling IS/OOS and OOS-end boundaries (informational)
    has_wfo_boundary = p.optimization_end_date is not None
    straddle_info: dict[str, dict] = {}
    if has_wfo_boundary and not df.empty and "Entry Time" in df.columns and "Exit Time" in df.columns:
        df["Entry Time"] = pd.to_datetime(df["Entry Time"])
        df["Exit Time"] = pd.to_datetime(df["Exit Time"])
        is_end_ts = pd.Timestamp(p.optimization_end_date)
        oos_end_ts = pd.Timestamp(p.created_at) if p.created_at else None

        for asset_name in df["Asset"].unique():
            adf_s = df[df["Asset"] == asset_name]
            # IS-boundary straddles: entered in IS, exited after IS end
            is_mask = (
                (adf_s["Entry Time"].dt.date <= is_end_ts.date())
                & (adf_s["Exit Time"].dt.date > is_end_ts.date())
            )
            is_count = int(is_mask.sum())
            # OOS-boundary straddles: entered in OOS, exited after OOS end
            oos_count = 0
            if oos_end_ts is not None:
                oos_mask = (
                    (adf_s["Entry Time"].dt.date > is_end_ts.date())
                    & (adf_s["Entry Time"].dt.date <= oos_end_ts.date())
                    & (adf_s["Exit Time"].dt.date > oos_end_ts.date())
                )
                oos_count = int(oos_mask.sum())
            if is_count > 0 or oos_count > 0:
                straddle_info[asset_name] = {
                    "is_boundary_straddles": is_count,
                    "oos_boundary_straddles": oos_count,
                }

    # Load structural metrics from BacktestRun.metrics per symbol
    structural_map: dict[str, dict] = {}
    _struct_keys = ("regime", "trend_grade", "meanrev_grade", "Hurst",
                    "avg_consec_bull", "avg_consec_bear", "avg_consec_all")
    # Collect Bybit rejection counts from run metrics
    bybit_rejections: dict[str, int] = {}  # symbol → total rejected trades
    if batch_ids:
        runs = db.query(BacktestRun).filter(
            BacktestRun.batch_id.in_(batch_ids),
            BacktestRun.status == "completed",
        ).all()
        for r in runs:
            if r.metrics:
                try:
                    m = json.loads(r.metrics)
                    structural_map[r.symbol] = {k: m.get(k) for k in _struct_keys if m.get(k) is not None}
                    rej = m.get("rejected_below_min_qty", 0)
                    if rej:
                        bybit_rejections[r.symbol] = bybit_rejections.get(r.symbol, 0) + rej
                except Exception as _exc:
                    logger.debug("swallowed: %s", _exc)

    result = []
    for asset in sorted(df["Asset"].unique()):
        adf = df[df["Asset"] == asset]
        wins = adf[adf["Net Profit"] > 0]
        losses = adf[adf["Net Profit"] <= 0]
        gp = float(wins["Net Profit"].sum()) if not wins.empty else 0
        gl = float(abs(losses["Net Profit"].sum())) if not losses.empty else 0
        eq = adf["Net Profit"].cumsum()
        max_dd = float((eq - eq.cummax()).min()) if not eq.empty else 0
        straddle = straddle_info.get(asset, {})

        # Map asset name (e.g. "BTCUSDT_Long") back to base symbol for structural lookup
        base_sym = asset
        if asset.endswith("_Long"):
            base_sym = asset[:-5]
        elif asset.endswith("_Short"):
            base_sym = asset[:-6]

        row: dict = {
            "asset": asset,
            "strategy_name": strat_map.get(asset, "Unknown"),
            "config_name": " / ".join(config_map.get(asset, [])) or "",
            "direction": dir_map.get(asset, "Both"),
            "net_profit": round(float(adf["Net Profit"].sum()), 2),
            "profit_factor": round(min(gp / gl, 10.0), 2) if gl > 0 else None,
            "win_rate": round(len(wins) / len(adf) * 100, 1) if len(adf) > 0 else 0,
            "trades": len(adf),
            "avg_trade": round(float(adf["Net Profit"].mean()), 2),
            "max_drawdown": round(max_dd, 2),
            "is_boundary_straddles": straddle.get("is_boundary_straddles", 0),
            "oos_boundary_straddles": straddle.get("oos_boundary_straddles", 0),
            "has_boundary_straddles": bool(straddle),
        }
        sm = structural_map.get(base_sym, {})
        if sm:
            row["structural"] = sm
        result.append(row)
    # Include stored cost settings so frontend can display them
    settings = json.loads(p.source_strategies) if p.source_strategies else {}
    cost_settings = {
        "extra_slippage_pct": float(settings.get("extra_slippage_pct", 0)),
        "extra_commission_pct": float(settings.get("extra_commission_pct", 0)),
        "funding_daily_pct": float(settings.get("funding_daily_pct", 0)),
        "spread_pct": float(settings.get("spread_pct", 0)),
    }
    has_any_cost = any(v > 0 for v in cost_settings.values())
    return {
        "assets": result,
        "has_wfo_boundary": has_wfo_boundary,
        "cost_settings": cost_settings if has_any_cost else None,
        "bybit_rejections": bybit_rejections if bybit_rejections else None,
    }


@router.get("/{portfolio_id}/assets/{asset_name}/trades")
def portfolio_asset_trades(
    portfolio_id: int, asset_name: str, db: Session = Depends(get_db),
):
    """Individual trade list for one asset within a portfolio, with sub-fills."""
    p = _get_portfolio(portfolio_id, db)

    # Find the specific batch_id for this asset from PortfolioAsset
    asset_batch_id = None
    for a in p.assets:
        col = _asset_col_name(a)
        # Match direct name or both-side variants
        if col == asset_name:
            asset_batch_id = a.batch_id
            break
        if a.trade_side not in ("Long", "Short"):
            if f"{a.symbol}_Long" == asset_name or f"{a.symbol}_Short" == asset_name:
                asset_batch_id = a.batch_id
                break

    batch_ids = _parse_batch_ids(p)
    if not batch_ids:
        return {"trades": []}

    # Load only from the asset's source batch (not all batches)
    if asset_batch_id is not None:
        runs = (
            db.query(BacktestRun)
            .filter(BacktestRun.batch_id == asset_batch_id, BacktestRun.status == "completed")
            .all()
        )
    else:
        # Legacy fallback
        runs = (
            db.query(BacktestRun)
            .filter(BacktestRun.batch_id.in_(batch_ids), BacktestRun.status == "completed")
            .all()
        )
    raw_trades = db.query(BacktestTrade).filter(
        BacktestTrade.run_id.in_([r.id for r in runs])
    ).all()

    # Apply stored extra costs (slippage, commission, funding, spread)
    # These are post-hoc adjustments saved in portfolio settings — not in DB trades.
    settings = json.loads(p.source_strategies) if p.source_strategies else {}
    _slip = float(settings.get("extra_slippage_pct", 0))
    _comm = float(settings.get("extra_commission_pct", 0))
    _fund = float(settings.get("funding_daily_pct", 0))
    _sprd = float(settings.get("spread_pct", 0))
    if _slip > 0 or _comm > 0 or _fund > 0 or _sprd > 0:
        from backend.api.routes.analysis import _apply_stress_slippage
        raw_trades = _apply_stress_slippage(
            raw_trades, _slip, db,
            extra_commission_pct=_comm,
            funding_daily_pct=_fund,
            spread_pct=_sprd,
        )

    # Apply stored risk profile scaling (if optimizer used a custom risk profile)
    rp = settings.get("risk_profile", {})
    if rp and rp.get("override_enabled"):
        from types import SimpleNamespace
        from backend.api.routes.analysis import _apply_risk_scaling
        batch_ids_all = settings.get("batch_ids") or (
            [settings["batch_id"]] if settings.get("batch_id") else []
        )
        run_batch_map = {
            r.id: (asset_batch_id if asset_batch_id else (batch_ids_all[0] if batch_ids_all else 0))
            for r in runs
        }
        # Resolve date_to: stored setting → portfolio optimization_end_date fallback
        _date_to = settings.get("date_to")
        if not _date_to and hasattr(p, 'optimization_end_date') and p.optimization_end_date:
            _date_to = (
                p.optimization_end_date.strftime("%Y-%m-%d")
                if hasattr(p.optimization_end_date, "strftime")
                else str(p.optimization_end_date)[:10]
            )
        mock_req = SimpleNamespace(
            risk_profile_mode=rp.get("mode", "fixed"),
            risk_profile_value=float(rp.get("initial_risk_usd", 0)),
            risk_profile_initial_capital=float(rp.get("initial_capital", 10000)),
            risk_profile_multiplier=float(rp.get("multiplier", 2.0)),
            risk_profile_max_streak=int(rp.get("max_streak", 3)),
            risk_profile_kelly_n=int(rp.get("kelly_n", 2)),
            risk_profile_kelly_warmup=int(rp.get("kelly_warmup", 20)),
            risk_profile_martingale_compound=rp.get("martingale_compound", "off"),
            risk_profile_martingale_scope=rp.get("martingale_scope", "asset"),
            risk_profile_martingale_count_mode=rp.get("martingale_count_mode", "full_position"),
            risk_profile_martingale_oos_reset=rp.get("martingale_oos_reset", True),
            risk_profile_martingale_start_after=int(rp.get("martingale_start_after", 0)),
            risk_profile_anti_martingale_reset_after=int(rp.get("anti_martingale_reset_after", 0)),
            risk_profile_sizing_master=rp.get("sizing_master", "portfolio"),
            risk_profile_batch_configs=json.dumps(rp["batch_configs"]) if rp.get("batch_configs") else None,
            batch_ids=batch_ids_all,
            batch_id=batch_ids_all[0] if batch_ids_all else None,
            date_to=_date_to,
        )
        _is_cross = settings.get("is_cross_strat", False)
        raw_trades = _apply_risk_scaling(
            raw_trades, mock_req, db, include_side=_is_cross,
            run_batch_map=run_batch_map,
        )

    # Build run_id → (strategy_name, config_name) lookup from PortfolioAsset
    _asset_by_batch = {a.batch_id: a for a in p.assets if a.symbol and a.batch_id}
    run_meta: dict[int, tuple[str, str]] = {}
    for r in runs:
        asset = _asset_by_batch.get(r.batch_id)
        if asset:
            run_meta[r.id] = (asset.strategy_name or "", asset.config_name or "")
        else:
            run_meta[r.id] = (r.strategy.name if r.strategy else "", "")

    # Group by (symbol, side, position_id) to prevent cross-symbol collisions
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for t in raw_trades:
        # Build asset key with side suffix
        side = t.side.capitalize() if t.side else "Unknown"
        asset_key = f"{t.symbol}_{side}" if side in ("Long", "Short") and not t.symbol.endswith(f"_{side}") else t.symbol
        if asset_key != asset_name:
            continue
        pid = getattr(t, "position_id", None) or f"_solo_{t.id}"
        key = f"{t.symbol}_{side}_{pid}"
        groups[key].append(t)

    if not groups:
        return {"trades": []}

    # Sort positions by earliest exit time
    sorted_positions = sorted(
        groups.values(),
        key=lambda fills: min((f.exit_time for f in fills if f.exit_time), default=None) or pd.Timestamp.min,
    )

    # Pre-compute stress cost rates for per-trade cost column
    _total_rt_pct = _slip + _comm + (_sprd / 2.0)
    _has_stress = _total_rt_pct > 0 or _fund > 0

    trades_list = []
    total_stress_cost = 0.0
    for i, fills in enumerate(sorted_positions, 1):
        first = fills[0]
        net_profit = sum(float(f.net_profit) if f.net_profit else 0.0 for f in fills)
        commission = sum(float(f.commission) if f.commission else 0.0 for f in fills)
        mae = sum(float(f.mae) if f.mae else 0.0 for f in fills)
        mfe = sum(float(f.mfe) if f.mfe else 0.0 for f in fills)
        exit_times = [f.exit_time for f in fills if f.exit_time]
        exit_time = max(exit_times) if exit_times else None
        reasons = [f.exit_reason for f in fills if f.exit_reason]
        exit_reason = "+".join(reasons) if reasons else ""
        # Weighted average exit price
        exit_prices = [(float(f.exit_price), abs(float(f.net_profit)) if f.net_profit else 1.0) for f in fills if f.exit_price]
        total_w = sum(w for _, w in exit_prices)
        avg_exit = sum(p * w for p, w in exit_prices) / total_w if total_w and exit_prices else None

        # Compute stress cost per fill (same formula as _apply_stress_slippage)
        stress_cost = 0.0
        if _has_stress:
            for f in fills:
                f_notional = abs(float(f.entry_price or 0) * float(f.quantity or 0))
                if _total_rt_pct > 0:
                    stress_cost += f_notional * (_total_rt_pct / 100) * 2
                if _fund > 0 and f.entry_time and f.exit_time:
                    hold_secs = (f.exit_time - f.entry_time).total_seconds()
                    stress_cost += f_notional * (_fund / 100) * max(hold_secs / 86400, 0)
        total_stress_cost += stress_cost

        # Build sub-fills array
        sub_fills = []
        if len(fills) > 1:
            for f in sorted(fills, key=lambda x: x.exit_time or pd.Timestamp.min):
                sub_fills.append({
                    "exit_time": str(f.exit_time) if f.exit_time else None,
                    "exit_price": round(float(f.exit_price), 6) if f.exit_price else None,
                    "qty": round(float(f.quantity), 4) if f.quantity else None,
                    "net_profit": round(float(f.net_profit), 2) if f.net_profit else 0,
                    "exit_reason": f.exit_reason or "",
                })

        strat_name, conf_name = run_meta.get(first.run_id, ("", ""))
        trades_list.append({
            "num": i,
            "side": first.side.capitalize() if first.side else "",
            "entry_time": str(first.entry_time) if first.entry_time else None,
            "exit_time": str(exit_time) if exit_time else None,
            "entry_price": round(float(first.entry_price), 4) if first.entry_price else None,
            "exit_price": round(avg_exit, 6) if avg_exit else None,
            "net_profit": round(net_profit, 2),
            "mae": round(mae, 2) if mae else None,
            "mfe": round(mfe, 2) if mfe else None,
            "exit_reason": exit_reason,
            "commission": round(commission, 4) if commission else None,
            "stress_cost": round(stress_cost, 2) if stress_cost > 0 else None,
            "fills": sub_fills,
            "strategy_name": strat_name,
            "config_name": conf_name,
            "size_pct": getattr(first, '_size_pct', 100.0),
        })
    return {
        "trades": trades_list,
        "cost_summary": {
            "extra_slippage_pct": _slip,
            "extra_commission_pct": _comm,
            "funding_daily_pct": _fund,
            "spread_pct": _sprd,
            "total_stress_cost": round(total_stress_cost, 2),
        },
    }


@router.get("/{portfolio_id}/correlation-stats")
def portfolio_correlation_stats(portfolio_id: int, db: Session = Depends(get_db)):
    """Correlation distribution stats for portfolio's selected assets."""
    p = _get_portfolio(portfolio_id, db)
    df = _load_trades(p, db)
    if df.empty or len(df["Asset"].unique()) < 2:
        return {"stats": {}, "n_pairs": 0}

    # Build daily PnL directly from the per-batch-filtered df to prevent
    # cross-batch contamination (previously loaded ALL trades from all batches).
    asset_names = list(df["Asset"].unique())
    if len(asset_names) < 2:
        return {"stats": {}, "n_pairs": 0}

    temp = df[["Exit Time", "Asset", "Net Profit"]].copy()
    temp["date"] = pd.to_datetime(temp["Exit Time"]).dt.floor("D")
    daily_pnl = temp.pivot_table(
        index="date", columns="Asset", values="Net Profit", aggfunc="sum",
    ).fillna(0)
    if not daily_pnl.empty:
        full_range = pd.date_range(
            start=daily_pnl.index.min(), end=daily_pnl.index.max(), freq="D",
        )
        daily_pnl = daily_pnl.reindex(full_range).fillna(0)

    corr = get_correlation_matrix(daily_pnl, "standard")
    stats = correlation_stats(corr)
    n_pairs = len(asset_names) * (len(asset_names) - 1) // 2
    return {"stats": stats, "n_pairs": n_pairs}


def _get_bybit_specs() -> dict:
    """Return cached Bybit instrument specs (reuses assets.py cache)."""
    from backend.api.routes.assets import (
        _instrument_specs_cache,
        _instrument_specs_ts,
    )
    import time

    # If cache is fresh, use it directly
    if _instrument_specs_cache and (time.time() - _instrument_specs_ts) < 3600:
        return _instrument_specs_cache

    # Otherwise trigger a fetch via the existing endpoint logic
    from backend.api.routes.assets import get_instrument_specs
    result = get_instrument_specs()
    return result.get("specs", {})


@router.get("/{portfolio_id}/bybit-size-check")
def portfolio_bybit_size_check(
    portfolio_id: int,
    risk_amount: float = Query(..., gt=0, description="Dollar risk per trade"),
    db: Session = Depends(get_db),
):
    """Check portfolio trades against Bybit size limits at a given risk level.

    For each trade with an SL price, computes the required position size at the
    target risk and checks it against Bybit min/max qty limits.
    """
    p = _get_portfolio(portfolio_id, db)
    batch_ids = _parse_batch_ids(p)
    if not batch_ids:
        return {"results": {}, "totals": {"trades_checked": 0}}

    # Load all trades for this portfolio's batches
    runs = (
        db.query(BacktestRun)
        .filter(BacktestRun.batch_id.in_(batch_ids), BacktestRun.status == "completed")
        .all()
    )
    if not runs:
        return {"results": {}, "totals": {"trades_checked": 0}}

    # Build run_id -> batch risk (from the batch's risk_per_trade setting)
    run_batch_map: dict[int, int] = {r.id: r.batch_id for r in runs}
    batch_risk_map: dict[int, float] = {}
    batches = db.query(BatchBacktestRun).filter(
        BatchBacktestRun.id.in_(batch_ids)
    ).all()
    for b in batches:
        try:
            cfg = json.loads(b.config) if b.config else {}
            batch_risk_map[b.id] = float(cfg.get("risk_per_trade", 100))
        except Exception:
            batch_risk_map[b.id] = 100.0

    raw_trades = (
        db.query(BacktestTrade)
        .filter(BacktestTrade.run_id.in_([r.id for r in runs]))
        .all()
    )

    # Fetch Bybit specs
    specs = _get_bybit_specs()
    if not specs:
        raise HTTPException(503, "Could not fetch Bybit instrument specs")

    # Per-symbol aggregation
    sym_stats: dict[str, dict] = {}
    for t in raw_trades:
        if not t.sl_price or not t.entry_price or not t.symbol:
            continue
        ep = float(t.entry_price)
        sl = float(t.sl_price)
        sl_distance = abs(ep - sl)
        if sl_distance < 1e-12:
            continue

        # Scale quantity to target risk: original_qty * (target_risk / batch_risk)
        batch_id = run_batch_map.get(t.run_id)
        batch_risk = batch_risk_map.get(batch_id, 100.0) if batch_id else 100.0
        factor = risk_amount / batch_risk if batch_risk > 0 else 1.0
        original_qty = float(t.quantity) if t.quantity else 0.0
        required_qty = original_qty * factor

        sym = t.symbol
        spec = specs.get(sym)
        if not spec:
            continue

        max_qty = spec.get("max_qty", 1e18)
        min_qty = spec.get("min_qty", 0)

        if sym not in sym_stats:
            sym_stats[sym] = {
                "trades_total": 0,
                "trades_exceed_max": 0,
                "trades_below_min": 0,
                "max_qty_needed": 0.0,
                "bybit_max_qty": max_qty,
                "bybit_min_qty": min_qty,
            }

        s = sym_stats[sym]
        s["trades_total"] += 1
        s["max_qty_needed"] = max(s["max_qty_needed"], required_qty)
        if required_qty > max_qty:
            s["trades_exceed_max"] += 1
        if required_qty < min_qty:
            s["trades_below_min"] += 1

    # Round floats for JSON
    for sym, s in sym_stats.items():
        s["max_qty_needed"] = round(s["max_qty_needed"], 8)

    # Totals
    total_checked = sum(s["trades_total"] for s in sym_stats.values())
    total_exceed = sum(s["trades_exceed_max"] for s in sym_stats.values())
    total_below = sum(s["trades_below_min"] for s in sym_stats.values())
    assets_with_issues = sum(
        1 for s in sym_stats.values()
        if s["trades_exceed_max"] > 0 or s["trades_below_min"] > 0
    )

    return {
        "results": sym_stats,
        "totals": {
            "trades_checked": total_checked,
            "trades_exceed_max": total_exceed,
            "trades_below_min": total_below,
            "assets_with_issues": assets_with_issues,
        },
    }
