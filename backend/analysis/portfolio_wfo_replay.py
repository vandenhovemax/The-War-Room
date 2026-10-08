"""Walk-forward replay loader for saved WFAS portfolios.

When a WFAS run is saved as a tracked portfolio, the portfolio's
"truth" is the exact rotation Phase 2 produced — not the final-window
basket applied to full history.

This module rebuilds the trade DataFrame from the stored rotation
metadata so every downstream view (monthly grid, monthly modal, full
metrics panel, metrics_snapshot) matches the optimizer exactly.

Two modes:
  * Hybrid (step2_result.selected_records): one record per selected
    position with (run_id, dt, symbol, side, scaled_pnl). Replay looks
    each position up in BacktestTrade for display fields but uses the
    record's PnL as the truth (already carries risk profile, exec
    filter, and pnl_multiplier).
  * Non-hybrid (wfo_per_window + wfo_windows_meta): window baskets.
    Keep only trades whose (batch, symbol, side) is in a window's
    basket AND whose entry_time falls in that window's OOS range.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime

import pandas as pd


_ASSET_RE = re.compile(r"^([A-Z0-9]+)~B(\d+)_(\w+)$")


def _record_key(rec: dict) -> tuple:
  """Natural key for a step2 selected_record: (dt, run_id, symbol, side).
  All four together uniquely identify a single position-fill record across
  save-time and refresh-time blobs. Lowercased side so 'Long' and 'long'
  collapse to one entry."""
  return (
    str(rec.get("dt") or ""),
    rec.get("run_id"),
    str(rec.get("symbol") or ""),
    str(rec.get("side") or "").lower(),
  )


def get_effective_step2(settings: dict) -> dict:
  """Return a step2_result-shaped dict that merges save-time and
  refresh-time records into one logical view.

  Background: pre-A3, nightly Y-B refresh wholesale-overwrote
  `source_strategies.step2_result`. A3 split that field into
    * `step2_result`           — the save-time multi-year matrix
    * `refresh_step2_result`   — every nightly tick replaces it whole
  so the save-time history survives. Downstream consumers want a single
  dict that looks like `step2_result` with all records visible.

  Merge rules:
    * `selected_records` — UNION of both lists, deduped on
      `_record_key(rec)`. On collision, the refresh-time record wins
      (it was produced more recently and may carry corrected PnL).
      With the anchored walk (2026-06-10) the two record sets never
      overlap in time: save records end at the committed boundary, the
      walk owns everything after it.
    * `aggregate_monthly_matrix` / `aggregate_equity_curve` /
      `aggregate_breakdown` / `cumulative_metrics` — REBUILT from the
      merged records (pure roll-up math). The old per-blob union broke
      at the anchor boundary: a month split across save and refresh
      (e.g. anchor on Apr 19) had its save-side PnL silently
      overwritten by the refresh blob's partial-month value. Building
      from the merged records makes every roll-up correct by
      construction and gives every tab the same numbers.
    * `latest_asset_scores` / `latest_portfolio_at_end` — refresh-time
      wins if present, otherwise the save-time value is preserved.

  Returns an empty dict when neither blob has any selected_records.
  Always shape-compatible with raw `step2_result` so callers can drop
  it in without branching.
  """
  if not isinstance(settings, dict):
    return {}
  save = settings.get("step2_result") or {}
  refresh = settings.get("refresh_step2_result") or {}
  if not isinstance(save, dict):
    save = {}
  if not isinstance(refresh, dict):
    refresh = {}

  save_recs = save.get("selected_records") or []
  refresh_recs = refresh.get("selected_records") or []
  if not save_recs and not refresh_recs:
    return {}

  # Union with refresh-wins-on-collision. Iterate save first then refresh
  # so the refresh entries overwrite same-key save entries in the dict.
  by_key: dict[tuple, dict] = {}
  for r in save_recs:
    if isinstance(r, dict):
      by_key[_record_key(r)] = r
  for r in refresh_recs:
    if isinstance(r, dict):
      by_key[_record_key(r)] = r
  merged_records = list(by_key.values())
  merged_records.sort(key=lambda r: (str(r.get("dt") or ""), r.get("run_id") or 0))

  result: dict = {"selected_records": merged_records}
  result.update(_rebuild_aggregates(merged_records, save.get("cumulative_metrics")))

  for k in ("latest_asset_scores", "latest_portfolio_at_end"):
    v = refresh.get(k)
    if v is None:
      v = save.get(k)
    if v is not None:
      result[k] = v
  return result


def _rebuild_aggregates(records: list[dict], base_cumulative: dict | None) -> dict:
  """Roll the merged selected_records up into the aggregate display blobs.

  Mirrors step2_service's aggregate math (entry-date bucketing, equity by
  record order, per-asset breakdown). `base_cumulative` (the save-time
  cumulative_metrics, if any) supplies ratio metrics we can't cheaply
  rebuild here (sortino/calmar/recovery); the count/PnL fields are always
  overwritten from the merged records.
  """
  from collections import defaultdict as _dd

  if not records:
    return {}

  recs = sorted(records, key=lambda r: str(r.get("dt") or ""))
  equity: list[dict] = []
  daily_pnl: dict[str, float] = _dd(float)
  cum = 0.0
  peak = 0.0
  max_dd = 0.0
  pnls: list[float] = []
  for r in recs:
    p = float(r.get("pnl", 0) or 0)
    pnls.append(p)
    cum += p
    peak = max(peak, cum)
    max_dd = max(max_dd, peak - cum)
    dd_pct = (cum - peak) / peak * 100 if peak > 0 else 0.0
    ds = str(r.get("dt", ""))[:10]
    equity.append({"date": ds, "equity": round(cum, 2), "drawdown_pct": round(dd_pct, 2)})
    daily_pnl[ds] += p

  month_pnl: dict[str, dict[str, float]] = _dd(lambda: _dd(float))
  for ds, p in daily_pnl.items():
    if len(ds) >= 7:
      month_pnl[ds[:4]][str(int(ds[5:7]))] += p
  years = sorted(month_pnl.keys())
  matrix = {y: {m: round(v, 2) for m, v in month_pnl[y].items()} for y in years}
  row_totals = {y: round(sum(month_pnl[y].values()), 2) for y in years}
  col_totals: dict[str, float] = _dd(float)
  for y in years:
    for m, v in month_pnl[y].items():
      col_totals[m] += v
  monthly = {
    "years": years, "matrix": matrix,
    "row_totals": row_totals,
    "col_totals": {m: round(v, 2) for m, v in col_totals.items()},
    "grand_total": round(sum(row_totals.values()), 2),
  }

  bd: dict[str, dict] = {}
  for r in recs:
    k = r.get("asset") or ""
    p = float(r.get("pnl", 0) or 0)
    slot = bd.setdefault(k, {
      "key": k, "batch_id": 0,
      "symbol": r.get("symbol", ""), "side": r.get("side", ""),
      "timeframe": r.get("timeframe", ""), "group": "",
      "oos_pnl": 0.0, "oos_trades": 0, "oos_wins": 0,
      "oos_gp": 0.0, "oos_gl": 0.0,
      "is_pnl": 0.0, "is_trades": 0, "is_wr": 0, "is_pf": 0,
    })
    slot["oos_pnl"] += p
    slot["oos_trades"] += 1
    if p > 0:
      slot["oos_wins"] += 1
      slot["oos_gp"] += p
    else:
      slot["oos_gl"] += abs(p)
  for a in bd.values():
    t = a["oos_trades"]
    a["oos_wr"] = (a["oos_wins"] / t) if t else 0
    a["oos_pf"] = min(a["oos_gp"] / a["oos_gl"], 10) if a["oos_gl"] > 0 else (10 if a["oos_gp"] > 0 else 0)
    a["oos_pnl"] = round(a["oos_pnl"], 2)
  breakdown = sorted(bd.values(), key=lambda x: x["oos_pnl"], reverse=True)

  gp = sum(p for p in pnls if p > 0)
  gl = abs(sum(p for p in pnls if p < 0))
  wins = sum(1 for p in pnls if p > 0)
  n = len(pnls)
  cumulative = dict(base_cumulative) if isinstance(base_cumulative, dict) else {}
  cumulative.update({
    "net_profit": round(sum(pnls), 2),
    "profit_factor": round(min(gp / gl, 10), 4) if gl > 0 else (10 if gp > 0 else 0),
    "win_rate": round(wins / n, 4) if n else 0,
    "total_trades": n,
    "total_positions": n,
    "max_drawdown": round(max_dd, 2),
    "cumulative_max_dd": round(max_dd, 2),
    "avg_trade": round(sum(pnls) / n, 2) if n else 0,
    "gross_profit": round(gp, 2),
    "gross_loss": round(gl, 2),
    "n_wins": wins,
    "n_losses": n - wins,
    "avg_win": round(gp / wins, 2) if wins > 0 else 0,
    "avg_loss": round(gl / (n - wins), 2) if (n - wins) > 0 else 0,
    "payoff_ratio": round((gp / wins) / (gl / (n - wins)), 4)
    if wins > 0 and (n - wins) > 0 and gl > 0 else 0,
  })

  return {
    "aggregate_monthly_matrix": monthly,
    "aggregate_equity_curve": equity,
    "aggregate_breakdown": breakdown,
    "cumulative_metrics": cumulative,
  }


def has_wfo_replay_metadata(settings: dict) -> bool:
  if not isinstance(settings, dict):
    return False
  effective = get_effective_step2(settings)
  if effective.get("selected_records"):
    return True
  return bool(settings.get("wfo_per_window") and settings.get("wfo_windows_meta"))


def get_wfas_forward_cutoff(settings: dict):
  """End date of the last *committed* OOS rotation window.

  Trades with entry_time strictly after this date are forward-period
  (not yet rotated, effectively "live" — the current basket is trading
  them). Returns a datetime.date or None.

  Resolution: take the **latest** date across all signals.
    1. max(`wfo_windows_meta[*].oos_end`) — the historical optimizer
       rolling-window boundary.
    2. max(`dt`) across the merged effective `selected_records`
       (save-time UNION refresh-time — A3 split lets the post-save
       nightly refreshes advance the cutoff past the original horizon).

  Why max of all sources, not a cross-wired lookup: pre-T-002 this
  function used `wfo_per_window[-1].window_num` as a key into
  `wfo_windows_meta`. But on hybrid portfolios those two structures
  use DIFFERENT numbering schemes — joining them landed on the FIRST
  historical window (years in the past) instead of the latest. The
  fix is to ignore `wfo_per_window` for cutoff purposes (it tracks
  refresh continuation state, not the OOS commit boundary) and just
  take the max of the boundary signals that actually mean what we
  need.

  Returns None when neither signal yields a valid date — callers
  must treat this as "no committed forward boundary; show nothing
  forward-period".
  """
  if not isinstance(settings, dict):
    return None

  candidates: list[date] = []

  step2 = get_effective_step2(settings)
  selected_records = step2.get("selected_records") or []
  is_hybrid = bool(selected_records)

  windows_meta = settings.get("wfo_windows_meta") or []

  # T-006: For NON-HYBRID portfolios, the optimizer save flow appends a
  # trailing windows_meta entry (a "live trailing" stub for future
  # phase-2) that has NO matching wfo_per_window entry. That stub is
  # semantically NOT a committed rotation — it's the next-window setup.
  # Including it here would shift forward_start past the committed
  # period and create a coverage gap (W_N.oos_end .. stub.oos_end) where
  # trades are invisible to BOTH _load_replay_non_hybrid (per_window
  # stops at W_N) AND load_wfas_forward_append (filters entry_time
  # >= forward_start). Gate meta entries by per_window membership for
  # non-hybrid; hybrid path is unchanged.
  perw_nums: set[int] | None = None
  if not is_hybrid:
    per_window = settings.get("wfo_per_window") or []
    if isinstance(per_window, list):
      perw_nums = set()
      for w in per_window:
        if not isinstance(w, dict):
          continue
        wn = w.get("window_num")
        if wn is None:
          continue
        try:
          perw_nums.add(int(wn))
        except (ValueError, TypeError):
          continue

  # For HYBRID portfolios, IGNORE windows_meta — selected_records (below) is
  # the authoritative signal for what's actually committed. windows_meta on a
  # hybrid portfolio includes a trailing stub whose oos_end is the save date,
  # which bumps the cutoff to today and creates a coverage gap (the period
  # between the last actual selected_records dt and today). This made
  # forward-period trades for portfolios saved today invisible to the UI.
  # Fix: for hybrid, skip windows_meta entirely. Non-hybrid path unchanged.
  if isinstance(windows_meta, list) and not is_hybrid:
    for meta in windows_meta:
      if not isinstance(meta, dict):
        continue
      if perw_nums is not None:
        wn = meta.get("window_num")
        if wn is None:
          continue
        try:
          if int(wn) not in perw_nums:
            continue
        except (ValueError, TypeError):
          continue
      raw = meta.get("oos_end")
      if not raw:
        continue
      try:
        candidates.append(datetime.strptime(str(raw)[:10], "%Y-%m-%d").date())
      except (ValueError, TypeError):
        continue

  for r in selected_records:
    if not isinstance(r, dict):
      continue
    raw = r.get("dt")
    if not raw:
      continue
    try:
      candidates.append(datetime.strptime(str(raw)[:10], "%Y-%m-%d").date())
    except (ValueError, TypeError):
      continue

  return max(candidates) if candidates else None


def _parse_window_asset(token: str):
  m = _ASSET_RE.match(token)
  if not m:
    return None
  return int(m.group(2)), m.group(1), m.group(3).lower()


def _merge_fills_to_row(fills: list, record_pnl: float | None, batch_id: int | None):
  """Shape matches tv_data_loader.trades_from_db so downstream code is uniform."""
  if not fills:
    return None
  first = fills[0]
  side = (first.side or "").capitalize() or "Unknown"
  sym = first.symbol or "Unknown"
  asset_with_side = (
    f"{sym}_{side}" if side in ("Long", "Short") and not sym.endswith(f"_{side}") else sym
  )

  db_net_profit = sum(float(f.net_profit or 0) for f in fills)
  net_profit_pct = sum(float(f.net_profit_pct or 0) for f in fills)
  commission = sum(float(f.commission or 0) for f in fills)
  quantity = sum(float(getattr(f, "quantity", 0) or 0) for f in fills)
  mae = sum(float(f.mae or 0) for f in fills)
  mfe = sum(float(f.mfe or 0) for f in fills)

  exit_times = [f.exit_time for f in fills if f.exit_time]
  exit_time = max(exit_times) if exit_times else None

  reasons = [getattr(f, "exit_reason", None) for f in fills]
  reasons = [r for r in reasons if r]
  exit_reason = "+".join(reasons) if reasons else ""

  exit_prices = [
    (float(f.exit_price), abs(float(f.net_profit or 0)) or 1.0)
    for f in fills if f.exit_price
  ]
  if exit_prices:
    total_w = sum(w for _, w in exit_prices)
    avg_exit = sum(p * w for p, w in exit_prices) / total_w if total_w else exit_prices[0][0]
  else:
    avg_exit = None

  net_profit = float(record_pnl) if record_pnl is not None else db_net_profit

  return {
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
    "_batch_id": batch_id,
    "_run_id": getattr(first, "run_id", None),
  }


def _load_replay_hybrid(records: list, db) -> pd.DataFrame:
  from backend.db.models import BacktestTrade

  if not records:
    return pd.DataFrame()
  run_ids = {int(r["run_id"]) for r in records if r.get("run_id") is not None}
  if not run_ids:
    return pd.DataFrame()

  # Positive run_ids → BacktestTrade rows. Negative run_ids are the
  # trade_log_unified sentinel for LIVE engine fills (run_id = -batch_id);
  # they have no BacktestTrade rows and are resolved separately below.
  pos_run_ids = {rid for rid in run_ids if rid > 0}
  fills = (
    db.query(BacktestTrade).filter(BacktestTrade.run_id.in_(pos_run_ids)).all()
    if pos_run_ids else []
  )
  groups: dict[tuple, list] = defaultdict(list)
  for t in fills:
    if t.entry_time is None:
      continue
    groups[(
      int(t.run_id),
      t.entry_time.isoformat()[:19],
      t.symbol or "",
      (t.side or "").lower(),
    )].append(t)

  # Live-fill lookup (2026-06-10 fix): records with negative run_id were
  # silently dropped pre-fix — the merged view simply never showed real
  # engine fills. Resolve them against `trades` rows, matching on
  # (batch_id, NYT entry time, symbol, side) — the same convention
  # trade_log_unified uses to mint the records.
  neg_bids = {-rid for rid in run_ids if rid < 0}
  live_by_key: dict[tuple, list] = defaultdict(list)
  if neg_bids:
    from backend.db.models import Trade
    from backend.analysis.trade_log_unified import _utc_to_nyt_naive

    live_rows = (
      db.query(Trade)
      .filter(Trade.batch_id.in_(neg_bids), Trade.is_test == False)  # noqa: E712
      .all()
    )
    for lt in live_rows:
      ent = _utc_to_nyt_naive(lt.opened_at)
      if ent is None:
        continue
      live_by_key[(
        -int(lt.batch_id),
        ent.isoformat()[:19],
        lt.symbol or "",
        (lt.side or "").lower(),
      )].append(lt)

  rows = []
  for r in records:
    rid = r.get("run_id")
    if rid is None:
      continue
    key = (
      int(rid),
      (r.get("dt") or "")[:19],
      r.get("symbol") or "",
      (r.get("side") or "").lower(),
    )
    if int(rid) < 0:
      lt_list = live_by_key.get(key)
      if not lt_list:
        continue
      row = _live_trade_to_row(lt_list[0], r)
    else:
      fills_list = groups.get(key)
      if not fills_list:
        continue
      row = _merge_fills_to_row(fills_list, r.get("pnl"), r.get("batch_id"))
    if row is not None:
      rows.append(row)
  return pd.DataFrame(rows) if rows else pd.DataFrame()


def _live_trade_to_row(lt, record: dict) -> dict:
  """Shape a live `Trade` row like _merge_fills_to_row's output so the
  merged display DataFrame is uniform across backtest and live fills."""
  from backend.analysis.trade_log_unified import _utc_to_nyt_naive

  side = (lt.side or "").capitalize() or "Unknown"
  sym = lt.symbol or "Unknown"
  asset_with_side = (
    f"{sym}_{side}" if side in ("Long", "Short") and not sym.endswith(f"_{side}") else sym
  )
  ent = _utc_to_nyt_naive(lt.opened_at)
  ext = _utc_to_nyt_naive(lt.closed_at)
  pnl = record.get("pnl")
  net_profit = float(pnl) if pnl is not None else float(lt.realized_pnl or 0)
  entry_px = float(lt.entry_price or 0)
  qty = float(lt.quantity or 0)
  return {
    "Asset": asset_with_side,
    "Side": side,
    "Entry Time": pd.Timestamp(ent) if ent else pd.NaT,
    "Exit Time": pd.Timestamp(ext) if ext else pd.NaT,
    "Net Profit": net_profit,
    "Net Profit %": (net_profit / (entry_px * qty) * 100) if entry_px and qty else 0.0,
    "Drawdown": 0.0,
    "Run-up": 0.0,
    "Commission": float(lt.commission or 0),
    "Quantity": qty,
    "Entry Price": entry_px,
    "Exit Price": float(lt.exit_price or 0),
    "Exit Reason": (lt.close_reason or "signal") + " (live)",
    "_batch_id": int(lt.batch_id) if lt.batch_id is not None else None,
    "_run_id": -int(lt.batch_id) if lt.batch_id is not None else None,
  }


def _build_pnl_multiplier_map(per_window: list, db) -> dict[int, float]:
  from backend.db.models import BatchBacktestRun, BatchGroup

  batch_ids: set[int] = set()
  for w in per_window:
    for a in w.get("assets") or []:
      p = _parse_window_asset(a)
      if p:
        batch_ids.add(p[0])
  if not batch_ids:
    return {}
  rows = (
    db.query(BatchBacktestRun.id, BatchGroup.pnl_multiplier)
    .join(BatchGroup, BatchBacktestRun.group_id == BatchGroup.id, isouter=True)
    .filter(BatchBacktestRun.id.in_(batch_ids))
    .all()
  )
  return {bid: float(mult) if mult is not None else 1.0 for bid, mult in rows}


def _load_replay_non_hybrid(
  per_window: list, windows_meta: list, db, settings: dict | None = None,
) -> pd.DataFrame:
  from backend.db.models import BacktestRun, BacktestTrade

  if not per_window or not windows_meta:
    return pd.DataFrame()
  meta_by_num = {int(m["window_num"]): m for m in windows_meta if "window_num" in m}
  windows = []
  all_batches: set[int] = set()
  for w in per_window:
    try:
      wnum = int(w["window_num"])
    except (KeyError, TypeError, ValueError):
      continue
    meta = meta_by_num.get(wnum)
    if not meta:
      continue
    try:
      oos_start = datetime.strptime(meta["oos_start"][:10], "%Y-%m-%d").date()
      oos_end = datetime.strptime(meta["oos_end"][:10], "%Y-%m-%d").date()
    except (KeyError, ValueError, TypeError):
      continue
    allowed = set()
    for a in w.get("assets") or []:
      p = _parse_window_asset(a)
      if p:
        allowed.add(p)
        all_batches.add(p[0])
    if allowed:
      windows.append((oos_start, oos_end, allowed))
  if not windows or not all_batches:
    return pd.DataFrame()

  runs = (
    db.query(BacktestRun.id, BacktestRun.batch_id)
    .filter(BacktestRun.batch_id.in_(all_batches), BacktestRun.status == "completed")
    .all()
  )
  run_batch_map = {r.id: r.batch_id for r in runs}
  if not run_batch_map:
    return pd.DataFrame()
  fills = db.query(BacktestTrade).filter(
    BacktestTrade.run_id.in_(list(run_batch_map.keys()))
  ).all()

  # Session filter (NYT hour/day) — mirrors load_wfas_forward_append so the
  # modal trade list agrees with the matrix. Required for legacy hybrid
  # portfolios that were saved with empty selected_records and fall through
  # to this non-hybrid path. entry_time is NYT-naive by convention.
  if settings and settings.get("session_filter_enabled") and \
      settings.get("session_filter_mode") == "fixed":
    excl_h = set(settings.get("session_filter_exclude_hours") or [])
    excl_d = set(settings.get("session_filter_exclude_days") or [])
    if excl_h or excl_d:
      fills = [
        t for t in fills
        if t.entry_time is not None
        and t.entry_time.hour not in excl_h
        and t.entry_time.weekday() not in excl_d
      ]
      if not fills:
        return pd.DataFrame()

  groups: dict[tuple, list] = defaultdict(list)
  for t in fills:
    if t.entry_time is None:
      continue
    groups[(
      int(t.run_id),
      t.entry_time.isoformat()[:19],
      t.symbol or "",
      (t.side or "").lower(),
    )].append(t)

  pm = _build_pnl_multiplier_map(per_window, db)
  rows = []
  for key, fills_list in groups.items():
    rid, _, sym, side_l = key
    bid = run_batch_map.get(rid)
    if bid is None:
      continue
    first = fills_list[0]
    et = first.entry_time
    if not et:
      continue
    et_date = et.date() if hasattr(et, "date") else datetime.fromisoformat(str(et)[:19]).date()
    matched = False
    for oos_start, oos_end, allowed in windows:
      if oos_start <= et_date <= oos_end and (bid, sym, side_l) in allowed:
        matched = True
        break
    if not matched:
      continue
    raw_pnl = sum(float(f.net_profit or 0) for f in fills_list)
    scaled_pnl = raw_pnl * float(pm.get(bid, 1.0))
    row = _merge_fills_to_row(fills_list, scaled_pnl, bid)
    if row is not None:
      rows.append(row)
  return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_wfas_replay(settings: dict, db) -> pd.DataFrame:
  """Top-level entrypoint. Returns the walk-forward replay trade DF.

  Returns an empty DataFrame when the portfolio has no WFAS metadata.
  """
  step2 = get_effective_step2(settings)
  recs = step2.get("selected_records")
  if recs:
    return _load_replay_hybrid(recs, db)
  per_window = settings.get("wfo_per_window") or []
  windows_meta = settings.get("wfo_windows_meta") or []
  if per_window and windows_meta:
    return _load_replay_non_hybrid(per_window, windows_meta, db, settings)
  return pd.DataFrame()


def _basket_key_to_tuple(key: str) -> tuple[int, str, str] | None:
  """Convert a `B{bid}_{SYMBOL}_{Side}` basket key to the
  `(bid, symbol, side_lowercase)` tuple format used to filter trades.

  Returns None for malformed or non-batch-prefixed keys (preserved as a
  defensive guard — pre-T-002 events sometimes had partial keys)."""
  parts = key.rsplit("_", 2)
  if len(parts) != 3 or not parts[0].startswith("B"):
    return None
  try:
    bid = int(parts[0][1:])
  except ValueError:
    return None
  return (bid, parts[1], parts[2].lower())


def load_wfas_forward_append(p, settings: dict, db) -> pd.DataFrame:
  """Forward-period trades, filtered through the basket as it was at
  each trade's entry_time — NOT the currently-active basket.

  T-002 fix (Bug: forward-period view shifts after nightly refresh).
  Before T-002 this read `PortfolioAsset.is_active` directly: when the
  nightly refresh flipped a row, every prior forward-period trade was
  retroactively re-filtered through the new basket, causing the live
  UI's forward window to shift overnight.

  The fix walks `LiveEvent` rows for the portfolio in chronological
  order (`iter_basket_segments`) and assigns each trade to the basket
  that was active at its `entry_time`. Each segment carries the basket
  from one phase-2 evaluation until the next — so an asset rotated out
  on day N still appears in the historical forward window for trades
  before day N.

  Fallback: when no recorder events exist (legacy portfolios that
  predate T-002), we fall back to the old `is_active` read so the UI
  doesn't go blank.

  PnLs carry each batch's pnl_multiplier, so appending this DF onto the
  committed replay keeps the whole matrix in one unit system.

  Returns an empty DataFrame when: no WFAS metadata, no committed
  boundary, no active basket, or no post-cutoff trades.
  """
  from datetime import timedelta

  from backend.db.models import (
    BacktestRun,
    BacktestTrade,
    BatchBacktestRun,
    BatchGroup,
    PortfolioAsset,
  )
  from backend.services.phase2_recorder import (
    iter_basket_segments,
    resolve_basket_at_t,
  )

  cutoff = get_wfas_forward_cutoff(settings)
  if cutoff is None:
    return pd.DataFrame()

  # Recorder-driven as-of-time segments. Empty list ⇒ legacy portfolio
  # with no T-002 events yet, fall back to is_active for back-compat.
  segments = iter_basket_segments(db, p.id)
  use_segments = bool(segments)

  # Currently-active basket — used for the legacy fallback AND to scope
  # which batch_ids we even need to load trades from. The universe
  # (batch_ids) is fixed across rotations (basket members rotate, the
  # batch universe doesn't), so PortfolioAsset rows give us the
  # complete batch_id set regardless of is_active state.
  pa_rows = (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == p.id)
    .all()
  )
  allowed_legacy: set[tuple[int, str, str]] = set()
  batch_ids: set[int] = set()
  for a in pa_rows:
    if a.batch_id is None or not a.symbol:
      continue
    bid = int(a.batch_id)
    batch_ids.add(bid)
    if a.is_active:
      sides = (
        [a.trade_side.lower()]
        if a.trade_side in ("Long", "Short")
        else ["long", "short"]
      )
      for s in sides:
        allowed_legacy.add((bid, a.symbol, s))
  if not batch_ids:
    return pd.DataFrame()
  if not use_segments and not allowed_legacy:
    return pd.DataFrame()

  # cutoff is inclusive end of the committed period → strictly-after = next day.
  forward_start = datetime.combine(cutoff + timedelta(days=1), datetime.min.time())

  runs = (
    db.query(BacktestRun.id, BacktestRun.batch_id)
    .filter(
      BacktestRun.batch_id.in_(batch_ids),
      BacktestRun.status == "completed",
    )
    .all()
  )
  run_batch_map = {r.id: int(r.batch_id) for r in runs}
  if not run_batch_map:
    return pd.DataFrame()

  fills = (
    db.query(BacktestTrade)
    .filter(
      BacktestTrade.run_id.in_(list(run_batch_map.keys())),
      BacktestTrade.entry_time >= forward_start,
    )
    .all()
  )
  if not fills:
    return pd.DataFrame()

  # Apply the portfolio's saved execution filter (NYT hour/day). The committed
  # hybrid replay is already filtered (Phase 2 only selected filter-passing
  # records); forward-period raw trades must be filtered here to match. Keys:
  # session_filter_enabled + session_filter_mode=="fixed" +
  # session_filter_exclude_hours/days. entry_time is NYT-naive by convention.
  if settings.get("session_filter_enabled") and \
      settings.get("session_filter_mode") == "fixed":
    excl_h = set(settings.get("session_filter_exclude_hours") or [])
    excl_d = set(settings.get("session_filter_exclude_days") or [])
    if excl_h or excl_d:
      fills = [
        t for t in fills
        if t.entry_time is not None
        and t.entry_time.hour not in excl_h
        and t.entry_time.weekday() not in excl_d
      ]
      if not fills:
        return pd.DataFrame()

  # Per-batch pnl_multiplier (matches the non-hybrid replay scaling).
  mult_rows = (
    db.query(BatchBacktestRun.id, BatchGroup.pnl_multiplier)
    .join(BatchGroup, BatchBacktestRun.group_id == BatchGroup.id, isouter=True)
    .filter(BatchBacktestRun.id.in_(batch_ids))
    .all()
  )
  pm = {bid: (float(mult) if mult is not None else 1.0) for bid, mult in mult_rows}

  # Group trades. Stash a representative entry_time per group so the
  # as-of-time basket lookup (segments) can resolve which basket was
  # active when the trade fired — fixes the UI shift bug.
  groups: dict[tuple, list] = defaultdict(list)
  group_first_entry: dict[tuple, datetime] = {}
  for t in fills:
    if t.entry_time is None:
      continue
    gkey = (
      int(t.run_id),
      t.entry_time.isoformat()[:19],
      t.symbol or "",
      (t.side or "").lower(),
    )
    groups[gkey].append(t)
    # First entry_time wins — within a group all fills share the same
    # second-resolution timestamp by construction (gkey includes :19s).
    group_first_entry.setdefault(gkey, t.entry_time)

  rows = []
  for gkey, fills_list in groups.items():
    rid, _, sym, side_l = gkey
    bid = run_batch_map.get(rid)
    if bid is None:
      continue
    # As-of-time gating (T-002): pick the basket that was active when
    # this trade entered, NOT today's is_active.
    if use_segments:
      entry_at = group_first_entry[gkey]
      basket_at_t = resolve_basket_at_t(segments, entry_at)
      if not basket_at_t:
        # Strict: empty basket-at-t means we have no positive evidence
        # that the live engine would have traded this asset at that time.
        # Pre-fix this path used `allowed_legacy` as a proxy, which
        # surfaced trades the engine could not have actually placed —
        # contradicting the user's "real overview of what the hybrid
        # did" requirement. Drop the trade. This keeps the trades-table
        # view consistent with paper_refresh's basket-at-t KPI.
        continue
      tup_set: set[tuple[int, str, str]] = set()
      for k in basket_at_t:
        tup = _basket_key_to_tuple(k)
        if tup is not None:
          tup_set.add(tup)
      if (bid, sym, side_l) not in tup_set:
        continue
    else:
      # Legacy fallback — pre-T-002 portfolios with no recorder events.
      if (bid, sym, side_l) not in allowed_legacy:
        continue
    raw_pnl = sum(float(f.net_profit or 0) for f in fills_list)
    scaled_pnl = raw_pnl * float(pm.get(bid, 1.0))
    row = _merge_fills_to_row(fills_list, scaled_pnl, bid)
    if row is not None:
      rows.append(row)
  return pd.DataFrame(rows) if rows else pd.DataFrame()


def get_portfolio_detail_dataframe(
  portfolio, settings: dict, db, *, include_forward: bool = True,
) -> pd.DataFrame | None:
  """Routing helper — single source of truth for hybrid WFAS detail DFs.

  T-003 Delta 4: every portfolio detail route (full-metrics-wfo,
  month-trades, monthly-filtered, assets, dd-status) routes its DataFrame
  through here so the same data backs every sub-view. New detail routes
  inherit the unification for free — they call this and don't reimplement
  the hybrid-vs-legacy branch.

  HYBRID-ONLY GATE. The helper fires only for portfolios that have
  hybrid WFAS metadata (`step2_result.selected_records` present in the
  merged save+refresh view via `get_effective_step2`). Non-hybrid
  portfolios — whether older non-hybrid WFAS (`wfo_per_window` +
  `wfo_windows_meta`) or truly non-WFAS — return `None` so the caller
  falls back to its legacy path. This matches ATLAS T-003 Section C:
  "For non-hybrid portfolios (mode != 'hybrid'), the existing path stays
  as-is. T-003 changes nothing for those."

  Returns:
    * `None` — not a hybrid WFAS portfolio. Caller MUST fall back to its
      legacy path. Returning None (not an empty DF) is the explicit
      signal to disambiguate "this isn't hybrid territory" from
      "hybrid path ran but returned zero rows".
    * `pd.DataFrame` (possibly empty) — hybrid WFAS metadata is present.
      The caller uses this DF directly and MUST NOT call the legacy
      stitcher (`_stitch_event_based_walkforward`).

  Hybrid composition (per T-003 work order Section C):
    df_committed = load_wfas_replay(settings, db)                 # pre-cutoff
    df_forward   = load_wfas_forward_append(p, settings, db)      # post-cutoff
    df = pd.concat([df_committed, df_forward], ignore_index=True)

  `load_wfas_replay` reads merged step2 (`get_effective_step2`) which
  unions save-time + nightly-refresh records. `load_wfas_forward_append`
  walks `LiveEvent` segments via `iter_basket_segments` to attribute each
  forward trade to the basket-at-its-entry-time (T-002 v3 fix).

  `include_forward=False` suppresses the post-cutoff append. Used by
  callers that explicitly want IS-only (committed-period) data, since
  forward trades are post-IS by construction.
  """
  step2 = get_effective_step2(settings)
  if not step2.get("selected_records"):
    return None
  df = load_wfas_replay(settings, db)
  if include_forward:
    fwd = load_wfas_forward_append(portfolio, settings, db)
    if not fwd.empty:
      df = pd.concat([df, fwd], ignore_index=True) if not df.empty else fwd
  return df


def rescale_df_to_user_risk(
  df: pd.DataFrame,
  risk_profile: dict | None,
  batch_risk_amount: float = 100.0,
) -> tuple[pd.DataFrame, bool]:
  """Rescale DB PnLs (base: batch_risk_amount × pnl_multiplier) to user-defined risk.

  Returns (df, supported) — `supported=False` means the risk mode is not
  yet implemented and the DF is returned unchanged; the frontend surfaces
  this as a disabled/"not adjusted" hint.

  Supported modes (scalar PnL rescale):
    * fixed_$ / fixed      → scale = user_$ / batch_risk_amount
    * pct / pct_equity     → scale = (initial_capital × pct% ) / batch_risk_amount
                              (flat snapshot; does NOT compound)

  NOT YET ADJUSTED (returns df unchanged, supported=False):
    * martingale / anti_martingale / inv_martingale / inv_anti_martingale
      → depends on streak sequence, not a scalar.
    * kelly → depends on per-trade capital + dynamic Kelly fraction.
  """
  if df is None or df.empty or not risk_profile:
    return df, True
  base = float(batch_risk_amount) if batch_risk_amount and batch_risk_amount > 0 else 100.0
  mode = str(risk_profile.get("mode", "fixed")).lower()
  if mode in (
    "martingale", "anti_martingale",
    "inv_martingale", "inv_anti_martingale",
    "kelly",
  ):
    return df, False
  if mode in ("fixed", "fixed_$"):
    user_usd = float(risk_profile.get("initial_risk_usd", base))
    scale = user_usd / base
  elif mode in ("pct", "pct_equity"):
    initial_capital = float(risk_profile.get("initial_capital", 10000.0))
    pct = float(
      risk_profile.get("initial_risk_pct",
        risk_profile.get("pct", 1.0))
    )
    user_usd = initial_capital * pct / 100.0
    scale = user_usd / base
  else:
    return df, True  # Unknown mode — leave as-is, treat as supported no-op.
  if scale == 1.0:
    return df, True
  out = df.copy()
  if "Net Profit" in out.columns:
    out["Net Profit"] = out["Net Profit"] * scale
  return out, True


def resolve_risk_context(
  settings: dict, batch_ids: list[int], db,
) -> tuple[dict, float]:
  """Return (effective_risk_profile, batch_risk_amount) for the rescale toggle.

  1. If settings.risk_profile is saved (Live Settings tab), use it.
  2. Otherwise fall back to a profile built from the global .env settings
     (RISK_MODE / RISK_PER_TRADE_USD / RISK_PER_TRADE_PCT) — this is what
     live orders actually use for portfolios without a saved profile.
  3. batch_risk_amount comes from the source batch's parameters
     (batch_risk_amount or risk_value). Falls back to 100.0.
  """
  from backend.db.models import BatchBacktestRun

  saved = settings.get("risk_profile") or {}
  if saved and saved.get("mode"):
    profile = dict(saved)
  else:
    # .env fallback — mirror live-trading truth.
    from backend.config.settings import get_settings

    s = get_settings()
    if getattr(s, "risk_mode", "pct") == "fixed":
      profile = {
        "mode": "fixed",
        "initial_risk_usd": float(s.risk_per_trade_usd),
      }
    else:
      profile = {
        "mode": "pct",
        "initial_risk_pct": float(s.risk_per_trade_pct),
        "initial_capital": 10000.0,
      }

  batch_risk_amount = 0.0
  if batch_ids:
    batch = (
      db.query(BatchBacktestRun)
      .filter(BatchBacktestRun.id == batch_ids[0])
      .first()
    )
    if batch and batch.parameters:
      import json as _json
      params = _json.loads(batch.parameters)
      batch_risk_amount = float(
        params.get("batch_risk_amount", 0) or params.get("risk_value", 0) or 0
      )
  if batch_risk_amount <= 0:
    batch_risk_amount = 100.0
  return profile, batch_risk_amount
