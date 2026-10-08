"""Helper functions for full_metrics.py — run-up, win/loss, professional KPIs,
event details, asset counts, MAE/MFE, equity curve data.

Split from full_metrics.py to stay under 150 lines per file.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.constants import BE_ZONE

# Negative threshold derived from shared constant
_BE_THRESHOLD = -BE_ZONE

# ---------------------------------------------------------------------------
# Run-up suite (6 keys)
# ---------------------------------------------------------------------------

def _runup_metrics(df: pd.DataFrame) -> dict:
    ru = df["Equity"] - df["Equity"].cummin()
    best_ru = ru.max()

    # Run-up periods
    periods = []
    in_ru = False
    start = None
    cycle_max = 0

    for idx, row in df.iterrows():
        ru_val = ru.loc[idx]
        if ru_val > 0:
            if not in_ru:
                in_ru = True
                start = row["Exit Time"]
                cycle_max = ru_val
            else:
                cycle_max = max(cycle_max, ru_val)
        else:
            if in_ru:
                periods.append({"Duration": row["Exit Time"] - start, "Height": cycle_max})
                in_ru = False
                cycle_max = 0

    if in_ru:
        periods.append({"Duration": df.iloc[-1]["Exit Time"] - start, "Height": cycle_max})

    avg_ru_dur = pd.Series([p["Duration"] for p in periods]).mean() if periods else pd.Timedelta(0)
    max_ru_dur = pd.Series([p["Duration"] for p in periods]).max() if periods else pd.Timedelta(0)
    avg_ru = pd.Series([p["Height"] for p in periods]).mean() if periods else 0

    return {
        "Best Run-up ($)": best_ru,
        "Avg Run-up ($)": avg_ru,
        "Max Run-up Duration": max_ru_dur,
        "Avg Run-up Duration": avg_ru_dur,
    }


# ---------------------------------------------------------------------------
# Win/Loss detail (6 keys)
# ---------------------------------------------------------------------------

def _win_loss_metrics(df: pd.DataFrame) -> dict:
    wins = df[df["Net Profit"] > 0]
    losses = df[df["Net Profit"] < _BE_THRESHOLD]

    avg_win = wins["Net Profit"].mean() if not wins.empty else 0
    max_win = wins["Net Profit"].max() if not wins.empty else 0
    avg_loss = losses["Net Profit"].mean() if not losses.empty else 0
    max_loss = losses["Net Profit"].min() if not losses.empty else 0

    dur = df["Exit Time"] - df["Entry Time"]
    avg_win_dur = dur[df["Net Profit"] > 0].mean() if not wins.empty else pd.Timedelta(0)
    avg_loss_dur = dur[df["Net Profit"] < _BE_THRESHOLD].mean() if not losses.empty else pd.Timedelta(0)

    return {
        "Avg Win ($)": avg_win,
        "Max Win ($)": max_win,
        "Avg Win Duration": avg_win_dur,
        "Avg Loss ($)": avg_loss,
        "Max Loss ($)": max_loss,
        "Avg Loss Duration": avg_loss_dur,
    }


# ---------------------------------------------------------------------------
# Professional KPIs (9 keys)
# ---------------------------------------------------------------------------

def _professional_metrics(df: pd.DataFrame, core: dict, dd: dict) -> dict:
    total_trades = core["Total Positions"]
    total_pnl = core["Total Net Profit"]
    max_dd = dd["Max Drawdown ($)"]

    # Sortino on CALENDAR-DAILY P&L (exit date, no-trade days = 0), annualised
    # with √365 (crypto) — same basis as portfolio_metrics / portfolio_health.
    # It used to be computed per TRADE and still multiplied by √365, so the
    # value moved with trade frequency (splitting one trade in two changed it).
    # Downside deviation = sqrt(mean(d²)) over losing days, the codebase-wide
    # convention.
    timed = df.dropna(subset=["Exit Time"])
    daily = timed.set_index("Exit Time")["Net Profit"].resample("D").sum()
    downside = daily[daily < 0]
    downside_dev = float(np.sqrt(np.mean(downside.values ** 2))) if not downside.empty else 0
    avg_ret = daily.mean() if not daily.empty else 0
    sortino = (avg_ret / downside_dev) * np.sqrt(365) if downside_dev > 0 else 0

    # SQN (Van Tharp)
    returns = df["Net Profit"].values
    std_ret = np.std(returns, ddof=1) if len(returns) > 1 else 1
    sqn = (np.mean(returns) / std_ret) * np.sqrt(min(total_trades, 100)) if std_ret > 0 else 0

    # Recovery Factor — preserve sign so losing strategies show negative
    # Cap ratios: low trade counts get tighter cap (avoids misleading outliers)
    _ratio_cap = 10.0 if total_trades < 10 else 99.0
    recovery = min(total_pnl / abs(max_dd), _ratio_cap) if max_dd < 0 else (_ratio_cap if total_pnl > 0 else 0)

    # Ulcer Index — shared reference scale (see scoring.ulcer_index)
    from backend.analysis.scoring import ulcer_index
    eq = df["Equity"].values
    ulcer = ulcer_index(eq, np.maximum.accumulate(eq))

    # Calmar
    total_days = max((df["Exit Time"].max() - df["Exit Time"].min()).days, 1)
    if total_days > 0 and max_dd < 0:
        ann_return = total_pnl * (365.25 / total_days)
        calmar = min(ann_return / abs(max_dd), _ratio_cap)
    else:
        calmar = _ratio_cap if total_pnl > 0 else 0

    # Payoff Ratio
    losses = df[df["Net Profit"] < _BE_THRESHOLD]
    avg_loss = losses["Net Profit"].mean() if not losses.empty else 0
    wins = df[df["Net Profit"] > 0]
    avg_w = wins["Net Profit"].mean() if not wins.empty else 0
    payoff = min(abs(avg_w / avg_loss), _ratio_cap) if avg_loss != 0 else (_ratio_cap if avg_w > 0 else 0)

    # Consecutive wins/losses with date tracking
    streaks = _consecutive_streaks(df)

    # T-Statistic
    if len(df) > 1:
        std_err = df["Net Profit"].std() / (len(df) ** 0.5)
        t_stat = df["Net Profit"].mean() / std_err if std_err > 0 else 0
    else:
        t_stat = 0

    return {
        "Sortino Ratio": sortino,
        "SQN": sqn,
        "Recovery Factor": recovery,
        "Ulcer Index": ulcer,
        "Calmar Ratio": calmar,
        "Avg Win/Loss Ratio": payoff,
        "Max Consecutive Wins": streaks["max_wins"],
        "Max Consecutive Losses": streaks["max_losses"],
        "T-Statistic": t_stat,
        # Streak "when" timestamps
        "Max Consec Wins Period Start": streaks["win_start"],
        "Max Consec Wins Period End": streaks["win_end"],
        "Max Consec Losses Period Start": streaks["loss_start"],
        "Max Consec Losses Period End": streaks["loss_end"],
    }


def _consecutive_streaks(df: pd.DataFrame) -> dict:
    best_win = {"count": 0, "start": None, "end": None}
    best_loss = {"count": 0, "start": None, "end": None}
    cur_wins = 0
    cur_losses = 0
    win_start = None
    loss_start = None

    for pnl, exit_time in zip(df["Net Profit"].values, df["Exit Time"].values):
        if pnl > BE_ZONE:
            if cur_wins == 0:
                win_start = exit_time
            cur_wins += 1
            cur_losses = 0
            loss_start = None
            if cur_wins > best_win["count"]:
                best_win = {"count": cur_wins, "start": win_start, "end": exit_time}
        elif pnl < -BE_ZONE:
            if cur_losses == 0:
                loss_start = exit_time
            cur_losses += 1
            cur_wins = 0
            win_start = None
            if cur_losses > best_loss["count"]:
                best_loss = {"count": cur_losses, "start": loss_start, "end": exit_time}

    return {
        "max_wins": best_win["count"],
        "win_start": best_win["start"],
        "win_end": best_win["end"],
        "max_losses": best_loss["count"],
        "loss_start": best_loss["start"],
        "loss_end": best_loss["end"],
    }
