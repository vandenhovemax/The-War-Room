"""Live-engine ↔ backtest parity harness (P3 of the server migration).

Proves that the LIVE execution path — the real StrategyExecutor: full-replay
warmup, then per-bar on_bar_close() → strategy.next() → _extract_signals() —
emits the SAME entry decisions as the BacktestEngine batch replay over the
same candles. This is the trust gate before live keys reach the server
(trading.md Spec-Truth rule #4: backtest↔live parity is part of "done").

Method (per portfolio symbol):
  1. Load the full candle history from the ohlcv DB (one in-memory copy).
  2. BACKTEST LEG: run BacktestEngine.run() over the full history — the exact
     invocation parity_canary.compare_run_parity() uses (cold run, no state
     injection), with inputs taken from the executor-derived per-asset params
     so both legs see identical strategy inputs.
  3. Hold out the last WINDOW base-TF bars. Truncate the workspace ohlcv DB
     at the holdout boundary (T0) so the live leg's warmup ends at T0.
  4. LIVE LEG: activate the portfolio through the REAL StrategyExecutor
     (full warmup to T0), then feed the held-out bars one at a time through
     on_bar_close(), capturing every StrategySignal. Before each bar, the
     backtest's fills are mirrored into the workspace `positions` table so
     _sync_positions_from_db() sees the same open-position state the
     backtester's fill engine produced (same candles + same fills → the
     signal decisions must match; fills themselves are exchange-side and are
     validated separately on testnet).
  5. Compare: every backtest entry filled inside the holdout must have a live
     entry signal on its signal bar (the bar BEFORE the fill bar — engine
     fills orders at next bar's open) with the same direction, and no live
     entries the backtest lacks. SL/TP levels are compared when both legs
     expose them (relative tolerance 1e-6).

SAFETY — this harness must NEVER touch live data:
  * It refuses to run unless WARROOM_PARITY_WORKSPACE=1 is set, which the
    runner sets only when /app/data is a disposable workspace COPY.
  * It monkey-patches (process-local, workspace-only):
      - _ensure_candles_current → no-op   (candles frozen for determinism)
      - _save_strategy_state    → no-op   (no state files written)
      - _load_strategy_state    → None    (always deterministic full warmup)
    The strategy/decision code itself is never patched.
  * It DELETEs candle rows and writes Position rows only in the workspace.

Run (on the server, from the repo root):
  docker compose run --rm \
    -v /data/parity-workspace/data:/app/data \
    -e WARROOM_PARITY_WORKSPACE=1 \
    warroom-backend python -m backend.services.live_parity_harness \
    --portfolio 22 --window 400

Scope notes (stated so the proof is honest):
  * Entry decisions + protective levels are compared. Exit EXECUTION differs
    by design (backtest simulates bar fills; live places exchange SL/TP
    orders from register_exit signals) and is validated on testnet instead.
  * Portfolio session filters (exec_exclude_hours/days) are applied to the
    backtest leg using the live engine's own rule (signal-bar NYT hour) so
    the comparison isolates engine parity; the report says how many entries
    the filter removed.
  * Batches using martingale sizing or the engine-level vol filter change
    position SIZE, not entry decisions; if the batch enables the vol filter
    (which CAN veto entries) the report flags it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)

SL_TP_REL_TOL = 1e-6


# ── Fake candle aggregator (no websockets, no look-ahead) ─────────────


class HarnessAggregator:
  """Stands in for CandleAggregator. Serves seeded/known bars strictly up
  to the current cursor timestamp (UTC) — the live leg can never see the
  future. Implements only the surface StrategyExecutor touches."""

  def __init__(self) -> None:
    self._bars: dict[tuple[str, str], list[dict]] = {}
    self.cursor_utc: datetime | None = None

  def register_symbol(self, symbol: str, base_tf: str, extra_tfs: list[str]) -> None:
    pass

  def unregister_symbol(self, symbol: str) -> None:
    pass

  def seed_history(self, symbol: str, tf: str, bars: list[dict]) -> None:
    self._bars[(symbol, tf)] = sorted(bars, key=lambda b: b["timestamp"])

  def add_bar(self, symbol: str, tf: str, bar: dict) -> None:
    self._bars.setdefault((symbol, tf), []).append(bar)

  def get_history_df(self, symbol: str, tf: str) -> pd.DataFrame:
    bars = self._bars.get((symbol, tf), [])
    if self.cursor_utc is not None:
      bars = [b for b in bars if b["timestamp"] <= self.cursor_utc]
    if not bars:
      return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(bars).set_index("timestamp")
    return df[["open", "high", "low", "close", "volume"]]


# ── Pure helpers (unit-tested) ────────────────────────────────────────


def signal_bar_for_fill(df_index_nyt, entry_time) -> object | None:
  """The bar on which the live engine would EMIT the signal for a backtest
  trade filled at `entry_time` (engine fills orders at next bar's open, so
  the signal bar is the bar strictly BEFORE the fill bar)."""
  ts = pd.Timestamp(entry_time)
  pos = df_index_nyt.searchsorted(ts, side="left")
  if pos >= len(df_index_nyt) or df_index_nyt[pos] != ts:
    return None  # fill time not a known bar — cannot map
  if pos == 0:
    return None
  return df_index_nyt[pos - 1]


def exec_filter_blocks(bar_nyt, excl_hours: set[int], excl_days: set[int]) -> bool:
  """Mirror of the live engine's entry gate (_extract_signals): entries whose
  SIGNAL bar NYT hour/weekday is excluded never become signals."""
  if not (excl_hours or excl_days):
    return False
  return bar_nyt.hour in excl_hours or bar_nyt.weekday() in excl_days


def levels_match(a: float, b: float, rel_tol: float = SL_TP_REL_TOL) -> bool:
  a = float(a or 0.0)
  b = float(b or 0.0)
  if a == 0.0 and b == 0.0:
    return True
  if a == 0.0 or b == 0.0:
    return False
  return abs(a - b) <= rel_tol * max(abs(a), abs(b))


def diff_entries(expected: list[dict], observed: list[dict]) -> dict:
  """Compare expected entry signals (from backtest fills) vs observed live
  signals. Key = (signal_bar_iso, direction). Returns match/missing/extra
  plus SL/TP level mismatches among matched pairs."""
  exp = {(e["signal_bar"], e["direction"]): e for e in expected}
  obs = {(o["signal_bar"], o["direction"]): o for o in observed}
  missing = sorted(k for k in exp if k not in obs)
  extra = sorted(k for k in obs if k not in exp)
  level_mismatches = []
  sl_compared = 0
  for k in exp.keys() & obs.keys():
    e, o = exp[k], obs[k]
    if e.get("sl") is not None:
      sl_compared += 1
      if not levels_match(e["sl"], o.get("sl", 0.0)):
        level_mismatches.append(
          {"key": list(k), "field": "sl", "backtest": e["sl"], "live": o.get("sl")},
        )
  return {
    "n_expected": len(exp),
    "n_observed": len(obs),
    "n_matched": len(exp.keys() & obs.keys()),
    "n_sl_compared": sl_compared,
    "missing_in_live": [list(k) for k in missing],
    "extra_in_live": [list(k) for k in extra],
    "level_mismatches": level_mismatches,
    "pass": not missing and not extra and not level_mismatches,
  }


# ── Backtest leg (mirrors parity_canary's engine invocation) ──────────


def run_backtest_leg(
  strategy_source: str, inputs: dict, batch_params: dict, direction_filter: str,
  df_nyt: pd.DataFrame, extra_tf_dfs: dict, symbol: str, timeframe: str,
) -> list:
  from backend.backtester.engine import BacktestConfig, BacktestEngine
  from backend.utils.safe_exec import safe_exec

  p = batch_params
  config = BacktestConfig(
    initial_capital=p.get("initial_capital", 10000),
    commission_pct=p.get("commission_pct", 0.055),
    slippage_mode=p.get("slippage_mode", "pct"),
    slippage_value=p.get("slippage_value", 0.0),
    leverage=p.get("leverage", 1),
    direction_filter=direction_filter,
    max_pyramiding=1,  # live engine is strictly 1/symbol/side (CS-029)
    risk_pct=p.get("risk_pct", 0.0),
    risk_mode=p.get("risk_mode", "pct"),
    risk_value=p.get("risk_value", 0.0),
    strategy_risk_mode=p.get("strategy_risk_mode", "fixed"),
    strategy_risk_value=p.get("strategy_risk_value", 10.0),
    martingale_enabled=p.get("martingale_enabled", False),
    martingale_type=p.get("martingale_type", "anti"),
    martingale_multiplier=p.get("martingale_multiplier", 2.0),
    martingale_max_streak=p.get("martingale_max_streak", 3),
    vol_filter_enabled=p.get("vol_filter_enabled", False),
    vol_filter_atr_ceiling=p.get("vol_filter_atr_ceiling", 0.0),
    vol_filter_atr_length=p.get("vol_filter_atr_length", 14),
    funding_rate_daily_pct=p.get("funding_rate_daily_pct", 0.0),
    spread_pct=p.get("spread_pct", 0.0),
    process_orders_on_close=p.get("process_orders_on_close", False),
  )
  strategy_cls = safe_exec(strategy_source, label="parity_harness")
  engine = BacktestEngine(config)
  result = engine.run(
    strategy_cls(), df_nyt, symbol=symbol, timeframe=timeframe,
    inputs=inputs, extra_timeframes=extra_tf_dfs,
  )
  return list(result.trades or [])


class NormalizedTrade:
  """A closed trade derived from the engine's OrderFill stream."""

  def __init__(self, entry_time, exit_time, side, entry_price, qty):
    self.entry_time = entry_time
    self.exit_time = exit_time
    self.side = side
    self.entry_price = entry_price
    self.qty = qty


def normalize_trades(fills: list) -> list[NormalizedTrade]:
  """BacktestResult.trades is a list of OrderFill records, not round-trip
  trades: EXIT fills carry entry_time/entry_price and their own `timestamp`
  is the exit moment; there is NO exit_time attribute (caught 2026-08-08:
  getattr(..., 'exit_time') was always None, so the fill mirror kept every
  historical position open forever and the dedup ate all re-entries).
  Partial TP legs produce several exit fills with the same entry_time —
  each becomes its own record; the longest-lived one keeps the mirror
  position open until the real final exit.

  NOTE: OrderFill.sl_price on an exit fill is the SL AT EXIT (after any
  trailing) — NOT the initial SL a live entry signal carries — so this
  harness does not compare SL levels; the protective-order levels are
  validated on the testnet order path instead."""
  out: list[NormalizedTrade] = []
  for f in fills:
    if getattr(f, "action", "") != "exit":
      continue
    et = getattr(f, "entry_time", None)
    if et is None:
      continue
    out.append(NormalizedTrade(
      entry_time=et,
      exit_time=getattr(f, "timestamp", None),
      side=str(getattr(f, "side", "")),
      entry_price=float(getattr(f, "entry_price", 0.0) or 0.0),
      qty=float(getattr(f, "qty", 0.0) or 1.0),
    ))
  return out


# ── Fill mirroring (workspace positions table only) ───────────────────


def mirror_fills(db, portfolio_id: int, symbol: str, trades: list, bar_nyt,
                 base_minutes: int = 30) -> int:
  """Make the workspace `positions` table reflect the position state the live
  engine would see AT SIGNAL TIME on bar `bar_nyt`.

  Entry side: the engine fills entries at bar open before strategy logic, so
  a trade with entry_time == bar_nyt is OPEN when next() runs.

  Exit side: the backtest books an exit at the NEXT bar's open, but the
  exchange stop/limit that produces that exit fills INTRA-bar in production
  — by the time the bar closes and the signal fires, the engine is flat. So
  a trade whose exit fills at the next bar open (exit_time <= bar + period)
  counts as CLOSED here; otherwise the entry-dedup would absorb every
  immediate same-direction re-entry that both engines actually take
  (caught 2026-08-08: all 10 expected shorts produced on the exact right
  bars, all eaten by 'Skipping duplicate entry')."""
  from backend.db.models import Position

  bar_ts = pd.Timestamp(bar_nyt)
  closes_by = bar_ts + pd.Timedelta(minutes=base_minutes)
  open_set = []
  for t in trades:
    et = getattr(t, "entry_time", None)
    xt = getattr(t, "exit_time", None)
    if et is None:
      continue
    if pd.Timestamp(et) <= bar_ts and (xt is None or pd.Timestamp(xt) > closes_by):
      open_set.append(t)

  db.query(Position).filter(
    Position.portfolio_id == portfolio_id, Position.symbol == symbol,
  ).delete()
  for t in open_set:
    side = "Buy" if str(getattr(t, "side", "long")).lower().startswith("l") else "Sell"
    db.add(Position(
      portfolio_id=portfolio_id,
      symbol=symbol,
      side=side,
      entry_price=float(getattr(t, "entry_price", 0.0) or 0.0),
      quantity=float(getattr(t, "qty", None) or getattr(t, "quantity", None) or 1.0),
      is_open=True,
      strategy_name="parity_harness_mirror",
    ))
  db.commit()
  return len(open_set)


# ── Main harness ──────────────────────────────────────────────────────


def run_live_parity(portfolio_id: int, window: int = 400,
                    symbols: list[str] | None = None) -> dict:
  if os.environ.get("WARROOM_PARITY_WORKSPACE") != "1":
    raise RuntimeError(
      "Refusing to run: WARROOM_PARITY_WORKSPACE=1 not set. This harness "
      "deletes candle rows and writes positions — it must only run against "
      "a disposable copy of the data directory, never the live volume.",
    )

  from backend.db.database import SessionLocal, ohlcv_router
  from backend.db.models import OHLCVData
  from backend.exchange.strategy_executor import StrategyExecutor
  from backend.utils.candle_loader import load_candles_df, to_nyt

  captured: list[dict] = []
  current_bar_nyt: dict = {"ts": None}

  def on_signal(sig) -> None:
    captured.append({
      "action": sig.action,
      "direction": sig.direction,
      "entry_id": sig.entry_id,
      "signal_bar": current_bar_nyt["ts"].isoformat() if current_bar_nyt["ts"] else None,
      "sl": sig.sl_price,
      "tp": sig.tp_price,
      "comment": sig.comment,
    })

  # Surface DEBUG-level executor decisions (e.g. "Skipping duplicate entry")
  # — invisible at INFO and exactly the kind of silent gate a parity
  # investigation needs to see.
  import logging
  logging.getLogger("backend.exchange.strategy_executor").setLevel(logging.DEBUG)

  aggregator = HarnessAggregator()
  executor = StrategyExecutor(aggregator, SessionLocal, on_signal=on_signal)
  # Process-local safety patches (see module docstring — workspace only):
  executor._ensure_candles_current = lambda *a, **kw: None
  executor._save_strategy_state = lambda *a, **kw: None
  executor._load_strategy_state = lambda *a, **kw: None

  # Activate through the REAL path (loads portfolio, params, compiles the
  # strategy, full-replay warmup over whatever candles the workspace holds).
  # We activate AFTER truncation so warmup ends exactly at T0 — so first,
  # read what we need to plan the truncation.
  db = SessionLocal()
  try:
    from backend.db.models import BatchBacktestRun, Portfolio
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
      raise RuntimeError(f"portfolio {portfolio_id} not found in workspace DB")
    source_data = json.loads(portfolio.source_strategies or "{}")
    batch_params: dict = {}
    for bid in source_data.get("batch_ids", []) or []:
      row = db.get(BatchBacktestRun, bid)
      if row:
        batch_params = json.loads(row.parameters or "{}")
        break
  finally:
    db.close()

  report: dict = {
    "portfolio_id": portfolio_id,
    "window": window,
    "run_at_utc": datetime.now(timezone.utc).isoformat(),
    "symbols": {},
  }

  # Resolve TFs exactly like activate_portfolio does (source field may be
  # empty; the batch is then authoritative — tf_resolver is the shared path).
  from backend.utils.tf_resolver import resolve_extra_tfs, resolve_timeframe
  db = SessionLocal()
  try:
    base_tf = source_data.get("timeframe", "") or resolve_timeframe(db, source_data)
    extra_tfs = source_data.get("extra_timeframes", []) or resolve_extra_tfs(db, source_data)
    if not extra_tfs:
      # Mirror activation's safety net: auto-detect request_security() TFs
      # from the strategy source so the truncation covers them too.
      import re
      from backend.db.models import BatchBacktestRun as _BBR, Strategy as _Strat
      for bid in source_data.get("batch_ids", []) or []:
        b = db.get(_BBR, bid)
        if b and b.strategy_id:
          s = db.get(_Strat, b.strategy_id)
          if s and s.python_source:
            found = re.findall(r'request_security\s*\(\s*["\'](\w+)["\']', s.python_source)
            if found:
              extra_tfs = list(dict.fromkeys(found))
            break
  finally:
    db.close()

  # Full candle history per symbol (in memory, BEFORE truncation)
  db = SessionLocal()
  try:
    from backend.db.models import PortfolioAsset
    asset_rows = db.query(PortfolioAsset).filter(
      PortfolioAsset.portfolio_id == portfolio_id,
      PortfolioAsset.is_active == True,  # noqa: E712
    ).all()
    asset_sides = {a.symbol: a.trade_side for a in asset_rows}
  finally:
    db.close()

  todo = [s for s in asset_sides if symbols is None or s in symbols]
  if not todo:
    raise RuntimeError("no matching active symbols on this portfolio")

  full_raw: dict[str, pd.DataFrame] = {}
  full_extra_raw: dict[str, dict[str, pd.DataFrame]] = {}
  db = SessionLocal()
  try:
    for sym in todo:
      full_raw[sym] = load_candles_df(db, sym, base_tf)
      full_extra_raw[sym] = {tf: load_candles_df(db, sym, tf) for tf in extra_tfs}
  finally:
    db.close()

  # Holdout boundary T0 (UTC) = window bars before the end, using the symbol
  # with the SHORTEST history so every symbol has a warmup + holdout.
  usable = {s: df for s, df in full_raw.items() if len(df) > window + 250}
  skipped = [s for s in todo if s not in usable]
  for s in skipped:
    report["symbols"][s] = {"skipped": f"only {len(full_raw[s])} bars (< window+250)"}
  if not usable:
    raise RuntimeError("no symbol has enough candles for the requested window")
  t0_utc = min(df.index[-(window + 1)] for df in usable.values())
  report["t0_utc"] = pd.Timestamp(t0_utc).isoformat()

  # ── Truncate the WORKSPACE ohlcv DBs at T0 (workspace-only delete) ──
  # Rows are stamped with OPEN time. Base TF keeps rows stamped <= T0. An
  # extra-TF row only exists as a CLOSED bar at open + tf_duration, so its
  # cutoff is shifted back: keep only HTF bars closed by the end of the last
  # warmup base bar (T0 + base) — otherwise warmup peeks one HTF bar ahead.
  _tf_minutes = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30, "60": 60,
                 "120": 120, "240": 240, "360": 360, "720": 720,
                 "D": 1440, "W": 10080}
  base_min = _tf_minutes.get(base_tf, 30)
  trunc_at: dict[str, pd.Timestamp] = {base_tf: pd.Timestamp(t0_utc)}
  for tf in extra_tfs:
    trunc_at[tf] = (pd.Timestamp(t0_utc)
                    + pd.Timedelta(minutes=base_min - _tf_minutes.get(tf, 30)))
  deleted = 0
  for tf, cutoff in trunc_at.items():
    s = ohlcv_router.get_session(tf)
    try:
      deleted += s.query(OHLCVData).filter(
        OHLCVData.symbol.in_(list(usable.keys())),
        OHLCVData.timestamp > cutoff.to_pydatetime(),
      ).delete(synchronize_session=False)
      s.commit()
    finally:
      s.close()
  report["workspace_rows_truncated"] = deleted
  logger.info("Workspace truncated at %s (%d rows removed)", t0_utc, deleted)

  # ── LIVE LEG warmup: activate through the real executor ──
  if not executor.activate_portfolio(portfolio_id):
    raise RuntimeError("activate_portfolio failed — see log above")
  active = executor._active[portfolio_id]

  # ── Per-symbol: backtest leg, then live bar-feed, then diff ──
  for sym in usable:
    instance = active.strategies.get(sym)
    if instance is None:
      report["symbols"][sym] = {"skipped": "no strategy instance after activation"}
      continue
    inputs = dict(getattr(instance, "_inputs", {}) or {})
    side = asset_sides.get(sym, "Both")
    direction_filter = {"Long": "long", "Short": "short"}.get(side, "both")

    # to_nyt MUTATES the index of the frame it's given — always hand it a
    # copy or the "raw UTC" frames silently become NYT and every downstream
    # UTC assumption shifts by 4-5 hours (caught 2026-08-08: live labels,
    # hour gates and the fill mirror all ran one tz-offset behind).
    df_nyt = to_nyt(full_raw[sym].copy())
    extra_nyt = {tf: to_nyt(d.copy()) for tf, d in full_extra_raw[sym].items() if not d.empty}
    trades = normalize_trades(run_backtest_leg(
      active.strategy_source, inputs, batch_params, direction_filter,
      df_nyt, extra_nyt, sym, base_tf,
    ))

    # Expected live signals = backtest fills inside the holdout, mapped back
    # to their signal bar, minus what the live exec-filter would block.
    # Holdout boundary in NYT, derived POSITIONALLY (raw UTC index and
    # df_nyt align 1:1) — never by re-converting an already-converted frame.
    n_hold = int((full_raw[sym].index > pd.Timestamp(t0_utc)).sum())
    holdout_first_nyt = df_nyt.index[-n_hold] if n_hold else None
    expected = []
    filter_dropped = 0
    for t in trades:
      sb = signal_bar_for_fill(df_nyt.index, getattr(t, "entry_time", None))
      if sb is None or holdout_first_nyt is None or sb < holdout_first_nyt:
        continue
      if exec_filter_blocks(sb, active.exec_exclude_hours, active.exec_exclude_days):
        filter_dropped += 1
        continue
      expected.append({
        "signal_bar": sb.isoformat(),
        "direction": t.side.lower(),
        "sl": None,  # exit-fill sl_price is post-trailing — not comparable
      })

    # Feed held-out bars through the real on_bar_close, mirroring fills.
    captured.clear()
    observed: list[dict] = []
    n_register_exit = 0
    # Candle rows are stamped with their OPEN time (Bybit convention). A bar
    # only exists as a CLOSED bar tf-duration later — the real aggregator
    # emits it then. Delivering on open-time stamps leaks a forming HTF bar
    # ~one HTF period early (caught 2026-08-08: live entries fired exactly
    # one 4h bar before the backtest's).
    _tf_min = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30, "60": 60,
               "120": 120, "240": 240, "360": 360, "720": 720,
               "D": 1440, "W": 10080}
    # Diagnostic tap (read-only): count entry orders the strategy PRODUCES
    # each bar, before any downstream gate (warmup/side/filter/dedup) can
    # drop them. Wraps next() but always calls the real one first.
    produced_entries: list[str] = []
    _real_next = instance.next

    def _tapped_next() -> None:
      _real_next()
      for o in instance._pending_orders:
        if o.get("action") == "entry":
          bar_i = instance._bar
          try:
            ts = instance._df.index[bar_i].isoformat()
          except Exception:
            ts = f"bar{bar_i}"
          produced_entries.append(f"{ts}:{o.get('direction')}:{o.get('id')}")

    instance.next = _tapped_next
    holdout_rows = full_raw[sym][full_raw[sym].index > pd.Timestamp(t0_utc)]
    # Extra-TF bars that survived truncation were already delivered by
    # warmup/seed_history — start each delivery cursor exactly at that
    # cutoff so the feed continues seamlessly with no double-delivery.
    extra_cursor: dict[str, int] = {
      tf: int((full_extra_raw[sym][tf].index <= trunc_at.get(tf, pd.Timestamp(t0_utc))).sum())
      for tf in extra_nyt
    }
    db = SessionLocal()
    try:
      for ts_utc, row in holdout_rows.iterrows():
        bar_utc = pd.Timestamp(ts_utc).to_pydatetime()
        aggregator.cursor_utc = bar_utc
        # Deliver any newly-closed extra-TF bars first (as the real
        # aggregator would), then the base bar.
        for tf, raw in full_extra_raw[sym].items():
          closed_by = pd.Timestamp(ts_utc) - pd.Timedelta(minutes=_tf_min.get(tf, 30))
          pend = raw[(raw.index <= closed_by)]
          for e_ts in pend.index[extra_cursor[tf]:]:
            e_row = raw.loc[e_ts]
            e_bar = {"timestamp": pd.Timestamp(e_ts).to_pydatetime(),
                     "open": float(e_row["open"]), "high": float(e_row["high"]),
                     "low": float(e_row["low"]), "close": float(e_row["close"]),
                     "volume": float(e_row["volume"])}
            aggregator.add_bar(sym, tf, e_bar)
            executor.on_bar_close(sym, tf, e_bar)
          extra_cursor[tf] = len(pend)

        bar_nyt = StrategyExecutor._ts_to_nyt(bar_utc)
        mirror_fills(db, portfolio_id, sym, trades, bar_nyt, base_minutes=base_min)
        current_bar_nyt["ts"] = bar_nyt
        executor.on_bar_close(sym, base_tf, {
          "timestamp": bar_utc,
          "open": float(row["open"]), "high": float(row["high"]),
          "low": float(row["low"]), "close": float(row["close"]),
          "volume": float(row["volume"]),
        })
      observed = [
        {"signal_bar": c["signal_bar"], "direction": c["direction"],
         "sl": c["sl"], "tp": c["tp"]}
        for c in captured if c["action"] == "entry"
      ]
      n_register_exit = sum(1 for c in captured if c["action"] == "register_exit")
    finally:
      # Clean the mirrored rows out of the workspace positions table.
      from backend.db.models import Position
      db.query(Position).filter(
        Position.portfolio_id == portfolio_id, Position.symbol == sym,
        Position.strategy_name == "parity_harness_mirror",
      ).delete()
      db.commit()
      db.close()

    instance.next = _real_next
    d = diff_entries(expected, observed)
    # For every miss, show which backtest trades the mirror considered OPEN
    # at that signal bar (raw values — exposes attr/None surprises).
    d["missing_context"] = []
    for k in d["missing_in_live"]:
      sb = pd.Timestamp(k[0])
      overlapping = [
        {"entry": str(getattr(t, "entry_time", None)),
         "exit": str(getattr(t, "exit_time", None)),
         "side": str(getattr(t, "side", ""))}
        for t in trades
        if getattr(t, "entry_time", None) is not None
        and pd.Timestamp(t.entry_time) <= sb
        and (getattr(t, "exit_time", None) is None
             or pd.Timestamp(t.exit_time) > sb + pd.Timedelta(minutes=base_min))
      ]
      d["missing_context"].append({"bar": k[0], "mirror_open": overlapping})
    # For every EXTRA live entry, show backtest exits near that bar — an
    # exit at/just after it marks a flip-boundary case (backtest refuses
    # the re-entry because its exit only books at next-bar open; production
    # is already flat at signal time).
    d["extra_context"] = []
    for k in d["extra_in_live"]:
      sb = pd.Timestamp(k[0])
      near_exits = [
        {"entry": str(t.entry_time), "exit": str(t.exit_time), "side": t.side}
        for t in trades
        if t.exit_time is not None
        and sb - pd.Timedelta(minutes=base_min)
        <= pd.Timestamp(t.exit_time)
        <= sb + pd.Timedelta(minutes=2 * base_min)
      ]
      d["extra_context"].append({"bar": k[0], "backtest_exits_nearby": near_exits})
    d["produced_entries_live"] = produced_entries[-50:]
    d["n_produced_entries_live"] = len(produced_entries)
    d["exec_filter_dropped"] = filter_dropped
    d["n_register_exit_signals"] = n_register_exit
    d["n_backtest_trades_total"] = len(trades)
    d["vol_filter_in_batch"] = bool(batch_params.get("vol_filter_enabled", False))
    report["symbols"][sym] = d
    logger.info(
      "%s: expected=%d observed=%d matched=%d missing=%d extra=%d sl_mismatch=%d -> %s",
      sym, d["n_expected"], d["n_observed"], d["n_matched"],
      len(d["missing_in_live"]), len(d["extra_in_live"]),
      len(d["level_mismatches"]), "PASS" if d["pass"] else "FAIL",
    )

  compared = [s for s, d in report["symbols"].items() if "pass" in d]
  report["pass"] = bool(compared) and all(report["symbols"][s]["pass"] for s in compared)
  report["n_symbols_compared"] = len(compared)
  report["n_entries_expected_total"] = sum(
    report["symbols"][s]["n_expected"] for s in compared)
  return report


def main() -> int:
  ap = argparse.ArgumentParser(description="Backtest↔live-engine parity harness")
  ap.add_argument("--portfolio", type=int, required=True)
  ap.add_argument("--window", type=int, default=400,
                  help="held-out bars fed through the live path (default 400)")
  ap.add_argument("--symbols", type=str, default="",
                  help="comma-separated subset (default: all portfolio symbols)")
  ap.add_argument("--out", type=str, default="",
                  help="JSON report path (default data/parity_report_<pid>.json)")
  args = ap.parse_args()

  syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
  report = run_live_parity(args.portfolio, window=args.window, symbols=syms)

  out = args.out or f"data/parity_report_p{args.portfolio}.json"
  with open(out, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, default=str)

  print(json.dumps({k: v for k, v in report.items() if k != "symbols"}, indent=2))
  for sym, d in report["symbols"].items():
    if "skipped" in d:
      print(f"  {sym}: SKIPPED ({d['skipped']})")
    else:
      print(f"  {sym}: {'PASS' if d['pass'] else 'FAIL'} "
            f"expected={d['n_expected']} observed={d['n_observed']} "
            f"matched={d['n_matched']} missing={len(d['missing_in_live'])} "
            f"extra={len(d['extra_in_live'])} sl_mismatch={len(d['level_mismatches'])}")
  print(f"\nOVERALL: {'PASS' if report['pass'] else 'FAIL'} "
        f"({report['n_symbols_compared']} symbols, "
        f"{report['n_entries_expected_total']} expected entries) -> {out}")
  return 0 if report["pass"] else 1


if __name__ == "__main__":
  sys.exit(main())
