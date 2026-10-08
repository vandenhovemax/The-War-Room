"""T-001 Stage 8 / Option Y-B — nightly hybrid portfolio refresh.

STATUS: PRODUCTION. Wired into APScheduler at 03:00 NYT via
`backend/main.py` (refresh_all_hybrid_portfolios) and into the WFAS
save flow at `routes/wfo.py:save-track` + `routes/wfo.py:save-phase2-track`
(immediate refresh on hybrid portfolio save).

ANCHORED WALK REWORK (2026-06-10) — replaces the seeded trailing-window
design. The June-2026 audit found two structural flaws in the old
approach:

  FLAW 1 — committed-history rewrite. `_build_live_trailing_window`
  Path B could recompute the walk window deep inside the period the
  save-time records already cover (p37: re-graded Oct-2025→today every
  night). The display merge then UNIONED two different simulations of
  the same months — Nov-2025..Mar-2026 showed $4,594/58 trades where
  the optimizer's truth was $1,377/32 trades.

  FLAW 2 — seed contamination. Each tick seeded Phase 2 with the
  PREVIOUS tick's end-of-walk state and then re-walked the same window
  from its fixed oos_start. Every window trade entered the buffers a
  second (third, fourth, ...) time, and decision points inside the
  window could see trades from after their own date via the seed
  (look-ahead). Selections drifted night-over-night — "the data keeps
  changing".

What the anchored walk does on every nightly tick (and on every save):

  1. Identity guards (mode/deleted/active/basket present).
  2. Resolve the FIXED walk anchor (`_resolve_walk_anchor`):
     the day after the last save-time selected_record (the committed
     boundary), falling back to the canonical rolling chain's last
     oos_end. Stored in src_cfg["refresh_anchor"] on first resolution
     and reused verbatim afterwards — the anchor NEVER moves, so the
     walk's decisions for a given day are decided once and stay.
  3. Build a single window: is_start = saved trailing window's
     is_start (warmup span), is_end = anchor - 1, oos_start = anchor,
     oos_end = today.
  4. Run `run_step2` with NO seed. Warmup trades ([is_start..anchor))
     populate the scoring buffers exactly like the optimizer's own
     walk — same data, same code — so the state at the anchor matches
     the optimizer's end-state WITHOUT double-counting. Records are
     only emitted for [anchor..today] (date_from gate).
  5. Extract latest basket (`latest_portfolio_at_end`, capped at
     hybrid_n_assets).
  6. Diff vs current `is_active`, flip.
  7. Write Kelly + LiveEvent (event_type='hybrid_refresh').
  8. Persist refresh_step2_result (replaced whole — its records can
     never overlap the save-time records because the anchor sits after
     them).

Determinism contract: same trade set in DB → byte-identical records
for every already-walked day. The only thing that can change a past
day's display is a genuinely late-arriving trade (a position that was
still open when that day was first walked), which is new information,
not contamination.

What we deliberately STOPPED writing (old design residue):
  * src_cfg["wfo_per_window"] — the old code overwrote the optimizer's
    saved multi-window history with the single live window. Destructive
    and unnecessary.
  * src_cfg["continuation_state"] — multi-MB JSON blob, grew every
    night, and was the FLAW-2 contamination vector. The unseeded walk
    rebuilds state from warmup instead. Existing blobs are left in
    place (unused) — cleanup is a separate, explicitly-approved step.

Display side: `refresh_step2_result.selected_records` is merged with
save-time `step2_result.selected_records` by `get_effective_step2`
(portfolio_wfo_replay.py), which rebuilds the monthly matrix / equity
curve from the merged records so partial months at the anchor boundary
roll up correctly.

Safety:
  * All writes happen in a single db.commit() at the end.
  * On exception: db.rollback() in caller.
  * Hard refuses to act on non-hybrid portfolios.
  * Refuses to flip on empty basket (defensive — preserves the live
    basket if grading produces nothing).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from backend.analysis.step2_service import run_step2
from backend.analysis.trade_log_unified import load_unified_trades
from backend.db.database import SessionLocal
from backend.db.models import (
  BatchBacktestRun,
  KellyAllocation,
  LiveEvent,
  Portfolio,
  PortfolioAsset,
  Strategy,
)
from backend.services.phase2_recorder import (
  DISPATCH_SUCCESS,
  record_phase2_event,
  verify_dispatch_outcome,
)
from backend.utils.clock import today as _clock_today

log = logging.getLogger(__name__)


# ── Result dataclass ────────────────────────────────────────────

@dataclass
class RefreshResult:
  portfolio_id: int
  ok: bool = False
  reason: str | None = None
  basket: list[str] = field(default_factory=list)
  added: list[str] = field(default_factory=list)
  removed: list[str] = field(default_factory=list)
  newcomers_created: list[str] = field(default_factory=list)
  skipped_newcomers: list[str] = field(default_factory=list)
  weights: dict[str, float] = field(default_factory=dict)
  walked_window: dict | None = None
  selected_records_total: int = 0
  rotation_invoked: bool = False
  used_seed: bool = False
  missing_config_keys: list[str] = field(default_factory=list)

  def as_dict(self) -> dict:
    return {
      "portfolio_id": self.portfolio_id,
      "ok": self.ok,
      "reason": self.reason,
      "basket_size": len(self.basket),
      "added": list(self.added),
      "removed": list(self.removed),
      "newcomers_created": list(self.newcomers_created),
      "skipped_newcomers": list(self.skipped_newcomers),
      "weights_written": len(self.weights),
      "walked_window": dict(self.walked_window) if self.walked_window else None,
      "selected_records_total": self.selected_records_total,
      "rotation_invoked": self.rotation_invoked,
      "used_seed": self.used_seed,
      "missing_config_keys": list(self.missing_config_keys),
    }


# ── Helpers ─────────────────────────────────────────────────────

def _portfolio_assets(db, portfolio_id: int) -> list[PortfolioAsset]:
  return (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == portfolio_id)
    .all()
  )


def _hybrid_portfolios(db) -> list[Portfolio]:
  return (
    db.query(Portfolio)
    .filter(
      Portfolio.mode == "hybrid",
      Portfolio.is_active == True,  # noqa: E712
      Portfolio.is_deleted == False,  # noqa: E712
    )
    .all()
  )


def _dispatch_executor_refresh(portfolio_id: int) -> None:
  """Best-effort post-commit cache invalidation for the live executor."""
  try:
    from backend.exchange.engine_coordinator import get_engine
    engine = get_engine()
  except Exception as e:
    log.warning("yb_refresh: engine import failed for %d: %s", portfolio_id, e)
    return
  if engine is None:
    return
  try:
    if hasattr(engine, "refresh_kelly_cache"):
      engine.refresh_kelly_cache(portfolio_id)
  except Exception as e:
    log.warning("yb_refresh: refresh_kelly_cache failed for %d: %s", portfolio_id, e)
  try:
    if hasattr(engine, "refresh_portfolio") and hasattr(engine, "_schedule"):
      coro = engine.refresh_portfolio(portfolio_id)
      loop = getattr(engine, "_loop", None)
      if loop is not None and loop.is_running():
        engine._schedule(coro)
      elif hasattr(coro, "close"):
        coro.close()
  except Exception as e:
    log.warning("yb_refresh: refresh_portfolio dispatch failed for %d: %s",
                portfolio_id, e)


def _basket_batch_ids(rows: list[PortfolioAsset]) -> list[int]:
  """All saved batches - including currently-inactive PortfolioAsset rows.
  The universe stays fixed across rotations (4c decision).
  """
  return sorted({r.batch_id for r in rows if r.batch_id is not None})


def _asset_key(pa: PortfolioAsset) -> str:
  if pa.batch_id is not None:
    return f"B{pa.batch_id}_{pa.symbol}_{pa.trade_side}"
  return f"{pa.symbol}_{pa.trade_side}"


def _project_weights(basket: list[str], scores: dict[str, float]) -> dict[str, float]:
  if not basket:
    return {}
  positive = {a: float(scores.get(a, 0.0) or 0.0) for a in basket}
  total = sum(v for v in positive.values() if v > 0)
  if total <= 0:
    w = 1.0 / len(basket)
    return {a: w for a in basket}
  return {a: (v / total) if v > 0 else 0.0 for a, v in positive.items()}


# Back-compat alias — the function was renamed from
# _project_weights_from_scores to _project_weights at some point;
# test_hybrid_graded_weights.py (and any external importer) still uses
# the old name. Same signature, same semantics.
_project_weights_from_scores = _project_weights


def _extract_basket(step2_result: dict, n_assets: int) -> tuple[list[str], dict[str, float]]:
  """Extract the hybrid basket from a step2 result, capped at n_assets.

  Resolution:
    1. `latest_portfolio_at_end` (preferred — step2's authoritative pick)
       — but CAP at hybrid_n_assets when present, taking top-N by score.
    2. `asset_frequency` fallback ranked by (selected, pct), already capped.

  The n_assets cap on path 1 was MISSING pre-2026-05-25 — `_extract_basket`
  returned `latest_portfolio_at_end` as-is. Audit across all 12 active
  hybrids showed every one trading 1.25-3.75x more assets than configured
  (pid=37 most extreme: 15 actual vs 4 configured). The optimizer's intent
  is "trade the top-N by score"; ignoring the cap dilutes per-asset signal
  quality with lower-ranked picks the optimizer specifically demoted.
  """
  scores = step2_result.get("latest_asset_scores") or {}
  basket = list(step2_result.get("latest_portfolio_at_end") or [])
  if basket:
    # CAP at hybrid_n_assets — take top-N by latest_asset_scores. Assets
    # missing from `scores` are treated as -inf so they sort to the end
    # (defensive: never silently retain an asset with no score evidence).
    n = int(n_assets or 0)
    if n > 0 and len(basket) > n:
      def _score_of(asset_key: str) -> float:
        v = scores.get(asset_key)
        try:
          return float(v)
        except (TypeError, ValueError):
          return float("-inf")
      basket = sorted(basket, key=_score_of, reverse=True)[:n]
    return basket, dict(scores)
  freq = step2_result.get("asset_frequency") or {}
  if not freq:
    return [], {}
  ranked = sorted(
    freq.items(),
    key=lambda kv: (kv[1].get("selected", 0), kv[1].get("pct", 0.0)),
    reverse=True,
  )
  k = max(0, int(n_assets or 0)) or len(ranked)
  return [a for a, _ in ranked[:k]], dict(scores)


def _persist_refresh(
  portfolio: Portfolio,
  src_cfg: dict,
  step2_result: dict,
  basket: list[str],
  scores: dict[str, float],
  weights: dict[str, float],
  target_window: dict,
  used_seed: bool,
) -> None:
  """Persist refresh_step2_result + advance the cursor on the portfolio.

  Critical for display: writes selected_records so get_effective_step2
  picks them up on the next read and the Portfolio Monthly modal renders
  correctly.

  ANCHORED WALK (2026-06-10): deliberately does NOT touch
  `wfo_per_window` (the optimizer's saved multi-window history — the old
  code overwrote it with the single live window) and does NOT write
  `continuation_state` (the seed-contamination vector; also a multi-MB
  JSON blob that bloated source_strategies on every tick).
  """
  rsr = {
    "selected_records": step2_result.get("selected_records"),
    "aggregate_monthly_matrix": step2_result.get("aggregate_monthly_matrix"),
    "aggregate_equity_curve": step2_result.get("aggregate_equity_curve"),
    "aggregate_breakdown": step2_result.get("aggregate_breakdown"),
    "cumulative_metrics": step2_result.get("cumulative_metrics"),
    "latest_asset_scores": dict(scores),
    "latest_portfolio_at_end": list(basket),
    "refreshed_at": datetime.now(timezone.utc).isoformat(),
    "trigger": "yb_refresh",
    "walk_anchor": target_window["oos_start"],
    "last_oos_end_walked": target_window["oos_end"],
    "last_window_num_walked": target_window["window_num"],
    "last_walked_used_seed": bool(used_seed),
  }
  src_cfg["refresh_step2_result"] = rsr
  portfolio.source_strategies = json.dumps(src_cfg)


# Grading keys the optimization used that the live walk must reuse.
# CORE keys must always be present in the saved top-level config; the
# remaining keys are flagged only when the saved preset_config shows the
# optimization actually set them (absence-with-no-preset-trace means the
# feature was never used, which is fine).
_CORE_GRADING_KEYS = (
  "hybrid_n_trades", "hybrid_n_assets", "hybrid_min_pf", "hybrid_min_wr",
  "hybrid_min_hold", "hybrid_rebalance_every", "hybrid_rebalance_mode",
  "hybrid_scoring_method", "hybrid_objective",
)
_OPTIONAL_GRADING_KEYS = (
  "hybrid_staleness_days", "hybrid_warmup_trades", "hybrid_composite_weights",
  "hybrid_exec_day_profit", "hybrid_sample_count_floor",
  "hybrid_rescue_enabled", "hybrid_rescue_quota_pct",
  "hybrid_rescue_min_trades", "hybrid_rescue_min_score",
  "hybrid_rescue_confidence_gap",
  "session_filter_enabled", "session_filter_mode",
  "session_filter_exclude_hours", "session_filter_exclude_days",
  "category_filter_enabled", "category_influence",
  "category_lookback_trades", "category_max_per_cat",
  "category_score_metric", "category_mode", "category_streak_required",
  "category_fire_multiplier", "category_fire_duration_trades",
)


def _missing_grading_keys(cfg: dict) -> list[str]:
  """Return grading keys the nightly walk would silently default.

  A key counts as missing when it is absent from the top-level saved
  config AND (it is a core key, or the saved preset_config carries it —
  i.e. the optimization demonstrably used it).
  """
  pc = cfg.get("preset_config") or {}
  if not isinstance(pc, dict):
    pc = {}
  missing: list[str] = []
  for k in _CORE_GRADING_KEYS:
    if k not in cfg:
      missing.append(k)
  for k in _OPTIONAL_GRADING_KEYS:
    if k not in cfg and k in pc:
      missing.append(k)
  return missing


def _canonical_chain(windows_meta: list[dict]) -> list[tuple[date, date, date, date, dict]]:
  """Parse windows_meta and return the longest contiguous rolling chain
  (each window's oos_start == prior window's oos_end + 1 day). Stub
  artifacts that break the rolling rhythm are skipped. Shared by the
  anchor resolver and the (legacy) trailing-window builder.
  """
  from dateutil.relativedelta import relativedelta as _rd

  parsed: list[tuple[date, date, date, date, dict]] = []
  for w in windows_meta or []:
    if not isinstance(w, dict):
      continue
    try:
      iss = date.fromisoformat(str(w["is_start"])[:10])
      ise = date.fromisoformat(str(w["is_end"])[:10])
      oss_ = date.fromisoformat(str(w["oos_start"])[:10])
      ose = date.fromisoformat(str(w["oos_end"])[:10])
    except (ValueError, TypeError, KeyError):
      continue
    parsed.append((iss, ise, oss_, ose, w))
  parsed.sort(key=lambda t: t[2])  # by oos_start

  chain: list[tuple[date, date, date, date, dict]] = []
  for entry in parsed:
    if not chain:
      chain.append(entry)
      continue
    if entry[2] == chain[-1][3] + _rd(days=1):
      chain.append(entry)
  return chain


def _resolve_walk_anchor(
  src_cfg: dict, windows_meta: list[dict], today: date,
) -> tuple[date | None, str]:
  """Return (anchor, source) — the FIXED first day the live walk owns.

  Everything strictly before the anchor is committed history (save-time
  records, immutable). Everything from the anchor onward is graded by
  the nightly walk. The anchor is resolved ONCE and persisted in
  src_cfg["refresh_anchor"]; later ticks reuse the stored value
  verbatim so the boundary can never drift.

  Resolution order:
    1. src_cfg["refresh_anchor"]            (stored — reuse verbatim)
    2. max(step2_result.selected_records.dt) + 1 day
       (the actual committed boundary — what the optimizer's save-time
       walk really graded, regardless of what the window stubs claim)
    3. canonical chain's last oos_end + 1 day (no save records at all)

  Returns (None, reason) when no boundary signal exists.
  """
  from datetime import timedelta as _td

  stored = src_cfg.get("refresh_anchor")
  if stored:
    try:
      return date.fromisoformat(str(stored)[:10]), "stored"
    except (ValueError, TypeError):
      pass  # malformed — fall through and re-derive

  save_recs = (src_cfg.get("step2_result") or {}).get("selected_records") or []
  best: date | None = None
  for r in save_recs:
    if not isinstance(r, dict):
      continue
    raw = str(r.get("dt") or "")[:10]
    try:
      d = date.fromisoformat(raw)
    except (ValueError, TypeError):
      continue
    if best is None or d > best:
      best = d
  if best is not None:
    return best + _td(days=1), "save_records"

  chain = _canonical_chain(windows_meta)
  ends = [c[3] for c in chain if c[3] <= today]
  if ends:
    return max(ends) + _td(days=1), "windows_chain"

  return None, "no_boundary_signal"


def _build_anchored_window(
  *, anchor: date, today: date, windows_meta: list[dict], oos_months: int,
) -> dict:
  """Build the single walk window: [anchor .. today] OOS with an IS/warmup
  span ending the day before the anchor.

  is_start preference: the newest saved window's is_start (the optimizer's
  own warmup depth), falling back to `anchor - max(oos_months, 6) months`.
  The IS span only feeds warmup buffers — trades in it populate scoring
  state but are never recorded (date_from gate in step2_service).
  """
  from dateutil.relativedelta import relativedelta as _rd

  is_start: date | None = None
  window_num = 0
  newest = _pick_newest_unrotated_window(windows_meta, today, since=None)
  if newest:
    try:
      window_num = int(newest.get("window_num") or 0)
    except (ValueError, TypeError):
      window_num = 0
    try:
      is_start = date.fromisoformat(str(newest.get("is_start"))[:10])
    except (ValueError, TypeError):
      is_start = None
  if is_start is None or is_start >= anchor:
    is_start = anchor - _rd(months=max(int(oos_months or 6), 6))

  return {
    "window_num": window_num,
    "is_start": is_start.isoformat(),
    "is_end": (anchor - _rd(days=1)).isoformat(),
    "oos_start": anchor.isoformat(),
    "oos_end": today.isoformat(),
  }


def _last_walked_cursor(src_cfg: dict) -> date | None:
  rsr = src_cfg.get("refresh_step2_result") or {}
  if isinstance(rsr, dict):
    v = rsr.get("last_oos_end_walked")
    if v:
      try:
        return date.fromisoformat(str(v)[:10])
      except (ValueError, TypeError):
        pass
  return None


def _build_live_trailing_window(
  *, windows_meta: list[dict], saved_target: dict, today: date,
) -> dict:
  """Return the saved target with OOS extended to today, OR - if the
  saved entry is malformed - recompute it from the rolling cadence.

  Two malformations the optimizer's stub logic can produce:
    1. The saved entry's `oos_start == prior_window.oos_end` (off by
       one - a normal rolling window has oos_start == prior.oos_end + 1).
       This is the "cutoff stub" the optimizer emits when last_oos_end
       falls before a full OOS would close.
    2. The saved entry's IS is a literal copy of the prior full
       window's IS (stub artifact), instead of the natural rolling
       continuation `prior.is_start + oos_months`.

  When either malformation is detected, we recompute the window from
  the canonical rolling chain. Otherwise we use the saved bounds and
  only extend oos_end to today.

  Returns a dict with keys: window_num, is_start, is_end, oos_start,
  oos_end (all ISO strings).
  """
  from dateutil.relativedelta import relativedelta as _rd

  # Build the canonical rolling chain - windows whose oos_start
  # follows the prior window's oos_end by exactly 1 day. This filters
  # out stub artifacts that don't follow the rolling rhythm.
  parsed: list[tuple[date, date, date, date, dict]] = []
  for w in windows_meta:
    if not isinstance(w, dict):
      continue
    try:
      iss = date.fromisoformat(str(w["is_start"])[:10])
      ise = date.fromisoformat(str(w["is_end"])[:10])
      oss_ = date.fromisoformat(str(w["oos_start"])[:10])
      ose = date.fromisoformat(str(w["oos_end"])[:10])
    except (ValueError, TypeError, KeyError):
      continue
    parsed.append((iss, ise, oss_, ose, w))

  parsed.sort(key=lambda t: t[2])  # by oos_start

  # Find the longest contiguous rolling chain (each window's oos_start
  # equals the prior window's oos_end + 1 day).
  chain: list[tuple[date, date, date, date, dict]] = []
  for entry in parsed:
    if not chain:
      chain.append(entry)
      continue
    prev_oe = chain[-1][3]
    if entry[2] == prev_oe + _rd(days=1):
      chain.append(entry)
    # Otherwise: skip this entry (stub artifact). Continue scanning -
    # later entries that DO match the chain still get included.

  # Detect whether the saved target is part of the canonical chain.
  saved_in_chain = False
  try:
    saved_oss = date.fromisoformat(str(saved_target["oos_start"])[:10])
    saved_oe = date.fromisoformat(str(saved_target["oos_end"])[:10])
    for c in chain:
      if c[2] == saved_oss and c[3] == saved_oe:
        saved_in_chain = True
        break
  except (ValueError, TypeError, KeyError):
    pass

  # Path A - saved target is canonical. If today is still inside the saved
  # OOS, return the saved bounds verbatim (back-compat). If today has run
  # past the saved oos_end, EXTEND the existing trailing window forward:
  # absorb the just-completed OOS into IS by setting is_end = prior_ose,
  # slide oos_start to prior_ose + 1d, and slide oos_end to today. The
  # saved is_start is INTENTIONALLY ANCHORED at its saved value - IS
  # widens (extend), it does not roll forward at fixed width. Rationale:
  # extension preserves the user's original IS anchor (relevant when the
  # save was calendar-aligned), gives Phase 2 maximum warmup buffer
  # history, and reads literally per Q1=(A) and the owner's directive
  # ("IS extends through the most recent OOS date already committed").
  # See COLLAB_T-002 ATLAS audit for full structural reasoning.
  if saved_in_chain:
    out = dict(saved_target)
    try:
      cur_oe = date.fromisoformat(str(out["oos_end"])[:10])
    except (ValueError, TypeError, KeyError):
      cur_oe = today
    if today > cur_oe:
      out["is_end"] = cur_oe.isoformat()                      # = prior_ose
      out["oos_start"] = (cur_oe + _rd(days=1)).isoformat()   # = prior_ose + 1d
      out["oos_end"] = today.isoformat()
      # is_start intentionally NOT mutated — Path-A-1 anchor preservation.
    return out

  # Path B - saved target is a stub. Recompute from the chain.
  if not chain:
    # No canonical reference. Fall back to saved with oos_end clamped.
    out = dict(saved_target)
    try:
      cur_oe = date.fromisoformat(str(out.get("oos_end"))[:10])
    except (ValueError, TypeError):
      cur_oe = today
    if today > cur_oe:
      out["oos_end"] = today.isoformat()
    return out

  prior_iss, prior_ise, prior_oss, prior_ose, prior_w = chain[-1]
  # Anchor on prior.oos_end to avoid relativedelta month-boundary
  # rounding errors. New OOS starts the day after the prior OOS ended;
  # new IS ends that same day. New IS_start is offset back from new
  # OOS_start by the same months gap the prior window used.
  is_offset_days = (prior_oss - prior_iss).days
  is_offset_months = max(1, round(is_offset_days / 30.44))

  new_oos_start = prior_ose + _rd(days=1)
  new_is_end = prior_ose
  new_is_start = new_oos_start - _rd(months=is_offset_months)
  new_oos_end = today

  try:
    next_num = int(saved_target.get("window_num") or prior_w.get("window_num", 0) + 1)
  except (ValueError, TypeError):
    next_num = 1

  return {
    "window_num": next_num,
    "is_start": new_is_start.isoformat(),
    "is_end": new_is_end.isoformat(),
    "oos_start": new_oos_start.isoformat(),
    "oos_end": new_oos_end.isoformat(),
  }


def _pick_newest_unrotated_window(
  windows_meta: list[dict], today: date, since: date | None,
) -> dict | None:
  """Most recent window with oos_end <= today AND (since is None OR oos_end > since)."""
  eligible: list[dict] = []
  for w in windows_meta:
    if not isinstance(w, dict):
      continue
    oos_end_raw = w.get("oos_end")
    oos_start_raw = w.get("oos_start")
    if not oos_end_raw or not oos_start_raw:
      continue
    try:
      oe = date.fromisoformat(str(oos_end_raw)[:10])
    except (ValueError, TypeError):
      continue
    if oe > today:
      continue
    if since is not None and oe <= since:
      continue
    eligible.append((oe, w))
  if not eligible:
    return None
  eligible.sort(key=lambda kv: kv[0], reverse=True)
  return eligible[0][1]


def _resolve_strategy(db, batch_id: int) -> tuple[str, str | None]:
  strat_name = ""
  cfg_name: str | None = None
  batch = db.get(BatchBacktestRun, batch_id)
  if batch is None:
    return strat_name, cfg_name
  try:
    bp = json.loads(batch.parameters or "{}")
    cfg_name = bp.get("config_name") or None
  except Exception:
    cfg_name = None
  try:
    strat = db.get(Strategy, batch.strategy_id)
    if strat is not None:
      strat_name = (strat.name or "").strip()
  except Exception:
    strat_name = ""
  return strat_name, cfg_name


# ── Main entry point ────────────────────────────────────────────

def refresh_portfolio(
  portfolio_id: int,
  db=None,
  *,
  simulate_today: date | None = None,
  run_step2_fn=None,
  load_trades_fn=None,
  allow_inactive: bool = False,
) -> RefreshResult:
  """Anchored-walk refresh: grade [anchor..today] with a fresh warmup.

  run_step2_fn / load_trades_fn default to the module globals AT CALL
  TIME (not import time) so tests can monkeypatch `svc.run_step2` /
  `svc.load_unified_trades` without threading kwargs through callers.
  """
  if run_step2_fn is None:
    run_step2_fn = run_step2
  if load_trades_fn is None:
    load_trades_fn = load_unified_trades
  own = db is None
  if own:
    db = SessionLocal()

  result = RefreshResult(portfolio_id=portfolio_id)

  try:
    # 1. Validate portfolio
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if portfolio is None:
      result.reason = "not_found"
      return result.as_dict()
    if portfolio.mode != "hybrid":
      result.reason = "not_hybrid"
      return result.as_dict()
    if portfolio.is_deleted:
      result.reason = "deleted"
      return result.as_dict()
    if not portfolio.is_active and not allow_inactive:
      result.reason = "inactive"
      return result.as_dict()

    pa_rows = _portfolio_assets(db, portfolio_id)
    batch_ids = _basket_batch_ids(pa_rows)
    if not batch_ids:
      result.reason = "no_basket_batches"
      return result.as_dict()

    try:
      src_cfg = json.loads(portfolio.source_strategies or "{}")
    except Exception:
      src_cfg = {}

    windows_meta = src_cfg.get("wfo_windows_meta") or []
    if not windows_meta:
      result.reason = "no_windows_meta"
      return result.as_dict()

    today = simulate_today if simulate_today is not None else _clock_today()

    # 2. Resolve the FIXED walk anchor (committed-history boundary).
    # The anchor never moves once resolved — that is the determinism
    # guarantee that stops the nightly walk from re-deciding (and
    # re-displaying) days it already graded. See module docstring,
    # FLAW 1 + FLAW 2.
    anchor, anchor_source = _resolve_walk_anchor(src_cfg, windows_meta, today)
    if anchor is None:
      # Benign no-op: a brand-new portfolio whose saved windows are all in
      # the future has no committed boundary to anchor on yet. Nothing to
      # walk — the nightly report should not count this as a failure.
      result.reason = f"no_walk_anchor ({anchor_source})"
      result.ok = True
      return result.as_dict()
    if anchor > today:
      # Committed history already reaches today — nothing to walk yet.
      result.reason = "anchor_after_today"
      result.ok = True
      return result.as_dict()

    # Persist the anchor on first resolution so every later tick (and
    # any other reader) sees the exact same boundary.
    if not src_cfg.get("refresh_anchor"):
      src_cfg["refresh_anchor"] = anchor.isoformat()

    target = _build_anchored_window(
      anchor=anchor,
      today=today,
      windows_meta=windows_meta,
      oos_months=int(src_cfg.get("oos_months", 6) or 6),
    )
    result.walked_window = dict(target)
    log.info(
      "yb_refresh: portfolio %d anchored walk %s -> %s (anchor source: %s, "
      "warmup from %s)",
      portfolio_id, target["oos_start"], target["oos_end"], anchor_source,
      target["is_start"],
    )

    # 3. Load trades + build single-window step1_result
    trades, rbm, batch_info = load_trades_fn(
      db, batch_ids=batch_ids,
      cutoff=portfolio.optimization_end_date,
      portfolio_id=portfolio_id,
    )
    if not trades:
      result.reason = "no_trades"
      return result.as_dict()

    # Defensive: legacy windows_meta entries may lack is_start/is_end
    step1_result = {"windows": [{
      "window_num": target["window_num"],
      "is_start": target.get("is_start") or target["oos_start"],
      "is_end": target.get("is_end") or target["oos_start"],
      "oos_start": target["oos_start"],
      "oos_end": target["oos_end"],
      "candidates": [{"selected_batches": list(batch_ids)}],
    }]}

    # 4. Build config — NO seed. The walk's warmup span (is_start..anchor)
    # rebuilds the scoring buffers from the same trades the optimizer
    # used, with the same code, so the state at the anchor matches the
    # optimizer's end-state without double-counting (FLAW 2 fix). Any
    # stale `_seed_state` left in a saved config is explicitly dropped.
    cfg = dict(src_cfg)
    cfg["hybrid_mode"] = True
    cfg.setdefault("oos_months", src_cfg.get("oos_months", 6))
    cfg.pop("_seed_state", None)
    result.used_seed = False

    # Config-parity guard: every grading key the optimizer used must be
    # present in the saved config — a missing key silently falls back to
    # run_step2's defaults, which means the live walk grades with a
    # DIFFERENT config than the optimization. Log loudly; the keys also
    # ride the RefreshResult so the live-loop report surfaces them.
    missing_cfg_keys = _missing_grading_keys(cfg)
    result.missing_config_keys = list(missing_cfg_keys)
    if missing_cfg_keys:
      log.warning(
        "yb_refresh: portfolio %d is missing grading config keys %s — "
        "run_step2 will use built-in defaults for these. Verify the saved "
        "preset matches the optimization.",
        portfolio_id, missing_cfg_keys,
      )

    # 5. Run Step-2
    step2_result = run_step2_fn(
      step1_result=step1_result,
      all_trades=trades,
      run_batch_map=rbm,
      config=cfg, db=db,
    )
    if step2_result is None:
      result.reason = "step2_returned_none"
      return result.as_dict()

    result.selected_records_total = len(step2_result.get("selected_records") or [])

    # 6. Extract basket
    n_assets = int(cfg.get("hybrid_n_assets") or 0)
    basket, scores = _extract_basket(step2_result, n_assets)
    if not basket:
      # Defensive: refuse to flip on empty basket (would preserve the
      # bleed-restore state instead of destroying it).
      result.reason = "step2_picked_empty"
      return result.as_dict()

    weights = _project_weights(basket, scores)
    result.basket = list(basket)
    result.weights = dict(weights)

    # 7. Diff vs current is_active
    current_active: set[str] = set()
    existing: set[str] = set()
    for pa in pa_rows:
      key = _asset_key(pa)
      existing.add(key)
      if pa.is_active:
        current_active.add(key)

    new_set = set(basket)
    added = sorted(new_set - current_active)
    removed = sorted(current_active - new_set)
    result.added = added
    result.removed = removed

    # Persist refresh state regardless of basket change - the cursor must
    # advance so the next nightly tick skips this window.
    _persist_refresh(
      portfolio, src_cfg, step2_result, basket, scores, weights, target,
      result.used_seed,
    )

    if not added and not removed:
      portfolio.last_refreshed_at = datetime.now(timezone.utc)
      result.ok = True
      result.reason = "basket_unchanged"
      # T-002: record the no-change phase-2 evaluation. SAVEPOINT
      # isolates a recorder failure from the trading-state commit.
      # No-change events have nothing to dispatch — stamp dispatch_outcome
      # = "success" inline so phase2_audit.dispatch_pending_stale doesn't
      # false-positive on the lingering pending sentinel.
      try:
        with db.begin_nested():
          live_event_no_change = record_phase2_event(
            db,
            portfolio_id=portfolio_id,
            target_window=target,
            windows_meta=windows_meta,
            src_cfg=src_cfg,
            step2_result=step2_result,
            batch_ids=batch_ids,
            rbm=rbm,
            prior_basket=sorted(current_active),
            chosen_basket=sorted(basket),
            added=[],
            removed=[],
            basket_unchanged=True,
            kelly_allocation_id=None,
            trigger="nightly_cron",
          )
          try:
            d = json.loads(live_event_no_change.details or "{}")
          except (ValueError, TypeError):
            d = {}
          d["dispatch_outcome"] = DISPATCH_SUCCESS
          live_event_no_change.details = json.dumps(d)
      except Exception as rec_e:
        log.exception(
          "yb_refresh: phase2_recorder failed (no-change path) for %d: %s",
          portfolio_id, rec_e,
        )
      db.commit()
      return result.as_dict()

    # 8. Flip is_active + insert newcomers
    for pa in pa_rows:
      key = _asset_key(pa)
      pa.is_active = key in new_set

    portfolio_strategy = (src_cfg.get("strategy_name") or "").strip()
    for key in basket:
      if key in existing:
        continue
      parts = key.rsplit("_", 2)
      if len(parts) != 3 or not parts[0].startswith("B"):
        result.skipped_newcomers.append(key)
        continue
      try:
        bid = int(parts[0][1:])
      except ValueError:
        result.skipped_newcomers.append(key)
        continue
      sym, side = parts[1], parts[2]
      info = batch_info.get(bid) or {}
      batch_strategy_name, config_name = _resolve_strategy(db, bid)
      strategy_name = portfolio_strategy or batch_strategy_name
      if not strategy_name:
        result.skipped_newcomers.append(key)
        continue
      db.add(PortfolioAsset(
        portfolio_id=portfolio_id,
        symbol=sym,
        trade_side=side,
        strategy_name=strategy_name,
        raw_name=f"{sym}_{side.capitalize()}",
        batch_id=bid,
        config_name=config_name,
        timeframe=info.get("timeframe"),
        group_name=info.get("group"),
        is_active=True,
      ))
      result.newcomers_created.append(key)

    # 9. Kelly + LiveEvent (T-002: recorder replaces legacy LiveEvent emit)
    db.query(KellyAllocation).filter(
      KellyAllocation.portfolio_id == portfolio_id,
      KellyAllocation.is_current == True,  # noqa: E712
    ).update({KellyAllocation.is_current: False}, synchronize_session=False)
    kelly_row = KellyAllocation(
      portfolio_id=portfolio_id,
      weights_json=json.dumps(weights),
      result_json=json.dumps({"basket": basket, "scores": scores}),
      config_json=json.dumps({
        "source": "yb_refresh",
        "walked_window": target,
        "used_seed": result.used_seed,
      }),
      trigger="step2_refresh",
      is_current=True,
    )
    db.add(kelly_row)
    db.flush()  # populate kelly_row.id for the recorder

    live_event_id: Optional[int] = None
    try:
      with db.begin_nested():
        live_event = record_phase2_event(
          db,
          portfolio_id=portfolio_id,
          target_window=target,
          windows_meta=windows_meta,
          src_cfg=src_cfg,
          step2_result=step2_result,
          batch_ids=batch_ids,
          rbm=rbm,
          prior_basket=sorted(current_active),
          chosen_basket=sorted(basket),
          added=added,
          removed=removed,
          basket_unchanged=False,
          kelly_allocation_id=kelly_row.id,
          trigger="nightly_cron",
          extra={
            "weights_count": len(weights),
            "walked_window": dict(target),
            "used_seed": bool(result.used_seed),
          },
        )
        live_event_id = live_event.id
    except Exception as rec_e:
      log.exception(
        "yb_refresh: phase2_recorder failed (basket-changed path) for %d: %s",
        portfolio_id, rec_e,
      )

    portfolio.last_refreshed_at = datetime.now(timezone.utc)
    db.commit()
    _dispatch_executor_refresh(portfolio_id)
    if live_event_id is not None:
      try:
        verify_dispatch_outcome(
          db=db,
          live_event_id=live_event_id,
          portfolio_id=portfolio_id,
        )
      except Exception as ver_e:
        log.warning(
          "yb_refresh: verify_dispatch_outcome failed for %d: %s",
          portfolio_id, ver_e,
        )
    result.ok = True
    result.reason = None
    result.rotation_invoked = True
    return result.as_dict()

  except Exception as e:
    db.rollback()
    log.exception("yb_refresh: portfolio %d failed: %s", portfolio_id, e)
    result.reason = f"exception: {e}"
    return result.as_dict()
  finally:
    if own:
      db.close()


def refresh_all_hybrid_portfolios() -> dict:
  """Scheduled nightly. Y-B policy applied to every hybrid portfolio."""
  started = datetime.now(timezone.utc)
  report: dict[str, Any] = {
    "started_at": started.isoformat(),
    "ended_at": None,
    "results": [],
    "total_hybrid": 0,
    "ok": 0,
    "failed": 0,
  }
  db = SessionLocal()
  try:
    hybrid_ids = [p.id for p in _hybrid_portfolios(db)]
  finally:
    db.close()
  report["total_hybrid"] = len(hybrid_ids)
  log.info("yb_refresh: nightly start - %d hybrid portfolios", len(hybrid_ids))
  for pid in hybrid_ids:
    r = refresh_portfolio(pid)
    report["results"].append(r)
    if r.get("ok"):
      report["ok"] += 1
    else:
      report["failed"] += 1
  report["ended_at"] = datetime.now(timezone.utc).isoformat()
  log.info("yb_refresh: nightly complete - %d ok, %d failed",
           report["ok"], report["failed"])
  return report


def _emit_metadata_only_event(db, portfolio_id: int):
  """Kept for import-compatibility with callers that may still invoke this."""
  try:
    evt = LiveEvent(
      portfolio_id=portfolio_id,
      event_type="hybrid_nightly_refresh_metadata_only",
      symbol="",
      details=json.dumps({
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "note": "Y-B refresh does not call this. Preserved for legacy import compat.",
      }),
    )
    db.add(evt)
  except Exception as e:
    log.warning("yb_refresh: metadata-only LiveEvent failed for %d: %s",
                portfolio_id, e)
