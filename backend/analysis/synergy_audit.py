"""
Strategy Synergy & Marginal Contribution Audit.

Pure logic — no Streamlit imports. Operates on OOS trade DataFrames
from cross-strategy portfolios to measure diversification benefit,
detect parasitic signals, and compare individual vs combined performance.

Ported verbatim from market_structure_dashboard/core/synergy_audit.py.
"""

import numpy as np
import pandas as pd

# -- Helpers ----------------------------------------------------------------


def _extract_strategy_name(asset_name, default=None):
    """'BTCUSDT (Strategy 1.1)_Long' -> 'Strategy 1.1'.
    Falls back to *default* for single-strat assets without parentheses."""
    if '(' in asset_name and ')' in asset_name:
        return asset_name.split('(')[-1].split(')')[0]
    return default


def _extract_base_symbol(asset_name):
    """'BTCUSDT_Long (Strategy 1.1)_Long' -> 'BTCUSDT'."""
    base = asset_name.split(' (')[0]
    for suffix in ('_Long', '_Short'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    return base


def _extract_side(asset_name):
    """Returns 'Long' or 'Short' from the final suffix."""
    return 'Long' if asset_name.endswith('_Long') else 'Short'


def _apply_friction(df, friction_pct=0.20):
    """
    Apply flat friction to each trade's Net Profit.

    Since position size is unavailable, deducts friction_pct% of |Net Profit|
    as a conservative proxy.
    """
    result = df.copy()
    result['Friction Cost'] = result['Net Profit'].abs() * (friction_pct / 100.0)
    result['Net Profit (Friction)'] = result['Net Profit'] - result['Friction Cost']
    return result


# -- Task 1: Marginal Contribution -----------------------------------------


def _compute_audit_metrics(df):
    """Compute Net Profit, PF, Sharpe, Max DD, SQN from friction-adjusted trades."""
    pnl_col = 'Net Profit (Friction)' if 'Net Profit (Friction)' in df.columns else 'Net Profit'

    if df.empty:
        return {
            'Net Profit': 0, 'Profit Factor': 0, 'Sharpe Ratio': 0,
            'Max Drawdown ($)': 0, 'SQN': 0, 'Trades': 0, 'Friction Cost': 0,
        }

    net_profit = df[pnl_col].sum()
    friction_total = df['Friction Cost'].sum() if 'Friction Cost' in df.columns else 0

    # Profit Factor
    wins = df[df[pnl_col] > 0][pnl_col].sum()
    losses = abs(df[df[pnl_col] <= 0][pnl_col].sum())
    pf = min(wins / losses, 10.0) if losses > 0 else (10.0 if wins > 0 else 0)

    # Sharpe Ratio (annualized from daily PnL)
    df_sorted = df.sort_values('Exit Time').copy()
    df_sorted['Date'] = df_sorted['Exit Time'].dt.floor('D')
    daily_pnl = df_sorted.groupby('Date')[pnl_col].sum()
    if len(daily_pnl) > 1:
        full_range = pd.date_range(daily_pnl.index.min(), daily_pnl.index.max(), freq='D')
        daily_pnl = daily_pnl.reindex(full_range, fill_value=0)
    sharpe = (daily_pnl.mean() / daily_pnl.std()) * np.sqrt(365) if len(daily_pnl) > 1 and daily_pnl.std() > 0 else 0

    # Max Drawdown
    equity = df_sorted[pnl_col].cumsum()
    peak = equity.cummax()
    max_dd = (equity - peak).min()

    # SQN
    trade_returns = df[pnl_col].values
    expectancy = np.mean(trade_returns)
    std_r = np.std(trade_returns, ddof=1) if len(trade_returns) > 1 else 1
    sqn = (expectancy / std_r) * np.sqrt(min(len(trade_returns), 100)) if std_r > 0 else 0

    return {
        'Net Profit': round(float(net_profit), 2),
        'Profit Factor': round(float(pf), 2),
        'Sharpe Ratio': round(float(sharpe), 2),
        'Max Drawdown ($)': round(float(max_dd), 2),
        'SQN': round(float(sqn), 2),
        'Trades': len(df),
        'Friction Cost': round(float(friction_total), 2),
    }


def compute_marginal_contribution(oos_df, friction_pct=0.20, default_strategy_name=None):
    """
    Per-strategy + combined metrics on OOS trades with friction.

    Returns list of dicts with strategy name + metrics.
    """
    _default = default_strategy_name or 'Strategy'
    df = _apply_friction(oos_df, friction_pct)
    df['Strategy'] = df['Asset'].apply(lambda a: _extract_strategy_name(a, default=_default)).fillna(_default)

    rows = []
    for strat in sorted(df['Strategy'].unique()):
        m = _compute_audit_metrics(df[df['Strategy'] == strat])
        m['Strategy'] = strat
        rows.append(m)

    combined = _compute_audit_metrics(df)
    combined['Strategy'] = 'COMBINED'
    rows.append(combined)

    return rows


# -- Task 2: Diversification Multiplier ------------------------------------


def compute_diversification_multiplier(oos_df, friction_pct=0.20, default_strategy_name=None):
    """
    DM = 1 - (StdDev_combined / Avg_StdDev_per_strategy).

    DM > 0.15 -> strategies are synergistic (combined volatility meaningfully
    lower than average individual volatility).
    """
    _default = default_strategy_name or 'Strategy'
    df = _apply_friction(oos_df, friction_pct)
    df['Strategy'] = df['Asset'].apply(lambda a: _extract_strategy_name(a, default=_default)).fillna(_default)
    df['Date'] = pd.to_datetime(df['Exit Time']).dt.floor('D')
    pnl_col = 'Net Profit (Friction)'

    strategies = sorted(df['Strategy'].unique())
    if len(strategies) < 2:
        return {'DM': 0, 'synergistic': False, 'reason': 'Need at least 2 strategies',
                'combined_std': 0, 'avg_individual_std': 0, 'per_strategy_std': {}}

    min_date, max_date = df['Date'].min(), df['Date'].max()
    full_range = pd.date_range(min_date, max_date, freq='D')

    strategy_stds = {}
    for strat in strategies:
        daily = df[df['Strategy'] == strat].groupby('Date')[pnl_col].sum()
        daily = daily.reindex(full_range, fill_value=0)
        strategy_stds[strat] = float(daily.std())

    combined_daily = df.groupby('Date')[pnl_col].sum().reindex(full_range, fill_value=0)
    combined_std = float(combined_daily.std())

    avg_std = float(np.mean(list(strategy_stds.values())))
    dm = 1 - (combined_std / avg_std) if avg_std > 0 else 0

    return {
        'DM': round(dm, 4),
        'synergistic': dm > 0.15,
        'combined_std': round(combined_std, 2),
        'avg_individual_std': round(avg_std, 2),
        'per_strategy_std': {k: round(v, 2) for k, v in strategy_stds.items()},
    }


# -- Task 3: Conflict & Netting Detector -----------------------------------


def detect_conflicts(oos_df, default_strategy_name=None):
    """
    Find parasitic signals: same base asset with Long (strategy A) and
    Short (strategy B) overlapping in time.

    Returns dict with conflict_count, wasted_commission estimate, and details.
    """
    if oos_df.empty:
        return {'conflict_count': 0, 'conflict_trade_count': 0,
                'wasted_commission_estimate': 0, 'net_offset_pnl': 0, 'conflicts': []}

    _default = default_strategy_name or 'Strategy'
    df = oos_df.copy()
    df['Strategy'] = df['Asset'].apply(lambda a: _extract_strategy_name(a, default=_default)).fillna(_default)
    df['BaseSymbol'] = df['Asset'].apply(_extract_base_symbol)
    df['TradeSide'] = df['Asset'].apply(_extract_side)
    df['Entry Time'] = pd.to_datetime(df['Entry Time'])
    df['Exit Time'] = pd.to_datetime(df['Exit Time'])

    conflicts = []

    for base, group in df.groupby('BaseSymbol'):
        longs = group[group['TradeSide'] == 'Long']
        shorts = group[group['TradeSide'] == 'Short']
        if longs.empty or shorts.empty:
            continue

        for _, lt in longs.iterrows():
            for _, st_row in shorts.iterrows():
                if lt['Strategy'] == st_row['Strategy']:
                    continue
                # Overlap check: A.entry < B.exit AND B.entry < A.exit
                if lt['Entry Time'] < st_row['Exit Time'] and st_row['Entry Time'] < lt['Exit Time']:
                    overlap_start = max(lt['Entry Time'], st_row['Entry Time'])
                    overlap_end = min(lt['Exit Time'], st_row['Exit Time'])
                    conflicts.append({
                        'Base Symbol': base,
                        'Long Strategy': lt['Strategy'],
                        'Short Strategy': st_row['Strategy'],
                        'Overlap Hours': round((overlap_end - overlap_start).total_seconds() / 3600, 1),
                        'Long PnL': round(float(lt['Net Profit']), 2),
                        'Short PnL': round(float(st_row['Net Profit']), 2),
                        'Net Offset PnL': round(float(lt['Net Profit'] + st_row['Net Profit']), 2),
                    })

    # Wasted commission: 0.20% on the gross exposure of offsetting trades
    wasted = sum(
        (abs(c['Long PnL']) + abs(c['Short PnL'])) * 0.002
        for c in conflicts
    )

    return {
        'conflict_count': len(conflicts),
        'conflict_trade_count': len(conflicts) * 2,
        'wasted_commission_estimate': round(wasted, 2),
        'net_offset_pnl': round(sum(c['Net Offset PnL'] for c in conflicts), 2),
        'conflicts': conflicts,
    }


# -- Task 4: Friction Sensitivity / Breakeven Friction ----------------------


def _pf_at_friction(oos_df, friction_pct):
    """Compute Profit Factor at a given friction level."""
    df = _apply_friction(oos_df, friction_pct)
    col = 'Net Profit (Friction)'
    wins = df[df[col] > 0][col].sum()
    losses = abs(df[df[col] <= 0][col].sum())
    if losses > 0:
        return min(wins / losses, 10.0)
    return 10.0 if wins > 0 else 0


def compute_breakeven_friction(oos_df, current_friction_pct=0.20):
    """
    Find the friction % where the portfolio's Profit Factor drops to 1.0.

    Uses binary search from 0% to 5% in 0.01% precision.
    Returns breakeven_pct, PF at 0% friction, PF at current friction,
    and margin of safety (breakeven - current).
    """
    if oos_df.empty:
        return {
            'breakeven_pct': 0, 'pf_at_zero': 0,
            'pf_at_current': 0, 'margin_of_safety': 0,
        }

    pf_zero = _pf_at_friction(oos_df, 0.0)
    pf_current = _pf_at_friction(oos_df, current_friction_pct)

    # If PF already <= 1.0 at zero friction, the system is dead
    if pf_zero <= 1.0:
        return {
            'breakeven_pct': 0, 'pf_at_zero': round(float(pf_zero), 4),
            'pf_at_current': round(float(pf_current), 4), 'margin_of_safety': 0,
        }

    # Binary search: find friction where PF crosses 1.0
    lo, hi = 0.0, 5.0
    for _ in range(50):  # 50 iterations -> precision < 0.0001%
        mid = (lo + hi) / 2
        if _pf_at_friction(oos_df, mid) > 1.0:
            lo = mid
        else:
            hi = mid

    breakeven = round((lo + hi) / 2, 4)
    margin = round(breakeven - current_friction_pct, 4)

    return {
        'breakeven_pct': breakeven,
        'pf_at_zero': round(float(pf_zero), 4),
        'pf_at_current': round(float(pf_current), 4),
        'margin_of_safety': margin,
    }
