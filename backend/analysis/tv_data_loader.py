"""TradingView Excel data loader — imports TV backtest exports into standardized format.

Reads .xlsx files exported from TradingView (both "List of Trades" and
"List of Executions" formats) and returns a unified DataFrame.

Also provides trades_from_db() to convert internal BacktestTrade records
into the same format, so all analysis code works on one common schema:

  Asset | Side | Entry Time | Exit Time | Net Profit | Net Profit % | Drawdown | Run-up
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Standardized column names — every loader must produce these
# ---------------------------------------------------------------------------
STANDARD_COLS = [
    "Asset", "Side", "Entry Time", "Exit Time",
    "Net Profit", "Net Profit %", "Drawdown", "Run-up",
]


# ---------------------------------------------------------------------------
# TradingView Excel loading
# ---------------------------------------------------------------------------

def load_tv_reports(
    directory: str,
    force_side: str | None = None,
    group_multi_tp: bool = False,
) -> pd.DataFrame:
    """Load all .xlsx TradingView exports from *directory*.

    Parameters
    ----------
    directory : path to folder containing .xlsx files
    force_side : "Long" or "Short" — override side detection from filename
    group_multi_tp : merge 2-exit multi-TP trade pairs into one row

    Returns standardized DataFrame (see STANDARD_COLS).
    """
    if not os.path.exists(directory):
        return pd.DataFrame(columns=STANDARD_COLS)

    files = [f for f in os.listdir(directory) if f.lower().endswith(".xlsx")]
    all_trades: list[pd.DataFrame] = []

    for file in files:
        file_path = os.path.join(directory, file)
        try:
            asset_name = _extract_asset_name(file, force_side)
            df = _load_single_file(file_path, asset_name, file)
            if df is not None and not df.empty:
                all_trades.append(df)
        except Exception as e:
            logger.warning("Error loading %s: %s", file, e)

    if not all_trades:
        return pd.DataFrame(columns=STANDARD_COLS)

    final = pd.concat(all_trades, ignore_index=True)
    return _clean_trade_list(final, group_multi_tp)


def _extract_asset_name(filename: str, force_side: str | None) -> str:
    """Extract asset name from TV export filename.

    Example: Example_Strategy_BYBIT_1000PEPEUSDT.P_2026-02-15_64a8b.xlsx
    → 1000PEPEUSDT_Long (if force_side="Long")
    """
    base_asset = "Unknown"
    suffix = ""

    parts = filename.split("_")
    for part in parts:
        if "USDT" in part:
            base_asset = part.replace(".P", "")
            break

    if force_side:
        if force_side.lower() == "long":
            suffix = "_Long"
        elif force_side.lower() == "short":
            suffix = "_Short"
    else:
        lower = filename.lower()
        is_long = "long" in lower
        is_short = "short" in lower
        if is_long and not is_short:
            suffix = "_Long"
        elif is_short and not is_long:
            suffix = "_Short"

    if base_asset != "Unknown":
        return base_asset + suffix
    return filename.replace(".xlsx", "") + suffix


def _load_single_file(
    file_path: str, asset_name: str, source_file: str,
) -> pd.DataFrame | None:
    """Read one .xlsx and return raw trades with Asset/SourceFile columns."""
    xls = pd.ExcelFile(file_path)

    # Find target sheet
    target_sheet = None
    for sheet in xls.sheet_names:
        if sheet.lower() == "list of trades":
            target_sheet = sheet
            break

    if target_sheet:
        df = pd.read_excel(file_path, sheet_name=target_sheet)
    else:
        # Fallback: scan sheets for known columns
        for sheet in xls.sheet_names:
            df = pd.read_excel(file_path, sheet_name=sheet)
            if "Date and time" in df.columns or "Entry time" in df.columns:
                break
        else:
            return None

    # Detect format and process
    if "Trade #" in df.columns and "Date and time" in df.columns:
        df["Asset"] = asset_name
        df["SourceFile"] = source_file
        return _process_execution_list(df)
    elif "Entry time" in df.columns:
        df["Asset"] = asset_name
        df["SourceFile"] = source_file
        return df
    return None


def _process_execution_list(df: pd.DataFrame) -> pd.DataFrame:
    """Convert "List of Executions" (entry/exit row pairs) into trade rows."""
    df["Date and time"] = pd.to_datetime(df["Date and time"])
    trades = []

    for _trade_id, group in df.groupby("Trade #"):
        group = group.sort_values("Date and time")
        entry_row = group.iloc[0]
        exit_row = group.iloc[-1]

        type_str = str(exit_row["Type"]).lower()
        side = "Long" if "long" in type_str else "Short" if "short" in type_str else "Unknown"

        trades.append({
            "Entry Time": entry_row["Date and time"],
            "Exit Time": exit_row["Date and time"],
            "Net Profit": exit_row.get("Net P&L USDT", 0),
            "Net Profit %": exit_row.get("Net P&L %", 0),
            "Run-up": exit_row.get("Favorable excursion USDT", 0),
            "Drawdown": exit_row.get("Adverse excursion USDT", 0),
            "Side": side,
            "Asset": df.iloc[0]["Asset"],
            "SourceFile": df.iloc[0]["SourceFile"],
        })

    return pd.DataFrame(trades)


def _clean_trade_list(df: pd.DataFrame, group_multi_tp: bool) -> pd.DataFrame:
    """Normalize column names, parse dates, clean numerics."""
    if df.empty:
        return df

    # Normalize column names from TV format
    if "Entry time" in df.columns and "Entry Time" not in df.columns:
        df["Entry Time"] = pd.to_datetime(df["Entry time"])
    if "Exit time" in df.columns and "Exit Time" not in df.columns:
        df["Exit Time"] = pd.to_datetime(df["Exit time"])

    # Ensure datetime
    for col in ["Entry Time", "Exit Time"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])

    # Clean numeric columns (TV sometimes exports as strings)
    for col in ["Net Profit", "Run-up", "Drawdown", "Net Profit %"]:
        if col in df.columns and df[col].dtype == "object":
            df[col] = df[col].astype(str).str.replace(r"[^\d.\-]", "", regex=True)
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Fill missing standard columns with 0
    for col in STANDARD_COLS:
        if col not in df.columns:
            df[col] = 0 if col not in ("Asset", "Side") else "Unknown"

    if group_multi_tp:
        df = _group_multi_tp_trades(df)

    return df


def _group_multi_tp_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Merge 2-exit multi-TP pairs (same Entry Time + Asset + Side) into one row.

    Only merges groups of EXACTLY 2 rows. Groups of 3+ are left as-is.
    """
    if df.empty or "Entry Time" not in df.columns or "Asset" not in df.columns:
        return df

    group_keys = ["Entry Time", "Asset", "Side"]

    # Add price key if available (prevents merging distinct trades on same day)
    for col in ["Price", "Entry Price", "Entry price"]:
        if col in df.columns:
            group_keys.append(col)
            break

    group_sizes = df.groupby(group_keys).size()
    pair_keys = group_sizes[group_sizes == 2].index
    if len(pair_keys) == 0:
        return df

    df_temp = df.set_index(group_keys)
    is_pair = df_temp.index.isin(pair_keys)

    df_pairs = df[is_pair].copy()
    df_singles = df[~is_pair].copy()

    agg_rules: dict = {
        "Net Profit": "sum",
        "Exit Time": "max",
    }
    if "Net Profit %" in df.columns:
        agg_rules["Net Profit %"] = "sum"
    if "Run-up" in df.columns:
        agg_rules["Run-up"] = "max"
    if "Drawdown" in df.columns:
        agg_rules["Drawdown"] = "min"
    if "Commission" in df.columns:
        agg_rules["Commission"] = "sum"
    if "SourceFile" in df.columns:
        agg_rules["SourceFile"] = "first"

    merged = df_pairs.groupby(group_keys, as_index=False).agg(agg_rules)
    result = pd.concat([df_singles, merged], ignore_index=True)
    return result.sort_values("Exit Time").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Internal backtester → standardized format converter
# ---------------------------------------------------------------------------

def trades_from_db(backtest_trades: list) -> pd.DataFrame:
    """Convert BacktestTrade ORM records into the standardized DataFrame.

    Multi-exit positions (e.g. TP1 + TP2) share a position_id and are
    merged into a single row.  This ensures all downstream analysis counts
    *positions* (the real trade unit) rather than individual exit fills.

    Parameters
    ----------
    backtest_trades : list of BacktestTrade ORM objects

    Returns standardized DataFrame (see STANDARD_COLS).
    """
    if not backtest_trades:
        return pd.DataFrame(columns=STANDARD_COLS + ["Commission", "Entry Price", "Exit Reason"])

    from collections import defaultdict

    # Group fills by (symbol, side, position_id) so multi-exit positions
    # become one row.  position_id alone is NOT unique across runs — it's a
    # global sequential counter that can collide when trades from different
    # symbols share the same ID.  Including symbol+side in the key prevents
    # accidentally merging fills from different instruments.
    groups: dict[str, list] = defaultdict(list)
    for t in backtest_trades:
        pid = getattr(t, "position_id", None) or f"_solo_{id(t)}"
        sym = getattr(t, "symbol", "")
        sd = getattr(t, "side", "")
        key = f"{sym}_{sd}_{pid}"
        groups[key].append(t)

    rows = []
    for _pid, fills in groups.items():
        first = fills[0]
        side = first.side.capitalize() if hasattr(first, "side") and first.side else "Unknown"
        asset = first.symbol if hasattr(first, "symbol") else "Unknown"
        if side in ("Long", "Short") and not asset.endswith(f"_{side}"):
            asset_with_side = f"{asset}_{side}"
        else:
            asset_with_side = asset

        # Merge: sum P&L / commission / quantity, take latest exit, keep first entry
        net_profit = sum(float(f.net_profit) if f.net_profit else 0.0 for f in fills)
        net_profit_pct = sum(float(f.net_profit_pct) if f.net_profit_pct else 0.0 for f in fills)
        commission = sum(float(f.commission) if f.commission else 0.0 for f in fills)
        quantity = sum(float(f.quantity) if getattr(f, "quantity", None) else 0.0 for f in fills)
        mae = sum(float(f.mae) if f.mae else 0.0 for f in fills)
        mfe = sum(float(f.mfe) if f.mfe else 0.0 for f in fills)
        exit_times = [f.exit_time for f in fills if f.exit_time]
        exit_time = max(exit_times) if exit_times else None

        # Exit reason: combine multi-fill reasons (e.g., "TP1+TSL")
        reasons = [getattr(f, "exit_reason", None) for f in fills]
        reasons = [r for r in reasons if r]
        exit_reason = "+".join(reasons) if reasons else ""

        # Exit price: weighted average for multi-fill, or single fill's exit_price
        exit_prices = [
            (float(f.exit_price), abs(float(f.net_profit)) if f.net_profit else 1.0)
            for f in fills if f.exit_price
        ]
        if exit_prices:
            total_weight = sum(w for _, w in exit_prices)
            avg_exit = sum(p * w for p, w in exit_prices) / total_weight if total_weight else exit_prices[0][0]
        else:
            avg_exit = None

        rows.append({
            "Asset": asset_with_side,
            "Side": side,
            "Entry Time": pd.Timestamp(first.entry_time) if first.entry_time else pd.NaT,
            "Exit Time": pd.Timestamp(exit_time) if exit_time else pd.NaT,
            "Net Profit": net_profit,
            "Net Profit %": net_profit_pct,
            "Drawdown": mae,
            "Run-up": mfe,
            "Commission": commission,
            "Quantity": quantity,
            "Entry Price": float(first.entry_price) if first.entry_price else 0.0,
            "Exit Price": avg_exit,
            "Exit Reason": exit_reason,
        })

    # Return all columns (STANDARD_COLS + extended)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# PnL matrix builders (used by correlation, optimizer, etc.)
# ---------------------------------------------------------------------------

def get_pnl_matrix(df_trades: pd.DataFrame, timeframe: str = "D") -> pd.DataFrame:
    """Build a PnL matrix (Date rows × Asset columns) from standardized trades.

    Parameters
    ----------
    timeframe : "D" (daily), "W" (weekly), "ME" (monthly)
    """
    if df_trades.empty or "Exit Time" not in df_trades.columns:
        return pd.DataFrame()

    df = df_trades.copy()
    df["Exit Time"] = pd.to_datetime(df["Exit Time"])
    df["Date"] = df["Exit Time"].dt.floor("D")

    daily = df.pivot_table(
        index="Date", columns="Asset", values="Net Profit", aggfunc="sum",
    ).fillna(0)

    # Fill gaps for continuous timeline
    if not daily.empty:
        full_range = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
        daily = daily.reindex(full_range).fillna(0)

    if timeframe == "D":
        return daily
    return daily.resample(timeframe).sum()


def get_split_pnl_matrix(df_trades: pd.DataFrame, timeframe: str = "D") -> pd.DataFrame:
    """Like get_pnl_matrix but splits each asset into Asset_Long / Asset_Short."""
    if df_trades.empty or "Exit Time" not in df_trades.columns:
        return pd.DataFrame()

    df = df_trades.copy()

    def _split_name(row: pd.Series) -> str:
        asset = row["Asset"]
        side = row["Side"]
        if asset.endswith(f"_{side}"):
            return asset
        return f"{asset}_{side}"

    df["SplitAsset"] = df.apply(_split_name, axis=1)
    df["Exit Time"] = pd.to_datetime(df["Exit Time"])
    df["Date"] = df["Exit Time"].dt.floor("D")

    daily = df.pivot_table(
        index="Date", columns="SplitAsset", values="Net Profit", aggfunc="sum",
    ).fillna(0)

    if not daily.empty:
        full_range = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
        daily = daily.reindex(full_range).fillna(0)

    if timeframe == "D":
        return daily
    return daily.resample(timeframe).sum()


# ---------------------------------------------------------------------------
# Strategy data loader (applies filters from strategy config)
# ---------------------------------------------------------------------------

def load_strategy_data(config: dict) -> pd.DataFrame:
    """Load and filter data based on a strategy config dict.

    Applies: directory loading, group multi-TP, min trades, min PF, WFE filter.
    Does NOT apply: date filters, side filters, manual asset selection.
    """
    if not config:
        return pd.DataFrame(columns=STANDARD_COLS)

    # Avoid circular import — wfe is in the same package
    from backend.analysis.benchmark import compute_benchmark_robustness_filter
    from backend.analysis.wfe import calculate_wfe_metrics

    dir_mode = config.get("dir_mode", "split")
    group_multi_tp = config.get("group_multi_tp", True)

    df = pd.DataFrame()

    if dir_mode == "single":
        reports_dir = config.get("single_dir", "")
        if reports_dir and os.path.exists(reports_dir):
            df = load_tv_reports(reports_dir, group_multi_tp=group_multi_tp)
    else:
        dfs = []
        long_dir = config.get("long_dir", "")
        short_dir = config.get("short_dir", "")
        if long_dir and os.path.exists(long_dir):
            dfs.append(load_tv_reports(long_dir, force_side="Long", group_multi_tp=group_multi_tp))
        if short_dir and os.path.exists(short_dir):
            dfs.append(load_tv_reports(short_dir, force_side="Short", group_multi_tp=group_multi_tp))
        if dfs:
            df = pd.concat(dfs, ignore_index=True)

    if df.empty:
        return pd.DataFrame(columns=STANDARD_COLS)

    # Min trades / min PF filter
    min_trades = config.get("min_trades", 0)
    min_pf = float(config.get("min_pf", 0.0))

    if min_trades > 0 or min_pf > 0:
        stats = df.groupby("Asset")["Net Profit"].agg(
            Count="count",
            ProfitFactor=lambda x: (
                x[x > 0].sum() / abs(x[x <= 0].sum())
                if abs(x[x <= 0].sum()) > 0
                else float("inf")
            ),
        )
        valid = stats[(stats["Count"] >= min_trades) & (stats["ProfitFactor"] >= min_pf)].index
        df = df[df["Asset"].isin(valid)]

    if df.empty:
        return pd.DataFrame(columns=STANDARD_COLS)

    # WFE filter
    if config.get("wfe_enabled", False):
        wfe_tf = config.get("wfe_tf", "30m")
        unseen = config.get("unseen_candles", 4000)
        total = config.get("total_samples", 20000)
        min_wfe = float(config.get("min_wfe", 0.6))
        max_wfe = float(config.get("max_wfe", 10.0))
        min_oos_pf = float(config.get("min_oos_pf", 1.0))

        passed = []
        for asset in df["Asset"].unique():
            trades = df[df["Asset"] == asset]
            m = calculate_wfe_metrics(trades, unseen, total, wfe_tf)
            if m and min_wfe <= m["WFE"] <= max_wfe and m["OOS_PF"] >= min_oos_pf:
                passed.append(asset)
        df = df[df["Asset"].isin(passed)]

    # Benchmark robustness filter
    if config.get("benchmark_enabled", False) and config.get("benchmark_apply_filter", False):
        df = compute_benchmark_robustness_filter(df, config)

    return df
