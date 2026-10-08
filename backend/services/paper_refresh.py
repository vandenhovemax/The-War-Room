"""Background refresh service for paper/real portfolios.

Downloads latest candles, re-runs strategy, extracts post-activation
trades, and computes live KPIs.

Supports both single-batch and multi-batch (cross-strategy) portfolios.

Hybrid portfolios (mode='hybrid') additionally filter trades through
the basket-at-time-of-trade so the KPI tile counts only trades on
symbols that were actually in the rotated basket when each trade fired
— matching the trades-table view (load_wfas_forward_append) and what
the live engine would have actually executed. Pre-fix the function
re-ran the strategy on every asset the portfolio had ever held and
counted them all, which inflated post-rotation hybrid KPIs because
rotated-out symbols never actually traded but still showed in the tile.
"""
import asyncio
import dataclasses
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backend.analysis.full_metrics import calculate_full_metrics
from backend.api.routes.portfolio_analysis import _serialize
from backend.constants import COMMISSION_PCT
from backend.data.bybit_downloader import BybitDownloader
from backend.data.data_manager import DataManager
from backend.db.database import SessionLocal
from backend.db.models import (
  BatchBacktestRun,
  Portfolio,
  Strategy,
)
from backend.utils.candle_loader import load_candles_df, to_nyt
from backend.utils.logger import get_logger

logger = get_logger(__name__)


# ── Incremental refresh cache (2026-05-11) ──────────────────────────────
#
# Before this cache: paper_refresh.run_refresh did a full strategy replay
# from the first available bar on every refresh, for every
# (portfolio, symbol) pair. With ~30 active portfolios × ~30 symbols and
# tens of thousands of historical bars each, this took ~30 minutes.
#
# The cache stores per-(portfolio, symbol) the strategy's accumulated
# state + the list of REAL completed exit fills (label != "backtest_end"
# is stripped — those are synthetic mark-to-market closes that go stale
# the moment the next bar arrives, so they must be regenerated fresh on
# every refresh, not cached). On the next refresh, the engine resumes
# from the last cached bar via `resume_from_bar`, runs strategy.next()
# only on the new bars, and re-emits fresh backtest_end fills (driven by
# `BacktestConfig.force_close_injected_positions=True`) so the user-facing
# trade list still shows mark-to-market for currently-open positions.
#
# Cache file: data/paper_refresh_state/{portfolio_id}/{symbol}.json
#
# Invalidation: cache is dropped when (a) `params_hash` no longer matches
# (strategy params or python_source changed), (b) the file is missing or
# unreadable, (c) the cached `last_bar_timestamp` doesn't sit at the
# same index in the current df (data was re-downloaded or bars shifted),
# or (d) the OrderFill schema changed. On any miss the engine falls back
# to a full replay (current behavior) and rebuilds the cache.
def _refresh_cache_root() -> Path:
  return Path(__file__).resolve().parents[2] / "data" / "paper_refresh_state"


def _refresh_cache_path(portfolio_id: int, symbol: str) -> Path:
  safe = re.sub(r"[^A-Za-z0-9_.-]", "_", symbol)
  return _refresh_cache_root() / str(portfolio_id) / f"{safe}.json"


def _params_hash(python_source: str, direction: str, params: dict) -> str:
  """Hash that invalidates cache when strategy logic or params change."""
  inputs = params.get("strategy_inputs") or {}
  payload = {
    "src": hashlib.sha1(python_source.encode("utf-8")).hexdigest(),
    "dir": direction,
    "inputs": json.dumps(inputs, sort_keys=True, default=str),
    "commission_pct": params.get("commission_pct", COMMISSION_PCT),
    "max_pyramiding": params.get("max_pyramiding", 1),
    "warmup_bars": params.get("warmup_bars", 0),
    "process_orders_on_close": params.get("process_orders_on_close", False),
  }
  return hashlib.sha1(
    json.dumps(payload, sort_keys=True).encode("utf-8")
  ).hexdigest()


def _serialize_trades(trades: list) -> list[dict]:
  """Convert OrderFill dataclass instances to JSON-safe dicts."""
  out = []
  for t in trades:
    d = dataclasses.asdict(t)
    for k in ("timestamp", "entry_time", "sl_past_be_time"):
      v = d.get(k)
      if v is not None and hasattr(v, "isoformat"):
        d[k] = v.isoformat()
    out.append(d)
  return out


def _deserialize_trades(rows: list[dict]) -> list:
  """Rebuild OrderFill instances from cached dicts.

  Raises TypeError if the OrderFill schema has changed — caller treats
  that as a hard cache miss and triggers a full rerun.
  """
  from backend.backtester.models import OrderFill
  out = []
  for d in rows:
    d = dict(d)
    for k in ("timestamp", "entry_time", "sl_past_be_time"):
      v = d.get(k)
      if isinstance(v, str):
        try:
          d[k] = pd.Timestamp(v)
        except (ValueError, TypeError):
          d[k] = None
    out.append(OrderFill(**d))
  return out


def _strip_backtest_end(trades: list) -> list:
  """Drop synthetic mark-to-market fills (label='backtest_end').

  These are emitted by the engine at end-of-backtest for any position
  still open at the last bar. They're useful for the user-facing trade
  list (shows currently-open positions) but go stale as soon as another
  bar arrives — every refresh must regenerate them fresh. So we strip
  them on cache save and rely on the engine to re-emit at the new
  last bar via `force_close_injected_positions=True`.
  """
  return [
    t for t in trades
    if getattr(t, "label", "") != "backtest_end"
  ]


def _load_refresh_cache(portfolio_id: int, symbol: str) -> dict | None:
  path = _refresh_cache_path(portfolio_id, symbol)
  if not path.exists():
    return None
  try:
    with path.open("r", encoding="utf-8") as f:
      return json.load(f)
  except (OSError, json.JSONDecodeError) as e:
    logger.debug("paper_refresh cache unreadable for p%d/%s: %s", portfolio_id, symbol, e)
    return None


def _save_refresh_cache(
  portfolio_id: int, symbol: str, *,
  params_hash: str,
  last_bar_index: int,
  last_bar_timestamp_iso: str,
  strategy_state: dict,
  ongoing_positions: list,
  trades_serialized: list[dict],
) -> None:
  """Atomically write the refresh cache for one (portfolio, symbol)."""
  path = _refresh_cache_path(portfolio_id, symbol)
  try:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    payload = {
      "params_hash": params_hash,
      "last_bar_index": int(last_bar_index),
      "last_bar_timestamp_iso": last_bar_timestamp_iso,
      "saved_at_iso": datetime.now(timezone.utc).isoformat(),
      "strategy_state": strategy_state,
      "ongoing_positions": ongoing_positions,
      "trades_serialized": trades_serialized,
    }
    with tmp.open("w", encoding="utf-8") as f:
      json.dump(payload, f)
    os.replace(tmp, path)
  except OSError as e:
    logger.warning("paper_refresh cache save failed for p%d/%s: %s", portfolio_id, symbol, e)


def _cleanup_stale_cache(portfolio_id: int, current_symbols: set[str]) -> int:
  """Remove cache files for symbols no longer in the portfolio's active asset list.

  Hybrid portfolios rotate baskets — a symbol traded last month may have
  rotated out today. Without cleanup, its cache file lingers on disk
  forever (never touched, never read, never written). This helper sweeps
  the per-portfolio cache directory and deletes any `{SYMBOL}.json` file
  whose symbol is no longer in `current_symbols`.

  Symbol-name comparison must mirror `_refresh_cache_path`'s sanitization
  (non-alphanumeric → "_") so files written by previous runs can be
  matched against the canonical symbol list.

  SAFETY: when `current_symbols` is empty we REFUSE to sweep. An empty
  set would otherwise be interpreted as "no symbol is current" and the
  helper would delete every cache file — but in practice an empty set
  almost always means the refresh failed to enumerate any assets (DB
  glitch, mid-resolution error, etc.), and wiping the cache in that
  case would force a slow full rerun on every other portfolio's next
  refresh. Better to no-op and rely on a healthy refresh to clean up.

  Returns the number of files deleted. Logs at INFO when anything is
  removed so the operator can see the cleanup happened.
  """
  if not current_symbols:
    logger.debug(
      "paper_refresh cache cleanup p%d: skipped (empty current_symbols)",
      portfolio_id,
    )
    return 0
  cache_dir = _refresh_cache_root() / str(portfolio_id)
  if not cache_dir.exists():
    return 0
  safe_current = {re.sub(r"[^A-Za-z0-9_.-]", "_", s) for s in current_symbols}
  removed = 0
  try:
    for f in cache_dir.iterdir():
      if not f.is_file() or f.suffix != ".json":
        continue
      file_sym = f.stem  # filename without .json
      if file_sym not in safe_current:
        try:
          f.unlink()
          removed += 1
        except OSError as e:
          logger.debug(
            "paper_refresh cache cleanup: failed to remove %s: %s",
            f, e,
          )
  except OSError as e:
    logger.debug(
      "paper_refresh cache cleanup: failed to list %s: %s",
      cache_dir, e,
    )
    return 0

  if removed > 0:
    logger.info(
      "paper_refresh cache cleanup p%d: removed %d stale symbol(s)",
      portfolio_id, removed,
    )
  return removed


def _persist_refresh_status(db, portfolio_id: int, task_id: str, progress: dict[str, dict]):
  """Write current refresh progress to Portfolio.refresh_status (survives restart)."""
  try:
    p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if p:
      p.refresh_status = json.dumps({"task_id": task_id, **progress.get(task_id, {})})
      db.commit()
  except Exception as _exc:
    logger.debug("swallowed: %s", _exc)  # best-effort, don't break the refresh


def run_refresh_all_active() -> dict:
  """Bulk-refresh every active paper/real portfolio. Scheduled weekly
  (Sun 02:00 NYT — between L1 batch refresh at 01:00 and L2 hybrid
  refresh at 03:00) so paper-portfolio KPI snapshots stay current
  without the user having to click Refresh on each row.

  Reconciles each portfolio's `live_metrics_snapshot` to "the truth"
  as recomputed by paper_refresh.run_refresh — which after the
  basket-at-t fix only counts trades the engine could have actually
  placed for hybrid portfolios. Non-hybrid get every replay trade
  (basket frozen).

  Returns a small report dict for the activity feed / logs:
    {started_at, ended_at, portfolios: [...], total_refreshed, errors}

  Each entry in `portfolios`: {id, name, status, error?}.

  Sequential by design — paper_refresh.run_refresh is CPU-heavy
  (re-runs strategies on each asset) and we don't want N concurrent
  refreshes saturating the host. ~30 portfolios × ~30s each ≈ 15-30 min.
  Acceptable for a weekly job.
  """
  started = datetime.now(timezone.utc)
  report: dict = {
    "started_at": started.isoformat(),
    "ended_at": None,
    "portfolios": [],
    "total_refreshed": 0,
    "errors": 0,
  }

  db = SessionLocal()
  try:
    ports = (
      db.query(Portfolio)
      .filter(
        Portfolio.is_active == True,  # noqa: E712
        Portfolio.is_deleted == False,  # noqa: E712
        Portfolio.portfolio_type.in_(("paper", "real")),
      )
      .order_by(Portfolio.id)
      .all()
    )
    pids = [(p.id, p.name) for p in ports]
  finally:
    db.close()

  logger.info("paper_refresh: weekly bulk-refresh — %d active portfolios", len(pids))

  task_prefix = f"weekly-{int(started.timestamp())}"
  for pid, name in pids:
    progress: dict[str, dict] = {}
    task_id = f"{task_prefix}-{pid}"
    try:
      run_refresh(pid, task_id, progress)
      status = progress.get(task_id, {}).get("status", "unknown")
      entry: dict = {"id": pid, "name": name, "status": status}
      if status == "completed":
        report["total_refreshed"] += 1
      else:
        report["errors"] += 1
        err = progress.get(task_id, {}).get("error")
        if err:
          entry["error"] = err
      report["portfolios"].append(entry)
    except Exception as e:
      report["errors"] += 1
      report["portfolios"].append({
        "id": pid, "name": name, "status": "error", "error": str(e),
      })
      logger.error("paper_refresh: weekly refresh — portfolio %d crashed: %s", pid, e)

  report["ended_at"] = datetime.now(timezone.utc).isoformat()
  logger.info(
    "paper_refresh: weekly bulk-refresh complete — %d refreshed, %d errors out of %d",
    report["total_refreshed"], report["errors"], len(pids),
  )
  return report


def run_refresh(
  portfolio_id: int,
  task_id: str,
  progress: dict[str, dict],
):
  """Background task: refresh a paper/real portfolio's live metrics."""
  db = SessionLocal()
  try:
    p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not p:
      progress[task_id] = {"status": "error", "error": "Portfolio not found"}
      return

    settings = json.loads(p.source_strategies) if p.source_strategies else {}

    # --- Build batch groups (handles both single and multi-batch) ---
    batch_groups = _build_batch_groups(db, p, settings)
    if not batch_groups:
      progress[task_id] = {"status": "error", "error": "No valid batches found for this portfolio"}
      return

    # Collect all unique (symbol, timeframe) pairs for downloading
    download_pairs: set[tuple[str, str]] = set()
    for grp in batch_groups:
      for sym in grp["symbols"]:
        for tf in grp["all_tfs"]:
          download_pairs.add((sym, tf))

    total_symbols = len({sym for sym, _ in download_pairs})
    progress[task_id] = {"status": "downloading", "current_asset": "", "total_assets": total_symbols}
    _persist_refresh_status(db, portfolio_id, task_id, progress)

    # Step 1: Download latest candles
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
      downloader = BybitDownloader()
      manager = DataManager()
      done = 0
      for sym, tf in sorted(download_pairs):
        done += 1
        progress[task_id]["current_asset"] = f"Downloading {sym} {tf} ({done}/{len(download_pairs)})"
        _download_symbol(loop, downloader, manager, sym, tf)
    finally:
      loop.close()

    # Step 2: Run backtests per batch group and collect trades
    progress[task_id]["status"] = "backtesting"
    _persist_refresh_status(db, portfolio_id, task_id, progress)
    is_saved = (p.portfolio_type == "saved")
    activated_at = p.activated_at
    if not activated_at:
      logger.warning(
        "Portfolio %d has no activated_at — skipping activation filter",
        portfolio_id,
      )
      activated_at = datetime(2000, 1, 1)  # effectively no filter

    all_trades_dfs: list[pd.DataFrame] = []
    sym_count = 0
    # Track every symbol we actually processed this refresh — used below
    # to sweep cache files for rotated-out / removed assets so they
    # don't accumulate forever on disk.
    processed_symbols: set[str] = set()
    for grp in batch_groups:
      grp_trades = _run_backtests_and_filter(
        db, grp["python_source"], grp["symbols"], grp["timeframe"],
        grp["direction"], grp["params"], activated_at,
        progress, task_id, total_symbols,
        extra_tfs=grp["extra_tfs"],
        skip_activation_filter=is_saved,
        symbol_offset=sym_count,
        portfolio_id=portfolio_id,
      )
      sym_count += len(grp["symbols"])
      for s in grp["symbols"]:
        processed_symbols.add(s)
      if not grp_trades.empty:
        # Tag each row with the source batch_id so the optional hybrid
        # basket-at-t filter below can match (batch, symbol, side) tuples
        # against `chosen_basket` entries (`B{bid}_{SYMBOL}_{Side}`).
        grp_trades = grp_trades.copy()
        grp_trades["_batch_id"] = grp["batch_id"]
        all_trades_dfs.append(grp_trades)

    # Cache hygiene — delete cache files for symbols no longer in the
    # portfolio's active asset list. Important for hybrid portfolios
    # which rotate baskets: without this, every previously-traded
    # symbol's cache file lingers indefinitely.
    _cleanup_stale_cache(portfolio_id, processed_symbols)

    all_trades = pd.concat(all_trades_dfs, ignore_index=True).sort_values("Exit Time") if all_trades_dfs else pd.DataFrame()

    # CS-014 SESSION-EXEC FILTER — drops trades whose entry_time falls in
    # a configured exclude_hour or exclude_day (NYT). This is the SAME
    # predicate the live engine (strategy_executor._extract_signals) and
    # the optimizer (batch_selection._filter_trades_exec) use. Without
    # this filter here, paper_refresh counts trades during disallowed
    # session hours that the live engine would never execute — KPI tiles
    # inflated ~8x for a typical [NYT 10-13, Tue/Wed/Thu/Sat/Sun] filter.
    #
    # Read the same source-of-truth fields strategy_executor reads
    # (mirrors activate_portfolio lines ~440-445). When session_filter_
    # enabled=false, both lists default to empty → no-op filter.
    if not all_trades.empty:
      _sf_enabled = bool(settings.get("session_filter_enabled", False))
      _excl_h_raw = settings.get("session_filter_exclude_hours", []) or []
      _excl_d_raw = settings.get("session_filter_exclude_days", []) or []
      try:
        exec_excl_hours = {int(h) for h in _excl_h_raw} if _sf_enabled else set()
        exec_excl_days = {int(d) for d in _excl_d_raw} if _sf_enabled else set()
      except (TypeError, ValueError) as e:
        logger.warning(
          "Portfolio %d session_filter parse failed (%s) — skipping filter",
          portfolio_id, e,
        )
        exec_excl_hours, exec_excl_days = set(), set()

      if exec_excl_hours or exec_excl_days:
        from backend.analysis.batch_selection import is_excluded_by_exec_filter
        before_n = len(all_trades)
        # Convert Entry Time column to per-row predicate. Entry Time is
        # already NYT-naive (set in _trades_to_df via the engine's NYT
        # convention; same shape as BacktestTrade.entry_time the optimizer
        # operates on, so the predicate's hour/weekday math matches).
        keep_mask = ~all_trades["Entry Time"].apply(
          lambda t: is_excluded_by_exec_filter(t, exec_excl_hours, exec_excl_days)
        )
        all_trades = all_trades[keep_mask].reset_index(drop=True)
        after_n = len(all_trades)
        dropped = before_n - after_n
        pass_rate = (after_n / before_n * 100) if before_n else 0.0
        logger.info(
          "Portfolio %d session_filter: kept %d/%d trades (%.1f%% pass rate, dropped %d) "
          "[exclude_hours=%s exclude_days=%s]",
          portfolio_id, after_n, before_n, pass_rate, dropped,
          sorted(exec_excl_hours), sorted(exec_excl_days),
        )

    # Hybrid basket-at-t filter — drops trades whose (batch, symbol, side)
    # was NOT in the rotated basket at the moment of entry. Only applies
    # to mode='hybrid' portfolios; non-hybrid portfolios have a frozen
    # basket and always count every replay trade.
    if not all_trades.empty and p.mode == "hybrid" and not is_saved:
      all_trades = _filter_trades_by_basket_at_t(db, p.id, all_trades)

    # Drop the helper column before metrics calculation to keep the
    # downstream DataFrame schema stable.
    if "_batch_id" in all_trades.columns:
      all_trades = all_trades.drop(columns=["_batch_id"])

    # Step 3: Compute live metrics
    progress[task_id]["status"] = "computing"
    progress[task_id]["current_asset"] = "Computing live KPIs..."
    # Always start with the empty-but-structured skeleton so the Paper
    # Overview panel can distinguish "never refreshed" (snapshot == {})
    # from "refreshed but no post-activation trades yet" (skeleton with
    # zeros + month labels + has_run flag). Without this, hybrid
    # portfolios whose strategies haven't fired since activation show a
    # misleading "click Refresh to populate" message even though they
    # have been refreshed — refreshing produces the same empty result.
    _now = datetime.now(timezone.utc)
    _current_period = pd.Period(year=_now.year, month=_now.month, freq="M")
    _prev_period = (_current_period - 1)
    live_metrics: dict = {
      "has_run": True,
      "Total Positions": 0,
      "Total Net Profit": 0.0,
      "Win Rate": 0.0,
      "equity_curve": [],
      "current_month": {"trades": 0, "pnl": 0.0, "win_rate": 0.0},
      "prev_month": {"trades": 0, "pnl": 0.0, "win_rate": 0.0},
      "current_month_label": str(_current_period),
      "prev_month_label": str(_prev_period),
    }
    if not all_trades.empty:
      full = calculate_full_metrics(all_trades)
      # MERGE (not replace) so the skeleton's has_run flag + month labels
      # survive — overwriting just the keys that calculate_full_metrics
      # actually produces (Total Positions, Win Rate, etc.).
      live_metrics.update(_serialize(full))
      # Tack on a sparse equity-curve sample so the Paper Overview panel
      # can render a sparkline directly from the snapshot — saves having
      # to recompute trades for every overview poll. Up to 60 evenly-
      # spaced (timestamp, cumulative_pnl) points.
      try:
        sorted_t = all_trades.sort_values("Exit Time")
        cum = sorted_t["Net Profit"].astype(float).cumsum()
        n = len(sorted_t)
        target = 60
        if n <= target:
          idx_iter = range(n)
        else:
          step = (n - 1) / (target - 1)
          seen: set[int] = set()
          idx_iter = [
            i for i in (int(round(j * step)) for j in range(target))
            if not (i in seen or seen.add(i))
          ]
        live_metrics["equity_curve"] = [
          {
            "t": pd.Timestamp(sorted_t["Exit Time"].iloc[i]).isoformat(),
            "pnl": round(float(cum.iloc[i]), 2),
          }
          for i in idx_iter
        ]
      except Exception as _exc:
        logger.debug("equity curve sampling failed: %s", _exc)

      # Per-month breakdown for the Paper Overview "past month" + "current
      # month" columns. Buckets by Exit Time calendar month.
      try:
        sorted_t = all_trades.sort_values("Exit Time")
        sorted_t["_exit_dt"] = pd.to_datetime(sorted_t["Exit Time"])
        sorted_t["_ym"] = sorted_t["_exit_dt"].dt.to_period("M")
        # NOTE: do NOT add a local `from datetime import datetime, timezone`
        # here — Python's scoping rule promotes the names to function-local
        # scope across the whole function, which then shadows the
        # module-level imports and triggers UnboundLocalError on the
        # `datetime.now(...)` calls used elsewhere in run_refresh when this
        # block is skipped (empty trades branch). Use the module imports.
        now = datetime.now(timezone.utc)
        current_period = pd.Period(year=now.year, month=now.month, freq="M")
        prev_period = (current_period - 1)

        def _bucket(period):
          rows = sorted_t[sorted_t["_ym"] == period]
          if rows.empty:
            return {"trades": 0, "pnl": 0.0, "win_rate": 0.0}
          pnls = rows["Net Profit"].astype(float)
          n = len(rows)
          return {
            "trades": int(n),
            "pnl": round(float(pnls.sum()), 2),
            "win_rate": round(float((pnls > 0).sum()) / n * 100.0, 1),
          }

        live_metrics["current_month"] = _bucket(current_period)
        live_metrics["prev_month"] = _bucket(prev_period)
        live_metrics["current_month_label"] = str(current_period)  # e.g. "2026-05"
        live_metrics["prev_month_label"] = str(prev_period)
      except Exception as _exc:
        logger.debug("monthly bucket sampling failed: %s", _exc)

    # Step 4: Update portfolio
    db.refresh(p)
    p.live_metrics_snapshot = json.dumps(live_metrics)
    p.last_refreshed_at = datetime.now(timezone.utc)
    db.commit()

    progress[task_id] = {
      "status": "completed",
      "current_asset": "",
      "total_assets": total_symbols,
      "live_metrics": live_metrics,
    }
    _persist_refresh_status(db, portfolio_id, task_id, progress)
    logger.info("Portfolio %d refresh done: %d trades across %d batches",
                portfolio_id, len(all_trades), len(batch_groups))

  except Exception as e:
    logger.error("Refresh failed for portfolio %d: %s", portfolio_id, e)
    progress[task_id] = {"status": "error", "error": str(e)}
    _persist_refresh_status(db, portfolio_id, task_id, progress)
  finally:
    db.close()


def _filter_trades_by_basket_at_t(
  db, portfolio_id: int, trades_df: pd.DataFrame,
) -> pd.DataFrame:
  """For hybrid portfolios: drop trades whose (batch_id, symbol, side)
  was NOT in the rotated basket at the trade's entry_time.

  Mirrors `load_wfas_forward_append`'s basket-at-t logic so the KPI tile
  agrees with the trades-table view. Trades that fall in the gap before
  the recorder started capturing baskets fall back to the legacy
  `is_active=True` filter — same fallback the trades-table modal uses.

  When no T-002 segments exist (legacy hybrid portfolios that pre-date
  the recorder), returns trades_df unchanged — preserves pre-feature
  behavior so legacy portfolios aren't suddenly emptied out.

  Requires `_batch_id` column on trades_df (added by the caller).
  """
  from backend.db.models import PortfolioAsset
  from backend.services.phase2_recorder import (
    iter_basket_segments,
    resolve_basket_at_t,
  )

  segments = iter_basket_segments(db, portfolio_id)
  if not segments:
    # Legacy hybrid (no recorder events) — fall back to is_active filter
    # so the KPI tile at least respects the current basket. Without this,
    # rotated-out symbols would still inflate the count.
    pa_rows = (
      db.query(PortfolioAsset)
      .filter(
        PortfolioAsset.portfolio_id == portfolio_id,
        PortfolioAsset.is_active == True,  # noqa: E712
      )
      .all()
    )
    allowed: set[tuple[int, str, str]] = set()
    for a in pa_rows:
      # Defensive: also check is_active in the loop in case the caller
      # mocks pa_rows without honoring the SQL filter (matches the
      # belt-and-suspenders pattern in load_wfas_forward_append).
      if a.batch_id is None or not a.is_active:
        continue
      sides = (
        [a.trade_side.lower()]
        if a.trade_side in ("Long", "Short")
        else ["long", "short"]
      )
      for s in sides:
        allowed.add((int(a.batch_id), a.symbol, s))
    if not allowed:
      return trades_df.iloc[0:0]
    return _apply_legacy_filter(trades_df, allowed)

  # Build the legacy-fallback set once (used when basket-at-t is empty
  # — trades in the recorder-pre-history gap).
  pa_rows = (
    db.query(PortfolioAsset)
    .filter(
      PortfolioAsset.portfolio_id == portfolio_id,
      PortfolioAsset.is_active == True,  # noqa: E712
    )
    .all()
  )
  allowed_legacy: set[tuple[int, str, str]] = set()
  for a in pa_rows:
    # Defensive: same as above — re-check is_active in the loop.
    if a.batch_id is None or not a.is_active:
      continue
    sides = (
      [a.trade_side.lower()]
      if a.trade_side in ("Long", "Short")
      else ["long", "short"]
    )
    for s in sides:
      allowed_legacy.add((int(a.batch_id), a.symbol, s))

  keep: list[bool] = []
  for _, row in trades_df.iterrows():
    bid_raw = row.get("_batch_id")
    if bid_raw is None or pd.isna(bid_raw):
      keep.append(False)
      continue
    bid = int(bid_raw)

    # Strip _Long / _Short suffix from Asset (only present when
    # direction == 'long_plus_short'); fall back to Side column.
    asset = str(row.get("Asset", ""))
    if asset.endswith("_Long"):
      sym = asset[:-5]
    elif asset.endswith("_Short"):
      sym = asset[:-6]
    else:
      sym = asset
    side = str(row.get("Side", "")).lower()
    if not sym or side not in ("long", "short"):
      keep.append(False)
      continue

    entry_at = row.get("Entry Time")
    if pd.isna(entry_at):
      keep.append(False)
      continue
    if hasattr(entry_at, "to_pydatetime"):
      entry_at_py = entry_at.to_pydatetime()
    else:
      entry_at_py = entry_at

    basket_at_t = resolve_basket_at_t(segments, entry_at_py)
    if not basket_at_t:
      # Strict: empty basket-at-t means the recorder either had no
      # basket data at trade time (pre-recorder gap), or recorded an
      # empty basket (no qualifying assets). In both cases the live
      # engine could NOT have placed this trade — drop it. Using the
      # current is_active set as a proxy here would inflate the KPI by
      # the very phantom trades this filter exists to remove.
      #
      # NOTE: this differs from `load_wfas_forward_append`'s empty-basket
      # fallback (which DOES use is_active to keep trades visible in the
      # trades-table view). The trade table is allowed to be lenient
      # because it's a "would-have-shown" historical view; the KPI tile
      # must reflect "what actually traded" because users compare it to
      # the optimizer's authoritative output.
      keep.append(False)
      continue

    # Build (bid, symbol, side) tuples from basket entries.
    from backend.analysis.portfolio_wfo_replay import _basket_key_to_tuple
    tup_set: set[tuple[int, str, str]] = set()
    for k in basket_at_t:
      tup = _basket_key_to_tuple(k)
      if tup is not None:
        tup_set.add(tup)
    keep.append((bid, sym, side) in tup_set)

  if not any(keep):
    return trades_df.iloc[0:0]
  return trades_df[keep].reset_index(drop=True)


def _apply_legacy_filter(
  trades_df: pd.DataFrame, allowed: set[tuple[int, str, str]],
) -> pd.DataFrame:
  """Apply the (batch, symbol, side) filter against a fixed allowed set."""
  keep: list[bool] = []
  for _, row in trades_df.iterrows():
    bid_raw = row.get("_batch_id")
    if bid_raw is None or pd.isna(bid_raw):
      keep.append(False)
      continue
    bid = int(bid_raw)
    asset = str(row.get("Asset", ""))
    if asset.endswith("_Long"):
      sym = asset[:-5]
    elif asset.endswith("_Short"):
      sym = asset[:-6]
    else:
      sym = asset
    side = str(row.get("Side", "")).lower()
    keep.append((bid, sym, side) in allowed)
  if not any(keep):
    return trades_df.iloc[0:0]
  return trades_df[keep].reset_index(drop=True)


def _build_batch_groups(db, portfolio: Portfolio, settings: dict) -> list[dict]:
  """Build batch groups — one per unique batch in the portfolio.

  For single-batch portfolios: one group with all symbols.
  For cross-strategy portfolios: one group per batch, each with its
  own strategy, params, timeframe, and symbol list.
  """
  # Collect all batch_ids (prefer asset-level, fall back to settings)
  asset_batch_map: dict[int, list[str]] = {}  # batch_id → [symbols]
  for a in portfolio.assets:
    bid = a.batch_id
    if bid:
      sym = a.symbol.split("_")[0]  # strip _Long/_Short suffix
      asset_batch_map.setdefault(bid, [])
      if sym not in asset_batch_map[bid]:
        asset_batch_map[bid].append(sym)

  # Fallback: if no assets have batch_id, use settings
  if not asset_batch_map:
    batch_ids = settings.get("batch_ids") or []
    if not batch_ids and settings.get("batch_id"):
      batch_ids = [settings["batch_id"]]
    symbols = list({a.symbol.split("_")[0] for a in portfolio.assets})
    for bid in batch_ids:
      asset_batch_map[bid] = symbols

  if not asset_batch_map:
    return []

  groups = []
  for bid, symbols in asset_batch_map.items():
    batch = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
    if not batch:
      logger.warning("Batch %d not found — skipping", bid)
      continue

    strategy = db.query(Strategy).filter(Strategy.id == batch.strategy_id).first()
    if not strategy or not strategy.python_source:
      logger.warning("Strategy for batch %d not found — skipping", bid)
      continue

    params = json.loads(batch.parameters) if batch.parameters else {}
    extra_tfs = _detect_extra_timeframes(params, strategy.python_source)

    groups.append({
      "batch_id": bid,
      "python_source": strategy.python_source,
      "timeframe": batch.timeframe,
      "direction": batch.direction_filter or "both",
      "params": params,
      "extra_tfs": extra_tfs,
      "all_tfs": [batch.timeframe] + extra_tfs,
      "symbols": symbols,
    })

  return groups


def _detect_extra_timeframes(params: dict, python_source: str) -> list[str]:
  """Auto-detect extra timeframes from strategy inputs and source code."""
  extra: set[str] = set()
  _TF_MAP = {"1h": "60", "4h": "240", "12h": "720", "1D": "D", "3D": "3D"}
  inputs = params.get("strategy_inputs", {})
  for param_key in ("RSI Timeframe", "MA Filter Timeframe"):
    tf_label = inputs.get(param_key)
    if tf_label and tf_label != "Current" and tf_label in _TF_MAP:
      extra.add(_TF_MAP[tf_label])
  # Also scan python source for request_security("D", ...) style calls
  for match in re.findall(r'request_security\(\s*["\'](\w+)["\']', python_source):
    extra.add(match)
  return list(extra)


def _download_symbol(loop, downloader, manager, symbol, timeframe):
  """Download latest candles for one symbol (incremental)."""
  latest = manager.get_latest_timestamp(symbol, timeframe)
  # Naive datetimes from DB are UTC — force tzinfo to avoid local-time shift
  if latest and latest.tzinfo is None:
    latest = latest.replace(tzinfo=timezone.utc)
  since_ms = int(latest.timestamp() * 1000) + 1 if latest else None
  try:
    candles = loop.run_until_complete(
      downloader.fetch_all_klines(symbol, timeframe, since_ms=since_ms)
    )
    if candles:
      manager.upsert_candles(symbol, timeframe, candles)
  except Exception as e:
    logger.warning("Download failed for %s: %s", symbol, e)


def _run_backtests_and_filter(
  db, python_source, symbols, timeframe, direction,
  params, activated_at, progress, task_id, total,
  extra_tfs: list[str] | None = None,
  skip_activation_filter: bool = False,
  symbol_offset: int = 0,
  portfolio_id: int | None = None,
) -> pd.DataFrame:
  """Run strategy on each symbol, return trades (optionally filtered by activation).

  When `portfolio_id` is supplied AND direction != "long_plus_short", uses
  the incremental-refresh cache (see _load_refresh_cache / _save_refresh_cache
  above) to skip re-iterating historical bars already processed in a prior
  refresh. Falls back to a full replay whenever the cache is missing or
  invalid.
  """
  from backend.backtester.dual_pass import run_dual_pass
  from backend.backtester.engine import BacktestConfig, BacktestEngine

  all_dfs = []
  activation_nyt = pd.Timestamp(activated_at).tz_localize("UTC").tz_convert(
    "America/New_York"
  ).tz_localize(None)

  # Hash params/source once per batch group — invariant across symbols.
  ph = _params_hash(python_source, direction, params) if portfolio_id is not None else None

  for i, sym in enumerate(symbols):
    idx = symbol_offset + i + 1
    progress[task_id]["current_asset"] = f"Backtesting {sym} ({idx}/{total})"
    try:
      df = load_candles_df(db, sym, timeframe)
      df = to_nyt(df)
      if df.empty:
        continue

      # Load extra TF data for this symbol
      extra_tf_dfs: dict[str, pd.DataFrame] = {}
      for tf in (extra_tfs or []):
        try:
          extra_tf_dfs[tf] = to_nyt(load_candles_df(db, sym, tf))
        except (ValueError, Exception):
          logger.warning("No %s data for %s — skipping", tf, sym)

      from backend.utils.safe_exec import safe_exec
      strategy_cls = safe_exec(python_source, label=f"paper_refresh_{sym}")
      if not strategy_cls:
        continue

      inputs = params.get("strategy_inputs")
      commission = params.get("commission_pct", COMMISSION_PCT)
      capital = params.get("initial_capital", 10000.0)
      warmup = params.get("warmup_bars", 0)
      max_pyr = params.get("max_pyramiding", 1)

      if direction == "long_plus_short":
        # Dual-pass not cached — too many code paths inside run_dual_pass
        # to plumb resume_from_bar through cleanly. Slow path stays slow.
        result = run_dual_pass(
          python_source, df, sym, timeframe, inputs,
          extra_tf_dfs, capital, commission,
          "pct", 0.0, 1, max_pyr, warmup, 0.0,
          process_orders_on_close=params.get("process_orders_on_close", False),
        )
        cached_real_trades: list = []
      else:
        # ── Incremental resume path ─────────────────────────────────────
        resume_bar = 0
        inject_state = None
        inject_pos = None
        cached_real_trades = []

        if portfolio_id is not None and ph is not None:
          cache = _load_refresh_cache(portfolio_id, sym)
          if cache and cache.get("params_hash") == ph:
            try:
              cached_last_idx = int(cache.get("last_bar_index", -1))
              cached_last_ts_iso = cache.get("last_bar_timestamp_iso") or ""
              # Validate that the cached last bar still sits at the same
              # index in the current df. Re-downloaded / shifted data
              # invalidates the index mapping → full rerun.
              if 0 <= cached_last_idx < len(df) and cached_last_ts_iso:
                df_ts_at_idx = pd.Timestamp(df.index[cached_last_idx]).isoformat()
                cache_ts = pd.Timestamp(cached_last_ts_iso).isoformat()
                if df_ts_at_idx == cache_ts and cached_last_idx < len(df) - 1:
                  resume_bar = cached_last_idx + 1
                  inject_state = cache.get("strategy_state") or {}
                  inject_pos = cache.get("ongoing_positions") or []
                  cached_real_trades = _deserialize_trades(
                    cache.get("trades_serialized") or [],
                  )
                  logger.info(
                    "paper_refresh cache HIT p%d/%s: resume_from_bar=%d "
                    "(skipping %d historical bars, processing %d new)",
                    portfolio_id, sym, resume_bar, resume_bar,
                    len(df) - resume_bar,
                  )
            except Exception as e:
              logger.debug(
                "paper_refresh cache invalid for p%d/%s: %s — full rerun",
                portfolio_id, sym, e,
              )
              resume_bar = 0
              inject_state = None
              inject_pos = None
              cached_real_trades = []

        config = BacktestConfig(
          initial_capital=capital, commission_pct=commission,
          direction_filter=direction, max_pyramiding=max_pyr,
          warmup_bars=warmup,
          process_orders_on_close=params.get("process_orders_on_close", False),
          # Always emit a fresh mark-to-market exit at current last bar
          # for ANY still-open position (including injected ones). Cache
          # strips these on save so they don't go stale.
          force_close_injected_positions=True,
        )
        engine = BacktestEngine(config)
        result = engine.run(
          strategy_cls(), df, symbol=sym, timeframe=timeframe,
          inputs=inputs, extra_timeframes=extra_tf_dfs,
          inject_strategy_state=inject_state,
          inject_positions=inject_pos,
          resume_from_bar=resume_bar,
        )

      # Merge cached real trades + this refresh's trades. The result already
      # includes fresh backtest_end fills (force_close_injected_positions
      # took care of it for both injected and new positions) AND any real
      # SL/TP/signal exits that fired during the resumed window.
      new_trades = list(result.trades or [])
      all_trades_for_sym = list(cached_real_trades) + new_trades

      # ── Save updated cache (single-direction path only) ───────────────
      # We DON'T save the cache if the strategy queued an entry or
      # registered an unbound exit on the very last iterated bar
      # (BacktestResult.had_pending_at_end). Those orders fill at
      # "the next bar's open" in a continuous run, but
      # `_capture_strategy_state_static` skips underscore-prefixed attrs,
      # so they'd be silently lost across the cache boundary —
      # producing missed trades. Forcing a full rerun in this rare case
      # (entry signal on the precise last bar) keeps the cache strictly
      # correctness-preserving. The speedup still applies on every
      # other refresh.
      had_pending = bool(getattr(result, "had_pending_at_end", False))
      if (portfolio_id is not None
          and direction != "long_plus_short"
          and ph is not None
          and len(df) > 0
          and not had_pending):
        try:
          last_idx = len(df) - 1
          last_ts_iso = pd.Timestamp(df.index[last_idx]).isoformat()
          result_state = result.strategy_state if hasattr(result, "strategy_state") else {}
          ongoing = result_state.get("_open_positions", []) if isinstance(result_state, dict) else []
          state_for_cache = dict(result_state) if isinstance(result_state, dict) else {}
          state_for_cache.pop("_open_positions", None)
          # Strip synthetic mark-to-market fills before caching (regenerated
          # fresh on every refresh — never stored).
          real_only = _strip_backtest_end(all_trades_for_sym)
          _save_refresh_cache(
            portfolio_id, sym,
            params_hash=ph,
            last_bar_index=last_idx,
            last_bar_timestamp_iso=last_ts_iso,
            strategy_state=state_for_cache,
            ongoing_positions=ongoing,
            trades_serialized=_serialize_trades(real_only),
          )
        except Exception as e:
          logger.debug(
            "paper_refresh cache save skipped for p%d/%s: %s",
            portfolio_id, sym, e,
          )

      # Convert merged trades (includes fresh backtest_end fills) to DataFrame
      if all_trades_for_sym:
        trade_df = _trades_to_df(all_trades_for_sym, sym, direction)
        if not trade_df.empty and "Exit Time" in trade_df.columns:
          if not skip_activation_filter:
            trade_df = trade_df[trade_df["Exit Time"] >= activation_nyt]
          if not trade_df.empty:
            all_dfs.append(trade_df)

    except Exception as e:
      logger.warning("Backtest failed for %s: %s", sym, e)

  if not all_dfs:
    return pd.DataFrame()
  return pd.concat(all_dfs, ignore_index=True).sort_values("Exit Time")


def _trades_to_df(trades: list, symbol: str, direction: str) -> pd.DataFrame:
  """Convert engine OrderFill objects to standardized DataFrame matching trades_from_db format.

  Only includes exit fills (action='exit') since those carry the full trade info.
  """
  rows = []
  for t in trades:
    # Support both OrderFill dataclass (attributes) and dict (legacy)
    if hasattr(t, "action"):
      # OrderFill dataclass — only exit fills are complete trades
      if t.action != "exit":
        continue
      side = t.side.capitalize()  # "long" → "Long"
      asset = f"{symbol}_{side}" if direction == "long_plus_short" else symbol
      entry_price = t.entry_price
      exit_price = t.price
      qty = t.qty
      net_profit = t.pnl
      net_profit_pct = (t.pnl / (entry_price * qty) * 100) if entry_price and qty else 0.0
      rows.append({
        "Asset": asset,
        "Entry Time": pd.Timestamp(t.entry_time) if t.entry_time else pd.NaT,
        "Exit Time": pd.Timestamp(t.timestamp),
        "Side": side,
        "Entry Price": entry_price,
        "Exit Price": exit_price,
        "Quantity": qty,
        "Net Profit": net_profit,
        "Net Profit %": net_profit_pct,
        "Commission": t.commission,
        "Drawdown": t.mae,
        "Run-up": t.mfe,
        "Exit Reason": t.label or "signal",
      })
    else:
      # Dict fallback (legacy)
      side = t.get("side", "Long")
      asset = f"{symbol}_{side}" if direction == "long_plus_short" else symbol
      rows.append({
        "Asset": asset,
        "Entry Time": pd.Timestamp(t.get("entry_time")),
        "Exit Time": pd.Timestamp(t.get("exit_time")),
        "Side": side,
        "Entry Price": t.get("entry_price", 0),
        "Exit Price": t.get("exit_price", 0),
        "Quantity": t.get("quantity", 1),
        "Net Profit": t.get("net_profit", 0),
        "Net Profit %": t.get("net_profit_pct", 0),
        "Commission": t.get("commission", 0),
        "Drawdown": t.get("mae", 0),
        "Run-up": t.get("mfe", 0),
        "Exit Reason": t.get("exit_reason", "signal"),
      })
  return pd.DataFrame(rows) if rows else pd.DataFrame()
