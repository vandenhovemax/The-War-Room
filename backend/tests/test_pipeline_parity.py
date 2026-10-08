"""Pipeline Parity Snapshot — infrastructure-drift detector.

STATUS: sandbox. Review, run locally, then decide whether to move to
`backend/tests/test_pipeline_parity.py`.

WHY THIS EXISTS
---------------
`test_golden_reference.py` locks down formula correctness (24 metrics,
hand-computed).
`test_parity_canary.py` locks down "stored trades reproduce on re-run"
using the user's live DB.

Neither catches: **"I changed the storage backend (or an indicator, or a
float-precision default) and now the same inputs produce different
outputs."**

This file closes that gap. It runs the FULL pipeline
(OHLCV → BacktestEngine → run_batch_selection) over a FIXED synthetic
fixture and snapshots every number. Any deviation from the committed
golden snapshot fails the test with a human-readable diff.

USAGE
-----
First time (or after an intentional change):
    pytest backend/tests/test_pipeline_parity.py::test_regenerate_golden_snapshot -v
    # Review the emitted JSON at `backend/tests/fixtures/pipeline_parity_golden.json`
    # Commit it to git.

Every subsequent run (CI, pre-push, before-merge):
    pytest backend/tests/test_pipeline_parity.py -v
    # If anything diverges, you'll see exactly which field drifted.

DESIGN PRINCIPLES
-----------------
1. Fully deterministic. No timestamps-as-entropy, no unseeded RNG, no
   network calls, no real DB.
2. Small. Runs in <5 seconds so it can live in the pre-commit hook.
3. Every output is captured. Trades, PnLs, selected batches, asset
   picks, OOS metrics — all serialised to a single nested dict.
4. Floats are rounded to 6 decimal places before snapshotting. Cross-
   platform sub-penny noise is expected; meaningful drift is not.
5. Zero coupling to storage. This catches code-path drift; a separate
   storage-parity harness (dual-write) catches storage drift.

WHAT IT DOES NOT CATCH
----------------------
- Real-data edge cases (missing bars, DST folds, Bybit quirks).
  Add fixtures from real OHLCV later if you want broader coverage.
- Live-execution divergence. The backtest-vs-live parity problem is
  out of scope for this file — see ADR-006 in the review plan.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.engine import BacktestConfig, BacktestEngine
from backend.analysis.batch_selection import (
    BatchSelectionConfig,
    run_batch_selection,
)
from backend.analysis.wfo_window_gen import generate_rolling_windows
from backend.db.models import BacktestTrade


# ═══════════════════════════════════════════════════════════════════════
# FIXTURE — deterministic OHLCV + simple strategy
# ═══════════════════════════════════════════════════════════════════════

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "pipeline_parity_golden.json"
SNAPSHOT_VERSION = 1  # bump when intentionally regenerating
FLOAT_PRECISION = 6


def _seeded_ohlcv(
    symbol: str,
    start: str = "2023-01-01",
    n_days: int = 600,
    tf_minutes: int = 30,
    seed: int = 0,
) -> pd.DataFrame:
    """Deterministic OHLCV — same seed → same bars, forever.

    Bars are produced from a seeded random walk with a mild drift and
    bounded volatility. Enough structure that a crossover strategy
    produces a meaningful number of trades.
    """
    rng = np.random.default_rng(seed)
    bars_per_day = int(24 * 60 / tf_minutes)
    n_bars = n_days * bars_per_day
    # Drift chosen so BTC-like symbols trend up, others are flatter
    drift = {"BTCUSDT": 0.0002, "ETHUSDT": 0.0001, "SOLUSDT": 0.00005}.get(symbol, 0.0)
    vol = 0.008
    returns = rng.normal(loc=drift, scale=vol, size=n_bars)
    # Start price also deterministic per symbol
    start_price = {"BTCUSDT": 30000.0, "ETHUSDT": 2000.0, "SOLUSDT": 50.0}.get(symbol, 100.0)
    close = start_price * np.exp(np.cumsum(returns))
    open_ = np.concatenate(([start_price], close[:-1]))
    # High/low derived from open/close with a bounded intrabar range
    range_pct = np.abs(rng.normal(0, 0.004, size=n_bars))
    high = np.maximum(open_, close) * (1 + range_pct)
    low = np.minimum(open_, close) * (1 - range_pct)
    volume = rng.uniform(100, 1000, size=n_bars)

    idx = pd.date_range(start=start, periods=n_bars, freq=f"{tf_minutes}min")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class _SMACrossoverStrategy(BaseStrategy):
    """Minimal SMA(10) x SMA(30) crossover.

    - Long when fast crosses above slow
    - Short when fast crosses below slow (if direction_filter allows)
    - Fixed SL at 2% from entry, TP at 4%
    - Single position at a time (max_pyramiding=1 via engine config)
    """

    def init(self):
        closes = self._df["close"].values
        self._fast = pd.Series(closes).rolling(10).mean().values
        self._slow = pd.Series(closes).rolling(30).mean().values

    def next(self):
        i = self._bar
        if i < 31:  # warmup
            return
        fast_now, fast_prev = self._fast[i], self._fast[i - 1]
        slow_now, slow_prev = self._slow[i], self._slow[i - 1]
        if np.isnan(fast_prev) or np.isnan(slow_prev):
            return

        has_position = bool(self._positions)
        close = self._df["close"].iloc[i]

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now

        if not has_position:
            if crossed_up:
                self.entry(id="long_entry", direction="long", qty=1.0)
                self.exit(id="long_sl", from_entry="long_entry", stop=close * 0.98)
                self.exit(id="long_tp", from_entry="long_entry", limit=close * 1.04)
            elif crossed_down:
                self.entry(id="short_entry", direction="short", qty=1.0)
                self.exit(id="short_sl", from_entry="short_entry", stop=close * 1.02)
                self.exit(id="short_tp", from_entry="short_entry", limit=close * 0.96)


# ═══════════════════════════════════════════════════════════════════════
# PIPELINE RUNNER
# ═══════════════════════════════════════════════════════════════════════

def _run_pipeline() -> dict[str, Any]:
    """Run the full pipeline once and return a fully-serialisable snapshot.

    Steps:
      1. Generate deterministic OHLCV for 3 symbols
      2. Run the backtest engine for each symbol → trades
      3. Assemble trades into 3 synthetic batches (one per symbol, all long)
      4. Run run_batch_selection with fixed windows + fixed config
      5. Snapshot: trades (summary stats per symbol), optimiser output (per-window
         selected_batches, asset picks, OOS metrics)
    """
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    bt_config = BacktestConfig(
        initial_capital=10_000.0,
        commission_pct=0.055,
        slippage_pct=0.01,
        default_qty=1.0,
        direction_filter="both",
        max_pyramiding=1,
        warmup_bars=0,
        risk_pct=0.0,
    )

    all_trades: list[BacktestTrade] = []
    trade_summary: dict[str, dict] = {}
    run_batch_map: dict[int, int] = {}
    next_trade_id = 0

    # One run per symbol, one batch per symbol (clean separation for the optimiser)
    for run_id, symbol in enumerate(symbols, start=1):
        df = _seeded_ohlcv(symbol=symbol, seed=run_id)
        engine = BacktestEngine(bt_config)
        strat = _SMACrossoverStrategy()
        result = engine.run(strat, df, symbol=symbol, timeframe="30")

        # BacktestResult.trades is list[OrderFill] — one per fill (entry OR exit).
        # A "trade" is an exit fill (where pnl is realised and entry_time is set).
        raw_fills = result.trades if hasattr(result, "trades") else []
        exit_fills = [t for t in raw_fills if getattr(t, "action", "") == "exit"]

        trade_summary[symbol] = {
            "n_trades": len(exit_fills),
            "total_net_profit": float(round(
                sum(getattr(t, "pnl", 0.0) for t in exit_fills), FLOAT_PRECISION
            )),
            "first_entry": (
                exit_fills[0].entry_time.isoformat()
                if exit_fills and exit_fills[0].entry_time else None
            ),
            "last_exit": (
                exit_fills[-1].timestamp.isoformat() if exit_fills else None
            ),
            # Hash the full exit-fill list so any single-trade change still fires
            "trades_hash": _hash_trades(exit_fills, symbol=symbol),
        }

        # Convert OrderFill → BacktestTrade-shaped duck type for the optimiser
        for t in exit_fills:
            next_trade_id += 1
            all_trades.append(_to_backtest_trade(t, run_id=run_id, tid=next_trade_id, symbol=symbol))
        run_batch_map[run_id] = 100 + run_id  # batch_id: 101, 102, 103

    # Optimiser input: fixed windows, fixed config
    windows = generate_rolling_windows(
        is_months=12,
        oos_months=3,
        last_oos_end="2024-08-31",
        data_start="2023-01-01",
    )
    bs_config = BatchSelectionConfig(
        max_batches=2,
        assets_per_batch=3,
        n_candidates=3,
        min_calibrated=2,
        batch_rank_metric="net_pnl",
        cal_rank_metric="net_pnl",
        asset_rank_metric="net_pnl",
        min_is_days=10,
        min_pf=0.5,
        min_asset_days=3,
        asset_wr_gate=0.0,
        q_starts=1,
    )
    # Pin the wall clock — the live window's oos_end tracks real `today`,
    # which would drift the golden daily. Fixed to 2024-10-15 (> last
    # historical oos_end 2024-08-31) so the live-window IS-extension path
    # is exercised deterministically.
    with patch(
        "backend.analysis.batch_selection._clock_today",
        return_value=date(2024, 10, 15),
    ):
        bs_result = run_batch_selection(all_trades, windows, bs_config, run_batch_map)

    # Serialise optimiser output deterministically — only the decision-critical fields
    opt_snapshot = []
    for w in bs_result.get("windows", []):
        window_rec = {
            "window_num": w.get("window_num"),
            "is_start": w.get("is_start"),
            "is_end": w.get("is_end"),
            "oos_start": w.get("oos_start"),
            "oos_end": w.get("oos_end"),
            "candidates": [],
        }
        for cand in w.get("candidates", []) or []:
            window_rec["candidates"].append({
                "selected_batches": sorted(cand.get("selected_batches", [])),
                "n_assets": cand.get("n_assets", 0),
                "asset_keys": sorted([a.get("key") for a in cand.get("asset_details", [])]),
                "oos_metrics": _round_dict(cand.get("oos_metrics", {})),
                "is_metrics": _round_dict(cand.get("is_metrics", {})),
            })
        opt_snapshot.append(window_rec)

    return {
        "version": SNAPSHOT_VERSION,
        "fixture": {
            "symbols": symbols,
            "n_days": 600,
            "tf_minutes": 30,
            "strategy": "_SMACrossoverStrategy",
            "bt_config_hash": _hash_dict(asdict(bt_config)),
            "bs_config_hash": _hash_dict(asdict(bs_config)),
        },
        "trades_per_symbol": trade_summary,
        "optimiser_windows": opt_snapshot,
        "all_trades_hash": _hash_trade_list(all_trades),
        "summary_totals": {
            "total_trades": sum(s["n_trades"] for s in trade_summary.values()),
            "grand_net_profit": float(round(
                sum(s["total_net_profit"] for s in trade_summary.values()),
                FLOAT_PRECISION,
            )),
            "n_windows_with_candidates": sum(
                1 for w in opt_snapshot if w["candidates"]
            ),
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _round_dict(d: dict | None) -> dict:
    """Round all float values in a (potentially nested) dict."""
    if d is None:
        return {}
    out = {}
    for k, v in sorted(d.items()):
        if isinstance(v, float):
            # Cast to float() to strip numpy.float64 — matches JSON round-trip semantics
            out[k] = float(round(v, FLOAT_PRECISION))
        elif isinstance(v, dict):
            out[k] = _round_dict(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [
                float(round(x, FLOAT_PRECISION)) if isinstance(x, float) else x for x in v
            ]
        else:
            out[k] = v
    return out


def _hash_dict(d: dict) -> str:
    """Stable hash of a dict — used for config fingerprints."""
    blob = json.dumps(_round_dict(d), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _hash_trades(trades, symbol: str = "") -> str:
    """Hash of all trade fields that affect downstream decisions.

    Accepts either:
      - OrderFill exit fills (fields: side/entry_time/timestamp/entry_price/price/pnl/qty)
      - BacktestTrade duck-types (fields: symbol/side/entry_time/exit_time/entry_price/exit_price/net_profit/quantity)
    The `symbol` kwarg is a fallback for OrderFill which doesn't carry symbol.
    """
    rows = []
    for t in trades:
        rows.append((
            getattr(t, "symbol", symbol),
            getattr(t, "side", ""),
            _iso(getattr(t, "entry_time", None)),
            _iso(getattr(t, "exit_time", None) or getattr(t, "timestamp", None)),
            round(getattr(t, "entry_price", 0.0), FLOAT_PRECISION),
            round(
                getattr(t, "exit_price", 0.0) or getattr(t, "price", 0.0),
                FLOAT_PRECISION,
            ),
            round(
                getattr(t, "net_profit", 0.0) or getattr(t, "pnl", 0.0),
                FLOAT_PRECISION,
            ),
            round(
                getattr(t, "quantity", 0.0) or getattr(t, "qty", 0.0),
                FLOAT_PRECISION,
            ),
        ))
    blob = json.dumps(rows, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _hash_trade_list(trades) -> str:
    """Hash of the assembled BacktestTrade-shaped list."""
    return _hash_trades(trades)


def _iso(x) -> str | None:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.isoformat()
    return str(x)


def _to_backtest_trade(fill, run_id: int, tid: int, symbol: str = ""):
    """Adapter: OrderFill exit fill → minimal BacktestTrade duck-type the optimiser accepts.

    OrderFill field mapping:
      - pnl        → net_profit
      - qty        → quantity
      - price      → exit_price (fill.price is the exit price on exit fills)
      - timestamp  → exit_time
      - entry_time → entry_time (already set on exit fills)
    """
    from dataclasses import dataclass

    @dataclass
    class _BT:
        id: int
        run_id: int
        symbol: str
        side: str
        entry_time: datetime
        exit_time: datetime
        net_profit: float
        entry_price: float = 0.0
        exit_price: float = 0.0
        quantity: float = 0.0
        position_id: str = ""

    entry_t = getattr(fill, "entry_time", None) or datetime(2023, 1, 1)
    exit_t = getattr(fill, "timestamp", None) or datetime(2023, 1, 1)

    return _BT(
        id=tid,
        run_id=run_id,
        symbol=symbol or getattr(fill, "symbol", ""),
        side=(getattr(fill, "side", "") or "long").capitalize(),
        entry_time=entry_t,
        exit_time=exit_t,
        net_profit=float(getattr(fill, "pnl", 0.0)),
        entry_price=float(getattr(fill, "entry_price", 0.0)),
        exit_price=float(getattr(fill, "price", 0.0)),
        quantity=float(getattr(fill, "qty", 0.0)),
        position_id=str(getattr(fill, "position_id", f"P{tid}")),
    )


# ═══════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════

def test_pipeline_snapshot_matches_golden():
    """The full pipeline must produce bit-identical output vs. committed golden.

    If this fails:
      1. Run `test_regenerate_golden_snapshot` to see the new snapshot.
      2. Diff against git HEAD of the golden JSON.
      3. Confirm every drifted field is EXPLAINED by the change you made.
      4. Only then, commit the new golden. If any drift is unexpected,
         the change has silently altered pipeline results — stop and fix.
    """
    if not GOLDEN_PATH.exists():
        pytest.skip(
            f"No golden snapshot at {GOLDEN_PATH}. "
            "Run test_regenerate_golden_snapshot first."
        )

    current = _run_pipeline()
    with open(GOLDEN_PATH) as f:
        golden = json.load(f)

    # Version check first — different snapshot versions mean intentional change
    assert current["version"] == golden["version"], (
        f"Snapshot version mismatch: code={current['version']}, "
        f"golden={golden['version']}. Regenerate if intentional."
    )

    # Deep-compare every field, reporting the first drift found
    drifts = _find_drifts(golden, current, path="")
    assert not drifts, (
        "Pipeline parity BROKEN — the same inputs produced different outputs.\n"
        "Drifts found:\n  " + "\n  ".join(drifts[:20])
        + (f"\n  ... and {len(drifts) - 20} more" if len(drifts) > 20 else "")
    )


def test_regenerate_golden_snapshot(request):
    """Emit a fresh snapshot. Only run this when intentionally accepting a change.

    Usage:
        pytest -k test_regenerate_golden_snapshot -s

    The snapshot is written to the fixtures path. Review the diff vs. git HEAD
    before committing.
    """
    # Guard: require explicit opt-in via env var to prevent accidental overwrites
    import os
    if not os.environ.get("REGENERATE_PARITY_GOLDEN"):
        pytest.skip(
            "Set REGENERATE_PARITY_GOLDEN=1 to write a new golden snapshot. "
            "Only do this when intentionally accepting a pipeline change."
        )

    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    snapshot = _run_pipeline()
    with open(GOLDEN_PATH, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True, default=str)
    print(f"\n[parity] wrote new golden snapshot to {GOLDEN_PATH}")
    print(f"[parity] version={snapshot['version']}")
    print(f"[parity] total_trades={snapshot['summary_totals']['total_trades']}")
    print(f"[parity] grand_net_profit={snapshot['summary_totals']['grand_net_profit']}")
    print(f"[parity] all_trades_hash={snapshot['all_trades_hash']}")


def test_pipeline_is_deterministic():
    """Running the pipeline twice in the same process must yield identical output.

    Cheap sanity check that catches unseeded RNG introduction before it reaches
    the golden-snapshot test.
    """
    a = _run_pipeline()
    b = _run_pipeline()
    drifts = _find_drifts(a, b, path="")
    assert not drifts, (
        "Pipeline is non-deterministic within a single process.\n"
        "This usually means an unseeded RNG snuck in somewhere.\n"
        "Drifts:\n  " + "\n  ".join(drifts[:10])
    )


# ═══════════════════════════════════════════════════════════════════════
# DIFFING
# ═══════════════════════════════════════════════════════════════════════

def _find_drifts(golden: Any, current: Any, path: str) -> list[str]:
    """Recursive deep-diff that returns a human-readable list of drift sites."""
    drifts: list[str] = []
    if type(golden) is not type(current):
        drifts.append(f"{path}: type {type(golden).__name__} → {type(current).__name__}")
        return drifts
    if isinstance(golden, dict):
        all_keys = sorted(set(golden) | set(current))
        for k in all_keys:
            sub = f"{path}.{k}" if path else k
            if k not in golden:
                drifts.append(f"{sub}: added in current = {current[k]!r}")
            elif k not in current:
                drifts.append(f"{sub}: removed in current (golden was {golden[k]!r})")
            else:
                drifts.extend(_find_drifts(golden[k], current[k], sub))
    elif isinstance(golden, list):
        if len(golden) != len(current):
            drifts.append(f"{path}: length {len(golden)} → {len(current)}")
            return drifts
        for i, (g, c) in enumerate(zip(golden, current)):
            drifts.extend(_find_drifts(g, c, f"{path}[{i}]"))
    elif isinstance(golden, float):
        # Floats were already rounded to FLOAT_PRECISION in _round_dict; any
        # remaining diff is meaningful.
        if golden != current:
            drifts.append(f"{path}: {golden} → {current}")
    else:
        if golden != current:
            drifts.append(f"{path}: {golden!r} → {current!r}")
    return drifts
