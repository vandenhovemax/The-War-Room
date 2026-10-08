"""Trade scaler — instant math-based risk profile simulation.

Takes normalized batch trades (fixed $ risk) and applies different
position sizing strategies by scaling PnL proportionally.
No engine re-run needed — pure DataFrame math.

Also provides `scale_backtest_trades()` for the optimizer pipeline,
which operates directly on BacktestTrade ORM objects in-place.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from backend.constants import BE_ZONE

logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd


def scale_fixed(
    trades_df: pd.DataFrame,
    batch_risk: float,
    target_risk: float,
    initial_capital: float = 10_000.0,
) -> dict:
    """Constant linear scaling: multiply all PnL by (target / batch).

    Every trade gets the same scale factor regardless of equity.
    """
    if batch_risk <= 0 or trades_df.empty:
        return _empty_result(initial_capital)

    factor = target_risk / batch_risk
    df = trades_df.sort_values("Exit Time").copy()
    df["Net Profit"] = df["Net Profit"] * factor
    if "Commission" in df.columns:
        df["Commission"] = df["Commission"] * factor
    if "Drawdown" in df.columns:
        df["Drawdown"] = df["Drawdown"] * factor
    if "Run-up" in df.columns:
        df["Run-up"] = df["Run-up"] * factor

    equity = initial_capital + df["Net Profit"].cumsum()
    return {
        "trades": df,
        "equity_curve": [initial_capital] + equity.tolist(),
        "metrics": compute_profile_metrics(equity, df, initial_capital),
    }


def scale_pct_equity(
    trades_df: pd.DataFrame,
    batch_risk: float,
    initial_capital: float,
    risk_pct: float,
) -> dict:
    """Walk trades chronologically, risk a % of current equity per trade.

    Each trade's scale = (equity * risk_pct/100) / batch_risk.
    Equity compounds after each trade.
    """
    if batch_risk <= 0 or trades_df.empty or risk_pct <= 0:
        return _empty_result(initial_capital)

    df = trades_df.sort_values("Exit Time").copy()
    equity = initial_capital
    eq_curve = [equity]
    scaled_pnls: list[float] = []
    scaled_comms: list[float] = []
    scaled_dd: list[float] = []
    scaled_ru: list[float] = []

    for i in range(len(df)):
        risk_amount = equity * (risk_pct / 100)
        factor = risk_amount / batch_risk

        pnl = float(df.iloc[i]["Net Profit"]) * factor
        scaled_pnls.append(pnl)
        scaled_comms.append(float(df.iloc[i].get("Commission", 0)) * factor)
        scaled_dd.append(float(df.iloc[i].get("Drawdown", 0)) * factor)
        scaled_ru.append(float(df.iloc[i].get("Run-up", 0)) * factor)

        equity += pnl
        eq_curve.append(equity)

    df["Net Profit"] = scaled_pnls
    if "Commission" in df.columns:
        df["Commission"] = scaled_comms
    if "Drawdown" in df.columns:
        df["Drawdown"] = scaled_dd
    if "Run-up" in df.columns:
        df["Run-up"] = scaled_ru

    return {
        "trades": df,
        "equity_curve": eq_curve,
        "metrics": compute_profile_metrics(
            pd.Series(eq_curve[1:], index=df.index), df, initial_capital,
        ),
    }


def scale_martingale(
    trades_df: pd.DataFrame,
    batch_risk: float,
    target_risk: float,
    initial_capital: float,
    mart_type: str = "anti",
    multiplier: float = 2.0,
    max_streak: int = 3,
) -> dict:
    """Walk trades chronologically, apply martingale streak multiplier.

    scale = (target / batch) * (multiplier ^ min(streak, max_streak)).
    Anti-martingale: multiply after consecutive wins.
    Martingale: multiply after consecutive losses.
    Inverse variants: divide instead of multiply (shrink size on streaks).
    """
    if batch_risk <= 0 or trades_df.empty or target_risk <= 0:
        return _empty_result(initial_capital)

    df = trades_df.sort_values("Exit Time").copy()
    base_factor = target_risk / batch_risk
    equity = initial_capital
    eq_curve = [equity]

    consecutive_wins = 0
    consecutive_losses = 0
    scaled_pnls: list[float] = []
    scaled_comms: list[float] = []
    scaled_dd: list[float] = []
    scaled_ru: list[float] = []

    for i in range(len(df)):
        # Determine streak-based multiplier
        if mart_type in ("anti", "inv_anti"):
            streak = consecutive_wins
        else:
            streak = consecutive_losses
        capped = min(streak, max_streak)
        if capped > 0:
            if mart_type in ("inv_martingale", "inv_anti"):
                mart_mult = (1.0 / multiplier) ** capped
            else:
                mart_mult = multiplier ** capped
        else:
            mart_mult = 1.0
        factor = base_factor * mart_mult

        pnl = float(df.iloc[i]["Net Profit"]) * factor
        scaled_pnls.append(pnl)
        scaled_comms.append(float(df.iloc[i].get("Commission", 0)) * factor)
        scaled_dd.append(float(df.iloc[i].get("Drawdown", 0)) * factor)
        scaled_ru.append(float(df.iloc[i].get("Run-up", 0)) * factor)

        equity += pnl
        eq_curve.append(equity)

        # Update streaks based on original trade outcome
        # Match engine.py: breakeven trades (±BE_ZONE) don't affect streaks
        raw_pnl = float(df.iloc[i]["Net Profit"])
        if raw_pnl > BE_ZONE:
            consecutive_wins += 1
            consecutive_losses = 0
        elif raw_pnl < -BE_ZONE:
            consecutive_losses += 1
            consecutive_wins = 0

    df["Net Profit"] = scaled_pnls
    if "Commission" in df.columns:
        df["Commission"] = scaled_comms
    if "Drawdown" in df.columns:
        df["Drawdown"] = scaled_dd
    if "Run-up" in df.columns:
        df["Run-up"] = scaled_ru

    return {
        "trades": df,
        "equity_curve": eq_curve,
        "metrics": compute_profile_metrics(
            pd.Series(eq_curve[1:], index=df.index), df, initial_capital,
        ),
    }


def scale_kelly(
    trades_df: pd.DataFrame,
    batch_risk: float,
    initial_capital: float,
    kelly_fraction: float = 1.0,
) -> dict:
    """Compute Kelly criterion from trade history, apply fractional Kelly.

    Kelly: f* = (p * b - q) / b
    where p = win_rate, q = 1-p, b = avg_win / avg_loss.
    Fractional Kelly: risk = f* * kelly_fraction * equity per trade.
    """
    if batch_risk <= 0 or trades_df.empty:
        return _empty_result(initial_capital)

    df = trades_df.sort_values("Exit Time").copy()
    pnls = df["Net Profit"].values

    # Compute Kelly from the full trade set
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    if len(wins) == 0 or len(losses) == 0:
        # Edge case: all wins or all losses — Kelly undefined
        return scale_fixed(trades_df, batch_risk, batch_risk, initial_capital)

    p = len(wins) / len(pnls)
    q = 1.0 - p
    avg_win = float(np.mean(wins))
    avg_loss = float(np.mean(np.abs(losses)))

    if avg_loss <= 0:
        return scale_fixed(trades_df, batch_risk, batch_risk, initial_capital)

    b = avg_win / avg_loss
    kelly_pct = (p * b - q) / b  # fraction of equity to risk
    kelly_pct = max(kelly_pct, 0.0)  # never negative
    kelly_pct *= kelly_fraction  # apply fractional Kelly

    # Now walk trades with Kelly-sized risk
    equity = initial_capital
    eq_curve = [equity]
    scaled_pnls: list[float] = []
    scaled_comms: list[float] = []
    scaled_dd: list[float] = []
    scaled_ru: list[float] = []

    for i in range(len(df)):
        risk_amount = equity * kelly_pct
        factor = risk_amount / batch_risk if batch_risk > 0 else 1.0

        pnl = float(df.iloc[i]["Net Profit"]) * factor
        scaled_pnls.append(pnl)
        scaled_comms.append(float(df.iloc[i].get("Commission", 0)) * factor)
        scaled_dd.append(float(df.iloc[i].get("Drawdown", 0)) * factor)
        scaled_ru.append(float(df.iloc[i].get("Run-up", 0)) * factor)

        equity += pnl
        eq_curve.append(equity)

    df["Net Profit"] = scaled_pnls
    if "Commission" in df.columns:
        df["Commission"] = scaled_comms
    if "Drawdown" in df.columns:
        df["Drawdown"] = scaled_dd
    if "Run-up" in df.columns:
        df["Run-up"] = scaled_ru

    metrics = compute_profile_metrics(
        pd.Series(eq_curve[1:], index=df.index), df, initial_capital,
    )
    metrics["kelly_pct"] = round(kelly_pct * 100, 2)
    metrics["kelly_fraction_used"] = kelly_fraction

    return {
        "trades": df,
        "equity_curve": eq_curve,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Shared metrics computation
# ---------------------------------------------------------------------------

def compute_profile_metrics(
    equity_series: pd.Series,
    trades_df: pd.DataFrame,
    initial_capital: float,
) -> dict:
    """Compute rich KPI set from scaled results."""
    pnls = trades_df["Net Profit"].values
    total = len(pnls)
    if total == 0:
        return {}

    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    n_wins = len(wins)
    n_losses = len(losses)

    net_profit = float(np.sum(pnls))
    gross_profit = float(np.sum(wins)) if n_wins > 0 else 0.0
    gross_loss = float(np.sum(np.abs(losses))) if n_losses > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 10.0
    win_rate = (n_wins / total) * 100 if total > 0 else 0.0

    # Equity curve stats
    eq = equity_series.values if hasattr(equity_series, "values") else np.array(equity_series)
    peak = np.maximum.accumulate(eq)
    drawdowns = eq - peak
    max_dd_dollar = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0
    max_dd_pct = (max_dd_dollar / float(np.max(peak)) * 100) if np.max(peak) > 0 else 0.0

    roi_pct = (net_profit / initial_capital * 100) if initial_capital > 0 else 0.0

    # Daily returns for Sharpe/Sortino
    if "Exit Time" in trades_df.columns:
        daily_pnl = trades_df.groupby(
            pd.to_datetime(trades_df["Exit Time"]).dt.date
        )["Net Profit"].sum()
    else:
        daily_pnl = pd.Series(pnls)

    daily_mean = float(daily_pnl.mean()) if len(daily_pnl) > 0 else 0.0
    daily_std = float(daily_pnl.std()) if len(daily_pnl) > 1 else 1.0
    downside = daily_pnl[daily_pnl < 0]
    downside_std = float(downside.std()) if len(downside) > 1 else 1.0

    sharpe = (daily_mean / daily_std * np.sqrt(365)) if daily_std > 0 else 0.0
    sortino = (daily_mean / downside_std * np.sqrt(365)) if downside_std > 0 else 0.0

    # Calmar = annual return / max DD
    trading_days = len(daily_pnl)
    annual_return = (net_profit / initial_capital) * (365 / max(trading_days, 1))
    calmar = (annual_return / abs(max_dd_pct / 100)) if max_dd_pct != 0 else 0.0

    recovery_factor = (net_profit / abs(max_dd_dollar)) if max_dd_dollar != 0 else 0.0

    avg_trade = net_profit / total if total > 0 else 0.0
    avg_winner = float(np.mean(wins)) if n_wins > 0 else 0.0
    avg_loser = float(np.mean(losses)) if n_losses > 0 else 0.0
    largest_win = float(np.max(wins)) if n_wins > 0 else 0.0
    largest_loss = float(np.min(losses)) if n_losses > 0 else 0.0

    # Expectancy = avg_win * win_rate - avg_loss * loss_rate
    expectancy = (
        (avg_winner * (n_wins / total)) + (avg_loser * (n_losses / total))
        if total > 0 else 0.0
    )

    # Stability (R² of equity curve)
    if len(eq) > 2:
        x = np.arange(len(eq))
        slope, intercept = np.polyfit(x, eq, 1)
        y_pred = slope * x + intercept
        ss_res = np.sum((eq - y_pred) ** 2)
        ss_tot = np.sum((eq - np.mean(eq)) ** 2)
        stability = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    else:
        stability = 0.0

    commission_total = float(trades_df["Commission"].sum()) if "Commission" in trades_df.columns else 0.0

    # Kelly % (informational — what Kelly would recommend)
    if n_wins > 0 and n_losses > 0 and avg_loser != 0:
        b = avg_winner / abs(avg_loser)
        kelly = ((n_wins / total) * b - (n_losses / total)) / b
        kelly_pct = max(kelly * 100, 0.0)
    else:
        kelly_pct = 0.0

    return {
        "net_profit": round(net_profit, 2),
        "roi_pct": round(roi_pct, 2),
        "profit_factor": round(profit_factor, 2),
        "win_rate": round(win_rate, 1),
        "max_dd_dollar": round(max_dd_dollar, 2),
        "max_dd_pct": round(max_dd_pct, 2),
        "recovery_factor": round(recovery_factor, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "stability": round(stability, 4),
        "avg_trade": round(avg_trade, 2),
        "expectancy": round(expectancy, 2),
        "largest_win": round(largest_win, 2),
        "largest_loss": round(largest_loss, 2),
        "avg_winner": round(avg_winner, 2),
        "avg_loser": round(avg_loser, 2),
        "total_trades": total,
        "commission_total": round(commission_total, 2),
        "kelly_pct": round(kelly_pct, 2),
        "initial_capital": initial_capital,
    }


# ---------------------------------------------------------------------------
# Per-asset scaling for optimizer pipeline (operates on BacktestTrade objects)
# ---------------------------------------------------------------------------

def scale_backtest_trades(
    trades: list,
    batch_risk: float,
    mode: str = "fixed",
    risk_value: float = 0.0,
    initial_capital: float = 10_000.0,
    multiplier: float = 2.0,
    max_streak: int = 3,
    kelly_n: float = 2.0,
    kelly_warmup: int = 20,
    kelly_scope: str = "portfolio",
    kelly_lookback: int = 0,
    kelly_cap: float = 0.0,
    include_side: bool = False,
    oos_date: str | None = None,
    martingale_compound: str = "off",
    martingale_scope: str = "asset",
    run_batch_map: dict[int, int] | None = None,
    martingale_count_mode: str = "full_position",
    martingale_oos_reset: bool = True,
    martingale_start_after: int = 0,
    anti_martingale_reset_after: int = 0,
    return_is_end_state: bool = False,
    initial_is_end_state: dict | None = None,
) -> list | tuple:
    """Scale BacktestTrade objects in-place per-asset with the chosen risk profile.

    Groups trades by asset, sorts chronologically, applies scaling to net_profit.
    If oos_date is provided, IS and OOS segments are scaled independently
    (equity/streaks reset at OOS boundary) when martingale_oos_reset=True.
    When martingale_oos_reset=False, equity resets but streaks carry from IS into OOS
    (simulates live deployment where you'd keep your winning/losing streak).

    Args:
        trades: list of BacktestTrade ORM objects (must be expunged from session)
        batch_risk: original fixed $ risk from the normalized batch
        mode: "fixed" | "pct" | "martingale" | "anti_martingale" | "kelly"
        risk_value: $ amount (fixed/mart) or % (pct/mart-pct). 0 = no scaling.
        initial_capital: starting equity for compounding modes
        multiplier: martingale multiplier per streak
        max_streak: max martingale iterations
        kelly_n: Kelly N divisor (1=full, 2=half, 4=quarter)
        kelly_warmup: trades before Kelly reaches full confidence
        include_side: if True, group by symbol_Side (for L/S split optimizer)
        oos_date: ISO date string — OOS trades after this date get independent scaling
        martingale_compound: "off" = flat base, "dollar" = compound $ with equity,
                             "pct" = base risk is equity * (risk_value/100)
        martingale_scope: how martingale streaks are grouped (only affects martingale modes):
            "asset"       — per symbol across all batches (default)
            "portfolio"   — all assets share one streak
            "asset_batch" — per symbol per batch (BTC batch1 ≠ BTC batch3)
            "batch"       — per batch (all assets in batch share one streak)
        run_batch_map: {run_id: batch_id} lookup — required for "asset_batch" and "batch"
                       scopes. BacktestTrade.run_id → batch_id.
        martingale_count_mode: how streak events are counted (only affects martingale modes):
            "full_position" — one +/- per complete position (all fills summed)
            "each_exit"     — each exit fill is an independent streak event
            "final_exit"    — only the last fill per position counts (TP2/TSL
                              if multi-exit, TP1 if single exit)
        martingale_oos_reset: when True (default), streaks reset to 0 at OOS boundary.
            When False, streaks carry from IS into OOS (simulates live deployment).
            Equity always resets at OOS boundary regardless of this setting.
        martingale_start_after: for martingale/inv_martingale modes only — skip
            the first X consecutive losses before applying the multiplier.
            Loss 1..X use base size; loss X+1 gets multiplier^1, etc. 0 = disabled.
        anti_martingale_reset_after: for anti_martingale/inv_anti_martingale modes
            only — reset the win streak counter to 0 after X consecutive wins.
            The Xth win triggers the reset, so the next trade starts at base size.
            0 = disabled (never auto-reset).
        return_is_end_state: if True, return (trades, is_end_state_dict) instead
            of just trades. is_end_state_dict has per-group IS-ending streak/equity
            values for deterministic replay on reload.
        initial_is_end_state: if provided, seed OOS segments with these IS-end
            streak values instead of computing from IS trades. Used when loading
            saved portfolios to reproduce the exact same OOS scaling.

    Returns:
        The same list with net_profit modified in-place.
        If return_is_end_state=True, returns (trades, is_end_state_dict).
    """
    # Defensive caps — match engine.py BacktestConfig.__post_init__
    multiplier = max(1.0, min(float(multiplier), 10.0))
    max_streak = max(1, min(int(max_streak), 20))

    if batch_risk <= 0 or not trades or (mode == "fixed" and risk_value <= 0):
        if return_is_end_state:
            return trades, {}
        return trades

    # Build grouping key based on scope
    is_martingale = mode in (
        "martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale",
    )
    if is_martingale:
        effective_scope = martingale_scope
    elif mode == "kelly":
        effective_scope = kelly_scope  # "asset" or "portfolio"
    else:
        effective_scope = "asset"

    if effective_scope in ("batch", "asset_batch") and not run_batch_map:
        logger.warning(
            "martingale_scope=%r requires run_batch_map but none provided — "
            "all trades will collapse to a single group (same as 'portfolio' scope)",
            effective_scope,
        )

    # Resolve batch_id for each trade (only needed for batch-aware scopes)
    def _batch_tag(t) -> str:
        if not run_batch_map:
            return "0"
        rid = getattr(t, "run_id", None)
        return str(run_batch_map.get(rid, rid or 0))

    def _symbol_tag(t) -> str:
        if include_side:
            return f"{t.symbol}_{(t.side or 'Long').title()}"
        return t.symbol

    # Build groups based on scope
    groups: dict[str, list] = defaultdict(list)
    for t in trades:
        if effective_scope == "portfolio":
            key = "__all__"
        elif effective_scope == "batch":
            key = _batch_tag(t)
        elif effective_scope == "asset_batch":
            key = f"{_symbol_tag(t)}_b{_batch_tag(t)}"
        else:  # "asset" (default)
            key = _symbol_tag(t)
        groups[key].append(t)

    scale_args = (batch_risk, mode, risk_value, initial_capital,
                  multiplier, max_streak, kelly_n, kelly_warmup,
                  kelly_lookback, kelly_cap,
                  martingale_compound, martingale_count_mode,
                  martingale_start_after, anti_martingale_reset_after)

    # Collect IS-end state for each group (for deterministic replay on reload)
    is_end_state: dict[str, dict] = {}

    for _key, group_trades in groups.items():
        group_trades.sort(key=lambda t: t.exit_time or t.entry_time)

        if oos_date:
            is_trades = [t for t in group_trades if str(t.entry_time)[:10] <= oos_date]
            oos_trades = [t for t in group_trades if str(t.entry_time)[:10] > oos_date]
            # Scale IS segment (always fresh streaks)
            is_ending_streaks = _scale_segment(is_trades, *scale_args)
            # Save IS-end state for this group
            if is_ending_streaks:
                is_end_state[_key] = {
                    "wins": is_ending_streaks[0],
                    "losses": is_ending_streaks[1],
                }
            # Determine OOS initial streaks: use saved state if loading from
            # a previously-saved portfolio, otherwise use freshly computed IS-end
            saved_streaks = None
            if initial_is_end_state and _key in initial_is_end_state:
                s = initial_is_end_state[_key]
                saved_streaks = (s.get("wins", 0), s.get("losses", 0))
            if not martingale_oos_reset:
                # Carry streaks into OOS — prefer saved state, fall back to computed
                carry = saved_streaks or is_ending_streaks
                if carry:
                    _scale_segment(oos_trades, *scale_args,
                                   initial_streaks=carry)
                else:
                    _scale_segment(oos_trades, *scale_args)
            elif saved_streaks:
                # OOS reset is on but we have saved state — use it for replay
                _scale_segment(oos_trades, *scale_args,
                               initial_streaks=saved_streaks)
            else:
                # Reset streaks at OOS boundary (default — clean validation)
                _scale_segment(oos_trades, *scale_args)
        else:
            _scale_segment(group_trades, *scale_args)

    if return_is_end_state:
        return trades, is_end_state
    return trades


def _scale_segment(
    segment: list,
    batch_risk: float,
    mode: str,
    risk_value: float,
    initial_capital: float,
    multiplier: float,
    max_streak: int,
    kelly_n: float,
    kelly_warmup: int,
    kelly_lookback: int = 0,
    kelly_cap: float = 0.0,
    martingale_compound: str = "off",
    martingale_count_mode: str = "full_position",
    martingale_start_after: int = 0,
    anti_martingale_reset_after: int = 0,
    initial_streaks: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    """Scale a chronological segment of trades in-place.

    Args:
        initial_streaks: (consecutive_wins, consecutive_losses) to seed the segment
            with (used when carrying IS streaks into OOS). None = start from 0.

    Returns:
        (consecutive_wins, consecutive_losses) at segment end for martingale modes,
        None for non-martingale modes.
    """
    if not segment:
        return initial_streaks

    def _apply_factor(t, factor: float) -> None:
        """Scale trade fields proportionally so notional reflects true position size."""
        t.net_profit = (t.net_profit or 0.0) * factor
        if hasattr(t, 'quantity'):
            t.quantity = (t.quantity or 0.0) * factor
        if hasattr(t, 'commission') and t.commission:
            t.commission = t.commission * factor

    if mode == "fixed":
        factor = risk_value / batch_risk
        for t in segment:
            _apply_factor(t, factor)
            t._size_pct = 100.0

    elif mode == "pct":
        equity = initial_capital
        for t in segment:
            risk_amount = max(equity * (risk_value / 100), 0.0)
            factor = risk_amount / batch_risk
            _apply_factor(t, factor)
            t._size_pct = round((equity / initial_capital) * 100, 1) if initial_capital > 0 else 100.0
            equity += t.net_profit
            if equity <= 0:
                equity = 0.0  # ruin — subsequent trades get factor=0

    elif mode in ("martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale"):
        return _scale_segment_martingale(
            segment, batch_risk, mode, risk_value, initial_capital,
            multiplier, max_streak, martingale_compound, martingale_count_mode,
            martingale_start_after=martingale_start_after,
            anti_martingale_reset_after=anti_martingale_reset_after,
            initial_streaks=initial_streaks,
        )

    elif mode == "kelly":
        equity = initial_capital
        seen_pnls: list[float] = []
        base_risk = risk_value if risk_value > 0 else batch_risk
        for t in segment:
            raw_pnl = t.net_profit or 0.0
            seen_pnls.append(raw_pnl)
            n_seen = len(seen_pnls)

            # Compute rolling Kelly — optionally use only last N trades
            pnl_window = seen_pnls[-kelly_lookback:] if kelly_lookback > 0 else seen_pnls
            kelly_f = _compute_kelly_f(pnl_window)

            # Progressive confidence ramp
            confidence = min(1.0, n_seen / max(kelly_warmup, 1))
            base_fixed_risk = base_risk
            kelly_risk = (kelly_f / max(kelly_n, 0.5)) * equity if kelly_f > 0 else 0.0

            # Blend: early trades use fixed, gradually shift to Kelly
            risk_amount = (1.0 - confidence) * base_fixed_risk + confidence * kelly_risk
            risk_amount = max(risk_amount, 0.01)  # floor to avoid zero

            # Apply Kelly cap — max % of equity per trade
            if kelly_cap > 0 and equity > 0:
                max_risk = equity * (kelly_cap / 100.0)
                risk_amount = min(risk_amount, max_risk)

            factor = risk_amount / batch_risk

            _apply_factor(t, factor)
            t._size_pct = round((risk_amount / base_fixed_risk) * 100, 1) if base_fixed_risk > 0 else 100.0
            equity += t.net_profit
            if equity <= 0:
                equity = 0.0  # ruin — subsequent trades get factor=0

    else:
        raise ValueError(f"Unknown risk scaling mode: {mode!r}")

    return None  # non-martingale modes don't track streaks


def _scale_segment_martingale(
    segment: list,
    batch_risk: float,
    mode: str,
    risk_value: float,
    initial_capital: float,
    multiplier: float,
    max_streak: int,
    martingale_compound: str,
    count_mode: str,
    martingale_start_after: int = 0,
    anti_martingale_reset_after: int = 0,
    initial_streaks: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Martingale scaling with multiplier locked at position entry.

    Groups fills by position_id so all exits from the same position share
    one multiplier (the streak at the time the position opened). Streak
    updates are controlled by count_mode:

        full_position — total position PnL determines +1 or -1
        each_exit     — each fill independently updates the streak
        final_exit    — only the last fill per position counts

    Args:
        initial_streaks: (consecutive_wins, consecutive_losses) to seed the
            segment with. None = start from 0. Used when carrying IS streaks
            into OOS (martingale_oos_reset=False).

    Returns:
        (consecutive_wins, consecutive_losses) at segment end.
    """

    def _apply_factor(t, factor: float) -> None:
        t.net_profit = (t.net_profit or 0.0) * factor
        if hasattr(t, 'quantity'):
            t.quantity = (t.quantity or 0.0) * factor
        if hasattr(t, 'commission') and t.commission:
            t.commission = t.commission * factor

    equity = initial_capital
    base_factor = risk_value / batch_risk if risk_value > 0 else 1.0
    consecutive_wins = initial_streaks[0] if initial_streaks else 0
    consecutive_losses = initial_streaks[1] if initial_streaks else 0

    # ── Group fills by position_id ──
    # Positions are processed by entry_time so the multiplier is locked at open.
    # Fills without position_id (legacy data) are treated as their own position.
    from collections import OrderedDict
    positions: OrderedDict[str, list] = OrderedDict()
    solo_counter = 0
    for t in segment:
        pid = getattr(t, 'position_id', None)
        if pid:
            if pid not in positions:
                positions[pid] = []
            positions[pid].append(t)
        else:
            solo_counter += 1
            positions[f"__solo_{solo_counter}__"] = [t]

    # Sort positions by earliest entry_time (multiplier locked at entry)
    sorted_positions = sorted(
        positions.items(),
        key=lambda kv: min(
            (f.entry_time for f in kv[1] if f.entry_time),
            default=kv[1][0].exit_time or kv[1][0].entry_time,
        ),
    )

    # ── Two-pass timeline modes ──────────────────────────────────────
    # each_exit: all fills create streak events at exit_time. Positions
    #   entering between another position's TP1 and TSL see the TP1
    #   streak, which is the correct causal ordering.
    # confirmed_tsl: like each_exit but profitable TSL fills are
    #   backdated to sl_past_be_time (when TSL moved past breakeven).
    if count_mode in ("each_exit", "confirmed_tsl"):
        _fn = _scale_confirmed_tsl if count_mode == "confirmed_tsl" else _scale_each_exit
        return _fn(
            sorted_positions, batch_risk, mode, risk_value,
            initial_capital, multiplier, max_streak,
            martingale_compound, base_factor, equity,
            martingale_start_after=martingale_start_after,
            anti_martingale_reset_after=anti_martingale_reset_after,
            initial_streaks=initial_streaks,
        )

    # ── Pass 1: Build streak timeline from position close events ──
    # Each position produces ONE streak event at its close_time.
    # full_position: sum of all fills' raw PnL
    # final_exit: last fill's raw PnL only
    streak_events: list[tuple] = []  # (close_time, raw_pnl, pid_index)
    for idx, (_pid, fills) in enumerate(sorted_positions):
        fills.sort(key=lambda t: t.exit_time or t.entry_time)
        close_time = max(
            (f.exit_time for f in fills if f.exit_time),
            default=fills[-1].entry_time,
        )
        if count_mode == "final_exit":
            raw_pnl = fills[-1].net_profit or 0.0
        else:
            # full_position: position is a WIN if every exit is "confirmed profitable":
            #   - TP exit with PnL > BE_ZONE → confirmed win
            #   - TSL exit with sl_past_be_time set → confirmed win (stop moved past BE,
            #     counts even if final PnL dipped slightly negative due to slippage)
            #   - TSL exit with PnL > BE_ZONE (no sl_past_be_time) → confirmed win
            #   - Any exit that is neither profitable nor BE-confirmed → loss
            # When ALL exits are confirmed → position is a full win (+1 streak).
            # Otherwise → loss (resets win streak).
            all_confirmed = True
            for f in fills:
                fpnl = f.net_profit or 0.0
                if fpnl > BE_ZONE:
                    continue  # profitable exit — confirmed
                # Not profitable — check if TSL that passed BE
                reason = (getattr(f, 'exit_reason', '') or '').upper()
                has_be_confirm = getattr(f, 'sl_past_be_time', None) is not None
                if reason.startswith('TSL') and has_be_confirm:
                    continue  # TSL past BE — confirmed even if PnL slightly negative
                all_confirmed = False
                break
            if all_confirmed:
                raw_pnl = sum(f.net_profit or 0.0 for f in fills)
                if raw_pnl <= BE_ZONE:
                    raw_pnl = 0.02  # ensure it registers as a win
            else:
                # Mixed or all negative → treat as loss to reset win streak
                raw_pnl = min(sum(f.net_profit or 0.0 for f in fills), -0.02)
        streak_events.append((close_time, raw_pnl, idx))

    streak_events.sort(key=lambda e: e[0])

    # Build timeline: timeline[i] = (close_time, wins, losses) AFTER event i
    # anti_martingale_reset_after: reset win streak to 0 after X consecutive wins
    #   (only meaningful for anti_martingale / inv_anti_martingale modes)
    _reset_w = (anti_martingale_reset_after
                if anti_martingale_reset_after > 0
                   and mode in ("anti_martingale", "inv_anti_martingale")
                else 0)
    cons_w = consecutive_wins
    cons_l = consecutive_losses
    timeline: list[tuple] = []
    for close_ts, pnl, _ in streak_events:
        if pnl > BE_ZONE:
            cons_w += 1
            cons_l = 0
            if _reset_w and cons_w >= _reset_w:
                cons_w = 0  # reset after X consecutive wins
        elif pnl < -BE_ZONE:
            cons_l += 1
            cons_w = 0
        timeline.append((close_ts, cons_w, cons_l))

    # Build equity timeline: track equity changes in close_time order
    # so compound modes only see PnL from positions that closed before entry.
    # We'll compute this on-the-fly in pass 2 after we know the factors.

    # ── Pass 2: Apply multipliers using timeline ──
    # Process positions in close_time order for correct equity tracking.
    # But look up streak at each position's ENTRY time (causal ordering).
    close_order = sorted(range(len(sorted_positions)),
                         key=lambda i: max(
                             (f.exit_time for f in sorted_positions[i][1] if f.exit_time),
                             default=sorted_positions[i][1][-1].entry_time,
                         ))

    for pos_idx in close_order:
        _pid, fills = sorted_positions[pos_idx]
        fills.sort(key=lambda t: t.exit_time or t.entry_time)

        entry_time = min(
            (f.entry_time for f in fills if f.entry_time),
            default=fills[0].exit_time or fills[0].entry_time,
        )

        # Look up streak at entry_time (last event strictly before entry)
        cw = initial_streaks[0] if initial_streaks else 0
        cl = initial_streaks[1] if initial_streaks else 0
        for close_ts, w, l in timeline:
            if close_ts < entry_time:
                cw, cl = w, l
            else:
                break

        # Compute multiplier
        # martingale_start_after: skip first X losses before applying multiplier
        #   (only for martingale / inv_martingale modes)
        if mode in ("anti_martingale", "inv_anti_martingale"):
            streak = cw
        else:
            streak = cl
            if martingale_start_after > 0:
                streak = max(0, streak - martingale_start_after)
        capped = min(streak, max_streak)
        if capped > 0:
            if mode in ("inv_martingale", "inv_anti_martingale"):
                mart_mult = (1.0 / multiplier) ** capped
            else:
                mart_mult = multiplier ** capped
        else:
            mart_mult = 1.0

        # Compute factor
        if martingale_compound == "dollar":
            equity_ratio = max(equity / initial_capital, 0.0) if initial_capital > 0 else 1.0
            factor = base_factor * mart_mult * equity_ratio
        elif martingale_compound == "pct":
            risk_amount = max(equity * (risk_value / 100), 0.0)
            factor = (risk_amount / batch_risk) * mart_mult
        else:
            factor = base_factor * mart_mult

        # Apply the SAME factor to every fill in this position
        for t in fills:
            _apply_factor(t, factor)
            if martingale_compound != "off":
                eq_ratio = max(equity / initial_capital, 0.0) if initial_capital > 0 else 1.0
                t._size_pct = round(mart_mult * eq_ratio * 100, 1)
            else:
                t._size_pct = round(mart_mult * 100, 1)

        # Update equity after position (in close_time order)
        position_scaled_pnl = sum(f.net_profit for f in fills)
        if martingale_compound != "off":
            equity += position_scaled_pnl
            if equity <= 0:
                equity = 0.0

    # Return ending streaks from timeline
    if timeline:
        return (timeline[-1][1], timeline[-1][2])
    return (consecutive_wins, consecutive_losses)


def _scale_each_exit(
    sorted_positions: list,
    batch_risk: float,
    mode: str,
    risk_value: float,
    initial_capital: float,
    multiplier_val: float,
    max_streak: int,
    martingale_compound: str,
    base_factor: float,
    equity: float,
    martingale_start_after: int = 0,
    anti_martingale_reset_after: int = 0,
    initial_streaks: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Two-pass chronological processing for each_exit count mode.

    Every fill creates a streak event at its exit_time. This ensures that
    when Position B enters between Position A's TP1 and TSL, it sees the
    streak from TP1 (the last event that occurred before B's entry).

    Pass 1: Collect ALL fills across ALL positions, sort by exit_time,
            build a streak timeline.
    Pass 2: For each position, look up the streak at entry_time from the
            timeline, compute multiplier, apply to all fills.
    """

    def _apply_factor(t, factor: float) -> None:
        t.net_profit = (t.net_profit or 0.0) * factor
        if hasattr(t, 'quantity'):
            t.quantity = (t.quantity or 0.0) * factor
        if hasattr(t, 'commission') and t.commission:
            t.commission = t.commission * factor

    # ── Pass 1: Build streak events from ALL fills at exit_time ──
    streak_events: list[tuple] = []
    for _pid, fills in sorted_positions:
        for f in fills:
            raw_pnl = f.net_profit or 0.0
            if abs(raw_pnl) <= BE_ZONE:
                continue
            streak_events.append((f.exit_time, raw_pnl))

    streak_events.sort(key=lambda e: e[0])

    # Build timeline: timeline[i] = (timestamp, wins, losses) AFTER event i
    _reset_w = (anti_martingale_reset_after
                if anti_martingale_reset_after > 0
                   and mode in ("anti_martingale", "inv_anti_martingale")
                else 0)
    cons_w = initial_streaks[0] if initial_streaks else 0
    cons_l = initial_streaks[1] if initial_streaks else 0
    timeline: list[tuple] = []
    for ts, pnl in streak_events:
        if pnl > BE_ZONE:
            cons_w += 1
            cons_l = 0
            if _reset_w and cons_w >= _reset_w:
                cons_w = 0
        elif pnl < -BE_ZONE:
            cons_l += 1
            cons_w = 0
        timeline.append((ts, cons_w, cons_l))

    # ── Pass 2: Apply multipliers from timeline ──
    for _pid, fills in sorted_positions:
        fills.sort(key=lambda t: t.exit_time or t.entry_time)

        entry_time = min(
            (f.entry_time for f in fills if f.entry_time),
            default=fills[0].exit_time or fills[0].entry_time,
        )

        # Look up streak at entry_time (last event strictly before entry)
        # Start from initial_streaks if provided (IS carry-over)
        cw = initial_streaks[0] if initial_streaks else 0
        cl = initial_streaks[1] if initial_streaks else 0
        for ts, w, l in timeline:
            if ts < entry_time:
                cw, cl = w, l
            else:
                break

        # Compute multiplier
        if mode in ("anti_martingale", "inv_anti_martingale"):
            streak = cw
        else:
            streak = cl
            if martingale_start_after > 0:
                streak = max(0, streak - martingale_start_after)
        capped = min(streak, max_streak)
        if capped > 0:
            if mode in ("inv_martingale", "inv_anti_martingale"):
                mart_mult = (1.0 / multiplier_val) ** capped
            else:
                mart_mult = multiplier_val ** capped
        else:
            mart_mult = 1.0

        # Compute factor
        if martingale_compound == "dollar":
            equity_ratio = max(equity / initial_capital, 0.0) if initial_capital > 0 else 1.0
            factor = base_factor * mart_mult * equity_ratio
        elif martingale_compound == "pct":
            risk_amount = max(equity * (risk_value / 100), 0.0)
            factor = (risk_amount / batch_risk) * mart_mult
        else:
            factor = base_factor * mart_mult

        # Apply factor to all fills
        for t in fills:
            _apply_factor(t, factor)
            if martingale_compound != "off":
                eq_ratio = max(equity / initial_capital, 0.0) if initial_capital > 0 else 1.0
                t._size_pct = round(mart_mult * eq_ratio * 100, 1)
            else:
                t._size_pct = round(mart_mult * 100, 1)

        # Update equity after position
        position_scaled_pnl = sum(f.net_profit for f in fills)
        if martingale_compound != "off":
            equity += position_scaled_pnl
            if equity <= 0:
                equity = 0.0

    # Return ending streaks (last entry in timeline, or initial if no events)
    if timeline:
        return (timeline[-1][1], timeline[-1][2])
    return initial_streaks or (0, 0)


def _scale_confirmed_tsl(
    sorted_positions: list,
    batch_risk: float,
    mode: str,
    risk_value: float,
    initial_capital: float,
    multiplier_val: float,
    max_streak: int,
    martingale_compound: str,
    base_factor: float,
    equity: float,
    martingale_start_after: int = 0,
    anti_martingale_reset_after: int = 0,
    initial_streaks: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Two-pass chronological processing for confirmed_tsl count mode.

    Pass 1: Build a streak timeline from raw PnLs with timestamps.
            TSL fills at profit use sl_past_be_time (from engine) as the
            event timestamp. Falls back to first TP fill's exit_time for
            legacy data without sl_past_be_time.
    Pass 2: For each position, look up the streak at entry_time from the
            timeline, compute multiplier, and apply factor to all fills.
    """

    def _apply_factor(t, factor: float) -> None:
        t.net_profit = (t.net_profit or 0.0) * factor
        if hasattr(t, 'quantity'):
            t.quantity = (t.quantity or 0.0) * factor
        if hasattr(t, 'commission') and t.commission:
            t.commission = t.commission * factor

    # ── Pass 1: Build streak events with timestamps ──
    streak_events: list[tuple] = []  # (timestamp, raw_pnl)

    for _pid, fills in sorted_positions:
        fills.sort(key=lambda t: t.exit_time or t.entry_time)

        # Find first TP fill's exit_time (heuristic fallback for old data)
        first_tp_exit = None
        for f in fills:
            reason = (getattr(f, 'exit_reason', '') or '').upper()
            if reason.startswith('TP') and f.exit_time:
                first_tp_exit = f.exit_time
                break

        for f in fills:
            raw_pnl = f.net_profit or 0.0
            if abs(raw_pnl) <= BE_ZONE:
                continue  # skip near-zero fills
            reason = (getattr(f, 'exit_reason', '') or '').upper()

            if reason.startswith('TP'):
                # TP fills: event at exit_time
                streak_events.append((f.exit_time, raw_pnl))
            elif reason.startswith('TSL'):
                be_time = getattr(f, 'sl_past_be_time', None)
                if raw_pnl > BE_ZONE:
                    # TSL at profit: use sl_past_be_time (precise) or TP exit (heuristic)
                    confirm_time = be_time or first_tp_exit or f.exit_time
                    streak_events.append((confirm_time, raw_pnl))
                elif be_time is not None:
                    # TSL passed BE but final PnL slightly negative (slippage) —
                    # still counts as a confirmed win since stop was past breakeven
                    confirm_time = be_time or first_tp_exit or f.exit_time
                    streak_events.append((confirm_time, 0.02))  # force positive
                else:
                    # TSL at loss with no BE confirmation: genuine loss
                    streak_events.append((f.exit_time, raw_pnl))
            else:
                # SL, signal, backtest_end, etc: count at exit_time
                streak_events.append((f.exit_time, raw_pnl))

    # Sort events chronologically
    streak_events.sort(key=lambda e: e[0])

    # ── Pass 1b: Build streak timeline ──
    # timeline[i] = (timestamp, consecutive_wins, consecutive_losses) AFTER event i
    _reset_w = (anti_martingale_reset_after
                if anti_martingale_reset_after > 0
                   and mode in ("anti_martingale", "inv_anti_martingale")
                else 0)
    cons_w = initial_streaks[0] if initial_streaks else 0
    cons_l = initial_streaks[1] if initial_streaks else 0
    timeline: list[tuple] = []
    for ts, pnl in streak_events:
        if pnl > BE_ZONE:
            cons_w += 1
            cons_l = 0
            if _reset_w and cons_w >= _reset_w:
                cons_w = 0
        elif pnl < -BE_ZONE:
            cons_l += 1
            cons_w = 0
        timeline.append((ts, cons_w, cons_l))

    # ── Pass 2: Apply multipliers from timeline ──
    for _pid, fills in sorted_positions:
        fills.sort(key=lambda t: t.exit_time or t.entry_time)

        # Find entry_time for this position
        entry_time = min(
            (f.entry_time for f in fills if f.entry_time),
            default=fills[0].exit_time or fills[0].entry_time,
        )

        # Look up streak at entry_time (last event strictly before entry)
        # Start from initial_streaks if provided (IS carry-over)
        cw = initial_streaks[0] if initial_streaks else 0
        cl = initial_streaks[1] if initial_streaks else 0
        for ts, w, l in timeline:
            if ts < entry_time:
                cw, cl = w, l
            else:
                break

        # Compute multiplier
        if mode in ("anti_martingale", "inv_anti_martingale"):
            streak = cw
        else:
            streak = cl
            if martingale_start_after > 0:
                streak = max(0, streak - martingale_start_after)
        capped = min(streak, max_streak)
        if capped > 0:
            if mode in ("inv_martingale", "inv_anti_martingale"):
                mart_mult = (1.0 / multiplier_val) ** capped
            else:
                mart_mult = multiplier_val ** capped
        else:
            mart_mult = 1.0

        # Compute factor
        if martingale_compound == "dollar":
            equity_ratio = max(equity / initial_capital, 0.0) if initial_capital > 0 else 1.0
            factor = base_factor * mart_mult * equity_ratio
        elif martingale_compound == "pct":
            risk_amount = max(equity * (risk_value / 100), 0.0)
            factor = (risk_amount / batch_risk) * mart_mult
        else:
            factor = base_factor * mart_mult

        # Apply factor to all fills
        for t in fills:
            _apply_factor(t, factor)
            if martingale_compound != "off":
                eq_ratio = max(equity / initial_capital, 0.0) if initial_capital > 0 else 1.0
                t._size_pct = round(mart_mult * eq_ratio * 100, 1)
            else:
                t._size_pct = round(mart_mult * 100, 1)

        # Update equity after position
        position_scaled_pnl = sum(f.net_profit for f in fills)
        if martingale_compound != "off":
            equity += position_scaled_pnl
            if equity <= 0:
                equity = 0.0

    # Return ending streaks (last entry in timeline, or initial if no events)
    if timeline:
        return (timeline[-1][1], timeline[-1][2])
    return initial_streaks or (0, 0)


def _compute_kelly_f(pnls: list[float]) -> float:
    """Compute Kelly fraction f* from a list of PnLs."""
    if len(pnls) < 2:
        return 0.0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    if not wins or not losses:
        return 0.0
    p = len(wins) / len(pnls)
    q = 1.0 - p
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(abs(x) for x in losses) / len(losses)
    if avg_loss <= 0:
        return 0.0
    b = avg_win / avg_loss
    kelly = (p * b - q) / b
    return max(kelly, 0.0)


def _empty_result(initial_capital: float) -> dict:
    """Return an empty result when no trades or invalid inputs."""
    return {
        "trades": pd.DataFrame(),
        "equity_curve": [initial_capital],
        "metrics": {},
    }
