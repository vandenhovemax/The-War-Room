"""Cross-strategy builder — merge multiple batch backtests, optimize combined."""
from __future__ import annotations

import itertools
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from backend.utils.logger import get_logger

logger = get_logger(__name__)

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from backend.api.rate_limit import rate_limit

from backend.analysis import calculate_stability
from backend.analysis.benchmark import compute_benchmark_robustness
from backend.analysis.boundary_clip import clip_is_trades
from backend.analysis.correlation import correlation_stats, get_correlation_matrix
from backend.analysis.daily_pnl import build_daily_pnl_matrix
from backend.analysis.diversification import _get_optimizer_funcs, diversify_portfolio, diversify_split_portfolio
from backend.analysis.optimizer_seeds import compute_benchmark_seed_scores
from backend.db.database import get_db
from backend.db.models import BacktestRun, BacktestTrade, BatchBacktestRun, Portfolio
from backend.utils.sanitize import sanitize_for_json

router = APIRouter()

# Reuse the shared optimization progress tracker from analysis.py
from backend.api.routes.analysis import _optimize_progress, _optimize_lock, _run_optimize_task, _update_progress


# ---------------------------------------------------------------------------
# Auto-save — persist optimization & recommender results to disk as JSON
# Directory: backend/data/saved_runs/
# ---------------------------------------------------------------------------
_SAVED_RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "saved_runs"
# Fix (2026-04-08): create at import time so the list endpoint never returns
# an empty list just because the directory hasn't been touched yet.
try:
  _SAVED_RUNS_DIR.mkdir(parents=True, exist_ok=True)
except Exception as _dir_exc:
  logger.warning("Could not pre-create saved_runs dir: %s", _dir_exc)
def _auto_save_run(run_type: str, result: dict, input_params: dict):
  """Save result to backend/data/saved_runs/ as JSON.  No auto-cleanup — user
  manages deletions manually via the Saved Runs tab."""
  try:
    _SAVED_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    short_id = uuid.uuid4().hex[:6]
    filename = f"{run_type}_{ts}_{short_id}.json"

    # Build summary for quick listing (no heavy data)
    summary: dict = {}
    if run_type == "build":
      candidates = result.get("candidates", [])
      summary["n_candidates"] = len(candidates)
      summary["n_assets"] = result.get("total_symbols", 0)
      if candidates:
        top = candidates[0].get("metrics", {})
        summary["top_pf"] = top.get("profit_factor")
        summary["top_stability"] = top.get("stability")
      # Elapsed time from timing breakdown
      timing = result.get("timing", {})
      if isinstance(timing, dict) and timing.get("total"):
        summary["elapsed_seconds"] = timing["total"]
      # Config fields for display in saved runs list
      summary["div_algo"] = input_params.get("div_algo")
      summary["combined_objective"] = input_params.get("combined_objective") or input_params.get("objective")
      summary["seed_metrics"] = input_params.get("seed_metrics")
      if input_params.get("split_mode"):
        summary["n_final_assets"] = (input_params.get("n_longs", 0) or 0) + (input_params.get("n_shorts", 0) or 0)
      else:
        summary["n_final_assets"] = input_params.get("n_assets", 0)
    elif run_type.startswith("recommend"):
      profile = result.get("profile", {})
      summary["n_assets"] = profile.get("n_assets", 0)
      summary["n_recommendations"] = len(result.get("recommendations", []))
      summary["n_top_presets"] = len(result.get("top_presets", []))
      summary["elapsed_seconds"] = result.get("elapsed_seconds", 0)

    summary["batch_ids"] = input_params.get("batch_ids", [])
    summary["date_from"] = input_params.get("date_from")
    summary["date_to"] = input_params.get("date_to")

    payload = {
      "type": run_type,
      "saved_at": datetime.now().isoformat(),
      "summary": summary,
      "params": input_params,
      "result": result,
    }

    filepath = _SAVED_RUNS_DIR / filename
    filepath.write_text(json.dumps(payload, default=str), encoding="utf-8")
    logger.info("Auto-saved %s run → %s", run_type, filename)

  except Exception as exc:
    # Never let save failures break the actual optimization
    logger.warning("Failed to auto-save %s run: %s", run_type, exc)


@router.get("/cross-strategy-saved-runs")
def list_saved_runs():
  """List all saved optimization/recommender runs."""
  # Dir is pre-created at import time, but be defensive in case it was
  # deleted externally while the process ran.
  if not _SAVED_RUNS_DIR.exists():
    try:
      _SAVED_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
      logger.error("saved_runs dir missing and cannot be created: %s", exc)
      return []
  runs = []
  parse_failures = 0
  for f in sorted(_SAVED_RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
    if not f.suffix == ".json":
      continue
    try:
      data = json.loads(f.read_text(encoding="utf-8"))
      runs.append({
        "filename": f.name,
        "type": data.get("type", "unknown"),
        "saved_at": data.get("saved_at", ""),
        "summary": data.get("summary", {}),
      })
    except Exception as exc:
      # Fix (2026-04-08): log corrupted/unparseable files instead of
      # silently dropping them — otherwise a single bad file makes saved
      # runs "go missing" with no indication why.
      parse_failures += 1
      logger.warning("Skipping unparseable saved run %s: %s", f.name, exc)
      continue
  if parse_failures > 0:
    logger.warning("saved_runs list: %d file(s) failed to parse", parse_failures)
  return runs


@router.get("/cross-strategy-saved-runs/{filename}")
def load_saved_run(filename: str):
  """Load a single saved run by filename."""
  # Sanitize filename to prevent path traversal
  safe = Path(filename).name
  filepath = _SAVED_RUNS_DIR / safe
  if not filepath.exists():
    raise HTTPException(404, f"Saved run '{safe}' not found")
  data = json.loads(filepath.read_text(encoding="utf-8"))

  # Backfill: inject run-context into presets saved before this feature existed
  params = data.get("params") or {}
  result = data.get("result") or {}
  _ctx_keys = [
      "n_assets", "n_longs", "n_shorts", "split_mode",
      "extra_slippage_pct", "extra_commission_pct",
      "funding_daily_pct", "spread_pct", "per_batch_costs",
      "date_from", "date_to", "clip_is", "corr_method",
  ]
  for preset in result.get("top_presets", []):
      fs = preset.get("full_settings")
      if isinstance(fs, dict):
          for k in _ctx_keys:
              if k not in fs and k in params:
                  fs[k] = params[k]

  return data


@router.delete("/cross-strategy-saved-runs/{filename}")
def delete_saved_run(filename: str):
  """Delete a saved run."""
  safe = Path(filename).name
  filepath = _SAVED_RUNS_DIR / safe
  if not filepath.exists():
    raise HTTPException(404, f"Saved run '{safe}' not found")
  filepath.unlink()
  logger.info("Deleted saved run: %s", safe)
  return {"deleted": True, "filename": safe}


class CleanupCriteria(BaseModel):
  """Criteria for auto-selecting small/test runs to delete."""
  min_batches: int = 3
  min_candidates: int = 3
  min_assets: int = 50
  dry_run: bool = True  # True = preview only, False = delete immediately


@router.post("/cross-strategy-saved-runs/cleanup")
def cleanup_saved_runs(criteria: CleanupCriteria):
  """Find (and optionally delete) saved runs that fall below quality thresholds.

  A run is flagged for cleanup if ANY of these are true:
  - n_batches < min_batches
  - n_candidates < min_candidates  (build runs only)
  - n_assets < min_assets
  Recommender runs are kept unless they fail the batches or assets check.
  """
  if not _SAVED_RUNS_DIR.exists():
    return {"flagged": [], "kept": 0, "deleted": 0}

  flagged: list[dict] = []
  kept = 0

  for f in sorted(_SAVED_RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
    if f.suffix != ".json":
      continue
    try:
      data = json.loads(f.read_text(encoding="utf-8"))
      s = data.get("summary", {})
      run_type = data.get("type", "unknown")
      n_batches = len(s.get("batch_ids", []))
      n_candidates = s.get("n_candidates", 0) or 0
      n_assets = s.get("n_assets", 0) or 0

      is_small = (
        n_batches < criteria.min_batches
        or n_assets < criteria.min_assets
        or (run_type == "build" and n_candidates < criteria.min_candidates)
      )

      if is_small:
        flagged.append({
          "filename": f.name,
          "type": run_type,
          "saved_at": data.get("saved_at", ""),
          "n_batches": n_batches,
          "n_candidates": n_candidates,
          "n_assets": n_assets,
        })
        if not criteria.dry_run:
          f.unlink()
      else:
        kept += 1
    except Exception:
      continue

  deleted = len(flagged) if not criteria.dry_run else 0
  if deleted:
    logger.info("Cleanup: deleted %d small saved runs (kept %d)", deleted, kept)

  return {"flagged": flagged, "kept": kept, "deleted": deleted}


# ---------------------------------------------------------------------------
# Batch-tag helpers — differentiate same-symbol assets from different batches
# Format: SYMBOL~B{batch_id}_Side  (preserves endswith("_Long") / "_Short")
# ---------------------------------------------------------------------------
_BATCH_TAG_RE = re.compile(r"~B-?\d+")


def _extract_batch_id(col: str) -> int | None:
  """BTCUSDT~B5_Long → 5, BTCUSDT~B-1_Short → -1, BTCUSDT_Long → None."""
  m = _BATCH_TAG_RE.search(col)
  return int(m.group(0)[2:]) if m else None


def _filter_to_requested(
  daily_pnl: pd.DataFrame,
  trades: list,
  run_batch_map: dict[int, int],
  requested_batch_ids: list[int],
  requested_portfolio_ids: list[int],
) -> tuple[pd.DataFrame, list]:
  """Filter cached daily_pnl and trades to only the requested batch/portfolio IDs.

  Prevents cache superset leak where extra batches accumulate in the cache
  and leak into subsequent runs that didn't request them.
  """
  # Build set of allowed batch IDs: positive for batches, negative for portfolios
  allowed = set(requested_batch_ids)
  for pid in requested_portfolio_ids:
    allowed.add(-pid)  # portfolios use negative IDs in batch tags

  # Filter daily_pnl columns
  keep_cols = []
  for col in daily_pnl.columns:
    bid = _extract_batch_id(col)
    if bid is None or bid in allowed:
      keep_cols.append(col)
  filtered_pnl = daily_pnl[keep_cols] if len(keep_cols) < len(daily_pnl.columns) else daily_pnl

  # Filter trades — only keep trades whose run_id maps to a requested batch
  allowed_run_ids = {rid for rid, bid in run_batch_map.items() if bid in allowed}
  if len(allowed_run_ids) < len(run_batch_map):
    filtered_trades = [t for t in trades if getattr(t, 'run_id', None) in allowed_run_ids]
  else:
    filtered_trades = trades

  return filtered_pnl, filtered_trades


def _add_batch_tag(asset_key: str, batch_id: int | None) -> str:
  """BTCUSDT_Short + batch_id=5 → BTCUSDT~B5_Short"""
  if batch_id is None:
    return asset_key
  parts = asset_key.rsplit("_", 1)
  if len(parts) == 2 and parts[1] in ("Long", "Short"):
    return f"{parts[0]}~B{batch_id}_{parts[1]}"
  return f"{asset_key}~B{batch_id}"


def _strip_batch_tag(name: str) -> str:
  """BTCUSDT~B5_Short → BTCUSDT_Short"""
  return _BATCH_TAG_RE.sub("", name)


def _clean_symbol(name: str) -> str:
  """BTCUSDT~B5_Short → BTCUSDT"""
  return _strip_batch_tag(name).rsplit("_", 1)[0]


def _parse_per_batch_costs(raw: str | None) -> dict[int, dict] | None:
  """Parse per_batch_costs JSON string → dict keyed by batch_id (int)."""
  if not raw:
    return None
  try:
    parsed = json.loads(raw)
    return {int(k): v for k, v in parsed.items()} if parsed else None
  except Exception:
    return None


def _dedup_by_symbol(
    assets: list[str],
    daily_pnl: pd.DataFrame,
    mode: str,
    tiebreaker: str = "pnl",
    seed_scores: dict[str, float] | None = None,
) -> list[str]:
  """Dedup assets by base symbol, keeping the best version.

  mode: "same_side" groups by stripped name (BTCUSDT_Long),
        "any_side" groups by clean symbol (BTCUSDT).
  tiebreaker: "pnl" = highest total PnL wins,
              "seed_rank" = lowest seed score wins (lower = better).
  """
  groups: dict[str, list[str]] = {}
  for a in assets:
    key = _strip_batch_tag(a) if mode == "same_side" else _clean_symbol(a)
    groups.setdefault(key, []).append(a)

  result = []
  for dupes in groups.values():
    if len(dupes) == 1:
      result.append(dupes[0])
    elif tiebreaker == "seed_rank" and seed_scores:
      # Lower seed score = better in seed ranking
      best = min(dupes, key=lambda a: seed_scores.get(a, float("inf")))
      result.append(best)
    else:
      # By PnL — higher = better
      best = max(dupes, key=lambda a: daily_pnl[a].sum() if a in daily_pnl.columns else -1e18)
      result.append(best)
  return result


def _trade_key(t: BacktestTrade, rbm: dict[int, int] | None = None) -> str:
  """Build tagged asset key for a BacktestTrade."""
  base = f"{t.symbol}_{(t.side or 'Long').title()}"
  if rbm and t.run_id in rbm:
    return _add_batch_tag(base, rbm[t.run_id])
  return base


def _rebuild_daily_pnl_from_trades(
  trades: list[BacktestTrade],
  run_batch_map: dict[int, int],
) -> pd.DataFrame:
  """Rebuild tagged daily PnL matrix from in-memory trades (e.g. after slippage).

  Unlike _build_cross_daily_pnl which re-queries the DB, this uses the already-
  modified trade objects so that slippage adjustments are reflected in daily PnL.
  """
  if not trades:
    return pd.DataFrame()
  rows = []
  for t in trades:
    if t.exit_time and t.net_profit is not None:
      rows.append({
        "date": pd.Timestamp(t.exit_time).floor("D"),
        "symbol": _trade_key(t, run_batch_map),
        "pnl": t.net_profit,
      })
  if not rows:
    return pd.DataFrame()
  df = pd.DataFrame(rows)
  daily = df.pivot_table(index="date", columns="symbol", values="pnl", aggfunc="sum").fillna(0)
  if not daily.empty:
    full_range = pd.date_range(start=daily.index.min(), end=daily.index.max(), freq="D")
    daily = daily.reindex(full_range).fillna(0)
  return daily


def _load_multi_batch_trades(
  batch_ids: list[int], db: Session,
  date_from: str | None = None, date_to: str | None = None,
  clip_boundary: bool = False,
) -> tuple[list, dict[int, int]]:
  """Load trades from multiple batches with optional date filters.

  Returns (trades, run_batch_map) where run_batch_map maps run_id → batch_id.
  clip_boundary: when True, force-close trades straddling date_to at boundary close price.

  CS-019: bulk-loads runs+trades+batches via IN() instead of one query per
  batch_id. For N batches this cuts 2N+N queries down to 3.
  """
  all_trades: list = []
  run_batch_map: dict[int, int] = {}
  if not batch_ids:
    return all_trades, run_batch_map

  runs = (
    db.query(BacktestRun)
    .filter(
      BacktestRun.batch_id.in_(batch_ids),
      BacktestRun.status == "completed",
    )
    .all()
  )
  runs_by_batch: dict[int, list] = {}
  for r in runs:
    runs_by_batch.setdefault(r.batch_id, []).append(r)
    run_batch_map[r.id] = r.batch_id
  all_run_ids = [r.id for r in runs]
  if not all_run_ids:
    return all_trades, run_batch_map

  query = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(all_run_ids))
  if date_to:
    cutoff_dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    query = query.filter(BacktestTrade.entry_time < cutoff_dt)
  if date_from:
    from_dt = datetime.strptime(date_from, "%Y-%m-%d")
    query = query.filter(BacktestTrade.entry_time >= from_dt)
  all_trades_flat = query.all()

  trades_by_batch: dict[int, list] = {}
  for t in all_trades_flat:
    bid = run_batch_map.get(t.run_id)
    if bid is not None:
      trades_by_batch.setdefault(bid, []).append(t)

  batches_by_id: dict[int, BatchBacktestRun] = {}
  if clip_boundary and date_to:
    batch_rows = (
      db.query(BatchBacktestRun)
      .filter(BatchBacktestRun.id.in_(batch_ids))
      .all()
    )
    batches_by_id = {b.id: b for b in batch_rows}

  # Preserve original iteration order by batch_ids.
  for bid in batch_ids:
    if not runs_by_batch.get(bid):
      continue
    batch_trades = trades_by_batch.get(bid, [])
    if clip_boundary and date_to:
      batch = batches_by_id.get(bid)
      tf = batch.timeframe if batch else "D"
      batch_trades = clip_is_trades(batch_trades, date_to, tf, db)
    all_trades.extend(batch_trades)
  return all_trades, run_batch_map


def _strategy_map(batch_ids: list[int], db: Session) -> dict[int, str]:
  """Map batch_id -> config-specific batch name (not generic parent strategy).

  CS-019: single IN()-query with joinedload instead of one per batch_id.
  """
  result: dict[int, str] = {}
  if not batch_ids:
    return result
  rows = (
    db.query(BatchBacktestRun)
    .options(joinedload(BatchBacktestRun.strategy))
    .filter(BatchBacktestRun.id.in_(batch_ids))
    .all()
  )
  batches_by_id = {b.id: b for b in rows}
  for bid in batch_ids:
    batch = batches_by_id.get(bid)
    if batch:
      # Use batch.name (auto-generated "StrategyName — ConfigName") for config-specific label
      result[bid] = batch.name or (batch.strategy.name if batch.strategy else f"Batch #{bid}")
    else:
      result[bid] = f"Batch #{bid}"
  return result


def _log_batch_counts(
  daily_pnl: pd.DataFrame, ab_map: dict[str, int], stage: str,
) -> None:
  """Log per-batch asset counts for debugging cross-strategy pipeline."""
  from collections import Counter
  counts: dict[int | None, int] = Counter()
  for c in daily_pnl.columns:
    bid = ab_map.get(c)
    counts[bid] = counts.get(bid, 0) + 1
  logger.info("%s: %d cols, per-batch: %s", stage, daily_pnl.shape[1], dict(counts))


def _asset_strategy_lookup(
  batch_ids: list[int], db: Session, tagged: bool = False,
) -> dict[str, str]:
  """Map asset name -> strategy name for attribution.

  tagged=True: keys include batch tag (BTCUSDT~B5_Short) for cross-strategy.

  CS-019: bulk runs+trades lookup via IN() instead of N per-run .first()
  queries. Preserves the original semantics of "first side seen per run".
  """
  lookup: dict[str, str] = {}
  if not batch_ids:
    return lookup
  strat_map = _strategy_map(batch_ids, db)

  runs = (
    db.query(BacktestRun)
    .filter(
      BacktestRun.batch_id.in_(batch_ids),
      BacktestRun.status == "completed",
    )
    .all()
  )
  run_ids = [r.id for r in runs]
  first_side_by_run: dict[int, str] = {}
  if run_ids:
    trade_rows = (
      db.query(BacktestTrade.run_id, BacktestTrade.side)
      .filter(BacktestTrade.run_id.in_(run_ids))
      .all()
    )
    for rid, side in trade_rows:
      if rid not in first_side_by_run:
        first_side_by_run[rid] = side or "Long"

  for r in runs:
    side = first_side_by_run.get(r.id)
    if not side:
      continue
    base = f"{r.symbol}_{side.title()}"
    key = _add_batch_tag(base, r.batch_id) if tagged else base
    lookup[key] = strat_map.get(r.batch_id, f"Batch #{r.batch_id}")
  return lookup


def _build_asset_batch_map(
  batch_ids: list[int], db: Session, tagged: bool = False,
) -> dict[str, int]:
  """Map asset name -> batch_id.

  tagged=True: keys include batch tag — every asset is unique, no collisions.
  Bulk-loads distinct sides per run to avoid N+1 queries and handle
  runs that have both Long and Short trades.
  """
  from sqlalchemy import func as sa_func

  mapping: dict[str, int] = {}
  if not batch_ids:
    return mapping

  # Single query: get all (run_id, symbol, batch_id, distinct sides)
  rows = (
    db.query(
      BacktestRun.id,
      BacktestRun.symbol,
      BacktestRun.batch_id,
      BacktestTrade.side,
    )
    .join(BacktestTrade, BacktestTrade.run_id == BacktestRun.id)
    .filter(
      BacktestRun.batch_id.in_(batch_ids),
      BacktestRun.status == "completed",
    )
    .group_by(BacktestRun.id, BacktestTrade.side)
    .all()
  )
  for _run_id, symbol, bid, side in rows:
    base = f"{symbol}_{(side or 'Long').title()}"
    key = _add_batch_tag(base, bid) if tagged else base
    if key not in mapping:
      mapping[key] = bid
  return mapping


def _build_cross_daily_pnl(
  batch_ids: list[int], db: Session,
  date_from: str | None = None, date_to: str | None = None,
  clip_boundary: bool = False,
) -> tuple[pd.DataFrame, list, dict[int, int]]:
  """Build batch-tagged daily PnL matrix for cross-strategy optimization.

  Each batch's assets get unique column names (SYMBOL~B{bid}_Side) so that
  same-symbol assets from different batches remain separate columns.

  Returns (daily_pnl, all_trades, run_batch_map).
  clip_boundary: when True, force-close trades straddling date_to at boundary close price.
  """
  all_trades: list = []
  run_batch_map: dict[int, int] = {}
  matrices: list[pd.DataFrame] = []

  if not batch_ids:
    return pd.DataFrame(), all_trades, run_batch_map

  # CS-019: bulk-load runs/trades/batches via IN() instead of per-batch loops.
  runs = (
    db.query(BacktestRun)
    .filter(
      BacktestRun.batch_id.in_(batch_ids),
      BacktestRun.status == "completed",
    )
    .all()
  )
  runs_by_batch: dict[int, list] = {}
  for r in runs:
    runs_by_batch.setdefault(r.batch_id, []).append(r)
    run_batch_map[r.id] = r.batch_id
  all_run_ids = [r.id for r in runs]
  if not all_run_ids:
    return pd.DataFrame(), all_trades, run_batch_map

  query = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(all_run_ids))
  if date_to:
    cutoff_dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    query = query.filter(BacktestTrade.entry_time < cutoff_dt)
  if date_from:
    from_dt = datetime.strptime(date_from, "%Y-%m-%d")
    query = query.filter(BacktestTrade.entry_time >= from_dt)
  all_trades_flat = query.all()

  trades_by_batch: dict[int, list] = {}
  for t in all_trades_flat:
    bid = run_batch_map.get(t.run_id)
    if bid is not None:
      trades_by_batch.setdefault(bid, []).append(t)

  batches_by_id: dict[int, BatchBacktestRun] = {}
  if clip_boundary and date_to:
    batch_rows = (
      db.query(BatchBacktestRun)
      .filter(BatchBacktestRun.id.in_(batch_ids))
      .all()
    )
    batches_by_id = {b.id: b for b in batch_rows}

  for bid in batch_ids:
    if not runs_by_batch.get(bid):
      continue
    batch_trades = trades_by_batch.get(bid, [])
    if clip_boundary and date_to:
      batch = batches_by_id.get(bid)
      tf = batch.timeframe if batch else "D"
      batch_trades = clip_is_trades(batch_trades, date_to, tf, db)
    all_trades.extend(batch_trades)

    m = build_daily_pnl_matrix(batch_trades, include_side=True)
    if not m.empty:
      m.columns = [_add_batch_tag(c, bid) for c in m.columns]
      matrices.append(m)

  if not matrices:
    return pd.DataFrame(), all_trades, run_batch_map

  daily_pnl = pd.concat(matrices, axis=1).fillna(0)
  if not daily_pnl.empty:
    full_range = pd.date_range(
      start=daily_pnl.index.min(), end=daily_pnl.index.max(), freq="D",
    )
    daily_pnl = daily_pnl.reindex(full_range).fillna(0)

  return daily_pnl, all_trades, run_batch_map


def _load_portfolio_trades(
  portfolio_ids: list[int], db: Session,
  date_from: str | None = None, date_to: str | None = None,
  tagged: bool = False,
  clip_boundary: bool = False,
) -> tuple[list, dict[str, int], dict[int, str], dict[str, str], dict[int, int]]:
  """Load trades from saved portfolios, filtered to each portfolio's assets.

  Each portfolio becomes a "virtual batch" with synthetic ID = -portfolio_id.
  Returns (trades, asset_batch_map, strategy_map, asset_strat_lookup, run_batch_map).
  tagged=True: asset keys include batch tag for cross-strategy.
  clip_boundary: when True, force-close trades straddling date_to at boundary close price.
  """
  all_trades: list = []
  asset_batch: dict[str, int] = {}
  strat_map: dict[int, str] = {}
  asset_strat: dict[str, str] = {}
  run_batch_map: dict[int, int] = {}

  for pid in portfolio_ids:
    portfolio = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not portfolio:
      continue

    # Group portfolio assets by their source batch_id to prevent
    # cross-batch leakage (same symbol from wrong batch).
    from collections import defaultdict
    batch_asset_names: dict[int | None, list[str]] = defaultdict(list)
    all_asset_names: set[str] = set()
    for a in portfolio.assets:
      if a.trade_side in ("Long", "Short"):
        name = f"{a.symbol}_{a.trade_side}"
      else:
        name = a.symbol
      batch_asset_names[a.batch_id].append(name)
      all_asset_names.add(name)

    # Resolve source batch_id(s) from saved settings
    settings = json.loads(portfolio.source_strategies) if portfolio.source_strategies else {}
    batch_ids = settings.get("batch_ids", [])
    if not batch_ids:
      bid = settings.get("batch_id")
      if bid:
        batch_ids = [bid]
    if not batch_ids:
      continue

    synthetic_id = -pid
    # Load trades from those batches, filtered to each batch's selected assets
    seen_assets: set[str] = set()

    # CS-019: pre-compute which batches actually have asset selections so
    # we don't fetch runs/trades for empty ones.
    active_bids = [
      bid for bid in batch_ids
      if set(batch_asset_names.get(bid, [])) or (None in batch_asset_names)
    ]
    if not active_bids:
      strat_map[synthetic_id] = portfolio.name
      continue

    # CS-019: bulk-load runs/trades/batches across all active_bids at once
    # instead of running 2–3 queries per bid.
    p_runs = (
      db.query(BacktestRun)
      .filter(
        BacktestRun.batch_id.in_(active_bids),
        BacktestRun.status == "completed",
      )
      .all()
    )
    p_runs_by_batch: dict[int, list] = {}
    for r in p_runs:
      p_runs_by_batch.setdefault(r.batch_id, []).append(r)
      run_batch_map[r.id] = synthetic_id
    p_run_ids = [r.id for r in p_runs]

    p_trades_by_batch: dict[int, list] = {}
    if p_run_ids:
      t_query = db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(p_run_ids))
      if date_from:
        from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        t_query = t_query.filter(BacktestTrade.entry_time >= from_dt)
      if date_to:
        cutoff_dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        t_query = t_query.filter(BacktestTrade.entry_time < cutoff_dt)
      # Need per-run batch_id lookup for grouping
      run_to_real_bid = {r.id: r.batch_id for r in p_runs}
      for t in t_query.all():
        bid_real = run_to_real_bid.get(t.run_id)
        if bid_real is not None:
          p_trades_by_batch.setdefault(bid_real, []).append(t)

    p_batches_by_id: dict[int, BatchBacktestRun] = {}
    if clip_boundary and date_to:
      p_batch_rows = (
        db.query(BatchBacktestRun)
        .filter(BatchBacktestRun.id.in_(active_bids))
        .all()
      )
      p_batches_by_id = {b.id: b for b in p_batch_rows}

    for bid in batch_ids:
      # Only load assets that were selected from THIS batch
      names_for_bid = set(batch_asset_names.get(bid, []))
      # Legacy fallback: assets without batch_id match against all
      if None in batch_asset_names:
        names_for_bid |= set(batch_asset_names[None])
      if not names_for_bid:
        continue
      if not p_runs_by_batch.get(bid):
        continue
      batch_trades = p_trades_by_batch.get(bid, [])
      if clip_boundary and date_to:
        batch_obj = p_batches_by_id.get(bid)
        tf = batch_obj.timeframe if batch_obj else "D"
        batch_trades = clip_is_trades(batch_trades, date_to, tf, db)
      for t in batch_trades:
        side = (t.side or "Long").title()
        asset_name = f"{t.symbol}_{side}"
        if asset_name in names_for_bid:
          all_trades.append(t)
          seen_assets.add(asset_name)

    strat_map[synthetic_id] = portfolio.name
    for name in seen_assets:
      key = _add_batch_tag(name, synthetic_id) if tagged else name
      asset_batch[key] = synthetic_id
      asset_strat[key] = portfolio.name

  return all_trades, asset_batch, strat_map, asset_strat, run_batch_map


def _get_excluded_asset_names(
  exclude_portfolio_ids: list[int], db: Session,
) -> set[str]:
  """Load asset names from portfolios to exclude.

  Returns a set of clean asset keys like "BTCUSDT_Long", "ETHUSDT_Short".
  """
  excluded: set[str] = set()
  for pid in exclude_portfolio_ids:
    portfolio = db.query(Portfolio).filter(Portfolio.id == pid).first()
    if not portfolio:
      continue
    for a in portfolio.assets:
      if a.trade_side in ("Long", "Short"):
        excluded.add(f"{a.symbol}_{a.trade_side}")
      else:
        excluded.add(a.symbol)
  return excluded


def _apply_exclude_portfolio_full(
  daily_pnl: pd.DataFrame, excluded_names: set[str],
) -> pd.DataFrame:
  """Remove all columns from daily_pnl whose clean name matches the excluded set."""
  keep_cols = [c for c in daily_pnl.columns if _strip_batch_tag(c) not in excluded_names]
  removed = daily_pnl.shape[1] - len(keep_cols)
  if removed > 0:
    logger.info("  Portfolio exclusion (full): removed %d/%d assets", removed, daily_pnl.shape[1])
    daily_pnl = daily_pnl[keep_cols]
  return daily_pnl


def _apply_exclude_portfolio_partial(
  daily_pnl: pd.DataFrame,
  excluded_names: set[str],
  keep_pct: float,
  method: str,
  pool_scores: dict[str, float],
  corr: pd.DataFrame | None = None,
) -> pd.DataFrame:
  """Keep only top X% of excluded-portfolio assets by score/correlation/blend.

  Args:
    daily_pnl: The full pool of assets (columns).
    excluded_names: Clean asset names from the excluded portfolio.
    keep_pct: Percentage (0-100) of matched assets to keep.
    method: "score" | "correlation" | "blend".
    pool_scores: L1 seed scores (lower rank = better).
    corr: Correlation matrix (needed for "correlation" and "blend" methods).
  """
  import math

  # Identify which pool columns match the excluded portfolio
  matched = [c for c in daily_pnl.columns if _strip_batch_tag(c) in excluded_names]
  non_matched = [c for c in daily_pnl.columns if _strip_batch_tag(c) not in excluded_names]

  if not matched:
    return daily_pnl

  max_keep = max(1, math.ceil(len(matched) * keep_pct / 100.0))
  if max_keep >= len(matched):
    logger.info("  Portfolio exclusion (partial): keep_pct=%.0f%% → keeping all %d matched", keep_pct, len(matched))
    return daily_pnl

  if method == "score":
    # Lower pool_score rank = better asset
    ranked = sorted(matched, key=lambda c: pool_scores.get(c, float("inf")))
    keep = set(ranked[:max_keep])

  elif method == "correlation" and corr is not None:
    # Keep assets with lowest average correlation to non-matched (most diverse)
    avg_corr: dict[str, float] = {}
    for c in matched:
      if c in corr.columns and non_matched:
        valid_nm = [nm for nm in non_matched if nm in corr.columns]
        if valid_nm:
          avg_corr[c] = abs(corr.loc[c, valid_nm]).mean()
        else:
          avg_corr[c] = 0.0
      else:
        avg_corr[c] = 0.0
    ranked = sorted(matched, key=lambda c: avg_corr.get(c, 0.0))
    keep = set(ranked[:max_keep])

  elif method == "blend" and corr is not None:
    # 50/50 blend of score rank + correlation rank
    score_ranked = sorted(matched, key=lambda c: pool_scores.get(c, float("inf")))
    score_rank = {c: i for i, c in enumerate(score_ranked)}

    avg_corr: dict[str, float] = {}
    for c in matched:
      if c in corr.columns and non_matched:
        valid_nm = [nm for nm in non_matched if nm in corr.columns]
        if valid_nm:
          avg_corr[c] = abs(corr.loc[c, valid_nm]).mean()
        else:
          avg_corr[c] = 0.0
      else:
        avg_corr[c] = 0.0
    corr_ranked = sorted(matched, key=lambda c: avg_corr.get(c, 0.0))
    corr_rank = {c: i for i, c in enumerate(corr_ranked)}

    # Normalize ranks to [0,1] then blend
    n = max(len(matched) - 1, 1)
    blended = {c: 0.5 * (score_rank.get(c, n) / n) + 0.5 * (corr_rank.get(c, n) / n) for c in matched}
    ranked = sorted(matched, key=lambda c: blended[c])
    keep = set(ranked[:max_keep])

  else:
    # Fallback to score if correlation not available
    ranked = sorted(matched, key=lambda c: pool_scores.get(c, float("inf")))
    keep = set(ranked[:max_keep])

  remove = set(matched) - keep
  keep_cols = [c for c in daily_pnl.columns if c not in remove]
  logger.info("  Portfolio exclusion (partial, %s): %d matched, keeping %d (%.0f%%), removed %d",
              method, len(matched), len(keep), keep_pct, len(remove))
  return daily_pnl[keep_cols]


def _apply_per_batch_filters(
  trades: list[BacktestTrade], daily_pnl: pd.DataFrame,
  per_batch: dict[str, dict], asset_batch: dict[str, int],
  run_batch_map: dict[int, int] | None = None,
  date_to: str | None = None,
  default_min_trades: int = 0,
  default_min_pf: float = 0.0,
) -> pd.DataFrame:
  """Per-batch quality filters + allocation caps.

  Counts unique positions (by position_id), not exit fills.
  run_batch_map: when provided, uses batch-tagged asset keys.
  date_to: OOS cutoff safety net — drops trades/daily_pnl rows beyond this date.
  """
  import pandas as _pd
  # Safety net: ensure no trade with entry beyond cutoff date leaks in
  # Do NOT truncate daily_pnl — IS trades may exit (realize PnL) after cutoff
  if date_to:
    cutoff = _pd.Timestamp(date_to)
    trades = [t for t in trades if t.entry_time and _pd.Timestamp(t.entry_time).date() <= cutoff.date()]

  from collections import defaultdict
  # Group fills into positions by (asset_key, position_id) → total P&L
  # Always use suffixed keys since cross-strategy columns have _Long/_Short
  pos_pnl: dict[str, dict[str, float]] = defaultdict(dict)
  for t in trades:
    pid = t.position_id if t.position_id else f"_solo_{t.id}"
    sym = _trade_key(t, run_batch_map)
    if pid not in pos_pnl[sym]:
      pos_pnl[sym][pid] = 0.0
    pos_pnl[sym][pid] += (t.net_profit or 0.0)
  # Per-asset aggregated stats from position-level P&L
  stats: dict[str, dict] = {}
  for sym, positions in pos_pnl.items():
    count = len(positions)
    wins = sum(1 for pnl in positions.values() if pnl > 0)
    gp = sum(pnl for pnl in positions.values() if pnl > 0)
    gl = sum(abs(pnl) for pnl in positions.values() if pnl <= 0)
    stats[sym] = {"n": count, "wins": wins, "gp": gp, "gl": gl}

  # Group assets by batch
  batch_assets: dict[int, list[str]] = defaultdict(list)
  for a in daily_pnl.columns:
    bid = asset_batch.get(a)
    if bid is not None:
      batch_assets[bid].append(a)

  keep: set[str] = set()
  for bid, assets in batch_assets.items():
    cfg = per_batch.get(str(bid), {})
    min_trades = cfg.get("min_trades", default_min_trades)
    min_pf = cfg.get("min_pf", default_min_pf)
    min_wr = cfg.get("min_wr", 0.0)
    max_longs = cfg.get("max_longs", 999)
    max_shorts = cfg.get("max_shorts", 999)
    qualified = []
    _rej_no_stats = 0
    _rej_min_trades = 0
    _rej_min_pf = 0
    _rej_min_wr = 0
    for a in assets:
      s = stats.get(a)
      if not s or s["n"] == 0:
        _rej_no_stats += 1
        continue
      if min_trades > 0 and s["n"] < min_trades:
        _rej_min_trades += 1
        continue
      pf = min(s["gp"] / s["gl"], 10.0) if s["gl"] > 0 else 10.0
      if min_pf > 0 and pf < min_pf:
        _rej_min_pf += 1
        continue
      wr = s["wins"] / s["n"] * 100
      if min_wr > 0 and wr < min_wr:
        _rej_min_wr += 1
        continue
      qualified.append(a)
    # Sort by PF descending, cap per direction
    def _pf(a: str) -> float:
      s = stats.get(a)
      return min(s["gp"] / s["gl"], 10.0) if s and s["gl"] > 0 else 10.0
    longs = sorted([a for a in qualified if a.endswith("_Long")], key=_pf, reverse=True)[:max_longs]
    shorts = sorted([a for a in qualified if a.endswith("_Short")], key=_pf, reverse=True)[:max_shorts]
    logger.info("  _apply_per_batch_filters batch %d: %d assets, cfg=%s, effective min_trades=%d min_pf=%.2f min_wr=%.1f | %d qualified (%dL+%dS) | rej: no_stats=%d, min_trades=%d, min_pf=%d, min_wr=%d",
                bid, len(assets), cfg, min_trades, min_pf, min_wr, len(qualified), len(longs), len(shorts),
                _rej_no_stats, _rej_min_trades, _rej_min_pf, _rej_min_wr)
    keep.update(longs)
    keep.update(shorts)

  # Keep assets not mapped to any batch (shouldn't happen but safe)
  for a in daily_pnl.columns:
    if a not in asset_batch:
      keep.add(a)
  filtered = [c for c in daily_pnl.columns if c in keep]
  return daily_pnl[filtered]


def _cross_prepare_benchmark(
  trades: list[BacktestTrade], daily_pnl: pd.DataFrame,
  source: str, split_method: str, threshold: float,
  elite_target: float, exclude_fragile: bool,
  run_batch_map: dict[int, int] | None = None,
  consistency_blend: float = 0.0,
  fragility_blend: float = 0.0,
  wr_harmony_blend: float = 0.0,
  dsr_gate: bool = False,
) -> tuple[pd.DataFrame, dict]:
  """Benchmark robustness for cross-strategy (uses tagged asset keys)."""
  valid_symbols = set(daily_pnl.columns)
  rows = []
  for t in trades:
    asset_key = _trade_key(t, run_batch_map)
    if asset_key in valid_symbols:
      rows.append({"Asset": asset_key, "Exit Time": t.exit_time, "Net Profit": t.net_profit})
  if not rows:
    return daily_pnl, {}
  df = pd.DataFrame(rows)
  stats, _ = compute_benchmark_robustness(
    df, source_asset=source, split_method=split_method,
    validation_threshold=threshold, elite_target=elite_target,
  )
  if exclude_fragile:
    keep = {a for a, s in stats.items() if s.get("status") not in ("Weak", "Fragile")}
    daily_pnl = daily_pnl[[c for c in daily_pnl.columns if c in keep]]
  bm_scores = compute_benchmark_seed_scores(
    stats,
    consistency_blend=consistency_blend,
    fragility_blend=fragility_blend,
    wr_harmony_blend=wr_harmony_blend,
    dsr_gate=dsr_gate,
    daily_pnl=daily_pnl,
  )
  return daily_pnl, {"benchmark": bm_scores}


def _compute_event_external_scores(
  trades, event_mode: bool, n_trades: int, staleness_days: int,
  per_batch: dict | None, seed_metrics: list[str],
  asset_batch: dict[str, int], include_side: bool = True,
  date_from: str | None = None,
  run_batch_map: dict[int, int] | None = None,
) -> dict[str, dict[str, float]]:
  """Compute event-based seed scores for cross-strategy endpoints.

  Respects both global event_mode and per-batch overrides inside per_batch dict
  (each entry may include {event_mode: bool, n_trades: int}).

  Returns {metric_key: {asset: score}} to merge into external_scores,
  or {} if no source uses event mode.
  """
  from collections import defaultdict

  from backend.analysis.optimizer_seeds import compute_event_seed_scores

  pb = per_batch or {}
  any_event = event_mode or any(cfg.get("event_mode", False) for cfg in pb.values())
  if not any_event:
    return {}

  metric_key = seed_metrics[0] if seed_metrics else "sortino"

  # Group trades by their source batch_id
  batch_trades: dict[int, list] = defaultdict(list)
  for t in trades:
    key = _trade_key(t, run_batch_map) if include_side else t.symbol
    bid = asset_batch.get(key)
    if bid is not None:
      batch_trades[bid].append(t)

  merged: dict[str, float] = {}
  for bid, btrades in batch_trades.items():
    use_event = pb.get(str(bid), {}).get("event_mode", event_mode)
    if not use_event:
      continue
    n = pb.get(str(bid), {}).get("n_trades", n_trades)
    scores = compute_event_seed_scores(
      btrades, n_trades=n,
      objective=metric_key,
      staleness_days=staleness_days,
      include_side=include_side,
      date_from=date_from,
    )
    # Re-map scores to tagged keys if using batch tags
    if run_batch_map:
      tagged_scores: dict[str, float] = {}
      for k, v in scores.items():
        tagged_scores[_add_batch_tag(k, bid)] = v
      merged.update(tagged_scores)
    else:
      merged.update(scores)

  return {metric_key: merged} if merged else {}


class CrossBuildRequest(BaseModel):
  batch_ids: list[int] = []
  portfolio_ids: list[int] = []  # saved portfolios as virtual batches
  split_mode: bool = True  # False = combined (single pool), True = L/S split
  n_assets: int = 10  # used when split_mode = False
  objective: str = "hybrid"  # used when split_mode = False
  n_longs: int = 5
  n_shorts: int = 5
  within_objective: str = "uncorrelated"
  combined_objective: str = "stability"
  corr_method: str = "standard"
  div_algo: str = "greedy"
  # Genetic algorithm params (only used when div_algo == "genetic")
  genetic_generations: int = 50
  genetic_population: int = 100
  genetic_mutation_rate: float = 0.10
  genetic_crossover_rate: float = 0.70
  genetic_elite_pct: float = 0.10
  n_portfolios: int = 3
  # Global optimizer params
  seed_metrics: list[str] = ["sortino"]
  max_seeds: int = 20
  prioritize_stability: bool = False
  date_from: str | None = None
  date_to: str | None = None
  extra_slippage_pct: float = 0.0
  extra_commission_pct: float = 0.0
  funding_daily_pct: float = 0.0
  spread_pct: float = 0.0
  # Per-batch cost overrides — JSON keyed by batch_id → {slippage}
  per_batch_costs: str | None = None
  # Per-batch config — keyed by batch_id as string
  # Each: {min_trades, min_pf, min_wr, max_longs, max_shorts, tc_*,
  #        event_mode, n_trades}  ← new event-mode per-batch overrides
  per_batch: dict[str, dict] | None = None
  # Global quality filters (fallback when per_batch is None)
  min_trades: int = 0
  min_pf: float = 0.0
  min_wr: float = 0.0
  # Per-asset max PF filter: 0 = off; > 0 = remove assets with PF above this
  max_asset_pf: float = 0.0
  # When True + max_asset_pf > 0: keep assets but cap their PF for portfolio PF calc
  cap_asset_pf: bool = False
  # Portfolio-level quality gates (post-optimization, hard reject)
  portfolio_min_pf: float = 0.0
  portfolio_min_wr: float = 0.0
  # Trade-count constraints (combined for L+S)
  tc_low_threshold: int = 0
  tc_max_low: int = 99
  tc_high_threshold: int = 0
  tc_min_high: int = 0
  # Event-based L1 scoring (global default; overridable per-batch via per_batch)
  event_mode: bool = False
  n_trades: int = 20
  staleness_days: int = 180
  # Recency-weighted seed scoring
  recency_enabled: bool = False
  recency_mode: str = "gradual"
  recency_batch_size: int = 10
  recency_boost_factor: float = 3.0
  # Benchmark robustness
  benchmark_enabled: bool = False
  benchmark_source: str = ""
  benchmark_split: str = "time"
  benchmark_threshold: float = 1.2
  benchmark_elite_target: float = 1.0
  benchmark_exclude_fragile: bool = True
  # Benchmark seed score enhancements (0.0 = off, 0.0-0.5 range)
  benchmark_consistency_blend: float = 0.0
  benchmark_fragility_blend: float = 0.0
  benchmark_wr_harmony_blend: float = 0.0
  streamlit_logic: bool = False
  # Expand greedy pool: when True, greedy picks from ALL profitable assets
  expand_greedy_pool: bool = True
  # Seed prior: soft tiebreaker weight for seed quality in greedy (0 = off)
  seed_prior_weight: float = 0.0
  # Per-metric seed weights: {metric_code: weight}
  seed_weights: dict[str, float] | None = None
  # Pool-per-batch: keep top N assets from each batch after L1 scoring
  # 0 = auto (2× target size), >0 = explicit pool size per batch
  pool_per_batch: int = 0
  # Category filter — only include assets matching selected categories
  category_filter: list[str] = []
  # Portfolio exclusion filter — remove/limit assets from a saved portfolio
  exclude_portfolio_ids: list[int] = []
  exclude_portfolio_mode: str = "full"  # "full" = remove all, "partial" = keep X%
  exclude_portfolio_keep_pct: float = 0.0  # 0-100, percentage to keep when partial
  exclude_portfolio_method: str = "score"  # "score" | "correlation" | "blend"
  # Portfolio diversity: max Jaccard overlap between candidates (None = off)
  max_overlap: float | None = None
  # Risk profile — position sizing applied to all batch trades before optimization
  risk_profile_mode: str = "fixed"
  risk_profile_value: float = 0.0
  risk_profile_initial_capital: float = 10_000.0
  risk_profile_multiplier: float = 2.0
  risk_profile_max_streak: int = 3
  risk_profile_martingale_compound: str = "off"
  risk_profile_martingale_scope: Literal["asset", "portfolio", "asset_batch", "batch"] = "asset"
  risk_profile_martingale_count_mode: Literal["full_position", "each_exit", "confirmed_tsl"] = "full_position"
  risk_profile_martingale_start_after: int = 0
  risk_profile_anti_martingale_reset_after: int = 0
  risk_profile_kelly_n: float = 2.0
  risk_profile_kelly_warmup: int = 20
  risk_profile_kelly_scope: str = "portfolio"
  risk_profile_kelly_lookback: int = 0
  risk_profile_kelly_cap: float = 0.0
  risk_profile_sizing_master: str = "portfolio"
  risk_profile_batch_configs: str | None = None
  risk_profile_auto_asset_configs: str | None = None
  # Boundary clipping — force-close IS trades at IS boundary close price
  clip_is: bool = True
  # Stage 1: Pre-filter (structural quality)
  prefilter_enabled: bool = False
  prefilter_mode: str = "trend"
  prefilter_hurst_min: float = 0.50
  prefilter_ter_percentile: int = 25
  prefilter_hurst_max: float = 0.50
  prefilter_vr_max: float = 1.0
  prefilter_min_crossing_rate: float = 0.05
  # Stage 3: DSR benchmark gate
  benchmark_dsr_gate: bool = False
  # Stage 4: AC post-filter
  ac_postfilter: bool = False
  ac_postfilter_type: str = "trend"
  ac_lag: int = 1
  # Max drawdown hard limit — reject portfolios exceeding this DD%
  max_allowed_drawdown_pct: float = 0.0  # 0 = disabled, e.g. 30.0 = reject >30% DD
  # Optimizer engine: "current" (principal), "fast" (batch-vectorized), "classic" (pre-2026-03-12)
  optimizer_engine: str = "current"
  # Correlation jitter — perturb correlation matrix for candidate diversity
  corr_jitter_enabled: bool = False
  corr_jitter_temperature: float = 0.03
  # Post-construction refinement
  refinement_enabled: bool = False
  refinement_mode: str = "prune_and_swap"  # "prune", "swap", "prune_and_swap"
  refinement_metric: str = ""  # empty = use same as objective
  # ── Overfitting Guard ──────────────────────────────────────────
  overfit_guard_enabled: bool = False
  of_log_enabled: bool = False
  of_log_cap_pf: float = 4.0
  of_log_cap_sortino: float = 3.0
  of_log_cap_payoff: float = 3.0
  of_log_cap_wr: float = 70.0
  of_log_cap_sharpe: float = 2.5
  of_log_cap_calmar: float = 3.0
  of_z_score_enabled: bool = False
  of_z_threshold: float = 2.0
  of_z_penalty_strength: float = 0.15
  of_bayes_enabled: bool = False
  of_bayes_k: float = 5.0
  of_sigmoid_enabled: bool = False
  of_sig_cap_pf: float = 4.0
  of_sig_cap_sortino: float = 3.0
  of_sig_cap_payoff: float = 3.0
  of_sig_cap_wr: float = 70.0
  of_sig_cap_sharpe: float = 2.5
  of_sig_cap_calmar: float = 3.0
  of_normal_range_enabled: bool = False
  of_nr_pct_low: float = 20.0
  of_nr_pct_high: float = 60.0
  of_nr_penalty_scale: float = 0.3
  of_nr_bonus: float = 0.1
  of_wf_rerank_enabled: bool = False
  of_wf_split_pct: float = 70.0
  of_wf_validation_metric: str = "stability"
  # Seed-blind selection: when True, Tournament/Quality First/Marginal Sharpe/Ensemble
  # treat all pool assets equally instead of using seed rankings (more diverse)
  seed_blind_selection: bool = True
  # Bybit minimum size gate — drop assets whose scaled trades violate exchange limits
  bybit_size_gate: bool = False
  bybit_size_gate_max_reject_pct: float = 0.0  # 0 = binary (any rejection = fail)
  # ── Harmony Gate (L1.5 / L2.5) ──────────────────────────────────
  harmony_enabled: bool = False
  harmony_strength_l15: int = 50
  harmony_strength_l25: int = 50
  harmony_weights: dict[str, float] | None = None
  harmony_per_group: dict[str, bool] | None = None
  # ── Session Filter — inline trade-time filtering ──
  session_filter_enabled: bool = False
  session_filter_mode: str = "fixed"  # "fixed" | "auto" | "saved_mask"
  session_filter_exclude_days: list[int] = []   # 0=Mon..6=Sun
  session_filter_exclude_hours: list[int] = []  # 0-23
  session_filter_auto_min_trades: int = 10
  session_filter_auto_include_insufficient: bool = True
  session_filter_grade_on_cropped: bool = False
  session_mask_id: int | None = None  # FK to session_time_masks (saved_mask mode)


class CrossBuildManualRequest(BaseModel):
  """Manual mode: per-strategy optimization with triple-layer objectives."""
  batch_ids: list[int] = []
  portfolio_ids: list[int] = []  # saved portfolios as virtual batches
  allocations: dict[str, dict] = {}  # batch_id -> {n_longs, n_shorts}
  l2_groups: list[list[int]] = []  # strategy groups for Phase 3
  obj_layer1: str = "hybrid"  # Within-group (3 choices)
  obj_layer2: str = "stability"  # Strategy balance (all objectives)
  obj_layer3: str = "hybrid"  # Global mix (all objectives)
  candidates_per_strategy: int = 3
  n_final_strats: int | None = None  # Default: all strategies
  n_portfolios: int = 3
  corr_method: str = "standard"
  div_algo: str = "greedy"
  # Genetic algorithm params (only used when div_algo == "genetic")
  genetic_generations: int = 50
  genetic_population: int = 100
  genetic_mutation_rate: float = 0.10
  genetic_crossover_rate: float = 0.70
  genetic_elite_pct: float = 0.10
  seed_metrics: list[str] = ["sortino"]
  max_seeds: int = 20
  prioritize_stability: bool = False
  date_from: str | None = None
  date_to: str | None = None
  extra_slippage_pct: float = 0.0
  extra_commission_pct: float = 0.0
  funding_daily_pct: float = 0.0
  spread_pct: float = 0.0
  per_batch_costs: str | None = None
  per_batch: dict[str, dict] | None = None
  # Global quality filters (fallback when per_batch is None)
  min_trades: int = 0
  min_pf: float = 0.0
  min_wr: float = 0.0
  # Per-asset max PF filter: 0 = off; > 0 = remove assets with PF above this
  max_asset_pf: float = 0.0
  cap_asset_pf: bool = False
  # Portfolio-level quality gates (post-optimization, hard reject)
  portfolio_min_pf: float = 0.0
  portfolio_min_wr: float = 0.0
  # Trade-count constraints (combined for L+S)
  tc_low_threshold: int = 0
  tc_max_low: int = 99
  tc_high_threshold: int = 0
  tc_min_high: int = 0
  # Event-based L1 scoring (global default; overridable per-batch via per_batch)
  event_mode: bool = False
  n_trades: int = 20
  staleness_days: int = 180
  # Recency-weighted seed scoring
  recency_enabled: bool = False
  recency_mode: str = "gradual"
  recency_batch_size: int = 10
  recency_boost_factor: float = 3.0
  # Benchmark robustness
  benchmark_enabled: bool = False
  benchmark_source: str = ""
  benchmark_split: str = "time"
  benchmark_threshold: float = 1.2
  benchmark_elite_target: float = 1.0
  benchmark_exclude_fragile: bool = True
  # Benchmark seed score enhancements (0.0 = off, 0.0-0.5 range)
  benchmark_consistency_blend: float = 0.0
  benchmark_fragility_blend: float = 0.0
  benchmark_wr_harmony_blend: float = 0.0
  # L1 quality gate mode
  l1_quality_gate: bool = False
  n_per_group: int = 15
  # ── Pool Mode ──────────────────────────────────────────────────
  # Seed-rank L1 → optimizer L2 → combiner L3 (alternative to classic manual)
  pool_mode: bool = False
  pool_per_batch: int = 50       # L1: top N assets per batch via seed ranking
  l2_n_assets: int = 15          # L2: total assets per variant
  l2_n_variants: int = 10        # L2: number of different selections to produce
  l2_max_overlap: float = 0.7    # L2: max Jaccard overlap between variants
  l3_max_overlap: float = 0.7    # L3: max Jaccard overlap between final candidates
  l2_symbol_dedup: str = "keep_all"       # "keep_all" | "same_side" | "any_side"
  l2_dedup_tiebreaker: str = "pnl"        # "pnl" | "seed_rank"
  streamlit_logic: bool = False
  # Expand greedy pool: when True, greedy picks from ALL profitable assets
  expand_greedy_pool: bool = True
  # Seed prior: soft tiebreaker weight for seed quality in greedy (0 = off)
  seed_prior_weight: float = 0.0
  # Per-metric seed weights: {metric_code: weight}
  seed_weights: dict[str, float] | None = None
  # Category filter — only include assets matching selected categories
  category_filter: list[str] = []
  # Portfolio exclusion filter — remove/limit assets from a saved portfolio
  exclude_portfolio_ids: list[int] = []
  exclude_portfolio_mode: str = "full"  # "full" = remove all, "partial" = keep X%
  exclude_portfolio_keep_pct: float = 0.0  # 0-100, percentage to keep when partial
  exclude_portfolio_method: str = "score"  # "score" | "correlation" | "blend"
  # Portfolio diversity: max Jaccard overlap between candidates (None = off)
  max_overlap: float | None = None
  # Risk profile — position sizing applied to all batch trades before optimization
  risk_profile_mode: str = "fixed"
  risk_profile_value: float = 0.0
  risk_profile_initial_capital: float = 10_000.0
  risk_profile_multiplier: float = 2.0
  risk_profile_max_streak: int = 3
  risk_profile_martingale_compound: str = "off"
  risk_profile_martingale_scope: Literal["asset", "portfolio", "asset_batch", "batch"] = "asset"
  risk_profile_martingale_count_mode: Literal["full_position", "each_exit", "confirmed_tsl"] = "full_position"
  risk_profile_martingale_start_after: int = 0
  risk_profile_anti_martingale_reset_after: int = 0
  risk_profile_kelly_n: float = 2.0
  risk_profile_kelly_warmup: int = 20
  risk_profile_kelly_scope: str = "portfolio"
  risk_profile_kelly_lookback: int = 0
  risk_profile_kelly_cap: float = 0.0
  risk_profile_sizing_master: str = "portfolio"
  risk_profile_batch_configs: str | None = None
  risk_profile_auto_asset_configs: str | None = None
  # Boundary clipping — force-close IS trades at IS boundary close price
  clip_is: bool = True
  # Stage 1: Pre-filter (structural quality)
  prefilter_enabled: bool = False
  prefilter_mode: str = "trend"
  prefilter_hurst_min: float = 0.50
  prefilter_ter_percentile: int = 25
  prefilter_hurst_max: float = 0.50
  prefilter_vr_max: float = 1.0
  prefilter_min_crossing_rate: float = 0.05
  # Stage 3: DSR benchmark gate
  benchmark_dsr_gate: bool = False
  # Stage 4: AC post-filter
  ac_postfilter: bool = False
  ac_postfilter_type: str = "trend"
  ac_lag: int = 1
  # Max drawdown hard limit — reject portfolios exceeding this DD%
  max_allowed_drawdown_pct: float = 0.0  # 0 = disabled, e.g. 30.0 = reject >30% DD
  # Optimizer engine: "current" (principal), "fast" (batch-vectorized), "classic" (pre-2026-03-12)
  optimizer_engine: str = "current"
  # Correlation jitter — perturb correlation matrix for candidate diversity
  corr_jitter_enabled: bool = False
  corr_jitter_temperature: float = 0.03
  # Post-construction refinement
  refinement_enabled: bool = False
  refinement_mode: str = "prune_and_swap"  # "prune", "swap", "prune_and_swap"
  refinement_metric: str = ""  # empty = use same as objective
  # ── Overfitting Guard ──────────────────────────────────────────
  overfit_guard_enabled: bool = False
  of_log_enabled: bool = False
  of_log_cap_pf: float = 4.0
  of_log_cap_sortino: float = 3.0
  of_log_cap_payoff: float = 3.0
  of_log_cap_wr: float = 70.0
  of_log_cap_sharpe: float = 2.5
  of_log_cap_calmar: float = 3.0
  of_z_score_enabled: bool = False
  of_z_threshold: float = 2.0
  of_z_penalty_strength: float = 0.15
  of_bayes_enabled: bool = False
  of_bayes_k: float = 5.0
  of_sigmoid_enabled: bool = False
  of_sig_cap_pf: float = 4.0
  of_sig_cap_sortino: float = 3.0
  of_sig_cap_payoff: float = 3.0
  of_sig_cap_wr: float = 70.0
  of_sig_cap_sharpe: float = 2.5
  of_sig_cap_calmar: float = 3.0
  of_normal_range_enabled: bool = False
  of_nr_pct_low: float = 20.0
  of_nr_pct_high: float = 60.0
  of_nr_penalty_scale: float = 0.3
  of_nr_bonus: float = 0.1
  of_wf_rerank_enabled: bool = False
  of_wf_split_pct: float = 70.0
  of_wf_validation_metric: str = "stability"
  # Bybit minimum size gate — drop assets whose scaled trades violate exchange limits
  bybit_size_gate: bool = False
  bybit_size_gate_max_reject_pct: float = 0.0  # 0 = binary (any rejection = fail)
  # ── Harmony Gate (L1.5 / L2.5) ──────────────────────────────────
  harmony_enabled: bool = False
  harmony_strength_l15: int = 50
  harmony_strength_l25: int = 50
  harmony_weights: dict[str, float] | None = None
  harmony_per_group: dict[str, bool] | None = None
  # ── Session Filter — inline trade-time filtering ──
  session_filter_enabled: bool = False
  session_filter_mode: str = "fixed"  # "fixed" | "auto" | "saved_mask"
  session_filter_exclude_days: list[int] = []   # 0=Mon..6=Sun
  session_filter_exclude_hours: list[int] = []  # 0-23
  session_filter_auto_min_trades: int = 10
  session_filter_auto_include_insufficient: bool = True
  session_filter_grade_on_cropped: bool = False
  session_mask_id: int | None = None  # FK to session_time_masks (saved_mask mode)


class CrossCorrelationRequest(BaseModel):
  batch_ids: list[int] = []
  portfolio_ids: list[int] = []  # saved portfolios as virtual batches
  corr_method: str = "standard"
  min_trades: int = 0
  min_pf: float = 0.0
  min_wr: float = 0.0
  date_from: str | None = None
  date_to: str | None = None
  # Boundary clipping — force-close IS trades at IS boundary close price
  clip_is: bool = True


def _portfolio_metrics(
  daily_pnl: pd.DataFrame, assets: list[str],
  trade_df: pd.DataFrame | None = None,
) -> dict:
  """Compute portfolio metrics for a set of assets.

  Expectancy metrics (PF, Win Rate, Avg Trade, Net Profit) are calculated
  at trade-level resolution when trade_df is available, merging multi-exit
  fills by position_id into positions.
  Time-series metrics (drawdown, stability, sortino, calmar, recovery) use
  daily PnL which is the standard approach in portfolio theory.

  trade_df: DataFrame with [Asset, Net Profit, position_id].
  """
  import numpy as np

  cols = [c for c in assets if c in daily_pnl.columns]
  if not cols:
    return {"net_profit": 0, "profit_factor": 0, "max_drawdown": 0, "stability": 0, "n_assets": 0}
  combined = daily_pnl[cols].sum(axis=1)
  equity = combined.cumsum()
  dd = float((equity - equity.cummax()).min())
  stab = float(calculate_stability(combined))

  # --- Expectancy metrics from trade-level data (position-merged) ---
  net = 0.0
  pf = 0.0
  total_trades = 0
  win_rate = 0.0
  avg_trade = 0.0
  if trade_df is not None and not trade_df.empty:
    ptf = trade_df[trade_df["Asset"].isin(cols)]
    if not ptf.empty and "position_id" in ptf.columns:
      pos_pnl = ptf.groupby(["Asset", "position_id"])["Net Profit"].sum()
      total_trades = len(pos_pnl)
      net = float(pos_pnl.sum())
      gp = float(pos_pnl[pos_pnl > 0].sum())
      gl = abs(float(pos_pnl[pos_pnl <= 0].sum()))
      pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)
      if total_trades > 0:
        wins = int((pos_pnl > 0).sum())
        win_rate = round(wins / total_trades * 100, 1)
        avg_trade = round(net / total_trades, 2)
    elif not ptf.empty:
      # Fallback: no position_id — count rows as trades
      total_trades = len(ptf)
      net = float(ptf["Net Profit"].sum())
      gp = float(ptf.loc[ptf["Net Profit"] > 0, "Net Profit"].sum())
      gl = abs(float(ptf.loc[ptf["Net Profit"] <= 0, "Net Profit"].sum()))
      pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)
      if total_trades > 0:
        wins = int((ptf["Net Profit"] > 0).sum())
        win_rate = round(wins / total_trades * 100, 1)
        avg_trade = round(net / total_trades, 2)
  if total_trades == 0:
    # No trade data — fall back to daily PnL for net profit and PF
    net = float(combined.sum())
    gp = float(combined[combined > 0].sum())
    gl = float(abs(combined[combined < 0].sum()))
    pf = round(min(gp / gl, 10.0), 4) if gl > 0 else (10.0 if gp > 0 else 0.0)

  # --- Time-series metrics from daily PnL (standard approach) ---
  neg = combined[combined < 0]
  # Downside deviation = sqrt(mean(d^2))
  downside_dev = float(np.sqrt(np.mean(neg.values ** 2))) if len(neg) > 0 else 0.0
  daily_mean = float(combined.mean()) if len(combined) > 0 else 0.0
  # Annualize using 365 for crypto
  sortino = round(daily_mean / downside_dev * (365 ** 0.5), 2) if downside_dev > 0 else 0.0

  # Calmar = annualized return / |max DD|
  annual_ret = daily_mean * 365
  calmar = round(annual_ret / abs(dd), 2) if dd < 0 else 0.0

  # Recovery factor = net profit / |max DD|
  recovery = round(net / abs(dd), 2) if dd < 0 else 0.0

  return sanitize_for_json({
    "net_profit": round(net, 2), "profit_factor": pf,
    "max_drawdown": round(dd, 2), "stability": round(stab, 4), "n_assets": len(cols),
    "win_rate": win_rate, "total_trades": total_trades, "avg_trade": avg_trade,
    "sortino": sortino, "calmar_ratio": calmar, "recovery_factor": recovery,
  })


def _apply_portfolio_gates(
  candidates: list[dict],
  trade_df: pd.DataFrame,
  min_pf: float = 0.0,
  min_wr: float = 0.0,
  cap_asset_pf: float = 0.0,
) -> list[dict]:
  """Post-optimization portfolio quality gate — hard reject candidates outside bounds.

  When cap_asset_pf > 0, recomputes portfolio PF by capping each asset's
  gross profit contribution so no single asset inflates the aggregate PF.
  The original metrics["profit_factor"] is kept for display.
  """
  if min_pf <= 0 and min_wr <= 0:
    return candidates

  kept = []
  _rej_pf = 0
  _rej_wr = 0
  for c in candidates:
    m = c.get("metrics", {})
    # --- PF gate ---
    pf_check = float(m.get("profit_factor", 0))
    if cap_asset_pf > 0 and min_pf > 0 and trade_df is not None and not trade_df.empty:
      # Recompute portfolio PF with per-asset PF capped
      assets = c.get("assets", [])
      ptf = trade_df[trade_df["Asset"].isin(assets)]
      if not ptf.empty and "position_id" in ptf.columns:
        capped_gp = 0.0
        capped_gl = 0.0
        for _a in assets:
          at = ptf[ptf["Asset"] == _a]
          if at.empty:
            continue
          pos_pnl = at.groupby("position_id")["Net Profit"].sum()
          a_gp = float(pos_pnl[pos_pnl > 0].sum())
          a_gl = abs(float(pos_pnl[pos_pnl <= 0].sum()))
          a_pf = min(a_gp / a_gl, 10.0) if a_gl > 0 else 10.0
          if a_pf > cap_asset_pf and a_gl > 0:
            # Cap: scale gross profit so PF equals the cap
            a_gp = cap_asset_pf * a_gl
          capped_gp += a_gp
          capped_gl += a_gl
        pf_check = round(min(capped_gp / capped_gl, 10.0), 4) if capped_gl > 0 else 10.0

    if min_pf > 0 and pf_check < min_pf:
      _rej_pf += 1
      continue

    # --- WR gate ---
    wr_check = float(m.get("win_rate", 0))
    if min_wr > 0 and wr_check < min_wr:
      _rej_wr += 1
      continue

    kept.append(c)

  if _rej_pf or _rej_wr:
    logger.info("  Portfolio gates: %d/%d kept | rejected: pf_gate=%d, wr_gate=%d",
                len(kept), len(candidates), _rej_pf, _rej_wr)
  return kept


@router.post("/cross-strategy-build")
def cross_strategy_build(request: Request, req: CrossBuildRequest, background_tasks: BackgroundTasks):
  """Merge batches and run cross-strategy L/S optimizer in background."""
  rate_limit(request, max_requests=3, window_seconds=60, key="optimizer")
  total_sources = len(req.batch_ids) + len(req.portfolio_ids)
  if total_sources < 2:
    raise HTTPException(400, "Need at least 2 data sources (batches or portfolios)")

  from backend.db.database import SessionLocal
  task_id = uuid.uuid4().hex[:12]
  with _optimize_lock:
    _optimize_progress[task_id] = {"status": "running", "result": None, "error": None}
  background_tasks.add_task(
    _run_optimize_task, task_id, req, SessionLocal,
    _cross_strategy_build_inner, "Cross-Strategy Build",
  )
  return {"task_id": task_id, "status": "running"}


def _cross_strategy_build_inner(req: CrossBuildRequest, db: Session, task_id: str = ""):
  """Inner logic for cross-strategy build (runs in background thread)."""
  try:
    import time as _time
    from backend.analysis.optimization_cache import (
      get_cached_l0_incremental, put_cached_l0, update_cached_l0,
      get_cached_l0, get_cached_corr, put_cached_corr,
      _make_date_key, _make_corr_key, clone_cached_trades,
    )
    _t0 = _time.perf_counter()
    logger.info("═══ CROSS-STRATEGY BUILD START ═══  batches=%s portfolios=%s date_to=%s",
                req.batch_ids, req.portfolio_ids, req.date_to)
    logger.info("Settings: n_assets=%s objective=%s pool=%s seeds=%s stability=%s corr=%s benchmark=%s",
                req.n_assets, req.objective, req.pool_per_batch, req.max_seeds,
                req.prioritize_stability, req.corr_method, req.benchmark_enabled)

    date_key = _make_date_key(req.date_from, req.date_to, req.clip_is)
    _update_progress(task_id, 0, 6, "Checking cache...")

    # [1/6] Build batch-tagged daily PnL — check cache first
    cached_entry, new_batches, new_portfolios = get_cached_l0_incremental(
      req.batch_ids, req.portfolio_ids or [],
      req.date_from, req.date_to, req.clip_is,
    )

    run_batch_map: dict[int, int] = {}
    p_asset_batch: dict[str, int] = {}
    p_strat_map: dict[int, str] = {}
    p_asset_strat: dict[str, str] = {}

    if cached_entry is not None and not new_batches and not new_portfolios:
      # Full cache hit — restore portfolio metadata from cache
      daily_pnl = cached_entry.daily_pnl.copy()
      trades = clone_cached_trades(cached_entry.trades)
      run_batch_map = cached_entry.run_batch_map.copy()
      p_asset_batch = cached_entry.p_asset_batch.copy()
      p_strat_map = cached_entry.p_strat_map.copy()
      p_asset_strat = cached_entry.p_asset_strat.copy()
      logger.info("[1/6] CACHE HIT — loaded %d assets from cache (%.1fs)",
                  daily_pnl.shape[1], _time.perf_counter() - _t0)
      # Filter to requested batches only (prevent cache superset leak)
      daily_pnl, trades = _filter_to_requested(
        daily_pnl, trades, run_batch_map,
        req.batch_ids, req.portfolio_ids or [],
      )

    elif cached_entry is not None and (new_batches or new_portfolios):
      # Incremental load — only fetch new batches/portfolios
      new_daily_pnl = pd.DataFrame()
      new_trades: list = []
      new_rbm: dict[int, int] = {}
      new_pab: dict[str, int] = {}
      new_psm: dict[int, str] = {}
      new_pas: dict[str, str] = {}
      if new_batches:
        new_daily_pnl, new_trades, new_rbm = _build_cross_daily_pnl(
          new_batches, db, req.date_from, req.date_to,
          clip_boundary=req.clip_is,
        )
      if new_portfolios:
        pt, p_ab, p_sm, p_as, p_rbm = _load_portfolio_trades(
          new_portfolios, db, req.date_from, req.date_to, tagged=True,
          clip_boundary=req.clip_is,
        )
        new_pab.update(p_ab); new_psm.update(p_sm); new_pas.update(p_as)
        new_trades.extend(pt)
        new_rbm.update(p_rbm)
        if pt:
          p_pnl = build_daily_pnl_matrix(pt, include_side=True)
          if not p_pnl.empty:
            tagged_cols = {}
            for col in p_pnl.columns:
              for pkey, pbid in p_ab.items():
                if _strip_batch_tag(pkey) == col:
                  tagged_cols[col] = pkey
                  break
              else:
                tagged_cols[col] = col
            p_pnl.columns = [tagged_cols.get(c, c) for c in p_pnl.columns]
            new_daily_pnl = pd.concat([new_daily_pnl, p_pnl], axis=1).fillna(0) if not new_daily_pnl.empty else p_pnl

      # Merge into cache (including portfolio metadata)
      update_cached_l0(date_key, cached_entry, new_batches, new_portfolios,
                       new_daily_pnl, new_trades, new_rbm,
                       new_pab, new_psm, new_pas)
      updated = get_cached_l0(req.batch_ids, req.portfolio_ids or [],
                              req.date_from, req.date_to, req.clip_is)
      daily_pnl = updated.daily_pnl.copy()
      trades = list(updated.trades)
      run_batch_map = updated.run_batch_map.copy()
      p_asset_batch = updated.p_asset_batch.copy()
      p_strat_map = updated.p_strat_map.copy()
      p_asset_strat = updated.p_asset_strat.copy()
      logger.info("[1/6] INCREMENTAL LOAD — added %d new batches, total %d assets (%.1fs)",
                  len(new_batches) + len(new_portfolios), daily_pnl.shape[1],
                  _time.perf_counter() - _t0)
      # Filter to requested batches only (prevent cache superset leak)
      daily_pnl, trades = _filter_to_requested(
        daily_pnl, trades, run_batch_map,
        req.batch_ids, req.portfolio_ids or [],
      )

    else:
      # Full load (cache miss)
      if req.batch_ids:
        logger.info("[1/6] Loading trades from %d batches...", len(req.batch_ids))
        daily_pnl, trades, run_batch_map = _build_cross_daily_pnl(
          req.batch_ids, db, req.date_from, req.date_to,
          clip_boundary=req.clip_is,
        )
      else:
        trades = []
        daily_pnl = pd.DataFrame()

      # Merge in portfolio trades
      if req.portfolio_ids:
        pt, p_asset_batch, p_strat_map, p_asset_strat, p_rbm = _load_portfolio_trades(
          req.portfolio_ids, db, req.date_from, req.date_to, tagged=True,
          clip_boundary=req.clip_is,
        )
        trades.extend(pt)
        run_batch_map.update(p_rbm)
        if pt:
          p_pnl = build_daily_pnl_matrix(pt, include_side=True)
          if not p_pnl.empty:
            tagged_cols = {}
            for col in p_pnl.columns:
              for pkey, pbid in p_asset_batch.items():
                if _strip_batch_tag(pkey) == col:
                  tagged_cols[col] = pkey
                  break
              else:
                tagged_cols[col] = col
            p_pnl.columns = [tagged_cols.get(c, c) for c in p_pnl.columns]
            if daily_pnl.empty:
              daily_pnl = p_pnl
            else:
              daily_pnl = pd.concat([daily_pnl, p_pnl], axis=1).fillna(0)

      # Store in cache (including portfolio metadata)
      if trades:
        put_cached_l0(req.batch_ids, req.portfolio_ids or [],
                      req.date_from, req.date_to, req.clip_is,
                      daily_pnl, trades, run_batch_map,
                      p_asset_batch, p_strat_map, p_asset_strat)

    if not trades:
      raise HTTPException(404, "No trades found across selected sources")

    # Early logging: what did we load?
    ab_map_early = _build_asset_batch_map(req.batch_ids, db, tagged=True) if req.batch_ids else {}
    ab_map_early.update(p_asset_batch)
    _log_batch_counts(daily_pnl, ab_map_early, "After load")
    _t1 = _time.perf_counter()
    logger.info("[1/6] Loaded %d trades, %d assets (%.1fs)",
                len(trades), daily_pnl.shape[1], _t1 - _t0)
    _update_progress(task_id, 1, 6, "Trades loaded", {"step_1_load": round(_t1 - _t0, 2)})

    # No daily_pnl truncation — trades filtered by entry_time may exit after cutoff

    # ── Session Filter — inline trade-time filtering ──
    if req.session_filter_enabled and trades:
      from backend.analysis.session_mask_filter import apply_session_mask as _sf_apply, build_fixed_mask
      _sf_mask_data = None
      if req.session_filter_mode == "fixed" and (req.session_filter_exclude_days or req.session_filter_exclude_hours):
        _sf_mask_data = build_fixed_mask(req.session_filter_exclude_days, req.session_filter_exclude_hours)
      elif req.session_filter_mode == "auto":
        from backend.analysis.session_auto_mask import compute_auto_mask
        _auto = compute_auto_mask(trades, req.session_filter_auto_min_trades, req.session_filter_auto_include_insufficient)
        _sf_mask_data = _auto["mask_data"]
        logger.info("Cross session filter (auto): kept hours=%s, kept days=%s", _auto["kept_hours"], _auto["kept_days"])
      elif req.session_filter_mode == "saved_mask" and req.session_mask_id:
        from backend.api.routes.analysis import _apply_session_mask
        trades = _apply_session_mask(trades, req.session_mask_id, db, n_free_params=req.n_assets)
        _sf_mask_data = None  # already applied
      if _sf_mask_data:
        _before = len(trades)
        trades = _sf_apply(trades, _sf_mask_data)
        logger.info("Cross session filter (%s): %d → %d trades", req.session_filter_mode, _before, len(trades))
      daily_pnl = _rebuild_daily_pnl_from_trades(trades, run_batch_map)

    # Apply risk profile scaling FIRST (scales quantity for correct notional in stress test)
    if req.risk_profile_mode != "fixed" or req.risk_profile_value > 0 or req.risk_profile_sizing_master == "per_batch":
      from backend.api.routes.analysis import _apply_risk_scaling
      trades = _apply_risk_scaling(trades, req, db, include_side=True, run_batch_map=run_batch_map)
      daily_pnl = _rebuild_daily_pnl_from_trades(trades, run_batch_map)

    # Apply extra cost stress test AFTER scaling (uses scaled notional)
    _pbc = _parse_per_batch_costs(req.per_batch_costs) if req.per_batch_costs else None
    if req.extra_slippage_pct > 0 or req.extra_commission_pct > 0 or req.funding_daily_pct > 0 or req.spread_pct > 0 or _pbc:
      from backend.api.routes.analysis import _apply_stress_slippage
      trades = _apply_stress_slippage(trades, req.extra_slippage_pct, db,
                                       extra_commission_pct=req.extra_commission_pct,
                                       funding_daily_pct=req.funding_daily_pct,
                                       spread_pct=req.spread_pct,
                                       per_batch_costs=_pbc,
                                       run_batch_map=run_batch_map)
      daily_pnl = _rebuild_daily_pnl_from_trades(trades, run_batch_map)

    # Category filter (match on clean symbol)
    if req.category_filter:
      from backend.api.routes.analysis import _filter_by_category
      # Map tagged columns to clean symbols for category matching
      clean_pnl = daily_pnl.copy()
      clean_pnl.columns = [_strip_batch_tag(c) for c in daily_pnl.columns]
      clean_pnl = _filter_by_category(clean_pnl, req.category_filter, db)
      # Keep only the tagged columns that survived filtering
      keep_clean = set(clean_pnl.columns)
      keep_cols = [c for c in daily_pnl.columns if _strip_batch_tag(c) in keep_clean]
      daily_pnl = daily_pnl[keep_cols]

    # Portfolio exclusion filter — full mode (remove before quality filters)
    excluded_names: set[str] = set()
    if req.exclude_portfolio_ids:
      excluded_names = _get_excluded_asset_names(req.exclude_portfolio_ids, db)
      if excluded_names and req.exclude_portfolio_mode == "full":
        daily_pnl = _apply_exclude_portfolio_full(daily_pnl, excluded_names)

    # Per-batch quality filters + allocation caps
    logger.info("[2/6] Quality filter params from request: min_trades=%d, min_pf=%.2f, min_wr=%.1f, per_batch=%s",
                req.min_trades, req.min_pf, req.min_wr, "YES" if req.per_batch else "NO")
    if req.per_batch:
      asset_batch = _build_asset_batch_map(req.batch_ids, db, tagged=True) if req.batch_ids else {}
      asset_batch.update(p_asset_batch)
      daily_pnl = _apply_per_batch_filters(
        trades, daily_pnl, req.per_batch, asset_batch, run_batch_map,
        date_to=req.date_to,
        default_min_trades=req.min_trades, default_min_pf=req.min_pf,
      )
    else:
      # Default quality filters — need tagged trade keys for matching
      from backend.api.routes.analysis import _filter_daily_pnl
      daily_pnl = _filter_daily_pnl(
        daily_pnl, trades, min_trades=req.min_trades, min_pf=req.min_pf,
        min_wr=req.min_wr,
        max_pf=req.max_asset_pf, cap_pf=req.cap_asset_pf,
        include_side=True, run_batch_map=run_batch_map, date_to=req.date_to,
      )

    # Bybit size gate — drop assets whose scaled trades violate exchange min/max qty
    if req.bybit_size_gate:
      from backend.api.routes.analysis import _filter_bybit_size_gate
      daily_pnl = _filter_bybit_size_gate(
        daily_pnl, trades, req.bybit_size_gate_max_reject_pct,
        include_side=True, run_batch_map=run_batch_map,
      )

    _log_batch_counts(daily_pnl, ab_map_early, "After quality filters")
    _t2 = _time.perf_counter()
    logger.info("[2/6] Quality filters done → %d assets remain (%.1fs)",
                daily_pnl.shape[1], _t2 - _t0)
    _update_progress(task_id, 2, 6, "Quality filters", {"step_2_filters": round(_t2 - _t1, 2)})

    # Pre-filter: remove structurally unsuitable assets
    if req.prefilter_enabled and daily_pnl.shape[1] > 0:
      if req.prefilter_mode == "trend":
        from backend.analysis.seed_ranker import prefilter_for_trend
        survivors = prefilter_for_trend(daily_pnl, req.prefilter_hurst_min, req.prefilter_ter_percentile)
      else:
        from backend.analysis.seed_ranker import prefilter_for_meanrev
        survivors = prefilter_for_meanrev(daily_pnl, req.prefilter_hurst_max, req.prefilter_vr_max, req.prefilter_min_crossing_rate)
      _removed = [c for c in daily_pnl.columns if c not in survivors]
      daily_pnl = daily_pnl[[c for c in daily_pnl.columns if c in survivors]]
      logger.info("[prefilter] mode=%s: %d survivors, %d removed", req.prefilter_mode, len(survivors), len(_removed))

    if daily_pnl.shape[1] < 2:
      raise HTTPException(400, f"Need at least 2 symbols after filtering (got {daily_pnl.shape[1]}). "
                           f"batch_ids={req.batch_ids}, per_batch keys={list((req.per_batch or {}).keys())}")

    external_scores: dict = {}
    if req.benchmark_enabled:
      logger.info("[3/6] Computing benchmark robustness (%d assets)...", daily_pnl.shape[1])
      daily_pnl, external_scores = _cross_prepare_benchmark(
        trades, daily_pnl, req.benchmark_source, req.benchmark_split,
        req.benchmark_threshold, req.benchmark_elite_target, req.benchmark_exclude_fragile,
        run_batch_map=run_batch_map,
        consistency_blend=req.benchmark_consistency_blend,
        fragility_blend=req.benchmark_fragility_blend,
        wr_harmony_blend=req.benchmark_wr_harmony_blend,
        dsr_gate=req.benchmark_dsr_gate,
      )
      if "benchmark" not in req.seed_metrics:
        req.seed_metrics = [*req.seed_metrics, "benchmark"]
      logger.info("[3/6] Benchmark done → %d assets remain, %d scored (%.1fs)",
                  daily_pnl.shape[1], len(external_scores.get("benchmark", {})),
                  _time.perf_counter() - _t0)
    else:
      logger.info("[3/6] Benchmark: skipped")
    _t3 = _time.perf_counter()
    _update_progress(task_id, 3, 6, "Scoring & pool filtering", {"step_3_benchmark": round(_t3 - _t2, 2)})

    # Event-based L1 scoring (global or per-batch)
    if req.event_mode or (req.per_batch and any(
      cfg.get("event_mode", False) for cfg in req.per_batch.values()
    )):
      ev_asset_batch = _build_asset_batch_map(req.batch_ids, db, tagged=True) if req.batch_ids else {}
      ev_asset_batch.update(p_asset_batch)
      event_ext = _compute_event_external_scores(
        trades, req.event_mode, req.n_trades, req.staleness_days,
        req.per_batch, req.seed_metrics, ev_asset_batch, include_side=True,
        date_from=req.date_from, run_batch_map=run_batch_map,
      )
      if event_ext:
        external_scores = {**external_scores, **event_ext}

    # Recency-weighted seed scoring
    if req.recency_enabled:
      from backend.analysis.optimizer_seeds import compute_recency_seed_scores
      trade_df = pd.DataFrame([
        {"Asset": _trade_key(t, run_batch_map), "Exit Time": t.exit_time, "Net Profit": t.net_profit}
        for t in trades if t.exit_time is not None and t.net_profit is not None
      ])
      if not trade_df.empty:
        external_scores["recency"] = compute_recency_seed_scores(
          trade_df, mode=req.recency_mode,
          batch_size=req.recency_batch_size, boost_factor=req.recency_boost_factor,
        )
        if "recency" not in req.seed_metrics:
          req.seed_metrics = [*req.seed_metrics, "recency"]

    # Pool-per-batch: restrict each batch to top K assets by seed scores
    pool_k = req.pool_per_batch
    if pool_k <= 0:
      target = (req.n_longs + req.n_shorts) if req.split_mode else req.n_assets
      pool_k = max(target * 2, req.max_seeds)

    ab_map = _build_asset_batch_map(req.batch_ids, db, tagged=True) if req.batch_ids else {}
    ab_map.update(p_asset_batch)

    logger.info("[3/6] Computing seed scores (metrics=%s, %d assets)...",
                req.seed_metrics, daily_pnl.shape[1])
    from backend.analysis.optimizer_seeds import compute_seed_scores
    all_assets = daily_pnl.columns.tolist()
    _pool_metrics = req.seed_metrics or ["sortino"]
    pool_scores = compute_seed_scores(daily_pnl, all_assets, _pool_metrics, external_scores or None, metric_weights=req.seed_weights)
    _t4 = _time.perf_counter()
    logger.info("[3/6] Seed scores done (%.1fs)", _t4 - _t0)

    # ── Overfitting Guard (Approaches 1-5): adjust seed scores ──
    if getattr(req, "overfit_guard_enabled", False):
      from backend.analysis.overfit_guard import apply_overfit_guard
      # Build per-asset metrics from daily PnL
      _og_metrics: dict[str, dict] = {}
      for a in all_assets:
        col = daily_pnl[a]
        pos = col[col > 0]
        neg = col[col < 0]
        cum = col.cumsum()
        dd = cum - cum.cummax()
        n_tr = len([t for t in trades if _trade_key(t, run_batch_map) == a])
        _og_metrics[a] = {
          "pf": float(pos.sum() / max(abs(neg.sum()), 1.0)) if len(neg) > 0 else float(pos.sum()) if len(pos) > 0 else 0.0,
          "sortino": float(col.mean() / neg.std()) if len(neg) > 1 and neg.std() > 0 else 0.0,
          "win_rate": float(len(pos) / max(len(col[col != 0]), 1) * 100),
          "max_dd": float(dd.min()) if len(dd) > 0 else 0.0,
          "net_profit": float(col.sum()),
          "payoff": float(pos.mean() / abs(neg.mean())) if len(neg) > 0 and neg.mean() != 0 else 0.0,
          "sharpe": float(col.mean() / col.std() * (365 ** 0.5)) if col.std() > 0 else 0.0,
          "calmar": float(col.sum() / abs(dd.min())) if dd.min() < 0 else 0.0,
          "n_trades": max(n_tr, 1),
          "batch_id": ab_map.get(a, 0),
        }
      og_config = {
        "log_enabled": req.of_log_enabled,
        "log_caps": {"pf": req.of_log_cap_pf, "sortino": req.of_log_cap_sortino,
                     "payoff": req.of_log_cap_payoff, "wr": req.of_log_cap_wr,
                     "sharpe": req.of_log_cap_sharpe, "calmar": req.of_log_cap_calmar},
        "z_score_enabled": req.of_z_score_enabled,
        "z_threshold": req.of_z_threshold,
        "z_penalty_strength": req.of_z_penalty_strength,
        "bayes_enabled": req.of_bayes_enabled,
        "bayes_k": req.of_bayes_k,
        "sigmoid_enabled": req.of_sigmoid_enabled,
        "sig_caps": {"pf": req.of_sig_cap_pf, "sortino": req.of_sig_cap_sortino,
                     "payoff": req.of_sig_cap_payoff, "wr": req.of_sig_cap_wr,
                     "sharpe": req.of_sig_cap_sharpe, "calmar": req.of_sig_cap_calmar},
        "normal_range_enabled": req.of_normal_range_enabled,
        "nr_pct_low": req.of_nr_pct_low,
        "nr_pct_high": req.of_nr_pct_high,
        "nr_penalty_scale": req.of_nr_penalty_scale,
        "nr_bonus": req.of_nr_bonus,
      }
      pool_scores = apply_overfit_guard(pool_scores, _og_metrics, og_config)
      logger.info("  Overfitting guard applied: %d assets adjusted", len(pool_scores))

    # Portfolio exclusion filter — partial mode (after seed scoring, before pool filter)
    if excluded_names and req.exclude_portfolio_mode == "partial" and req.exclude_portfolio_keep_pct < 100:
      # For correlation/blend, compute a quick correlation on the current pool
      _excl_corr = None
      if req.exclude_portfolio_method in ("correlation", "blend"):
        _excl_corr = daily_pnl.corr()
      daily_pnl = _apply_exclude_portfolio_partial(
        daily_pnl, excluded_names, req.exclude_portfolio_keep_pct,
        req.exclude_portfolio_method, pool_scores, _excl_corr,
      )
      # Refresh all_assets after partial exclusion
      all_assets = daily_pnl.columns.tolist()

    keep_pool: set[str] = set()
    batch_groups: dict[int, list[str]] = {}
    for a in all_assets:
      bid = ab_map.get(a)
      if bid is not None:
        batch_groups.setdefault(bid, []).append(a)
      else:
        keep_pool.add(a)

    for bid, assets in batch_groups.items():
      ranked = sorted(assets, key=lambda a: pool_scores.get(a, float("inf")))
      keep_pool.update(ranked[:pool_k])

    pool_cols = [c for c in daily_pnl.columns if c in keep_pool]
    if len(pool_cols) >= 2:
      daily_pnl = daily_pnl[pool_cols]
    n_longs_avail = sum(1 for c in daily_pnl.columns if c.endswith("_Long"))
    n_shorts_avail = sum(1 for c in daily_pnl.columns if c.endswith("_Short"))

    _log_batch_counts(daily_pnl, ab_map, "After pool filter")
    _t5 = _time.perf_counter()
    logger.info("[3/6] Pool filter: %d/%d kept (%dL %dS), pool_per_batch=%d (%.1fs)",
                len(pool_cols), len(all_assets), n_longs_avail, n_shorts_avail,
                pool_k, _t5 - _t0)

    # Validate request doesn't ask for more than available
    if req.split_mode:
      if n_longs_avail == 0 or n_shorts_avail == 0:
        raise HTTPException(400, f"L/S Split needs both Long and Short assets. "
                             f"Found {n_longs_avail} Long, {n_shorts_avail} Short after filtering.")
      if req.n_longs > n_longs_avail:
        raise HTTPException(400, f"Requested {req.n_longs} longs but only {n_longs_avail} available after filtering")
      if req.n_shorts > n_shorts_avail:
        raise HTTPException(400, f"Requested {req.n_shorts} shorts but only {n_shorts_avail} available after filtering")
    elif req.n_assets > daily_pnl.shape[1]:
      raise HTTPException(400, f"Requested {req.n_assets} assets but only {daily_pnl.shape[1]} available after filtering")

    logger.info("[4/6] Computing %s correlation matrix (%d assets)...",
                req.corr_method, daily_pnl.shape[1])
    corr_key = _make_corr_key(req.corr_method, daily_pnl.columns.tolist())
    cached_corr = get_cached_corr(date_key, corr_key)
    if cached_corr is not None:
      corr = cached_corr
      logger.info("[4/6] Correlation matrix from CACHE")
    else:
      corr = get_correlation_matrix(daily_pnl, req.corr_method)
      put_cached_corr(date_key, corr_key, corr)
    _t6 = _time.perf_counter()
    logger.info("[4/6] Correlation matrix done (%.1fs)", _t6 - _t0)
    _update_progress(task_id, 4, 6, "Correlation matrix", {"step_4_corr": round(_t6 - _t5, 2)})

    # Correlation jitter — perturb matrix for candidate diversity
    if getattr(req, "corr_jitter_enabled", False):
      from backend.analysis.corr_jitter import perturb_correlation_matrix
      corr = perturb_correlation_matrix(corr, req.corr_jitter_temperature)
      logger.info("  Correlation jitter applied (temp=%.3f)", req.corr_jitter_temperature)

    tc_active = req.tc_low_threshold > 0 or req.tc_high_threshold > 0

    _algo_labels = {
      "greedy": "iterative (objective-driven)", "hrp": "HRP", "pca": "PCA",
      "quality_first": "quality-first", "tournament": "tournament",
      "marginal_sharpe": "marginal-sharpe", "ensemble": "ensemble",
      "walk_forward": "walk-forward validated",
    }
    logger.info("[5/6] Running %s optimizer (n=%s, seeds=%d, stability=%s)...",
                _algo_labels.get(req.div_algo, req.div_algo), req.n_assets, req.max_seeds, req.prioritize_stability)
    if req.split_mode:
      tc_kwargs = {}
      if tc_active:
        # Build trade counts with tagged keys
        tagged_tc: dict[str, int] = {}
        for t in trades:
          k = _trade_key(t, run_batch_map)
          tagged_tc[k] = tagged_tc.get(k, 0) + 1
        tc_dict = {
          "low_threshold": req.tc_low_threshold, "max_low": req.tc_max_low,
          "high_threshold": req.tc_high_threshold, "min_high": req.tc_min_high,
        }
        tc_kwargs = {"trade_counts": tagged_tc, "tc_longs": tc_dict, "tc_shorts": tc_dict}
      raw = diversify_split_portfolio(
        req.div_algo, daily_pnl, corr,
        n_longs=min(req.n_longs, max(n_longs_avail, 1)),
        n_shorts=min(req.n_shorts, max(n_shorts_avail, 1)),
        within_objective=req.within_objective,
        combined_objective=req.combined_objective,
        n_portfolios=req.n_portfolios,
        seed_metrics=req.seed_metrics, max_seeds=req.max_seeds,
        prioritize_stability=req.prioritize_stability,
        external_scores=external_scores or None,
        streamlit_logic=req.streamlit_logic,
        max_overlap=req.max_overlap,
        expand_pool=req.expand_greedy_pool,
        seed_prior_weight=req.seed_prior_weight,
        seed_weights=req.seed_weights,
        max_dd_pct=req.max_allowed_drawdown_pct,
        precomputed_seeds=None if req.seed_blind_selection else pool_scores,
        fast_mode=(req.optimizer_engine == "fast"),
        engine=req.optimizer_engine,
        genetic_generations=req.genetic_generations, genetic_population=req.genetic_population,
        genetic_mutation_rate=req.genetic_mutation_rate, genetic_crossover_rate=req.genetic_crossover_rate,
        genetic_elite_pct=req.genetic_elite_pct,
        **tc_kwargs,
      )
    else:
      n_total = min(req.n_assets, daily_pnl.shape[1])
      tc_kwargs = {}
      if tc_active:
        tagged_tc = {}
        for t in trades:
          k = _trade_key(t, run_batch_map)
          tagged_tc[k] = tagged_tc.get(k, 0) + 1
        tc_dict = {
          "low_threshold": req.tc_low_threshold, "max_low": req.tc_max_low,
          "high_threshold": req.tc_high_threshold, "min_high": req.tc_min_high,
        }
        tc_kwargs = {"trade_counts": tagged_tc, "tc_constraints": tc_dict}
      raw = diversify_portfolio(
        req.div_algo, daily_pnl, corr, n_total, req.objective,
        n_portfolios=req.n_portfolios,
        seed_metrics=req.seed_metrics, max_seeds=req.max_seeds,
        prioritize_stability=req.prioritize_stability,
        external_scores=external_scores or None,
        streamlit_logic=req.streamlit_logic,
        max_overlap=req.max_overlap,
        expand_pool=req.expand_greedy_pool,
        seed_prior_weight=req.seed_prior_weight,
        seed_weights=req.seed_weights,
        max_dd_pct=req.max_allowed_drawdown_pct,
        precomputed_seeds=None if req.seed_blind_selection else pool_scores,
        fast_mode=(req.optimizer_engine == "fast"),
        engine=req.optimizer_engine,
        genetic_generations=req.genetic_generations, genetic_population=req.genetic_population,
        genetic_mutation_rate=req.genetic_mutation_rate, genetic_crossover_rate=req.genetic_crossover_rate,
        genetic_elite_pct=req.genetic_elite_pct,
        **tc_kwargs,
      )

    # Post-construction refinement pass
    if getattr(req, "refinement_enabled", False) and raw:
      from backend.analysis.optimizer_refinement import refine_candidates
      raw = refine_candidates(
        raw, daily_pnl, daily_pnl.columns.tolist(),
        refinement_mode=req.refinement_mode,
        refinement_metric=req.refinement_metric,
        objective=req.objective if hasattr(req, "objective") else "stability",
        target_size=req.n_assets if hasattr(req, "n_assets") else 10,
      )
      logger.info("  Refinement pass complete (%s)", req.refinement_mode)

    # ── Overfitting Guard Approach 6: Walk-Forward Re-rank ──
    if getattr(req, "overfit_guard_enabled", False) and getattr(req, "of_wf_rerank_enabled", False) and raw:
      from backend.analysis.overfit_guard import walk_forward_rerank
      raw = walk_forward_rerank(
        raw, daily_pnl,
        split_pct=req.of_wf_split_pct / 100.0,
        metric=req.of_wf_validation_metric,
      )
      logger.info("  WF re-rank applied: candidates reordered by validation %s", req.of_wf_validation_metric)

    _t7 = _time.perf_counter()
    logger.info("[5/6] Optimizer done → %d candidates (%.1fs)",
                len(raw), _t7 - _t0)
    _update_progress(task_id, 5, 6, "Optimizer complete", {"step_5_optimize": round(_t7 - _t6, 2)})

    logger.info("[6/6] Building final metrics...")
    # Build strategy attribution map (tagged keys)
    asset_strat = _asset_strategy_lookup(req.batch_ids, db, tagged=True) if req.batch_ids else {}
    asset_strat.update(p_asset_strat)
    strat_map = _strategy_map(req.batch_ids, db) if req.batch_ids else {}
    strat_map.update(p_strat_map)

    # Build trade-level DataFrame with tagged keys for accurate metrics
    # Include position_id so _portfolio_metrics can merge multi-exit fills
    _tdf = pd.DataFrame([
      {"Asset": _trade_key(t, run_batch_map), "Net Profit": t.net_profit,
       "position_id": t.position_id or f"_solo_{t.id}"}
      for t in trades if t.net_profit is not None
    ])

    # Compute per-asset marginal contribution (blueprint) for each candidate
    from backend.analysis.asset_contribution import compute_asset_marginal_contribution
    candidates = []
    for c in raw:
      assets = c.get("assets", [])
      metrics = _portfolio_metrics(daily_pnl, assets, _tdf)
      # Strip batch tags for display-friendly names in attribution
      attribution = {a: asset_strat.get(a, "Unknown") for a in assets}
      # Return both tagged (for OOS) and display (for UI) asset names
      display_assets = [_strip_batch_tag(a) for a in assets]
      # Blueprint: per-asset leave-one-out contribution
      blueprint = []
      if len(assets) >= 2:
        try:
          blueprint = compute_asset_marginal_contribution(assets, daily_pnl)
        except Exception as _exc:
          logger.debug("swallowed: %s", _exc)
      candidates.append({
        "longs": c.get("longs", [a for a in assets if a.endswith("_Long")]),
        "shorts": c.get("shorts", [a for a in assets if a.endswith("_Short")]),
        "assets": assets,
        "display_assets": display_assets,
        "metrics": metrics,
        "score": round(float(c.get("score", 0)), 4),
        "attribution": attribution,
        "blueprint": blueprint,
      })

    # Portfolio-level quality gates (hard reject)
    candidates = _apply_portfolio_gates(
      candidates, _tdf,
      min_pf=req.portfolio_min_pf,
      min_wr=req.portfolio_min_wr,
      cap_asset_pf=req.max_asset_pf if req.cap_asset_pf else 0,
    )

    # AC post-filter: flag mismatched assets per candidate
    if req.ac_postfilter:
      from backend.analysis.seed_ranker import postfilter_ac
      for c in candidates:
        ac_results = postfilter_ac(daily_pnl, c["assets"], strategy_type=req.ac_postfilter_type, lag=req.ac_lag)
        c["ac_flags"] = [a for a, info in ac_results.items() if info["flagged"]]
        c["ac_scores"] = {a: info["ac"] for a, info in ac_results.items()}

    # Per-batch availability after filtering
    batch_avail: dict[str, int] = {}
    for bid, name in strat_map.items():
      count = sum(1 for c in daily_pnl.columns if ab_map.get(c) == bid)
      batch_avail[name] = batch_avail.get(name, 0) + count

    _t8 = _time.perf_counter()
    logger.info("═══ CROSS-STRATEGY BUILD DONE ═══  %d candidates, %d assets, %.1fs total",
                len(candidates), daily_pnl.shape[1], _t8 - _t0)
    _update_progress(task_id, 6, 6, "Complete", {
      "step_6_metrics": round(_t8 - _t7, 2), "total": round(_t8 - _t0, 2),
    })

    # Include timing breakdown in result for persistence
    with _optimize_lock:
      prog = _optimize_progress.get(task_id)
      step_timing = prog.get("timing", {}) if prog else {}

    out = sanitize_for_json({
      "candidates": candidates,
      "total_symbols": daily_pnl.shape[1],
      "n_longs_available": n_longs_avail,
      "n_shorts_available": n_shorts_avail,
      "strategies": strat_map,
      "batch_asset_counts": batch_avail,
      "timing": step_timing,
    })
    _auto_save_run("build", out, req.model_dump(mode="json"))
    return out
  except HTTPException:
    raise
  except Exception as exc:
    from backend.api.routes.optimization_errors import wrap_optimization_error
    raise wrap_optimization_error("Cross Strategy Build", exc) from exc


@router.post("/cross-strategy-build-manual")
def cross_strategy_build_manual(request: Request, req: CrossBuildManualRequest, background_tasks: BackgroundTasks):
  """Manual mode — runs in background, returns task_id immediately."""
  rate_limit(request, max_requests=3, window_seconds=60, key="optimizer")
  total_sources = len(req.batch_ids) + len(req.portfolio_ids)
  if total_sources < 2:
    raise HTTPException(400, "Need at least 2 data sources (batches or portfolios)")

  from backend.db.database import SessionLocal
  task_id = uuid.uuid4().hex[:12]
  with _optimize_lock:
    _optimize_progress[task_id] = {"status": "running", "result": None, "error": None}
  background_tasks.add_task(
    _run_optimize_task, task_id, req, SessionLocal,
    _cross_strategy_build_manual_inner, "Cross-Strategy Manual",
  )
  return {"task_id": task_id, "status": "running"}


def _pool_mode_build(
  req: "CrossBuildManualRequest",
  full_daily_pnl: pd.DataFrame,
  asset_batch: dict[str, int],
  asset_strat: dict[str, str],
  strat_map: dict[int, str],
  all_trades: list,
  all_source_ids: list[int],
  external_scores: dict,
  run_batch_map: dict | None,
  task_id: str,
  _t3: float,
) -> dict:
  """Pool mode: seed-rank L1 → optimizer L2 → combiner L3."""
  import time as _time
  from backend.analysis.optimizer_seeds import compute_seed_scores
  from backend.analysis.asset_contribution import compute_asset_marginal_contribution

  # ── L1: Seed ranking per batch — keep top pool_per_batch ──
  all_seed_scores: dict[str, float] = {}  # accumulated for dedup tiebreaker
  pool_assets: dict[int, list[str]] = {}
  for bid in all_source_ids:
    batch_cols = [c for c in full_daily_pnl.columns if asset_batch.get(c) == bid]
    if len(batch_cols) < 1:
      continue
    scores = compute_seed_scores(
      full_daily_pnl, batch_cols, req.seed_metrics,
      external_scores or None, metric_weights=req.seed_weights,
    )
    if req.l2_dedup_tiebreaker == "seed_rank":
      all_seed_scores.update(scores)
    ranked = sorted(batch_cols, key=lambda a: scores.get(a, float("inf")))
    pool_assets[bid] = ranked[: req.pool_per_batch]

  _t4 = _time.perf_counter()
  total_pool = sum(len(v) for v in pool_assets.values())
  logger.info("[Pool L1] Kept %d assets across %d batches (%.1fs)",
              total_pool, len(pool_assets), _t4 - _t3)

  # ── L2: Optimizer on grouped pools — produce Z variants per group ──
  _update_progress(task_id, 4, 6, "L2: Cross-batch optimization")
  l2_obj = req.obj_layer2
  if l2_obj == "min_drawdown":
    l2_obj = "min_max_drawdown"

  l2_variants: dict[str, list[str]] = {}
  l2_pnls: dict[str, pd.Series] = {}

  grouped_bids: set[int] = set()
  if req.l2_groups:
    for gi, group_bids in enumerate(req.l2_groups):
      grouped_bids.update(group_bids)
      # Merge L1 pools from all batches in this group (sorted for determinism)
      group_cols: list[str] = []
      for bid in sorted(group_bids):
        group_cols.extend(pool_assets.get(bid, []))
      group_cols.sort()
      if len(group_cols) < 2:
        continue
      group_pnl = full_daily_pnl[group_cols]
      group_corr = get_correlation_matrix(group_pnl, req.corr_method)
      n_pick = min(req.l2_n_assets, len(group_cols))
      results = diversify_portfolio(
        req.div_algo, group_pnl, group_corr,
        n_assets=n_pick, objective=l2_obj,
        n_portfolios=req.l2_n_variants,
        seed_metrics=req.seed_metrics, max_seeds=req.max_seeds,
        prioritize_stability=req.prioritize_stability,
        max_overlap=req.l2_max_overlap if req.l2_max_overlap < 1.0 else None,
        expand_pool=req.expand_greedy_pool,
        seed_prior_weight=req.seed_prior_weight,
        seed_weights=req.seed_weights,
        max_dd_pct=req.max_allowed_drawdown_pct,
        engine=req.optimizer_engine,
        genetic_generations=req.genetic_generations, genetic_population=req.genetic_population,
        genetic_mutation_rate=req.genetic_mutation_rate, genetic_crossover_rate=req.genetic_crossover_rate,
        genetic_elite_pct=req.genetic_elite_pct,
      )
      for vi, r in enumerate(results):
        vn = f"Group{gi + 1}_v{vi + 1}"
        l2_variants[vn] = r["assets"]
        l2_pnls[vn] = group_pnl[r["assets"]].sum(axis=1)

  # Ungrouped batches: pass their L1 pool as a single variant each
  for bid in set(all_source_ids) - grouped_bids:
    assets = pool_assets.get(bid, [])
    if not assets:
      continue
    sname = strat_map.get(bid, f"Batch_{bid}")
    vn = f"{sname}_pool"
    l2_variants[vn] = assets
    l2_pnls[vn] = full_daily_pnl[assets].sum(axis=1)

  _t5 = _time.perf_counter()
  logger.info("[Pool L2] %d variants produced (%.1fs)", len(l2_pnls), _t5 - _t4)

  if len(l2_pnls) < 2:
    raise HTTPException(400, "Need at least 2 L2 variants after pool optimization")

  # ── L3: Mix and match L2 variants → final portfolio ──
  # n_assets = number of L2 groups (pick 1 variant per source)
  # n_portfolios = L3 Slots (how many different combinations to try)
  # Then trim to req.n_portfolios (Results)
  _update_progress(task_id, 5, 6, "L3: Final assembly")
  l3_obj = req.obj_layer3
  if l3_obj == "min_drawdown":
    l3_obj = "min_max_drawdown"
  n_l2_sources = len(req.l2_groups) + len(set(all_source_ids) - grouped_bids)
  n_assets_l3 = min(n_l2_sources, len(l2_pnls))
  n_combos = min(req.n_final_strats or 50, max(len(l2_pnls), 2))

  vdf = pd.DataFrame(l2_pnls).fillna(0)
  vcorr = get_correlation_matrix(vdf, req.corr_method)
  global_res = diversify_portfolio(
    req.div_algo, vdf, vcorr,
    n_assets=n_assets_l3, objective=l3_obj,
    n_portfolios=n_combos,
    seed_metrics=req.seed_metrics, max_seeds=req.max_seeds,
    prioritize_stability=req.prioritize_stability,
    max_overlap=req.l3_max_overlap if req.l3_max_overlap < 1.0 else None,
    expand_pool=req.expand_greedy_pool,
    seed_prior_weight=req.seed_prior_weight,
    seed_weights=req.seed_weights,
    engine=req.optimizer_engine,
    genetic_generations=req.genetic_generations, genetic_population=req.genetic_population,
    genetic_mutation_rate=req.genetic_mutation_rate, genetic_crossover_rate=req.genetic_crossover_rate,
    genetic_elite_pct=req.genetic_elite_pct,
  )

  # Build trade DataFrame for metrics
  _tdf = pd.DataFrame([
    {"Asset": _trade_key(t, run_batch_map), "Net Profit": t.net_profit,
     "position_id": t.position_id or f"_solo_{t.id}"}
    for t in all_trades if t.net_profit is not None
  ])

  # Build candidates — expand selected variants back to individual assets
  candidates = []
  for r in global_res:
    sel_variants = r["assets"]
    assets = list(dict.fromkeys(
      a for vn in sel_variants for a in l2_variants.get(vn, [])
    ))
    assets.sort()
    # Cross-group symbol dedup
    if req.l2_symbol_dedup != "keep_all":
      assets = _dedup_by_symbol(
        assets, full_daily_pnl, req.l2_symbol_dedup,
        req.l2_dedup_tiebreaker, all_seed_scores,
      )
    metrics = _portfolio_metrics(full_daily_pnl, assets, _tdf)
    blueprint = []
    if len(assets) >= 2:
      try:
        blueprint = compute_asset_marginal_contribution(assets, full_daily_pnl)
      except Exception as _exc:
        logger.debug("swallowed: %s", _exc)
    candidates.append({
      "longs": [a for a in assets if a.endswith("_Long")],
      "shorts": [a for a in assets if a.endswith("_Short")],
      "assets": assets,
      "display_assets": [_strip_batch_tag(a) for a in assets],
      "metrics": metrics,
      "score": round(float(r.get("score", 0.0)), 4),
      "attribution": {a: asset_strat.get(a, "Unknown") for a in assets},
      "selected_variants": sel_variants,
      "blueprint": blueprint,
    })

  # Trim to requested Results count (L3 Slots produced more for diversity)
  candidates = candidates[: req.n_portfolios]

  # Portfolio-level quality gates
  candidates = _apply_portfolio_gates(
    candidates, _tdf,
    min_pf=req.portfolio_min_pf,
    min_wr=req.portfolio_min_wr,
    cap_asset_pf=req.max_asset_pf if req.cap_asset_pf else 0,
  )

  # AC post-filter
  if req.ac_postfilter:
    from backend.analysis.seed_ranker import postfilter_ac
    for c in candidates:
      ac_results = postfilter_ac(full_daily_pnl, c["assets"], strategy_type=req.ac_postfilter_type, lag=req.ac_lag)
      c["ac_flags"] = [a for a, info in ac_results.items() if info["flagged"]]
      c["ac_scores"] = {a: info["ac"] for a, info in ac_results.items()}

  _t6 = _time.perf_counter()
  _update_progress(task_id, 6, 6, "Complete", {"total": round(_t6 - _t3, 2)})

  with _optimize_lock:
    prog = _optimize_progress.get(task_id)
    step_timing = prog.get("timing", {}) if prog else {}

  out = sanitize_for_json({
    "candidates": candidates,
    "total_symbols": full_daily_pnl.shape[1],
    "total_variants": len(l2_pnls),
    "strategies": strat_map,
    "timing": step_timing,
    "pool_mode": True,
  })
  _auto_save_run("build", out, req.model_dump(mode="json"))
  return out


def _cross_strategy_build_manual_inner(req: CrossBuildManualRequest, db: Session, task_id: str = ""):
  """Inner logic for cross-strategy manual build (runs in background thread)."""
  try:
    import time as _time
    from backend.analysis.optimization_cache import (
      get_cached_l0_incremental, put_cached_l0, update_cached_l0,
      get_cached_l0, _make_date_key, clone_cached_trades,
    )
    _t0 = _time.perf_counter()
    date_key = _make_date_key(req.date_from, req.date_to, req.clip_is)
    _update_progress(task_id, 0, 6, "Checking cache...")

    # [1/6] Build batch-tagged daily PnL — check cache first
    cached_entry, new_batches, new_portfolios = get_cached_l0_incremental(
      req.batch_ids, req.portfolio_ids or [],
      req.date_from, req.date_to, req.clip_is,
    )

    run_batch_map: dict[int, int] = {}
    p_asset_batch: dict[str, int] = {}
    p_strat_map: dict[int, str] = {}
    p_asset_strat: dict[str, str] = {}

    if cached_entry is not None and not new_batches and not new_portfolios:
      # Full cache hit — restore portfolio metadata
      full_daily_pnl = cached_entry.daily_pnl.copy()
      all_trades = clone_cached_trades(cached_entry.trades)
      run_batch_map = cached_entry.run_batch_map.copy()
      p_asset_batch = cached_entry.p_asset_batch.copy()
      p_strat_map = cached_entry.p_strat_map.copy()
      p_asset_strat = cached_entry.p_asset_strat.copy()
      logger.info("[1/6] CACHE HIT — loaded %d assets from cache", full_daily_pnl.shape[1])
      # Filter to requested batches only (prevent cache superset leak)
      full_daily_pnl, all_trades = _filter_to_requested(
        full_daily_pnl, all_trades, run_batch_map,
        req.batch_ids, req.portfolio_ids or [],
      )
    elif cached_entry is not None and (new_batches or new_portfolios):
      new_daily_pnl = pd.DataFrame()
      new_trades: list = []
      new_rbm: dict[int, int] = {}
      new_pab: dict[str, int] = {}
      new_psm: dict[int, str] = {}
      new_pas: dict[str, str] = {}
      if new_batches:
        new_daily_pnl, new_trades, new_rbm = _build_cross_daily_pnl(
          new_batches, db, req.date_from, req.date_to, clip_boundary=req.clip_is,
        )
      if new_portfolios:
        pt, p_ab, p_sm, p_as, p_rbm = _load_portfolio_trades(
          new_portfolios, db, req.date_from, req.date_to, tagged=True, clip_boundary=req.clip_is,
        )
        new_pab.update(p_ab); new_psm.update(p_sm); new_pas.update(p_as)
        new_trades.extend(pt); new_rbm.update(p_rbm)
        if pt:
          p_pnl = build_daily_pnl_matrix(pt, include_side=True)
          if not p_pnl.empty:
            tagged_cols = {}
            for col in p_pnl.columns:
              for pkey, pbid in p_ab.items():
                if _strip_batch_tag(pkey) == col:
                  tagged_cols[col] = pkey
                  break
              else:
                tagged_cols[col] = col
            p_pnl.columns = [tagged_cols.get(c, c) for c in p_pnl.columns]
            new_daily_pnl = pd.concat([new_daily_pnl, p_pnl], axis=1).fillna(0) if not new_daily_pnl.empty else p_pnl
      update_cached_l0(date_key, cached_entry, new_batches, new_portfolios,
                       new_daily_pnl, new_trades, new_rbm,
                       new_pab, new_psm, new_pas)
      updated = get_cached_l0(req.batch_ids, req.portfolio_ids or [],
                              req.date_from, req.date_to, req.clip_is)
      full_daily_pnl = updated.daily_pnl.copy()
      all_trades = list(updated.trades)
      run_batch_map = updated.run_batch_map.copy()
      p_asset_batch = updated.p_asset_batch.copy()
      p_strat_map = updated.p_strat_map.copy()
      p_asset_strat = updated.p_asset_strat.copy()
      logger.info("[1/6] INCREMENTAL LOAD — total %d assets", full_daily_pnl.shape[1])
      # Filter to requested batches only (prevent cache superset leak)
      full_daily_pnl, all_trades = _filter_to_requested(
        full_daily_pnl, all_trades, run_batch_map,
        req.batch_ids, req.portfolio_ids or [],
      )
    else:
      if req.batch_ids:
        full_daily_pnl, all_trades, run_batch_map = _build_cross_daily_pnl(
          req.batch_ids, db, req.date_from, req.date_to, clip_boundary=req.clip_is,
        )
      else:
        all_trades = []
        full_daily_pnl = pd.DataFrame()
      if req.portfolio_ids:
        pt, p_asset_batch, p_strat_map, p_asset_strat, p_rbm = _load_portfolio_trades(
          req.portfolio_ids, db, req.date_from, req.date_to, tagged=True, clip_boundary=req.clip_is,
        )
        all_trades.extend(pt); run_batch_map.update(p_rbm)
        if pt:
          p_pnl = build_daily_pnl_matrix(pt, include_side=True)
          if not p_pnl.empty:
            tagged_cols = {}
            for col in p_pnl.columns:
              for pkey, pbid in p_asset_batch.items():
                if _strip_batch_tag(pkey) == col:
                  tagged_cols[col] = pkey
                  break
              else:
                tagged_cols[col] = col
            p_pnl.columns = [tagged_cols.get(c, c) for c in p_pnl.columns]
            if full_daily_pnl.empty:
              full_daily_pnl = p_pnl
            else:
              full_daily_pnl = pd.concat([full_daily_pnl, p_pnl], axis=1).fillna(0)
      if all_trades:
        put_cached_l0(req.batch_ids, req.portfolio_ids or [],
                      req.date_from, req.date_to, req.clip_is,
                      full_daily_pnl, all_trades, run_batch_map,
                      p_asset_batch, p_strat_map, p_asset_strat)

    if not all_trades:
      raise HTTPException(404, "No trades found across selected sources")

    _t1 = _time.perf_counter()
    logger.info("[1/6] Manual build: loaded %d trades, %d assets (%.1fs)",
                len(all_trades), full_daily_pnl.shape[1], _t1 - _t0)
    _update_progress(task_id, 1, 6, "Trades loaded", {"step_1_load": round(_t1 - _t0, 2)})

    # No daily_pnl truncation — trades filtered by entry_time may exit after cutoff

    # ── Session Filter — inline trade-time filtering ──
    if req.session_filter_enabled and all_trades:
      from backend.analysis.session_mask_filter import apply_session_mask as _sf_apply, build_fixed_mask
      _sf_mask_data = None
      if req.session_filter_mode == "fixed" and (req.session_filter_exclude_days or req.session_filter_exclude_hours):
        _sf_mask_data = build_fixed_mask(req.session_filter_exclude_days, req.session_filter_exclude_hours)
      elif req.session_filter_mode == "auto":
        from backend.analysis.session_auto_mask import compute_auto_mask
        _auto = compute_auto_mask(all_trades, req.session_filter_auto_min_trades, req.session_filter_auto_include_insufficient)
        _sf_mask_data = _auto["mask_data"]
        logger.info("Cross manual session filter (auto): kept hours=%s, kept days=%s", _auto["kept_hours"], _auto["kept_days"])
      elif req.session_filter_mode == "saved_mask" and req.session_mask_id:
        from backend.api.routes.analysis import _apply_session_mask
        n_fp = sum(a.get("n_longs", 0) + a.get("n_shorts", 0) for a in req.allocations.values()) or 10
        all_trades = _apply_session_mask(all_trades, req.session_mask_id, db, n_free_params=n_fp)
        _sf_mask_data = None  # already applied
      if _sf_mask_data:
        _before = len(all_trades)
        all_trades = _sf_apply(all_trades, _sf_mask_data)
        logger.info("Cross manual session filter (%s): %d → %d trades", req.session_filter_mode, _before, len(all_trades))
      full_daily_pnl = _rebuild_daily_pnl_from_trades(all_trades, run_batch_map)

    # Apply risk profile scaling FIRST (scales quantity for correct notional in stress test)
    if req.risk_profile_mode != "fixed" or req.risk_profile_value > 0 or req.risk_profile_sizing_master == "per_batch":
      from backend.api.routes.analysis import _apply_risk_scaling
      all_trades = _apply_risk_scaling(all_trades, req, db, include_side=True, run_batch_map=run_batch_map)
      full_daily_pnl = _rebuild_daily_pnl_from_trades(all_trades, run_batch_map)

    # Apply extra cost stress test AFTER scaling (uses scaled notional)
    _pbc = _parse_per_batch_costs(req.per_batch_costs) if req.per_batch_costs else None
    if req.extra_slippage_pct > 0 or req.extra_commission_pct > 0 or req.funding_daily_pct > 0 or req.spread_pct > 0 or _pbc:
      from backend.api.routes.analysis import _apply_stress_slippage
      all_trades = _apply_stress_slippage(all_trades, req.extra_slippage_pct, db,
                                           extra_commission_pct=req.extra_commission_pct,
                                           funding_daily_pct=req.funding_daily_pct,
                                           spread_pct=req.spread_pct,
                                           per_batch_costs=_pbc,
                                           run_batch_map=run_batch_map)
      full_daily_pnl = _rebuild_daily_pnl_from_trades(all_trades, run_batch_map)

    # Category filter
    if req.category_filter:
      from backend.api.routes.analysis import _filter_by_category
      clean_pnl = full_daily_pnl.copy()
      clean_pnl.columns = [_strip_batch_tag(c) for c in full_daily_pnl.columns]
      clean_pnl = _filter_by_category(clean_pnl, req.category_filter, db)
      keep_clean = set(clean_pnl.columns)
      keep_cols = [c for c in full_daily_pnl.columns if _strip_batch_tag(c) in keep_clean]
      full_daily_pnl = full_daily_pnl[keep_cols]

    # Portfolio exclusion filter — full mode
    excluded_names: set[str] = set()
    if req.exclude_portfolio_ids:
      excluded_names = _get_excluded_asset_names(req.exclude_portfolio_ids, db)
      if excluded_names and req.exclude_portfolio_mode == "full":
        full_daily_pnl = _apply_exclude_portfolio_full(full_daily_pnl, excluded_names)

    asset_batch = _build_asset_batch_map(req.batch_ids, db, tagged=True) if req.batch_ids else {}
    asset_batch.update(p_asset_batch)
    strat_map = _strategy_map(req.batch_ids, db) if req.batch_ids else {}
    strat_map.update(p_strat_map)
    asset_strat = _asset_strategy_lookup(req.batch_ids, db, tagged=True) if req.batch_ids else {}
    asset_strat.update(p_asset_strat)

    # Apply per-batch quality filters
    if req.per_batch:
      full_daily_pnl = _apply_per_batch_filters(
        all_trades, full_daily_pnl, req.per_batch, asset_batch, run_batch_map,
        date_to=req.date_to,
        default_min_trades=req.min_trades, default_min_pf=req.min_pf,
      )
    else:
      from backend.api.routes.analysis import _filter_daily_pnl
      full_daily_pnl = _filter_daily_pnl(
        full_daily_pnl, all_trades, min_trades=req.min_trades, min_pf=req.min_pf,
        max_pf=req.max_asset_pf, cap_pf=req.cap_asset_pf,
        include_side=True, run_batch_map=run_batch_map, date_to=req.date_to,
      )

    # Bybit size gate — drop assets whose scaled trades violate exchange min/max qty
    if req.bybit_size_gate:
      from backend.api.routes.analysis import _filter_bybit_size_gate
      full_daily_pnl = _filter_bybit_size_gate(
        full_daily_pnl, all_trades, req.bybit_size_gate_max_reject_pct,
        include_side=True, run_batch_map=run_batch_map,
      )

    _t2 = _time.perf_counter()
    _update_progress(task_id, 2, 6, "Quality filters", {"step_2_filters": round(_t2 - _t1, 2)})

    # Pre-filter: remove structurally unsuitable assets
    if req.prefilter_enabled and full_daily_pnl.shape[1] > 0:
      if req.prefilter_mode == "trend":
        from backend.analysis.seed_ranker import prefilter_for_trend
        survivors = prefilter_for_trend(full_daily_pnl, req.prefilter_hurst_min, req.prefilter_ter_percentile)
      else:
        from backend.analysis.seed_ranker import prefilter_for_meanrev
        survivors = prefilter_for_meanrev(full_daily_pnl, req.prefilter_hurst_max, req.prefilter_vr_max, req.prefilter_min_crossing_rate)
      full_daily_pnl = full_daily_pnl[[c for c in full_daily_pnl.columns if c in survivors]]

    external_scores: dict = {}
    if req.benchmark_enabled:
      full_daily_pnl, external_scores = _cross_prepare_benchmark(
        all_trades, full_daily_pnl, req.benchmark_source, req.benchmark_split,
        req.benchmark_threshold, req.benchmark_elite_target, req.benchmark_exclude_fragile,
        run_batch_map=run_batch_map,
        consistency_blend=req.benchmark_consistency_blend,
        fragility_blend=req.benchmark_fragility_blend,
        wr_harmony_blend=req.benchmark_wr_harmony_blend,
        dsr_gate=req.benchmark_dsr_gate,
      )
      if "benchmark" not in req.seed_metrics:
        req.seed_metrics = [*req.seed_metrics, "benchmark"]

    # Event-based L1 scoring
    if req.event_mode or (req.per_batch and any(
      cfg.get("event_mode", False) for cfg in req.per_batch.values()
    )):
      event_ext = _compute_event_external_scores(
        all_trades, req.event_mode, req.n_trades, req.staleness_days,
        req.per_batch, req.seed_metrics, asset_batch, include_side=True,
        date_from=req.date_from, run_batch_map=run_batch_map,
      )
      if event_ext:
        external_scores = {**external_scores, **event_ext}

    # Recency-weighted seed scoring
    if req.recency_enabled:
      from backend.analysis.optimizer_seeds import compute_recency_seed_scores
      trade_df = pd.DataFrame([
        {"Asset": _trade_key(t, run_batch_map), "Exit Time": t.exit_time, "Net Profit": t.net_profit}
        for t in all_trades if t.exit_time is not None and t.net_profit is not None
      ])
      if not trade_df.empty:
        external_scores["recency"] = compute_recency_seed_scores(
          trade_df, mode=req.recency_mode,
          batch_size=req.recency_batch_size, boost_factor=req.recency_boost_factor,
        )
        if "recency" not in req.seed_metrics:
          req.seed_metrics = [*req.seed_metrics, "recency"]

    # Portfolio exclusion filter — partial mode (after external scoring, before optimizer)
    if excluded_names and req.exclude_portfolio_mode == "partial" and req.exclude_portfolio_keep_pct < 100:
      from backend.analysis.optimizer_seeds import compute_seed_scores as _excl_seed_scores
      _excl_pool = _excl_seed_scores(full_daily_pnl, full_daily_pnl.columns.tolist(),
                                      req.seed_metrics or ["sortino"], external_scores or None,
                                      metric_weights=req.seed_weights)
      _excl_corr = full_daily_pnl.corr() if req.exclude_portfolio_method in ("correlation", "blend") else None
      full_daily_pnl = _apply_exclude_portfolio_partial(
        full_daily_pnl, excluded_names, req.exclude_portfolio_keep_pct,
        req.exclude_portfolio_method, _excl_pool, _excl_corr,
      )

    _t3 = _time.perf_counter()
    _update_progress(task_id, 3, 6, "Scoring & pool filtering", {"step_3_scoring": round(_t3 - _t2, 2)})

    all_source_ids = list(req.batch_ids) + [-pid for pid in req.portfolio_ids]

    # ── Pool Mode: seed-rank L1 → optimizer L2 → combiner L3 ──
    if req.pool_mode:
      return _pool_mode_build(
        req, full_daily_pnl, asset_batch, asset_strat, strat_map,
        all_trades, all_source_ids, external_scores, run_batch_map, task_id, _t3,
      )

    # Phase 1+2: Per-strategy optimization (classic manual mode)
    _update_progress(task_id, 4, 6, "L1: Per-batch optimization")
    variant_pnls: dict[str, pd.Series] = {}
    variant_assets: dict[str, list[str]] = {}
    bid_variants: dict[int, list[str]] = {}

    for bid in all_source_ids:
      batch_cols = [c for c in full_daily_pnl.columns if asset_batch.get(c) == bid]
      if len(batch_cols) < 2:
        continue
      batch_pnl = full_daily_pnl[batch_cols]

      if req.l1_quality_gate:
        sname = strat_map.get(bid, f"Batch_{bid}")
        vn = f"{sname}_v1"
        variant_assets[vn] = batch_cols
        variant_pnls[vn] = batch_pnl.sum(axis=1)
        bid_variants[bid] = [vn]
        continue

      n_l_avail = sum(1 for c in batch_cols if c.endswith("_Long"))
      n_s_avail = sum(1 for c in batch_cols if c.endswith("_Short"))
      alloc = req.allocations.get(str(bid), {})
      n_l = min(alloc.get("n_longs", 5), max(n_l_avail, 1))
      n_s = min(alloc.get("n_shorts", 5), max(n_s_avail, 1))

      corr = get_correlation_matrix(batch_pnl, req.corr_method)
      # Correlation jitter for L1 per-batch diversity
      if getattr(req, "corr_jitter_enabled", False):
        from backend.analysis.corr_jitter import perturb_correlation_matrix
        corr = perturb_correlation_matrix(corr, req.corr_jitter_temperature)
      tc_active = req.tc_low_threshold > 0 or req.tc_high_threshold > 0
      tc_kwargs = {}
      if tc_active:
        # Build tagged trade counts for this batch's columns
        batch_syms = {_clean_symbol(c) for c in batch_cols}
        batch_trades = [t for t in all_trades if t.symbol in batch_syms]
        tagged_tc: dict[str, int] = {}
        for t in batch_trades:
          k = _trade_key(t, run_batch_map)
          if k in set(batch_cols):
            tagged_tc[k] = tagged_tc.get(k, 0) + 1
        tc_dict = {
          "low_threshold": req.tc_low_threshold, "max_low": req.tc_max_low,
          "high_threshold": req.tc_high_threshold, "min_high": req.tc_min_high,
        }
        tc_kwargs = {"trade_counts": tagged_tc, "tc_longs": tc_dict, "tc_shorts": tc_dict}
      variants = diversify_split_portfolio(
        req.div_algo, batch_pnl, corr, n_longs=n_l, n_shorts=n_s,
        within_objective=req.obj_layer1, combined_objective=req.obj_layer2,
        n_portfolios=req.candidates_per_strategy,
        seed_metrics=req.seed_metrics, max_seeds=req.max_seeds,
        prioritize_stability=req.prioritize_stability,
        external_scores=external_scores or None,
        streamlit_logic=req.streamlit_logic,
        max_overlap=req.max_overlap,
        expand_pool=req.expand_greedy_pool,
        seed_prior_weight=req.seed_prior_weight,
        seed_weights=req.seed_weights,
        max_dd_pct=req.max_allowed_drawdown_pct,
        fast_mode=(req.optimizer_engine == "fast"),
        engine=req.optimizer_engine,
        genetic_generations=req.genetic_generations, genetic_population=req.genetic_population,
        genetic_mutation_rate=req.genetic_mutation_rate, genetic_crossover_rate=req.genetic_crossover_rate,
        genetic_elite_pct=req.genetic_elite_pct,
        **tc_kwargs,
      )
      sname = strat_map.get(bid, f"Batch_{bid}")
      bid_vnames: list[str] = []
      for vi, v in enumerate(variants):
        vn = f"{sname}_v{vi + 1}"
        variant_assets[vn] = v["assets"]
        variant_pnls[vn] = batch_pnl[v["assets"]].sum(axis=1)
        bid_vnames.append(vn)
      bid_variants[bid] = bid_vnames

    _t5 = _time.perf_counter()
    _update_progress(task_id, 4, 6, "L1 optimization done", {
      "step_4_l1": round(_t5 - _t3, 2),
    })

    if len(variant_pnls) < 2:
      raise HTTPException(400, "Need at least 2 strategy variants after optimization")

    l3_obj = req.obj_layer3
    if l3_obj == "min_drawdown":
      l3_obj = "min_max_drawdown"

    # Build trade-level DataFrame with tagged keys
    # Include position_id so _portfolio_metrics can merge multi-exit fills
    _tdf = pd.DataFrame([
      {"Asset": _trade_key(t, run_batch_map), "Net Profit": t.net_profit,
       "position_id": t.position_id or f"_solo_{t.id}"}
      for t in all_trades if t.net_profit is not None
    ])

    def _build_manual_candidates(global_res, variant_map, pnl_df):
      from backend.analysis.asset_contribution import compute_asset_marginal_contribution
      cands = []
      for r in global_res:
        sel_variants = r["assets"]
        assets = list(dict.fromkeys(
          a for vn in sel_variants for a in variant_map.get(vn, [])
        ))
        assets.sort()
        metrics = _portfolio_metrics(pnl_df, assets, _tdf)
        blueprint = []
        if len(assets) >= 2:
          try:
            blueprint = compute_asset_marginal_contribution(assets, pnl_df)
          except Exception as _exc:
            logger.debug("swallowed: %s", _exc)
        cands.append({
          "longs": [a for a in assets if a.endswith("_Long")],
          "shorts": [a for a in assets if a.endswith("_Short")],
          "assets": assets,
          "display_assets": [_strip_batch_tag(a) for a in assets],
          "metrics": metrics,
          "score": round(float(r.get("score", 0.0)), 4),
          "attribution": {a: asset_strat.get(a, "Unknown") for a in assets},
          "selected_variants": sel_variants,
          "blueprint": blueprint,
        })
      return cands

    _t6 = _time.perf_counter()
    _update_progress(task_id, 4, 6, "L2/L3: Global assembly")
    if req.l2_groups:
      from backend.analysis.beam_optimizer import _score_combo
      _l2_corr = get_correlation_matrix(full_daily_pnl, req.corr_method)
      grouped_bids = set(b for group in req.l2_groups for b in group)
      l2_super_variants: dict[str, list[str]] = {}
      l2_super_pnls: dict[str, pd.Series] = {}

      # === L1.5: Harmony gate — filter batches within groups (Manual mode) ===
      if req.harmony_enabled and req.harmony_strength_l15 > 0:
        from backend.analysis.harmony import build_batch_curves, harmony_gate as _hgate
        _h_pg = {int(k): v for k, v in req.harmony_per_group.items()} if req.harmony_per_group else None
        for gi, group_bids_h in enumerate(req.l2_groups):
          apply_l15 = True
          if _h_pg is not None:
            apply_l15 = _h_pg.get(gi, False)
          if apply_l15 and len(group_bids_h) >= 2:
            # Build batch-level curves from the already-loaded PnL
            _asset_batch_local = {}
            for t in all_trades:
              _bid = run_batch_map.get(t.run_id, 0)
              k = _trade_key(t, run_batch_map)
              if k in full_daily_pnl.columns:
                _asset_batch_local[k] = _bid
            _batch_curves = build_batch_curves(full_daily_pnl, _asset_batch_local)
            _group_curves = {b: _batch_curves[b] for b in group_bids_h if b in _batch_curves}
            if len(_group_curves) >= 2:
              _hr = _hgate(_group_curves, strength=req.harmony_strength_l15, weights=req.harmony_weights)
              if not _hr.all_pass:
                # Remove worst-harmony batches, keep at least 2
                _batch_avg = {b: 0.0 for b in _group_curves}
                _bc = {b: 0 for b in _group_curves}
                for p in _hr.pairs:
                  _batch_avg[p.id_a] = _batch_avg.get(p.id_a, 0) + p.harmony_score
                  _batch_avg[p.id_b] = _batch_avg.get(p.id_b, 0) + p.harmony_score
                  _bc[p.id_a] = _bc.get(p.id_a, 0) + 1
                  _bc[p.id_b] = _bc.get(p.id_b, 0) + 1
                for b in _batch_avg:
                  if _bc[b] > 0:
                    _batch_avg[b] /= _bc[b]
                _thr = req.harmony_strength_l15 / 100.0
                _sorted_b = sorted(_group_curves.keys(), key=lambda x: _batch_avg.get(x, 0), reverse=True)
                _kept = [b for b in _sorted_b if _batch_avg.get(b, 0) >= _thr]
                if len(_kept) < 2:
                  _kept = _sorted_b[:2]
                _removed = set(group_bids_h) - set(_kept)
                if _removed:
                  req.l2_groups[gi] = [b for b in group_bids_h if b not in _removed]
                  for b in _removed:
                    bid_variants.pop(b, None)
                  logger.info("L1.5 manual group %d: removed batches %s (harmony below %.2f)", gi, _removed, _thr)

      for gi, group_bids in enumerate(req.l2_groups):
        group_variant_lists = [bid_variants.get(bid, []) for bid in sorted(group_bids)]
        if not all(group_variant_lists):
          continue
        combos = list(itertools.product(*group_variant_lists))
        scored: list[tuple[float, list[str], list[str]]] = []
        for combo in combos:
          merged = list(dict.fromkeys(
            a for vn in combo for a in variant_assets.get(vn, [])
          ))
          merged.sort()
          if not merged:
            continue
          score = _score_combo(full_daily_pnl, merged, req.obj_layer2, _l2_corr)
          scored.append((score, list(combo), merged))
        scored.sort(key=lambda x: x[0])
        for si, (_, combo_names, merged) in enumerate(scored[:req.candidates_per_strategy]):
          sv_name = f"Group{gi + 1}_v{si + 1}"
          l2_super_variants[sv_name] = merged
          l2_super_pnls[sv_name] = full_daily_pnl[merged].sum(axis=1)

      for bid in set(all_source_ids) - grouped_bids:
        for vn in bid_variants.get(bid, []):
          if vn in variant_pnls:
            l2_super_variants[vn] = variant_assets[vn]
            l2_super_pnls[vn] = variant_pnls[vn]

      if len(l2_super_pnls) < 2:
        raise HTTPException(400, "Need at least 2 L2 variant groups after optimization")

      # === L2.5: Harmony gate — check group-output affinity before L3 (Manual mode) ===
      if req.harmony_enabled and req.harmony_strength_l25 > 0 and len(l2_super_pnls) >= 2:
        from backend.analysis.harmony import harmony_gate as _hgate25
        # Build group-level curves from the L2 super variants (best per group)
        _group_curves_l25: dict[int, pd.Series] = {}
        for gi_l25, group_bids_l25 in enumerate(req.l2_groups):
          sv_name = f"Group{gi_l25 + 1}_v1"
          if sv_name in l2_super_pnls:
            _group_curves_l25[gi_l25] = l2_super_pnls[sv_name]
        if len(_group_curves_l25) >= 2:
          _hr25 = _hgate25(_group_curves_l25, strength=req.harmony_strength_l25, weights=req.harmony_weights)
          logger.info("L2.5 manual: harmony=%.3f, all_pass=%s, failed=%s",
                       _hr25.mean_harmony, _hr25.all_pass, _hr25.failed_pairs)
          # Apply penalty: scale down PnLs for poorly-harmonized groups so L3 deprioritizes them
          if not _hr25.all_pass:
            _group_harm_avg: dict[int, float] = {}
            _group_harm_cnt: dict[int, int] = {}
            for p in _hr25.pairs:
              for gid in (p.id_a, p.id_b):
                _group_harm_avg[gid] = _group_harm_avg.get(gid, 0) + p.harmony_score
                _group_harm_cnt[gid] = _group_harm_cnt.get(gid, 0) + 1
            for gid in _group_harm_avg:
              if _group_harm_cnt[gid] > 0:
                _group_harm_avg[gid] /= _group_harm_cnt[gid]
            _l25_threshold = req.harmony_strength_l25 / 100.0
            for gid, avg_h in _group_harm_avg.items():
              if avg_h < _l25_threshold:
                # Penalty factor: how far below threshold (0.5 = halve PnL, 0.0 = zero out)
                penalty = max(0.1, avg_h / max(_l25_threshold, 0.01))
                # Apply to all super-variants from this group
                for sv_key in list(l2_super_pnls.keys()):
                  if sv_key.startswith(f"Group{gid + 1}_"):
                    l2_super_pnls[sv_key] = l2_super_pnls[sv_key] * penalty
                    logger.info("L2.5 manual: penalized %s by %.2f (harmony=%.3f < threshold=%.3f)",
                                sv_key, penalty, avg_h, _l25_threshold)

      n_final = min(req.n_final_strats or len(l2_super_pnls), len(l2_super_pnls))
      vdf = pd.DataFrame(l2_super_pnls).fillna(0)
      vcorr = get_correlation_matrix(vdf, req.corr_method)
      _op_fn = _get_optimizer_funcs(req.optimizer_engine)[0]
      global_res = _op_fn(
        daily_pnl=vdf, corr_matrix=vcorr,
        n_assets=n_final, objective=l3_obj,
        n_portfolios=req.n_portfolios,
        seed_metrics=req.seed_metrics, max_seeds=req.max_seeds,
        prioritize_stability=req.prioritize_stability,
        expand_pool=req.expand_greedy_pool,
        seed_prior_weight=req.seed_prior_weight,
        seed_weights=req.seed_weights,
      )
      candidates = _build_manual_candidates(global_res, l2_super_variants, full_daily_pnl)
    else:
      n_final = min(req.n_final_strats or len(variant_pnls), len(variant_pnls))
      vdf = pd.DataFrame(variant_pnls).fillna(0)
      vcorr = get_correlation_matrix(vdf, req.corr_method)
      _op_fn = _get_optimizer_funcs(req.optimizer_engine)[0]
      global_res = _op_fn(
        daily_pnl=vdf, corr_matrix=vcorr,
        n_assets=n_final, objective=l3_obj,
        n_portfolios=req.n_portfolios,
        seed_metrics=req.seed_metrics, max_seeds=req.max_seeds,
        prioritize_stability=req.prioritize_stability,
        expand_pool=req.expand_greedy_pool,
        seed_prior_weight=req.seed_prior_weight,
        seed_weights=req.seed_weights,
      )
      candidates = _build_manual_candidates(global_res, variant_assets, full_daily_pnl)

    # Portfolio-level quality gates (hard reject)
    candidates = _apply_portfolio_gates(
      candidates, _tdf,
      min_pf=req.portfolio_min_pf,
      min_wr=req.portfolio_min_wr,
      cap_asset_pf=req.max_asset_pf if req.cap_asset_pf else 0,
    )

    _t7 = _time.perf_counter()
    _update_progress(task_id, 5, 6, "Optimizer complete", {
      "step_5_assembly": round(_t7 - _t6, 2), "step_5_optimize": round(_t7 - _t5, 2),
    })

    # AC post-filter: flag mismatched assets per candidate
    if req.ac_postfilter:
      from backend.analysis.seed_ranker import postfilter_ac
      for c in candidates:
        ac_results = postfilter_ac(full_daily_pnl, c["assets"], strategy_type=req.ac_postfilter_type, lag=req.ac_lag)
        c["ac_flags"] = [a for a, info in ac_results.items() if info["flagged"]]
        c["ac_scores"] = {a: info["ac"] for a, info in ac_results.items()}

    _t8 = _time.perf_counter()
    _update_progress(task_id, 6, 6, "Complete", {
      "step_6_metrics": round(_t8 - _t7, 2), "total": round(_t8 - _t0, 2),
    })

    with _optimize_lock:
      prog = _optimize_progress.get(task_id)
      step_timing = prog.get("timing", {}) if prog else {}

    out = sanitize_for_json({
      "candidates": candidates,
      "total_symbols": full_daily_pnl.shape[1],
      "total_variants": len(variant_pnls),
      "strategies": strat_map,
      "timing": step_timing,
    })
    _auto_save_run("build", out, req.model_dump(mode="json"))
    return out
  except HTTPException:
    raise
  except Exception as exc:
    from backend.api.routes.optimization_errors import wrap_optimization_error
    raise wrap_optimization_error("Cross Strategy Manual Build", exc) from exc


class CrossBuildDirectionalRequest(BaseModel):
  """Directional batches mode: beam search with user-defined L2 groups."""
  batch_ids: list[int] = []
  portfolio_ids: list[int] = []  # saved portfolios as virtual batches
  l2_groups: list[list[int]] = []
  n_per_batch: int = 5
  obj_l1: str = "hybrid"
  obj_l2: str = "uncorrelated"
  obj_l3: str = "max_sortino"
  k_l1: int = 5
  k_l2: int = 5
  k_l3: int = 3
  corr_method: str = "standard"
  div_algo: str = "greedy"
  # Genetic algorithm params (only used when div_algo == "genetic")
  genetic_generations: int = 50
  genetic_population: int = 100
  genetic_mutation_rate: float = 0.10
  genetic_crossover_rate: float = 0.70
  genetic_elite_pct: float = 0.10
  seed_metrics: list[str] = ["sortino"]
  max_seeds: int = 25
  prioritize_stability: bool = False
  date_from: str | None = None
  date_to: str | None = None
  extra_slippage_pct: float = 0.0
  extra_commission_pct: float = 0.0
  funding_daily_pct: float = 0.0
  spread_pct: float = 0.0
  per_batch_costs: str | None = None
  per_batch: dict[str, dict] | None = None
  # Global quality filters (fallback when per_batch is None)
  min_trades: int = 0
  min_pf: float = 0.0
  min_wr: float = 0.0
  # Per-asset max PF filter: 0 = off; > 0 = remove assets with PF above this
  max_asset_pf: float = 0.0
  cap_asset_pf: bool = False
  # Portfolio-level quality gates (post-optimization, hard reject)
  portfolio_min_pf: float = 0.0
  portfolio_min_wr: float = 0.0
  # Position-count constraints
  tc_low_threshold: int = 0
  tc_max_low: int = 99
  tc_high_threshold: int = 0
  tc_min_high: int = 0
  # Event-based L1 scoring (global default; overridable per-batch via per_batch)
  event_mode: bool = False
  n_trades: int = 20
  staleness_days: int = 180
  # Recency-weighted seed scoring
  recency_enabled: bool = False
  recency_mode: str = "gradual"
  recency_batch_size: int = 10
  recency_boost_factor: float = 3.0
  # Benchmark robustness
  benchmark_enabled: bool = False
  benchmark_source: str = ""
  benchmark_split: str = "time"
  benchmark_threshold: float = 1.2
  benchmark_elite_target: float = 1.0
  benchmark_exclude_fragile: bool = True
  # Benchmark seed score enhancements (0.0 = off, 0.0-0.5 range)
  benchmark_consistency_blend: float = 0.0
  benchmark_fragility_blend: float = 0.0
  benchmark_wr_harmony_blend: float = 0.0
  # L1 quality gate mode
  l1_quality_gate: bool = False
  n_per_group: int = 15
  # Expand greedy pool: when True, greedy picks from ALL profitable assets
  expand_greedy_pool: bool = True
  # Seed prior: soft tiebreaker weight for seed quality in greedy (0 = off)
  seed_prior_weight: float = 0.0
  # Per-metric seed weights: {metric_code: weight}
  seed_weights: dict[str, float] | None = None
  # Category filter — only include assets matching selected categories
  category_filter: list[str] = []
  # Portfolio exclusion filter — remove/limit assets from a saved portfolio
  exclude_portfolio_ids: list[int] = []
  exclude_portfolio_mode: str = "full"  # "full" = remove all, "partial" = keep X%
  exclude_portfolio_keep_pct: float = 0.0  # 0-100, percentage to keep when partial
  exclude_portfolio_method: str = "score"  # "score" | "correlation" | "blend"
  # Portfolio diversity: max Jaccard overlap between candidates (None = off)
  max_overlap: float | None = None
  # Risk profile — position sizing applied to all batch trades before optimization
  risk_profile_mode: str = "fixed"
  risk_profile_value: float = 0.0
  risk_profile_initial_capital: float = 10_000.0
  risk_profile_multiplier: float = 2.0
  risk_profile_max_streak: int = 3
  risk_profile_martingale_compound: str = "off"
  risk_profile_martingale_scope: Literal["asset", "portfolio", "asset_batch", "batch"] = "asset"
  risk_profile_martingale_count_mode: Literal["full_position", "each_exit", "confirmed_tsl"] = "full_position"
  risk_profile_martingale_start_after: int = 0
  risk_profile_anti_martingale_reset_after: int = 0
  risk_profile_kelly_n: float = 2.0
  risk_profile_kelly_warmup: int = 20
  risk_profile_kelly_scope: str = "portfolio"
  risk_profile_kelly_lookback: int = 0
  risk_profile_kelly_cap: float = 0.0
  risk_profile_sizing_master: str = "portfolio"
  risk_profile_batch_configs: str | None = None
  risk_profile_auto_asset_configs: str | None = None
  # Boundary clipping — force-close IS trades at IS boundary close price
  clip_is: bool = True
  # Stage 1: Pre-filter (structural quality)
  prefilter_enabled: bool = False
  prefilter_mode: str = "trend"
  prefilter_hurst_min: float = 0.50
  prefilter_ter_percentile: int = 25
  prefilter_hurst_max: float = 0.50
  prefilter_vr_max: float = 1.0
  prefilter_min_crossing_rate: float = 0.05
  # Stage 3: DSR benchmark gate
  benchmark_dsr_gate: bool = False
  # Stage 4: AC post-filter
  ac_postfilter: bool = False
  ac_postfilter_type: str = "trend"
  ac_lag: int = 1
  # Optimizer engine: "current" (principal), "fast" (batch-vectorized), "classic" (pre-2026-03-12)
  optimizer_engine: str = "current"
  # Correlation jitter — perturb corr matrix for candidate diversity
  corr_jitter_enabled: bool = False
  corr_jitter_temperature: float = 0.03
  # Post-construction refinement pass
  refinement_enabled: bool = False
  refinement_mode: str = "prune_and_swap"  # prune | swap | prune_and_swap
  refinement_metric: str = ""  # blank = use objective
  # ── Overfitting Guard ──────────────────────────────────────────
  overfit_guard_enabled: bool = False
  of_log_enabled: bool = False
  of_log_cap_pf: float = 4.0
  of_log_cap_sortino: float = 3.0
  of_log_cap_payoff: float = 3.0
  of_log_cap_wr: float = 70.0
  of_log_cap_sharpe: float = 2.5
  of_log_cap_calmar: float = 3.0
  of_z_score_enabled: bool = False
  of_z_threshold: float = 2.0
  of_z_penalty_strength: float = 0.15
  of_bayes_enabled: bool = False
  of_bayes_k: float = 5.0
  of_sigmoid_enabled: bool = False
  of_sig_cap_pf: float = 4.0
  of_sig_cap_sortino: float = 3.0
  of_sig_cap_payoff: float = 3.0
  of_sig_cap_wr: float = 70.0
  of_sig_cap_sharpe: float = 2.5
  of_sig_cap_calmar: float = 3.0
  of_normal_range_enabled: bool = False
  of_nr_pct_low: float = 20.0
  of_nr_pct_high: float = 60.0
  of_nr_penalty_scale: float = 0.3
  of_nr_bonus: float = 0.1
  of_wf_rerank_enabled: bool = False
  of_wf_split_pct: float = 70.0
  of_wf_validation_metric: str = "stability"
  # Bybit minimum size gate — drop assets whose scaled trades violate exchange limits
  bybit_size_gate: bool = False
  bybit_size_gate_max_reject_pct: float = 0.0  # 0 = binary (any rejection = fail)
  # ── Harmony Gate (L1.5 / L2.5) ──────────────────────────────────
  harmony_enabled: bool = False
  harmony_strength_l15: int = 50   # 0-100 strength for L1.5 (within-group batch filtering)
  harmony_strength_l25: int = 50   # 0-100 strength for L2.5 (cross-group filtering)
  harmony_weights: dict[str, float] | None = None  # custom metric weights
  harmony_per_group: dict[str, bool] | None = None  # manual mode: per-group L1.5 toggle {"0": true, "1": false}
  # ── Session Filter — inline trade-time filtering ──
  session_filter_enabled: bool = False
  session_filter_mode: str = "fixed"  # "fixed" | "auto" | "saved_mask"
  session_filter_exclude_days: list[int] = []   # 0=Mon..6=Sun
  session_filter_exclude_hours: list[int] = []  # 0-23
  session_filter_auto_min_trades: int = 10
  session_filter_auto_include_insufficient: bool = True
  session_filter_grade_on_cropped: bool = False
  session_mask_id: int | None = None  # FK to session_time_masks (saved_mask mode)


@router.post("/cross-strategy-build-directional")
def cross_strategy_build_directional(
  request: Request, req: CrossBuildDirectionalRequest, background_tasks: BackgroundTasks,
):
  """Directional beam search — runs in background, returns task_id."""
  rate_limit(request, max_requests=3, window_seconds=60, key="optimizer")
  total_sources = len(req.batch_ids) + len(req.portfolio_ids)
  if total_sources < 2:
    raise HTTPException(400, "Need at least 2 data sources (batches or portfolios)")
  if not req.l2_groups:
    raise HTTPException(400, "Define at least one L2 strategy group")

  from backend.db.database import SessionLocal
  task_id = uuid.uuid4().hex[:12]
  with _optimize_lock:
    _optimize_progress[task_id] = {"status": "running", "result": None, "error": None}
  background_tasks.add_task(
    _run_optimize_task, task_id, req, SessionLocal,
    _cross_strategy_build_directional_inner, "Cross-Strategy Directional",
  )
  return {"task_id": task_id, "status": "running"}


def _cross_strategy_build_directional_inner(
  req: CrossBuildDirectionalRequest, db: Session, task_id: str = "",
):
  """Inner logic for cross-strategy directional build (background thread)."""
  try:
    import time as _time
    from backend.analysis.optimization_cache import (
      get_cached_l0_incremental, put_cached_l0, update_cached_l0,
      get_cached_l0, _make_date_key, clone_cached_trades,
    )
    _t0 = _time.perf_counter()
    date_key = _make_date_key(req.date_from, req.date_to, req.clip_is)
    _update_progress(task_id, 0, 6, "Checking cache...")

    # [1/6] Build batch-tagged daily PnL — check cache first
    cached_entry, new_batches, new_portfolios = get_cached_l0_incremental(
      req.batch_ids, req.portfolio_ids or [],
      req.date_from, req.date_to, req.clip_is,
    )

    run_batch_map: dict[int, int] = {}
    p_asset_batch: dict[str, int] = {}
    p_strat_map: dict[int, str] = {}
    p_asset_strat: dict[str, str] = {}

    if cached_entry is not None and not new_batches and not new_portfolios:
      # Full cache hit — restore portfolio metadata
      daily_pnl = cached_entry.daily_pnl.copy()
      trades = clone_cached_trades(cached_entry.trades)
      run_batch_map = cached_entry.run_batch_map.copy()
      p_asset_batch = cached_entry.p_asset_batch.copy()
      p_strat_map = cached_entry.p_strat_map.copy()
      p_asset_strat = cached_entry.p_asset_strat.copy()
      logger.info("[1/6] CACHE HIT — loaded %d assets from cache", daily_pnl.shape[1])
      # Filter to requested batches only (prevent cache superset leak)
      daily_pnl, trades = _filter_to_requested(
        daily_pnl, trades, run_batch_map,
        req.batch_ids, req.portfolio_ids or [],
      )
    elif cached_entry is not None and (new_batches or new_portfolios):
      new_daily_pnl = pd.DataFrame()
      new_trades: list = []
      new_rbm: dict[int, int] = {}
      new_pab: dict[str, int] = {}
      new_psm: dict[int, str] = {}
      new_pas: dict[str, str] = {}
      if new_batches:
        new_daily_pnl, new_trades, new_rbm = _build_cross_daily_pnl(
          new_batches, db, req.date_from, req.date_to, clip_boundary=req.clip_is,
        )
      if new_portfolios:
        pt, p_ab, p_sm, p_as, p_rbm = _load_portfolio_trades(
          new_portfolios, db, req.date_from, req.date_to, tagged=True, clip_boundary=req.clip_is,
        )
        new_pab.update(p_ab); new_psm.update(p_sm); new_pas.update(p_as)
        new_trades.extend(pt); new_rbm.update(p_rbm)
        if pt:
          p_pnl = build_daily_pnl_matrix(pt, include_side=True)
          if not p_pnl.empty:
            tagged_cols = {}
            for col in p_pnl.columns:
              for pkey, pbid in p_ab.items():
                if _strip_batch_tag(pkey) == col:
                  tagged_cols[col] = pkey
                  break
              else:
                tagged_cols[col] = col
            p_pnl.columns = [tagged_cols.get(c, c) for c in p_pnl.columns]
            new_daily_pnl = pd.concat([new_daily_pnl, p_pnl], axis=1).fillna(0) if not new_daily_pnl.empty else p_pnl
      update_cached_l0(date_key, cached_entry, new_batches, new_portfolios,
                       new_daily_pnl, new_trades, new_rbm,
                       new_pab, new_psm, new_pas)
      updated = get_cached_l0(req.batch_ids, req.portfolio_ids or [],
                              req.date_from, req.date_to, req.clip_is)
      daily_pnl = updated.daily_pnl.copy()
      trades = list(updated.trades)
      run_batch_map = updated.run_batch_map.copy()
      p_asset_batch = updated.p_asset_batch.copy()
      p_strat_map = updated.p_strat_map.copy()
      p_asset_strat = updated.p_asset_strat.copy()
      logger.info("[1/6] INCREMENTAL LOAD — total %d assets", daily_pnl.shape[1])
      # Filter to requested batches only (prevent cache superset leak)
      daily_pnl, trades = _filter_to_requested(
        daily_pnl, trades, run_batch_map,
        req.batch_ids, req.portfolio_ids or [],
      )
    else:
      if req.batch_ids:
        daily_pnl, trades, run_batch_map = _build_cross_daily_pnl(
          req.batch_ids, db, req.date_from, req.date_to, clip_boundary=req.clip_is,
        )
      else:
        trades = []
        daily_pnl = pd.DataFrame()
      if req.portfolio_ids:
        pt, p_asset_batch, p_strat_map, p_asset_strat, p_rbm = _load_portfolio_trades(
          req.portfolio_ids, db, req.date_from, req.date_to, tagged=True, clip_boundary=req.clip_is,
        )
        trades.extend(pt); run_batch_map.update(p_rbm)
        if pt:
          p_pnl = build_daily_pnl_matrix(pt, include_side=True)
          if not p_pnl.empty:
            tagged_cols = {}
            for col in p_pnl.columns:
              for pkey, pbid in p_asset_batch.items():
                if _strip_batch_tag(pkey) == col:
                  tagged_cols[col] = pkey
                  break
              else:
                tagged_cols[col] = col
            p_pnl.columns = [tagged_cols.get(c, c) for c in p_pnl.columns]
            if daily_pnl.empty:
              daily_pnl = p_pnl
            else:
              daily_pnl = pd.concat([daily_pnl, p_pnl], axis=1).fillna(0)
      if trades:
        put_cached_l0(req.batch_ids, req.portfolio_ids or [],
                      req.date_from, req.date_to, req.clip_is,
                      daily_pnl, trades, run_batch_map,
                      p_asset_batch, p_strat_map, p_asset_strat)

    if not trades:
      raise HTTPException(404, "No trades found across selected sources")

    _t1 = _time.perf_counter()
    logger.info("[1/6] Directional build: loaded %d trades, %d assets (%.1fs)",
                len(trades), daily_pnl.shape[1], _t1 - _t0)
    _update_progress(task_id, 1, 6, "Trades loaded", {"step_1_load": round(_t1 - _t0, 2)})

    # No daily_pnl truncation — trades filtered by entry_time may exit after cutoff

    # ── Session Filter — inline trade-time filtering ──
    if req.session_filter_enabled and trades:
      from backend.analysis.session_mask_filter import apply_session_mask as _sf_apply, build_fixed_mask
      _sf_mask_data = None
      if req.session_filter_mode == "fixed" and (req.session_filter_exclude_days or req.session_filter_exclude_hours):
        _sf_mask_data = build_fixed_mask(req.session_filter_exclude_days, req.session_filter_exclude_hours)
      elif req.session_filter_mode == "auto":
        from backend.analysis.session_auto_mask import compute_auto_mask
        _auto = compute_auto_mask(trades, req.session_filter_auto_min_trades, req.session_filter_auto_include_insufficient)
        _sf_mask_data = _auto["mask_data"]
        logger.info("Cross directional session filter (auto): kept hours=%s, kept days=%s", _auto["kept_hours"], _auto["kept_days"])
      elif req.session_filter_mode == "saved_mask" and req.session_mask_id:
        from backend.api.routes.analysis import _apply_session_mask
        trades = _apply_session_mask(trades, req.session_mask_id, db, n_free_params=req.n_per_batch * len(req.batch_ids))
        _sf_mask_data = None  # already applied
      if _sf_mask_data:
        _before = len(trades)
        trades = _sf_apply(trades, _sf_mask_data)
        logger.info("Cross directional session filter (%s): %d → %d trades", req.session_filter_mode, _before, len(trades))
      daily_pnl = _rebuild_daily_pnl_from_trades(trades, run_batch_map)

    # Apply risk profile scaling FIRST (scales quantity for correct notional in stress test)
    if req.risk_profile_mode != "fixed" or req.risk_profile_value > 0 or req.risk_profile_sizing_master == "per_batch":
      from backend.api.routes.analysis import _apply_risk_scaling
      trades = _apply_risk_scaling(trades, req, db, include_side=True, run_batch_map=run_batch_map)
      daily_pnl = _rebuild_daily_pnl_from_trades(trades, run_batch_map)

    # Apply extra cost stress test AFTER scaling (uses scaled notional)
    _pbc = _parse_per_batch_costs(req.per_batch_costs) if req.per_batch_costs else None
    if req.extra_slippage_pct > 0 or req.extra_commission_pct > 0 or req.funding_daily_pct > 0 or req.spread_pct > 0 or _pbc:
      from backend.api.routes.analysis import _apply_stress_slippage
      trades = _apply_stress_slippage(trades, req.extra_slippage_pct, db,
                                       extra_commission_pct=req.extra_commission_pct,
                                       funding_daily_pct=req.funding_daily_pct,
                                       spread_pct=req.spread_pct,
                                       per_batch_costs=_pbc,
                                       run_batch_map=run_batch_map)
      daily_pnl = _rebuild_daily_pnl_from_trades(trades, run_batch_map)

    # Category filter (match on clean symbol)
    if req.category_filter:
      from backend.api.routes.analysis import _filter_by_category
      clean_pnl = daily_pnl.copy()
      clean_pnl.columns = [_strip_batch_tag(c) for c in daily_pnl.columns]
      clean_pnl = _filter_by_category(clean_pnl, req.category_filter, db)
      keep_clean = set(clean_pnl.columns)
      keep_cols = [c for c in daily_pnl.columns if _strip_batch_tag(c) in keep_clean]
      daily_pnl = daily_pnl[keep_cols]
      trades = [t for t in trades if _trade_key(t, run_batch_map) in set(daily_pnl.columns)]

    # Portfolio exclusion filter — full mode (remove before quality filters)
    excluded_names: set[str] = set()
    if req.exclude_portfolio_ids:
      excluded_names = _get_excluded_asset_names(req.exclude_portfolio_ids, db)
      if excluded_names and req.exclude_portfolio_mode == "full":
        daily_pnl = _apply_exclude_portfolio_full(daily_pnl, excluded_names)

    # Quality filter on SCALED PnL (after risk scaling so PF reflects martingale)
    asset_batch = _build_asset_batch_map(req.batch_ids, db, tagged=True) if req.batch_ids else {}
    asset_batch.update(p_asset_batch)

    if req.per_batch:
      daily_pnl = _apply_per_batch_filters(
        trades, daily_pnl, req.per_batch, asset_batch, run_batch_map,
        date_to=req.date_to,
        default_min_trades=req.min_trades, default_min_pf=req.min_pf,
      )
    else:
      from backend.api.routes.analysis import _filter_daily_pnl
      daily_pnl = _filter_daily_pnl(
        daily_pnl, trades, min_trades=req.min_trades, min_pf=req.min_pf,
        max_pf=req.max_asset_pf, cap_pf=req.cap_asset_pf,
        include_side=True, run_batch_map=run_batch_map, date_to=req.date_to,
      )
    _qualified_cols = set(daily_pnl.columns)
    trades = [t for t in trades if _trade_key(t, run_batch_map) in _qualified_cols]

    # Bybit size gate — drop assets whose scaled trades violate exchange min/max qty
    if req.bybit_size_gate:
      from backend.api.routes.analysis import _filter_bybit_size_gate
      daily_pnl = _filter_bybit_size_gate(
        daily_pnl, trades, req.bybit_size_gate_max_reject_pct,
        include_side=True, run_batch_map=run_batch_map,
      )

    _t2 = _time.perf_counter()
    _update_progress(task_id, 2, 6, "Quality filters", {"step_2_filters": round(_t2 - _t1, 2)})

    # Pre-filter: remove structurally unsuitable assets
    if req.prefilter_enabled and daily_pnl.shape[1] > 0:
      if req.prefilter_mode == "trend":
        from backend.analysis.seed_ranker import prefilter_for_trend
        survivors = prefilter_for_trend(daily_pnl, req.prefilter_hurst_min, req.prefilter_ter_percentile)
      else:
        from backend.analysis.seed_ranker import prefilter_for_meanrev
        survivors = prefilter_for_meanrev(daily_pnl, req.prefilter_hurst_max, req.prefilter_vr_max, req.prefilter_min_crossing_rate)
      daily_pnl = daily_pnl[[c for c in daily_pnl.columns if c in survivors]]

    if daily_pnl.shape[1] < 2:
      raise HTTPException(400, "Need at least 2 symbols after filtering")

    external_scores: dict = {}
    if req.benchmark_enabled:
      daily_pnl, external_scores = _cross_prepare_benchmark(
        trades, daily_pnl, req.benchmark_source, req.benchmark_split,
        req.benchmark_threshold, req.benchmark_elite_target, req.benchmark_exclude_fragile,
        run_batch_map=run_batch_map,
        consistency_blend=req.benchmark_consistency_blend,
        fragility_blend=req.benchmark_fragility_blend,
        wr_harmony_blend=req.benchmark_wr_harmony_blend,
        dsr_gate=req.benchmark_dsr_gate,
      )
      if "benchmark" not in req.seed_metrics:
        req.seed_metrics = [*req.seed_metrics, "benchmark"]

    if req.event_mode or (req.per_batch and any(
      cfg.get("event_mode", False) for cfg in req.per_batch.values()
    )):
      event_ext = _compute_event_external_scores(
        trades, req.event_mode, req.n_trades, req.staleness_days,
        req.per_batch, req.seed_metrics, asset_batch, include_side=True,
        date_from=req.date_from, run_batch_map=run_batch_map,
      )
      if event_ext:
        external_scores = {**external_scores, **event_ext}

    if req.recency_enabled:
      from backend.analysis.optimizer_seeds import compute_recency_seed_scores
      trade_df = pd.DataFrame([
        {"Asset": _trade_key(t, run_batch_map), "Exit Time": t.exit_time, "Net Profit": t.net_profit}
        for t in trades if t.exit_time is not None and t.net_profit is not None
      ])
      if not trade_df.empty:
        external_scores["recency"] = compute_recency_seed_scores(
          trade_df, mode=req.recency_mode,
          batch_size=req.recency_batch_size, boost_factor=req.recency_boost_factor,
        )
        if "recency" not in req.seed_metrics:
          req.seed_metrics = [*req.seed_metrics, "recency"]

    # Portfolio exclusion filter — partial mode (after external scoring, before optimizer)
    if excluded_names and req.exclude_portfolio_mode == "partial" and req.exclude_portfolio_keep_pct < 100:
      from backend.analysis.optimizer_seeds import compute_seed_scores as _excl_seed_scores
      _excl_pool = _excl_seed_scores(daily_pnl, daily_pnl.columns.tolist(),
                                      req.seed_metrics or ["sortino"], external_scores or None,
                                      metric_weights=req.seed_weights)
      _excl_corr = daily_pnl.corr() if req.exclude_portfolio_method in ("correlation", "blend") else None
      daily_pnl = _apply_exclude_portfolio_partial(
        daily_pnl, excluded_names, req.exclude_portfolio_keep_pct,
        req.exclude_portfolio_method, _excl_pool, _excl_corr,
      )
      trades = [t for t in trades if _trade_key(t, run_batch_map) in set(daily_pnl.columns)]

    _t3 = _time.perf_counter()
    _update_progress(task_id, 3, 6, "Scoring & pool filtering", {"step_3_scoring": round(_t3 - _t2, 2)})
    _update_progress(task_id, 4, 6, "Running beam optimizer")

    if req.optimizer_engine == "og_v1":
      # CS-026: loud one-line warning so any accidental hit on the frozen
      # A/B variant is visible. These modules do NOT receive current-engine
      # fixes; they exist only to reproduce saved research runs.
      logger.warning(
        "CS-026 LEGACY-FROZEN: beam optimizer_engine='og_v1' dispatched. "
        "Pinned A/B variant — not receiving current fixes (CS-005, CS-015, "
        "CS-016). Results are RESEARCH-ONLY and not comparable to the "
        "current engine."
      )
      from backend.analysis.beam_optimizer_og_v1 import run_beam_optimization
    elif req.optimizer_engine == "og_v2":
      logger.warning(
        "CS-026 LEGACY-FROZEN: beam optimizer_engine='og_v2' dispatched. "
        "Pinned A/B variant — not receiving current fixes (CS-005, CS-015, "
        "CS-016). Results are RESEARCH-ONLY and not comparable to the "
        "current engine."
      )
      from backend.analysis.beam_optimizer_og_v2 import run_beam_optimization
    else:
      from backend.analysis.beam_optimizer import run_beam_optimization
    tc_config = None
    if req.tc_low_threshold > 0 or req.tc_high_threshold > 0:
      tagged_tc: dict[str, int] = {}
      for t in trades:
        k = _trade_key(t, run_batch_map)
        tagged_tc[k] = tagged_tc.get(k, 0) + 1
      tc_config = {
        "low_threshold": req.tc_low_threshold, "max_low": req.tc_max_low,
        "high_threshold": req.tc_high_threshold, "min_high": req.tc_min_high,
      }
      trade_counts_for_beam = tagged_tc
    else:
      trade_counts_for_beam = None
    # Convert harmony_per_group keys from str to int (JSON limitation)
    _harmony_pg: dict[int, bool] | None = None
    if req.harmony_per_group:
      _harmony_pg = {int(k): v for k, v in req.harmony_per_group.items()}

    raw = run_beam_optimization(
      daily_pnl, asset_batch, req.l2_groups,
      req.n_per_batch, req.obj_l1, req.obj_l2, req.obj_l3,
      req.k_l1, req.k_l2, req.k_l3, req.corr_method,
      req.seed_metrics, req.max_seeds, req.prioritize_stability,
      external_scores or None,
      tc_config=tc_config, trade_counts=trade_counts_for_beam,
      l1_quality_gate=req.l1_quality_gate,
      n_per_group=req.n_per_group,
      fast_mode=(req.optimizer_engine == "fast"),
      harmony_enabled=req.harmony_enabled,
      harmony_strength_l15=req.harmony_strength_l15,
      harmony_strength_l25=req.harmony_strength_l25,
      harmony_weights=req.harmony_weights,
      harmony_per_group=_harmony_pg,
    )

    _t7 = _time.perf_counter()
    _update_progress(task_id, 5, 6, "Beam optimizer complete", {
      "step_5_beam": round(_t7 - _t3, 2),
    })

    # Apply Jaccard diversity filter on beam results
    if req.max_overlap is not None:
      from backend.analysis.diversification import _diverse_select
      raw = _diverse_select(raw, req.k_l3, req.max_overlap)

    # Post-construction refinement pass (directional)
    if getattr(req, "refinement_enabled", False) and raw:
      from backend.analysis.optimizer_refinement import refine_candidates
      raw = refine_candidates(
        raw, daily_pnl, daily_pnl.columns.tolist(),
        refinement_mode=req.refinement_mode,
        refinement_metric=req.refinement_metric,
        objective=req.obj_l3,
      )

    asset_strat = _asset_strategy_lookup(req.batch_ids, db, tagged=True) if req.batch_ids else {}
    asset_strat.update(p_asset_strat)
    strat_map = _strategy_map(req.batch_ids, db) if req.batch_ids else {}
    strat_map.update(p_strat_map)

    # Build trade-level DataFrame for position-merged metrics
    _tdf = pd.DataFrame([
      {"Asset": _trade_key(t, run_batch_map), "Net Profit": t.net_profit,
       "position_id": t.position_id or f"_solo_{t.id}"}
      for t in trades if t.net_profit is not None
    ])

    from backend.analysis.asset_contribution import compute_asset_marginal_contribution
    candidates = []
    for c in raw:
      assets = c["assets"]
      metrics = _portfolio_metrics(daily_pnl, assets, _tdf)
      blueprint = []
      if len(assets) >= 2:
        try:
          blueprint = compute_asset_marginal_contribution(assets, daily_pnl)
        except Exception as _exc:
          logger.debug("swallowed: %s", _exc)
      candidates.append({
        "longs": [a for a in assets if a.endswith("_Long")],
        "shorts": [a for a in assets if a.endswith("_Short")],
        "assets": assets,
        "display_assets": [_strip_batch_tag(a) for a in assets],
        "metrics": metrics,
        "attribution": {a: asset_strat.get(a, "Unknown") for a in assets},
        "blueprint": blueprint,
      })

    # Portfolio-level quality gates (hard reject)
    candidates = _apply_portfolio_gates(
      candidates, _tdf,
      min_pf=req.portfolio_min_pf,
      min_wr=req.portfolio_min_wr,
      cap_asset_pf=req.max_asset_pf if req.cap_asset_pf else 0,
    )

    # AC post-filter: flag mismatched assets per candidate
    if req.ac_postfilter:
      from backend.analysis.seed_ranker import postfilter_ac
      for c in candidates:
        ac_results = postfilter_ac(daily_pnl, c["assets"], strategy_type=req.ac_postfilter_type, lag=req.ac_lag)
        c["ac_flags"] = [a for a, info in ac_results.items() if info["flagged"]]
        c["ac_scores"] = {a: info["ac"] for a, info in ac_results.items()}

    _t8 = _time.perf_counter()
    _update_progress(task_id, 6, 6, "Complete", {
      "step_6_metrics": round(_t8 - _t7, 2), "total": round(_t8 - _t0, 2),
    })

    with _optimize_lock:
      prog = _optimize_progress.get(task_id)
      step_timing = prog.get("timing", {}) if prog else {}

    out = sanitize_for_json({
      "candidates": candidates,
      "total_symbols": daily_pnl.shape[1],
      "strategies": strat_map,
      "timing": step_timing,
    })
    _auto_save_run("build", out, req.model_dump(mode="json"))
    return out
  except HTTPException:
    raise
  except Exception as exc:
    from backend.api.routes.optimization_errors import wrap_optimization_error
    raise wrap_optimization_error("Cross Strategy Directional Build", exc) from exc


@router.post("/cross-strategy-correlation")
def cross_strategy_correlation(req: CrossCorrelationRequest, db: Session = Depends(get_db)):
  """Correlation matrix across multiple strategy batches."""
  total_sources = len(req.batch_ids) + len(req.portfolio_ids)
  if total_sources < 2:
    raise HTTPException(400, "Need at least 2 data sources")

  try:
    # Use batch-tagged PnL for correlation too
    if req.batch_ids:
      daily_pnl, trades, run_batch_map = _build_cross_daily_pnl(
        req.batch_ids, db, req.date_from, req.date_to,
        clip_boundary=req.clip_is,
      )
    else:
      trades = []
      daily_pnl = pd.DataFrame()
      run_batch_map = {}

    if req.portfolio_ids:
      pt, _, _, _, p_rbm = _load_portfolio_trades(
        req.portfolio_ids, db, req.date_from, req.date_to, tagged=True,
        clip_boundary=req.clip_is,
      )
      trades.extend(pt)
      run_batch_map.update(p_rbm)
    if not trades:
      raise HTTPException(404, "No trades found")

    # No daily_pnl truncation — trades filtered by entry_time may exit after cutoff

    # Pre-filter: remove poor assets BEFORE expensive correlation
    if req.min_trades > 0 or req.min_pf > 0 or req.min_wr > 0:
      from backend.api.routes.analysis import _filter_daily_pnl
      daily_pnl = _filter_daily_pnl(
        daily_pnl, trades, req.min_trades, req.min_pf, req.min_wr, include_side=True,
        run_batch_map=run_batch_map, date_to=req.date_to,
      )

    if daily_pnl.shape[1] < 2:
      raise HTTPException(400, "Need at least 2 symbols after filtering")

    # Cap at 500 assets — a 500x500 matrix is ~2MB JSON.
    # Beyond this, the response is too large and can crash the browser tab.
    _MAX_CORR_RESPONSE = 500
    if daily_pnl.shape[1] > _MAX_CORR_RESPONSE:
      raise HTTPException(
        400,
        f"Too many assets ({daily_pnl.shape[1]}) for correlation matrix display. "
        f"Maximum is {_MAX_CORR_RESPONSE}. Apply tighter filters (min trades, min PF, min WR) "
        f"to reduce the pool size.",
      )

    corr = get_correlation_matrix(daily_pnl, req.corr_method)
    stats = correlation_stats(corr)

    # Strip batch tags from column names for display
    display_symbols = [_strip_batch_tag(s) for s in corr.columns.tolist()]

    return sanitize_for_json({
      "symbols": display_symbols,
      "matrix": corr.values.tolist(),
      "stats": stats,
    })
  except HTTPException:
    raise
  except Exception as exc:
    from backend.api.routes.optimization_errors import wrap_optimization_error
    raise wrap_optimization_error("Cross Strategy Correlation", exc) from exc


# ═══════════════════════════════════════════════════════════════════════
#  Settings Recommender
# ═══════════════════════════════════════════════════════════════════════

class RecommendSettingsRequest(BaseModel):
  """Request model for the settings recommender."""
  batch_ids: list[int] = []
  portfolio_ids: list[int] = []
  mode: str = "quick"  # "quick" or "thorough"
  split_mode: bool = True
  n_assets: int = 10
  n_longs: int = 5
  n_shorts: int = 5
  seed_metrics: list[str] = ["sortino"]
  date_from: str | None = None
  date_to: str | None = None
  clip_is: bool = True
  corr_method: str = "standard"
  # Quality filters for initial load
  min_trades: int = 0
  min_pf: float = 0.0
  min_wr: float = 0.0
  max_asset_pf: float = 0.0
  cap_asset_pf: bool = False
  per_batch: dict[str, dict] | None = None
  # Extra cost stress
  extra_slippage_pct: float = 0.0
  extra_commission_pct: float = 0.0
  funding_daily_pct: float = 0.0
  spread_pct: float = 0.0
  per_batch_costs: str | None = None
  # Thorough mode sweep options
  algos: list[str] | None = None  # None = default 4
  objectives: list[str] | None = None  # None = default 5
  sweep_guards: bool = True
  sweep_seed_prior: bool = True
  sweep_filters: bool = True
  max_combos: int = 500
  # Depth controls (thorough mode)
  depth_pct: int = 100            # 1-100 exploration depth slider
  extreme_values: bool = False    # add extreme guard/filter presets
  # Per-dimension depth overrides (None = auto from master depth_pct)
  dim_guards: int | None = None
  dim_filters: int | None = None
  dim_seed_combos: int | None = None
  dim_folds: int | None = None
  # Per-axis filter overrides (None = falls back to dim_filters)
  dim_filter_pf: int | None = None
  dim_filter_trades: int | None = None
  dim_filter_wr: int | None = None
  # Custom seed metric combos — user-built list of combos to sweep
  seed_combos: list[list[str]] | None = None
  # Snapshot of ALL current SharedSettings so presets can restore everything
  current_shared_settings: dict | None = None


def _recommend_settings_inner(req: RecommendSettingsRequest, db: Session, task_id: str = ""):
  """Inner logic for settings recommender (runs in background thread for thorough mode)."""
  try:
    import time as _time
    from backend.analysis.settings_recommender import recommend_settings, result_to_dict
    from backend.analysis.optimization_cache import (
      get_cached_l0_incremental, put_cached_l0, update_cached_l0,
      get_cached_l0, get_cached_corr, put_cached_corr,
      _make_date_key, _make_corr_key, clone_cached_trades,
    )
    _t0 = _time.perf_counter()
    logger.info("═══ SETTINGS RECOMMENDER START ═══  mode=%s batches=%s", req.mode, req.batch_ids)
    _update_progress(task_id, 0, 4, "Loading trades...")

    # [1/4] Load trades (reuse cache infrastructure)
    cached_entry, new_batches, new_portfolios = get_cached_l0_incremental(
      req.batch_ids, req.portfolio_ids or [],
      req.date_from, req.date_to, req.clip_is,
    )

    run_batch_map: dict[int, int] = {}
    p_asset_batch: dict[str, int] = {}

    if cached_entry is not None and not new_batches and not new_portfolios:
      daily_pnl = cached_entry.daily_pnl.copy()
      trades = clone_cached_trades(cached_entry.trades)
      run_batch_map = cached_entry.run_batch_map.copy()
      p_asset_batch = cached_entry.p_asset_batch.copy()
      # Filter to requested batches only (prevent cache superset leak)
      daily_pnl, trades = _filter_to_requested(
        daily_pnl, trades, run_batch_map,
        req.batch_ids, req.portfolio_ids or [],
      )
    else:
      if req.batch_ids:
        daily_pnl, trades, run_batch_map = _build_cross_daily_pnl(
          req.batch_ids, db, req.date_from, req.date_to,
          clip_boundary=req.clip_is,
        )
      else:
        trades = []
        daily_pnl = pd.DataFrame()
        run_batch_map = {}

      if req.portfolio_ids:
        pt, p_asset_batch, _, _, p_rbm = _load_portfolio_trades(
          req.portfolio_ids, db, req.date_from, req.date_to, tagged=True,
          clip_boundary=req.clip_is,
        )
        trades.extend(pt)
        run_batch_map.update(p_rbm)
        if pt:
          p_pnl = build_daily_pnl_matrix(pt, include_side=True)
          if not p_pnl.empty:
            tagged_cols = {}
            for col in p_pnl.columns:
              for pkey, pbid in p_asset_batch.items():
                if _strip_batch_tag(pkey) == col:
                  tagged_cols[col] = pkey
                  break
              else:
                tagged_cols[col] = col
            p_pnl.columns = [tagged_cols.get(c, c) for c in p_pnl.columns]
            if daily_pnl.empty:
              daily_pnl = p_pnl
            else:
              daily_pnl = pd.concat([daily_pnl, p_pnl], axis=1).fillna(0)

      if trades:
        put_cached_l0(req.batch_ids, req.portfolio_ids or [],
                      req.date_from, req.date_to, req.clip_is,
                      daily_pnl, trades, run_batch_map,
                      p_asset_batch, {}, {})

    if not trades:
      raise HTTPException(404, "No trades found across selected sources")

    # Apply extra cost stress
    _pbc = _parse_per_batch_costs(req.per_batch_costs) if req.per_batch_costs else None
    if req.extra_slippage_pct > 0 or req.extra_commission_pct > 0 or req.funding_daily_pct > 0 or req.spread_pct > 0 or _pbc:
      from backend.api.routes.analysis import _apply_stress_slippage
      trades = _apply_stress_slippage(trades, req.extra_slippage_pct, db,
                                       extra_commission_pct=req.extra_commission_pct,
                                       funding_daily_pct=req.funding_daily_pct,
                                       spread_pct=req.spread_pct,
                                       per_batch_costs=_pbc,
                                       run_batch_map=run_batch_map)
      daily_pnl = _rebuild_daily_pnl_from_trades(trades, run_batch_map)

    # Apply quality filters
    if req.per_batch:
      asset_batch = _build_asset_batch_map(req.batch_ids, db, tagged=True) if req.batch_ids else {}
      asset_batch.update(p_asset_batch)
      daily_pnl = _apply_per_batch_filters(
        trades, daily_pnl, req.per_batch, asset_batch, run_batch_map,
        date_to=req.date_to,
        default_min_trades=req.min_trades, default_min_pf=req.min_pf,
      )
    elif req.min_trades > 0 or req.min_pf > 0 or req.min_wr > 0 or (req.max_asset_pf > 0 and not req.cap_asset_pf):
      from backend.api.routes.analysis import _filter_daily_pnl
      daily_pnl = _filter_daily_pnl(
        daily_pnl, trades, min_trades=req.min_trades, min_pf=req.min_pf,
        min_wr=req.min_wr,
        max_pf=req.max_asset_pf, cap_pf=req.cap_asset_pf,
        include_side=True, run_batch_map=run_batch_map, date_to=req.date_to,
      )

    if daily_pnl.shape[1] < 2:
      raise HTTPException(400, f"Need at least 2 symbols after filtering (got {daily_pnl.shape[1]})")

    _t1 = _time.perf_counter()
    logger.info("[1/4] Loaded %d trades, %d assets (%.1fs)", len(trades), daily_pnl.shape[1], _t1 - _t0)
    _update_progress(task_id, 1, 4, "Trades loaded")

    # [2/4] Correlation matrix
    corr = get_correlation_matrix(daily_pnl, req.corr_method)
    _t2 = _time.perf_counter()
    logger.info("[2/4] Correlation matrix computed (%.1fs)", _t2 - _t1)
    _update_progress(task_id, 2, 4, "Correlation computed")

    # [3/4] Build asset-batch map
    ab_map = _build_asset_batch_map(req.batch_ids, db, tagged=True) if req.batch_ids else {}
    ab_map.update(p_asset_batch)

    # Trade key function for the recommender
    def _trade_key_fn(t):
      return _trade_key(t, run_batch_map)

    _update_progress(task_id, 3, 4, "Analysing pool..." if req.mode == "quick" else "Running cross-validation...")

    # Progress callback for thorough mode — reports per-combo progress
    def _cv_progress(current: int, total: int):
      pct = int(current / max(total, 1) * 100)
      _update_progress(task_id, 3, 4, f"CV sweep: {current}/{total} combos ({pct}%)")

    # [4/4] Run recommender
    result = recommend_settings(
      daily_pnl=daily_pnl,
      trades=trades,
      asset_batch_map=ab_map,
      corr_matrix=corr,
      mode=req.mode,
      split_mode=req.split_mode,
      n_assets=req.n_assets,
      n_longs=req.n_longs,
      n_shorts=req.n_shorts,
      seed_metrics=req.seed_metrics,
      algos=req.algos,
      objectives=req.objectives,
      sweep_guards=req.sweep_guards,
      sweep_seed_prior=req.sweep_seed_prior,
      sweep_filters=req.sweep_filters,
      trade_key_fn=_trade_key_fn,
      max_combos=req.max_combos,
      progress_fn=_cv_progress if req.mode == "thorough" else None,
      depth_pct=req.depth_pct,
      extreme_values=req.extreme_values,
      dim_guards=req.dim_guards,
      dim_filters=req.dim_filters,
      dim_seed_combos=req.dim_seed_combos,
      dim_folds=req.dim_folds,
      user_min_pf=req.min_pf,
      user_min_trades=req.min_trades,
      user_min_wr=req.min_wr,
      dim_filter_pf=req.dim_filter_pf,
      dim_filter_trades=req.dim_filter_trades,
      dim_filter_wr=req.dim_filter_wr,
      seed_combos_override=req.seed_combos,
    )

    _t3 = _time.perf_counter()
    logger.info("═══ SETTINGS RECOMMENDER DONE ═══  mode=%s, %d recommendations, %.1fs total",
                req.mode, len(result.recommendations), _t3 - _t0)
    _update_progress(task_id, 4, 4, "Complete")

    run_type = "recommend_thorough" if req.mode == "thorough" else "recommend_quick"
    out = sanitize_for_json(result_to_dict(result))

    # Inject run-context into each preset's full_settings so "Load" restores
    # asset counts, costs, dates, pool mode — not just optimizer settings
    _run_context = {
        "n_assets": req.n_assets,
        "n_longs": req.n_longs,
        "n_shorts": req.n_shorts,
        "split_mode": req.split_mode,
        "extra_slippage_pct": req.extra_slippage_pct,
        "extra_commission_pct": req.extra_commission_pct,
        "funding_daily_pct": req.funding_daily_pct,
        "spread_pct": req.spread_pct,
        "per_batch_costs": req.per_batch_costs,
        "date_from": req.date_from,
        "date_to": req.date_to,
        "clip_is": req.clip_is,
        "corr_method": req.corr_method,
    }
    # Merge user's full SharedSettings snapshot so presets restore everything
    if req.current_shared_settings:
        _run_context.update(req.current_shared_settings)
    for preset in out.get("top_presets", []):
        fs = preset.get("full_settings")
        if isinstance(fs, dict):
            fs.update(_run_context)

    _auto_save_run(run_type, out, req.model_dump(mode="json"))
    return out
  except HTTPException:
    raise
  except Exception as exc:
    from backend.api.routes.optimization_errors import wrap_optimization_error
    raise wrap_optimization_error("Settings Recommender", exc) from exc


@router.post("/cross-strategy-recommend-settings")
def cross_strategy_recommend_settings(
  req: RecommendSettingsRequest,
  background_tasks: BackgroundTasks,
):
  """Analyse IS data and recommend optimal optimizer settings.

  Quick mode returns immediately. Thorough mode runs in background.
  """
  total_sources = len(req.batch_ids) + len(req.portfolio_ids)
  if total_sources < 1:
    raise HTTPException(400, "Need at least 1 data source (batch or portfolio)")

  from backend.db.database import SessionLocal

  if req.mode == "quick":
    # Quick mode: run synchronously (fast enough)
    db = SessionLocal()
    try:
      result = _recommend_settings_inner(req, db)
      return result
    finally:
      db.close()
  else:
    # Thorough mode: run in background
    task_id = uuid.uuid4().hex[:12]
    with _optimize_lock:
      _optimize_progress[task_id] = {"status": "running", "result": None, "error": None}
    background_tasks.add_task(
      _run_optimize_task, task_id, req, SessionLocal,
      _recommend_settings_inner, "Settings Recommender",
    )
    return {"task_id": task_id, "status": "running"}


# ── Harmony Gate: standalone analysis endpoint (manual mode) ──────────────


class HarmonyAnalyzeRequest(BaseModel):
  """Analyze batch harmony for a set of batches."""
  batch_ids: list[int]
  portfolio_ids: list[int] = []
  strength: int = 50
  weights: dict[str, float] | None = None
  date_from: str | None = None
  date_to: str | None = None
  clip_is: bool = True


@router.post("/harmony-analyze")
def harmony_analyze(req: HarmonyAnalyzeRequest, db=Depends(get_db)):
  """Compute batch-level harmony matrix for manual portfolio construction.

  Returns pairwise harmony scores + per-metric breakdowns so the user
  can see which batches complement each other.
  """
  if len(req.batch_ids) + len(req.portfolio_ids) < 2:
    raise HTTPException(400, "Need at least 2 batches/portfolios for harmony analysis")

  from backend.analysis.harmony import build_batch_curves, harmony_gate

  # Reuse the same PnL builder as directional builds
  daily_pnl, trades, run_batch_map = _build_cross_daily_pnl(
    req.batch_ids, db, req.date_from, req.date_to, clip_boundary=req.clip_is,
  )

  # Also load portfolio sources if any
  p_asset_batch: dict[str, int] = {}
  if req.portfolio_ids:
    pt, p_ab, _p_sm, _p_as, p_rbm = _load_portfolio_trades(
      req.portfolio_ids, db, req.date_from, req.date_to,
      tagged=True, clip_boundary=req.clip_is,
    )
    if pt:
      p_pnl = build_daily_pnl_matrix(pt, include_side=True)
      if not p_pnl.empty:
        # Tag portfolio columns with portfolio batch IDs
        for col in p_pnl.columns:
          for pkey, pbid in p_ab.items():
            if pkey in col:
              p_asset_batch[col] = pbid
        # Merge portfolio PnL into main matrix
        daily_pnl = pd.concat([daily_pnl, p_pnl], axis=1).fillna(0)
        run_batch_map.update(p_rbm)

  if daily_pnl.empty:
    raise HTTPException(400, "Empty PnL matrix after processing trades")

  # Build asset → batch map
  asset_batch: dict[str, int] = {}
  for t in trades:
    bid = run_batch_map.get(t.run_id, 0)
    key = _trade_key(t, run_batch_map)
    if key in daily_pnl.columns:
      asset_batch[key] = bid
  asset_batch.update(p_asset_batch)

  # Build batch-level equity curves
  batch_curves = build_batch_curves(daily_pnl, asset_batch)

  if len(batch_curves) < 2:
    raise HTTPException(400, "Need at least 2 batches with trades for harmony analysis")

  # Run harmony analysis
  hr = harmony_gate(batch_curves, strength=req.strength, weights=req.weights)

  # Enrich response with batch names
  batch_names: dict[int, str] = {}
  for bid in batch_curves:
    batch_obj = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
    batch_names[bid] = batch_obj.name if batch_obj else f"Batch {bid}"

  result = hr.to_dict()
  result["batch_names"] = {str(k): v for k, v in batch_names.items()}
  result["batch_ids"] = [int(bid) for bid in batch_curves.keys()]
  return result


class SmartGroupRequest(BaseModel):
  """Auto-detect strategy families and suggest L2 groups."""
  batch_ids: list[int]
  date_from: str | None = None
  date_to: str | None = None       # IS boundary — OOS is NEVER used
  clip_is: bool = True
  family_threshold: float = 0.65   # correlation above this = same family
  max_groups: int = 6              # max L2 groups to create
  rolling_window: int = 75         # rolling correlation window (trading days, ~3mo)
  use_deflated: bool = True        # deflate IS Sharpe for greedy selection
  base_haircut: float = 0.35       # IS→OOS haircut (0.35 = keep 65%)
  # IS asset filters — same as optimizer uses, so batch curves reflect real pool
  min_pf: float = 0.0
  min_wr: float = 0.0
  min_trades: int = 0
  cv_folds: int = 5               # cross-validation folds (0 = skip)
  # V2: IS validation holdout (Enhancement 2)
  holdout_ratio: float = 0.0      # fraction of IS to hold out (0 = disabled)
  holdout_weight: float = 0.4     # holdout marginal weight
  # V2: Bootstrap stability (Enhancement 3)
  bootstrap_n: int = 100           # bootstrap iterations
  skip_bootstrap: bool = True      # skip by default (compute-heavy)
  bootstrap_drop_pct: float = 0.15 # fraction of trades to drop per iteration
  # V2: Regime-weighted family detection (Enhancement 4)
  regime_ref_days: int = 0         # 0 = disabled (use median), 30 = last 30 IS days
  regime_bandwidth: float = 0.0    # 0 = auto (1 std), >0 = manual bandwidth
  # V2: Trend direction tilt (Enhancement 5)
  trend_strength: str = "none"     # "none"/"small"/"medium"/"high"/"extreme"
  trend_lookback: int = 20         # last N ranges to examine
  trend_regime_pct: float = 0.65   # % threshold to declare bull/bear regime


@router.post("/smart-groups")
def smart_groups(req: SmartGroupRequest, db=Depends(get_db)):
  """Auto-detect batch families and suggest L2 grouping.

  Uses ONLY IS-period data (never OOS).  Clusters batches by daily PnL
  correlation, picks the strongest representative per family (by PF, WR,
  grade, trade count), and returns suggested L2 groups.
  """
  if len(req.batch_ids) < 2:
    raise HTTPException(400, "Need at least 2 batches for smart grouping")

  from backend.analysis.harmony import build_batch_curves
  from backend.analysis.smart_groups import build_smart_groups, BatchInfo, _compute_trend_regime

  # Build IS curves
  daily_pnl, trades, run_batch_map = _build_cross_daily_pnl(
    req.batch_ids, db, req.date_from, req.date_to, clip_boundary=req.clip_is,
  )
  if daily_pnl.empty:
    raise HTTPException(400, "Empty PnL matrix — no trades found")

  # Build asset → batch map
  asset_batch: dict[str, int] = {}
  for t in trades:
    bid = run_batch_map.get(t.run_id, 0)
    key = _trade_key(t, run_batch_map)
    if key in daily_pnl.columns:
      asset_batch[key] = bid

  # Build batch-level curves
  batch_curves = build_batch_curves(daily_pnl, asset_batch)
  if len(batch_curves) < 2:
    raise HTTPException(400, "Need at least 2 batches with trades")

  # Build per-asset IS metrics (PF, WR, trade count) for IS filtering
  # Same logic as _filter_daily_pnl but we collect the metrics instead of filtering
  from collections import defaultdict
  pos_pnl: dict[str, dict[str, float]] = defaultdict(dict)
  for t in trades:
    pid = t.position_id if t.position_id else f"_solo_{t.id}"
    key = _trade_key(t, run_batch_map)
    if pid not in pos_pnl[key]:
      pos_pnl[key][pid] = 0.0
    pos_pnl[key][pid] += (t.net_profit or 0.0)

  asset_metrics: dict[str, dict] = {}
  for sym in daily_pnl.columns:
    positions = pos_pnl.get(sym, {})
    count = len(positions)
    if count == 0:
      asset_metrics[sym] = {"pf": 0.0, "wr": 0.0, "trades": 0}
      continue
    wins = sum(1 for pnl in positions.values() if pnl > 0)
    gp = sum(pnl for pnl in positions.values() if pnl > 0)
    gl = sum(abs(pnl) for pnl in positions.values() if pnl <= 0)
    pf = min(gp / gl, 10.0) if gl > 0 else 10.0
    wr = (wins / count * 100) if count > 0 else 0
    asset_metrics[sym] = {"pf": pf, "wr": wr, "trades": count}

  # Build batch info from DB (quality metrics from aggregate_metrics JSON)
  batch_infos: dict[int, BatchInfo] = {}
  for bid in batch_curves:
    batch_obj = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
    info = BatchInfo(batch_id=bid, name=batch_obj.name if batch_obj else f"Batch {bid}")
    if batch_obj and batch_obj.aggregate_metrics:
      try:
        agg = json.loads(batch_obj.aggregate_metrics)
        info.avg_profit_factor = agg.get("avg_profit_factor") or 0.0
        info.avg_win_rate = agg.get("avg_win_rate") or 0.0
        info.total_net_profit = agg.get("total_net_profit") or 0.0
        info.batch_grade = agg.get("batch_grade") or ""
        info.total_trades = agg.get("total_trades_all") or 0
        syms = agg.get("symbols_with_trades") or 0
        info.avg_trades_per_symbol = info.total_trades / max(syms, 1)
        if info.total_net_profit < 0 and info.batch_grade:
          info.batch_grade = "D-"
      except Exception as _exc:
        logger.debug("swallowed: %s", _exc)
    batch_infos[bid] = info

  # V2: Build trades_by_batch for bootstrap (Enhancement 3)
  trades_by_batch: dict[int, list[dict]] | None = None
  if not req.skip_bootstrap:
    from collections import defaultdict as _defaultdict
    _tbb: dict[int, list[dict]] = _defaultdict(list)
    for t in trades:
      bid = run_batch_map.get(t.run_id, 0)
      if bid == 0:
        continue
      key = _trade_key(t, run_batch_map)
      if key not in daily_pnl.columns:
        continue
      _tbb[bid].append({
        "date": str(t.exit_time.date()) if t.exit_time else str(t.entry_time.date()),
        "pnl": float(t.net_profit or 0.0),
        "asset_key": key,
      })
    trades_by_batch = dict(_tbb)

  # V2: Compute trend regime data from OHLCV close prices (Enhancement 5)
  trend_regime_info: dict | None = None
  if req.trend_strength != "none":
    import numpy as np
    from itertools import groupby as _groupby
    from backend.db.database import ohlcv_router
    from backend.utils.tf_resolver import canonical_tf as _canonical_tf
    from sqlalchemy import text

    trend_data: dict[str, dict] = {}

    # Collect unique base symbols + timeframes across all batches
    batch_tfs: dict[int, str] = {}
    for bid in batch_curves:
      batch_obj = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
      batch_tfs[bid] = _canonical_tf(batch_obj.timeframe) if batch_obj else "D"

    # Get unique symbols from daily_pnl columns (strip batch/side suffixes)
    unique_syms: set[str] = set()
    for col in daily_pnl.columns:
      # Column format: SYMBOL~B{bid}_Side — extract base symbol
      base = col.split("~")[0] if "~" in col else col
      base = base.replace("_Long", "").replace("_Short", "")
      unique_syms.add(base)

    # Use the most common timeframe for OHLCV queries
    tf_counts: dict[str, int] = {}
    for tf in batch_tfs.values():
      tf_counts[tf] = tf_counts.get(tf, 0) + 1
    primary_tf = max(tf_counts, key=lambda t: tf_counts[t]) if tf_counts else "D"

    cutoff_iso = (datetime.strptime(req.date_to, "%Y-%m-%d") + timedelta(days=1)).isoformat() if req.date_to else None
    ohlcv_engine = ohlcv_router.get_engine(primary_tf)

    with ohlcv_engine.connect() as conn:
      for sym in unique_syms:
        query_params = {"sym": sym, "tf": primary_tf}
        sql = "SELECT close FROM ohlcv_data WHERE symbol = :sym AND timeframe = :tf"
        if cutoff_iso:
          sql += " AND timestamp < :cutoff"
          query_params["cutoff"] = cutoff_iso
        sql += " ORDER BY timestamp"
        rows = conn.execute(text(sql), query_params).fetchall()
        if len(rows) < 20:
          continue

        closes = [r[0] for r in rows]
        directions = [1 if closes[i] > closes[i - 1] else -1
                      for i in range(1, len(closes))]

        # Build streak runs
        bull_runs: list[int] = []
        bear_runs: list[int] = []
        all_streaks: list[tuple[str, int]] = []  # (direction, length) newest first
        for d, g in _groupby(directions):
          run_len = sum(1 for _ in g)
          streak_dir = "bull" if d == 1 else "bear"
          all_streaks.append((streak_dir, run_len))
          if d == 1:
            bull_runs.append(run_len)
          else:
            bear_runs.append(run_len)

        # Reverse so newest is first
        all_streaks.reverse()
        avg_bull = float(np.mean(bull_runs)) if bull_runs else 0.0
        avg_bear = float(np.mean(bear_runs)) if bear_runs else 0.0

        # Current streak = the first in reversed list (most recent)
        current_dir = all_streaks[0][0] if all_streaks else "bull"
        current_len = all_streaks[0][1] if all_streaks else 0

        trend_data[sym] = {
          "recent_streaks": all_streaks[:req.trend_lookback],
          "avg_bull_len": avg_bull,
          "avg_bear_len": avg_bear,
          "current_dir": current_dir,
          "current_len": current_len,
        }

    # Compute regime from aggregated trend data
    if trend_data:
      trend_regime_info = _compute_trend_regime(
        trend_data,
        lookback=req.trend_lookback,
        regime_pct=req.trend_regime_pct,
      )
      logger.info("Smart groups V2: trend regime=%s (bull=%.1f%%, bear=%.1f%%, streak_ratio=%.2f)",
                   trend_regime_info.get("regime"), trend_regime_info.get("bull_pct", 0) * 100,
                   trend_regime_info.get("bear_pct", 0) * 100, trend_regime_info.get("streak_ratio", 1.0))

  result = build_smart_groups(
    batch_curves, batch_infos,
    asset_pnl=daily_pnl,
    asset_batch=asset_batch,
    asset_metrics=asset_metrics,
    min_pf=req.min_pf,
    min_wr=req.min_wr,
    min_trades=req.min_trades,
    family_threshold=req.family_threshold,
    max_groups=req.max_groups,
    rolling_window=req.rolling_window,
    use_deflated=req.use_deflated,
    base_haircut=req.base_haircut,
    cv_folds=req.cv_folds,
    # V2 enhancements
    holdout_ratio=req.holdout_ratio,
    holdout_weight=req.holdout_weight,
    skip_bootstrap=req.skip_bootstrap,
    bootstrap_n=req.bootstrap_n,
    bootstrap_drop_pct=req.bootstrap_drop_pct,
    trades_by_batch=trades_by_batch,
    regime_ref_days=req.regime_ref_days,
    regime_bandwidth=req.regime_bandwidth,
    trend_strength=req.trend_strength,
    trend_regime_info=trend_regime_info,
  )

  # Enrich with batch names
  batch_names = {str(bid): batch_infos[bid].name for bid in batch_infos}
  d = result.to_dict()
  d["batch_names"] = batch_names
  return d


class AffinityReportRequest(BaseModel):
  """Generate a full Batch Affinity Report post-optimization."""
  batch_ids: list[int]
  portfolio_ids: list[int] = []
  date_from: str | None = None
  date_to: str | None = None         # IS/OOS boundary
  clip_is: bool = True
  # OOS validation settings
  include_oos: bool = False           # whether to run OOS1 validation
  oos1_months: int = 6                # total OOS1 length
  oos2_months: int = 3                # OOS2 length (subtracted from end)
  # Optional custom metric weights
  weights: dict[str, float] | None = None
  # Pinned anchors — user-chosen "must-keep" batches
  pinned_anchors: list[int] | None = None


@router.post("/affinity-report")
def affinity_report(req: AffinityReportRequest, db=Depends(get_db)):
  """Generate a comprehensive Batch Affinity Report.

  Computes 7 pairwise metrics for all batches, classifies roles
  (anchor/complement/redundant/outlier), finds clusters, builds
  per-metric leaderboards, and optionally validates against OOS1
  (with OOS2 window excluded).
  """
  if len(req.batch_ids) + len(req.portfolio_ids) < 2:
    raise HTTPException(400, "Need at least 2 batches for affinity report")

  from backend.analysis.harmony import build_batch_curves
  from backend.analysis.affinity_report import generate_affinity_report
  from dateutil.relativedelta import relativedelta

  # ── Build IS curves ──────────────────────────────────────────────
  daily_pnl, trades, run_batch_map = _build_cross_daily_pnl(
    req.batch_ids, db, req.date_from, req.date_to, clip_boundary=req.clip_is,
  )

  # Also load portfolio sources if any
  p_asset_batch: dict[str, int] = {}
  if req.portfolio_ids:
    pt, p_ab, _p_sm, _p_as, p_rbm = _load_portfolio_trades(
      req.portfolio_ids, db, req.date_from, req.date_to,
      tagged=True, clip_boundary=req.clip_is,
    )
    if pt:
      p_pnl = build_daily_pnl_matrix(pt, include_side=True)
      if not p_pnl.empty:
        for col in p_pnl.columns:
          for pkey, pbid in p_ab.items():
            if pkey in col:
              p_asset_batch[col] = pbid
        daily_pnl = pd.concat([daily_pnl, p_pnl], axis=1).fillna(0)
        run_batch_map.update(p_rbm)

  if daily_pnl.empty:
    raise HTTPException(400, "Empty PnL matrix — no trades found")

  # Build asset → batch map
  asset_batch: dict[str, int] = {}
  for t in trades:
    bid = run_batch_map.get(t.run_id, 0)
    key = _trade_key(t, run_batch_map)
    if key in daily_pnl.columns:
      asset_batch[key] = bid
  asset_batch.update(p_asset_batch)

  # Build IS batch-level curves
  is_curves = build_batch_curves(daily_pnl, asset_batch)
  if len(is_curves) < 2:
    raise HTTPException(400, "Need at least 2 batches with trades")

  # ── Build OOS1 curves (optional) ────────────────────────────────
  oos_curves = None
  if req.include_oos and req.date_to:
    try:
      from datetime import datetime as _dt
      oos_boundary = _dt.strptime(req.date_to, "%Y-%m-%d").date()
      # OOS1 report window: shift back by oos2_months so we never touch OOS2
      # Report OOS1 start = date_to - oos2_months
      # Report OOS1 end   = date_to + (oos1_months - oos2_months)
      oos_report_start = oos_boundary - relativedelta(months=req.oos2_months)
      oos_report_end = oos_boundary + relativedelta(months=req.oos1_months - req.oos2_months)
      logger.info(
        "Affinity report OOS window: %s to %s (OOS2 from %s untouched)",
        oos_report_start, oos_report_end,
        oos_boundary + relativedelta(months=req.oos1_months - req.oos2_months),
      )

      # Load OOS data with the shifted window
      oos_daily_pnl, oos_trades, oos_rbm = _build_cross_daily_pnl(
        req.batch_ids, db,
        date_from=str(oos_report_start),
        date_to=str(oos_report_end),
        clip_boundary=True,
      )

      if req.portfolio_ids:
        pt2, p_ab2, _, _, p_rbm2 = _load_portfolio_trades(
          req.portfolio_ids, db,
          date_from=str(oos_report_start),
          date_to=str(oos_report_end),
          tagged=True, clip_boundary=True,
        )
        if pt2:
          p_pnl2 = build_daily_pnl_matrix(pt2, include_side=True)
          if not p_pnl2.empty:
            p_ab2_map = {}
            for col in p_pnl2.columns:
              for pkey, pbid in p_ab2.items():
                if pkey in col:
                  p_ab2_map[col] = pbid
            oos_daily_pnl = pd.concat([oos_daily_pnl, p_pnl2], axis=1).fillna(0)
            oos_rbm.update(p_rbm2)

      if not oos_daily_pnl.empty:
        oos_ab: dict[str, int] = {}
        for t in oos_trades:
          bid = oos_rbm.get(t.run_id, 0)
          key = _trade_key(t, oos_rbm)
          if key in oos_daily_pnl.columns:
            oos_ab[key] = bid
        oos_ab.update(p_ab2_map if req.portfolio_ids else {})
        oos_curves = build_batch_curves(oos_daily_pnl, oos_ab)
        if len(oos_curves) < 2:
          oos_curves = None
          logger.warning("Affinity report: OOS data insufficient (< 2 batches), skipping validation")
    except Exception as e:
      logger.warning("Affinity report: OOS validation failed: %s", e)
      oos_curves = None

  # ── Build batch quality data ────────────────────────────────────
  from backend.analysis.affinity_report import BatchQuality, _compute_quality_score

  batch_quality: dict[int, BatchQuality] = {}
  batch_names: dict[int, str] = {}
  for bid in is_curves:
    batch_obj = db.query(BatchBacktestRun).filter(BatchBacktestRun.id == bid).first()
    batch_names[bid] = batch_obj.name if batch_obj else f"Batch {bid}"
    if batch_obj:
      # Quality metrics live inside aggregate_metrics JSON, not on ORM columns
      pf, wr, grade, net_pnl = 0.0, 0.0, "", 0.0
      if batch_obj.aggregate_metrics:
        try:
          agg = json.loads(batch_obj.aggregate_metrics)
          pf = agg.get("avg_profit_factor") or 0.0
          wr = agg.get("avg_win_rate") or 0.0
          grade = agg.get("batch_grade") or ""
          net_pnl = agg.get("total_net_profit") or 0.0
          # Force D- for negative net profit (same as _batch_to_dict)
          if net_pnl < 0 and grade:
            grade = "D-"
        except Exception as _exc:
          logger.debug("swallowed: %s", _exc)
      batch_quality[bid] = BatchQuality(
        batch_id=bid,
        avg_profit_factor=pf,
        avg_win_rate=wr,
        total_net_profit=net_pnl,
        batch_grade=grade,
        quality_score=_compute_quality_score(pf, wr, grade),
      )

  # ── Generate report ─────────────────────────────────────────────
  report = generate_affinity_report(
    is_curves, oos_curves, req.weights,
    pinned_anchors=req.pinned_anchors,
    batch_quality=batch_quality,
  )

  result = report.to_dict()
  result["batch_names"] = {str(k): v for k, v in batch_names.items()}
  result["batch_ids"] = [int(bid) for bid in is_curves.keys()]
  if req.include_oos and req.date_to:
    result["oos_window"] = {
      "start": str(oos_report_start) if oos_curves else None,
      "end": str(oos_report_end) if oos_curves else None,
      "oos2_excluded_from": str(oos_boundary + relativedelta(months=req.oos1_months - req.oos2_months)) if oos_curves else None,
    }
  return result
