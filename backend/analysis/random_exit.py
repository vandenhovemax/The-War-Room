"""Randomized exit stress test — proves exit logic has edge.

Keeps exact original entries, randomizes exit timing or price.
Compares actual Profit Factor to distribution of random-exit PFs
to determine if exit logic is statistically better than random.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range for a candle DataFrame."""
    h = df["high"]
    l = df["low"]
    c_prev = df["close"].shift(1)
    tr = pd.concat(
        [h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _pf_from_pnls(pnls: list[float], cap: float = 0.0) -> float:
    """Compute Profit Factor from a list of PnL values.

    cap: if > 0, clamp the result to this maximum (0 = no cap).
    """
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = sum(abs(p) for p in pnls if p < 0)
    if gross_loss == 0:
        pf = 99.0 if gross_profit > 0 else 0.0
    else:
        pf = gross_profit / gross_loss
    return min(pf, cap) if cap > 0 else pf


def _apply_exit_slippage(
    price: float, side_dir: float,
    slip_mode: str, slip_value: float,
) -> float:
    """Apply slippage to an exit price (worse fill, same as engine).

    Long exit → price goes DOWN.  Short exit → price goes UP.
    """
    if slip_value <= 0:
        return price
    if slip_mode == "pips":
        return price - slip_value if side_dir > 0 else price + slip_value
    # "pct" and "risk_pct" both use % of price
    slip = price * (slip_value / 100)
    return price - slip if side_dir > 0 else price + slip


def random_exit_stress_test(
    trades: list[dict],
    candle_cache: dict[str, pd.DataFrame],
    n_iterations: int = 500,
    exit_mode: str = "random_bars",
    max_hold_bars: int = 50,
    slippage_mode: str = "pct",
    slippage_value: float = 0.0,
) -> dict | None:
    """Run randomized exit test.

    Args:
        trades: List of dicts with keys:
            entry_time (pd.Timestamp), symbol (str), side (str),
            entry_price (float), exit_price (float), net_profit (float),
            commission (float, optional)
        candle_cache: Pre-loaded {symbol: DataFrame with OHLCV, DatetimeIndex}
        n_iterations: Number of random exit simulations.
        exit_mode: "random_bars" or "random_atr"
        max_hold_bars: Maximum bars a random trade can stay open.
        slippage_mode: "pct" | "pips" | "risk_pct" — same as backtester.
        slippage_value: Slippage amount (0 = none).

    Returns dict with actual_pf, random_pfs, z_score, p_value, etc.
    Returns None if no valid trades.
    """
    if not trades:
        return None

    rng = np.random.default_rng()

    # Pre-compute ATR for each symbol if needed
    atr_cache: dict[str, pd.Series] = {}
    if exit_mode == "random_atr":
        for sym, df in candle_cache.items():
            atr_cache[sym] = _compute_atr(df)

    # Build trade lookup: find entry bar index in candle data
    valid_trades: list[dict] = []
    for t in trades:
        sym = t["symbol"]
        if sym not in candle_cache:
            continue
        df = candle_cache[sym]
        entry_time = pd.Timestamp(t["entry_time"])

        # Find nearest bar at or after entry time
        idx_pos = df.index.searchsorted(entry_time)
        if idx_pos >= len(df):
            continue

        direction = 1.0 if t["side"] in ("Long", "long") else -1.0

        # Derive implied quantity so ghost PnL is in the same dollar
        # scale as the actual net_profit.  qty = net_profit / price_move.
        entry_px = float(t["entry_price"])
        exit_px = float(t.get("exit_price") or 0)
        net_pnl = float(t["net_profit"])
        commission = float(t.get("commission", 0))
        price_move = (exit_px - entry_px) * direction
        if abs(price_move) > 1e-12:
            # gross_pnl = net_profit + commission (restore pre-commission PnL)
            implied_qty = (net_pnl + abs(commission)) / price_move
        else:
            # Fallback: estimate qty from net_profit / entry_price (% basis)
            implied_qty = abs(net_pnl / entry_px) if entry_px else 1.0

        # Per-trade commission to deduct from each ghost trade
        trade_commission = abs(commission)

        valid_trades.append({
            **t,
            "df_key": sym,
            "entry_idx": int(idx_pos),
            "direction": direction,
            "implied_qty": abs(implied_qty),
            "trade_commission": trade_commission,
        })

    if not valid_trades:
        return None

    # Actual PF from real trades (no cap — show true value)
    actual_pnls = [t["net_profit"] for t in valid_trades]
    actual_pf = _pf_from_pnls(actual_pnls)

    # Run iterations
    random_pfs: list[float] = []
    for _ in range(n_iterations):
        ghost_pnls: list[float] = []

        for t in valid_trades:
            df = candle_cache[t["df_key"]]
            entry_idx = t["entry_idx"]
            entry_price = t["entry_price"]
            direction = t["direction"]
            max_idx = len(df) - 1

            if exit_mode == "random_bars":
                offset = int(rng.integers(1, max_hold_bars + 1))
                exit_idx = min(entry_idx + offset, max_idx)
                exit_price = float(df.iloc[exit_idx]["close"])

            else:  # random_atr
                atr_series = atr_cache.get(t["df_key"])
                if atr_series is None or entry_idx >= len(atr_series):
                    # Fallback to random bars if no ATR
                    offset = int(rng.integers(1, max_hold_bars + 1))
                    exit_idx = min(entry_idx + offset, max_idx)
                    exit_price = float(df.iloc[exit_idx]["close"])
                else:
                    atr_val = atr_series.iloc[entry_idx]
                    if pd.isna(atr_val) or atr_val <= 0:
                        offset = int(rng.integers(1, max_hold_bars + 1))
                        exit_idx = min(entry_idx + offset, max_idx)
                        exit_price = float(df.iloc[exit_idx]["close"])
                    else:
                        mult = rng.uniform(0.5, 5.0)
                        target_dist = atr_val * mult
                        exit_price = entry_price  # default
                        scan_end = min(entry_idx + max_hold_bars, max_idx)
                        for j in range(entry_idx + 1, scan_end + 1):
                            bar = df.iloc[j]
                            if direction > 0:  # long
                                if bar["high"] >= entry_price + target_dist:
                                    exit_price = entry_price + target_dist
                                    break
                                if bar["low"] <= entry_price - target_dist:
                                    exit_price = entry_price - target_dist
                                    break
                            else:  # short
                                if bar["low"] <= entry_price - target_dist:
                                    exit_price = entry_price - target_dist
                                    break
                                if bar["high"] >= entry_price + target_dist:
                                    exit_price = entry_price + target_dist
                                    break
                        else:
                            exit_price = float(df.iloc[scan_end]["close"])

            # Apply same slippage the real backtest used (worse exit fill)
            exit_price = _apply_exit_slippage(
                exit_price, direction, slippage_mode, slippage_value,
            )

            # Scale by implied qty so ghost PnL matches actual dollar scale,
            # then deduct the same commission the real trade paid.
            ghost_pnl = (
                (exit_price - entry_price) * direction * t["implied_qty"]
                - t["trade_commission"]
            )
            ghost_pnls.append(ghost_pnl)

        random_pfs.append(_pf_from_pnls(ghost_pnls))

    # Statistics
    random_arr = np.array(random_pfs)
    mean_random = float(np.mean(random_arr))
    std_random = float(np.std(random_arr))

    z_score = (
        (actual_pf - mean_random) / std_random if std_random > 0 else 0.0
    )
    p_value = float(stats.norm.sf(z_score)) if z_score > 0 else 1.0

    return {
        "actual_pf": round(actual_pf, 4),
        "mean_random_pf": round(mean_random, 4),
        "std_random_pf": round(std_random, 4),
        "z_score": round(z_score, 2),
        "p_value": round(p_value, 6),
        "alpha_gap": round(actual_pf - mean_random, 4),
        "is_significant": z_score >= 2.0,
        "random_pfs": [round(x, 4) for x in random_pfs],
        "n_trades_tested": len(valid_trades),
        "n_iterations": n_iterations,
        "exit_mode": exit_mode,
    }
