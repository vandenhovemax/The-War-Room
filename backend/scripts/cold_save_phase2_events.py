"""T-002 Step 6 — Cold-save reconstruction script.

Backfills a baseline `phase2_evaluated_no_change` LiveEvent for every
hybrid portfolio that pre-dates T-002 (i.e. has zero recorder events).

Why we need this
----------------
The Step-4 forward-period read uses `iter_basket_segments(...)` to walk
LiveEvent rows and resolve the basket as-of each trade's entry_time.
Pre-T-002 portfolios have ZERO LiveEvent rows for phase-2 events, so
the resolver returns `[]` and the call site falls back to the legacy
`is_active` read — which is the bug Step 4 fixed for new portfolios.

Cold-save anchors a single baseline event per portfolio so:
  * `iter_basket_segments(p)` returns a non-empty list immediately after
    cold-save runs.
  * The first post-deploy nightly tick extends the segment chain
    naturally — no special-case bridging code in the resolver.
  * The fallback to `is_active` only fires for portfolios that have
    been deliberately skipped by cold-save (e.g. legacy/dead ones).

Why ONE event, not full per-rotation history
---------------------------------------------
Pre-T-002 we did NOT record per-rotation baskets. Reconstructing them
from `step2_result.selected_records` is lossy (lock-state isn't
recoverable) and the Step-4 resolver only needs *something* anchored
at-or-before "now". One baseline event is sufficient: it gives the
resolver `(baseline_ts, current_basket)`. Trades after baseline_ts
resolve to `current_basket` (which is what the legacy `is_active` path
already showed). The next nightly tick adds a real event and the chain
grows from there.

`created_at` choice
-------------------
We backdate the baseline to the latest known rotation boundary so the
segment ordering stays sensible:
  * Prefer `max(wfo_windows_meta[*].oos_end)` at 08:00 NYT (which is
    ~03:00 UTC EDT — the typical Y-B nightly tick wall-clock).
  * Otherwise: `now() - 1 day` (defensive — ensures every existing
    historical trade has a valid as-of-time anchor).

Idempotency
-----------
Skip portfolios that already have at least one phase-2 LiveEvent
(any of: `hybrid_refresh`, `hybrid_rotation`, `phase2_evaluated_no_change`).
Running twice produces identical state.

Usage
-----
    # Dry-run (default — prints what would be written, no commit).
    python -m backend.scripts.cold_save_phase2_events --dry-run

    # Live (writes one baseline LiveEvent per pre-T-002 portfolio).
    python -m backend.scripts.cold_save_phase2_events

    # Limit to specific portfolio IDs.
    python -m backend.scripts.cold_save_phase2_events --ids 23,24
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, time, timedelta, timezone

from backend.db.database import SessionLocal
from backend.db.models import KellyAllocation, LiveEvent, Portfolio, PortfolioAsset
from backend.services.phase2_recorder import (
  EVENT_TYPE_HYBRID_REFRESH,
  EVENT_TYPE_HYBRID_ROTATION,
  EVENT_TYPE_NO_CHANGE,
  Phase2EventDetails,
  RECORDER_VERSION,
  _build_session_filter_snapshot,
  _utc_to_nyt_naive,
  compute_decision_inputs_hash,
  fingerprint_run_batch_map,
  iter_basket_segments,
  resolve_basket_at_t,
)


class FullHistoryParityError(Exception):
  """Raised when the closing parity assertion (post-Pass-A + Pass-B
  resolver basket vs. portfolio.is_active basket) fails. Triggers the
  enclosing SAVEPOINT to roll back so no partial v3 chain is committed."""
  def __init__(self, *, expected: set[str], got: set[str]):
    self.expected = expected
    self.got = got
    super().__init__(
      f"parity-mismatch expected={sorted(expected)} got={sorted(got)}"
    )


log = logging.getLogger(__name__)


_PHASE2_EVENT_TYPES = (
  EVENT_TYPE_HYBRID_REFRESH,
  EVENT_TYPE_HYBRID_ROTATION,
  EVENT_TYPE_NO_CHANGE,
)


def _has_existing_phase2_events(db, portfolio_id: int) -> bool:
  """True iff any phase-2 LiveEvent already exists for this portfolio.
  Idempotency guard — cold-save is a no-op when this returns True."""
  exists = (
    db.query(LiveEvent.id)
    .filter(
      LiveEvent.portfolio_id == portfolio_id,
      LiveEvent.event_type.in_(_PHASE2_EVENT_TYPES),
    )
    .first()
  )
  return exists is not None


def _current_basket_keys(db, portfolio_id: int) -> list[str]:
  """Build the live `B{bid}_{SYMBOL}_{Side}` basket keys from the
  portfolio's currently-active PortfolioAsset rows.

  Mirrors the basket-key shape used by the live recorder so the
  resolver in `iter_basket_segments` interprets cold-save events
  identically to live ones."""
  rows = (
    db.query(PortfolioAsset)
    .filter(
      PortfolioAsset.portfolio_id == portfolio_id,
      PortfolioAsset.is_active == True,  # noqa: E712 — SA wants ==
    )
    .all()
  )
  keys: list[str] = []
  for r in rows:
    if r.batch_id is None or not r.symbol or not r.trade_side:
      continue
    side = str(r.trade_side).strip().capitalize()
    if side not in ("Long", "Short"):
      continue
    keys.append(f"B{int(r.batch_id)}_{r.symbol}_{side}")
  return sorted(set(keys))


def _resolve_anchor_timestamp(src_cfg: dict, *, now_utc: datetime) -> datetime:
  """Pick the cold-save baseline `created_at`.

  Order of preference:
    1. max(`wfo_windows_meta[*].oos_end`) at 08:00 NYT, converted to UTC.
       This aligns with the typical Y-B nightly tick wall-clock so the
       segment ordering matches what live events would carry.
    2. `now_utc - 1 day` — defensive fallback so all historical trades
       are after the baseline anchor.
  """
  windows_meta = src_cfg.get("wfo_windows_meta") or []
  latest: datetime | None = None
  for m in windows_meta if isinstance(windows_meta, list) else []:
    if not isinstance(m, dict):
      continue
    raw = m.get("oos_end")
    if not raw:
      continue
    try:
      d = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
      continue
    if latest is None or d > latest.date():
      # Compose at 08:00 NYT, treat as UTC for storage. The recorder
      # uses datetime.now(timezone.utc) for live events, so we mirror
      # that convention by pinning a tz-aware UTC datetime here.
      try:
        from zoneinfo import ZoneInfo
        nyt = ZoneInfo("America/New_York")
        wall = datetime.combine(d, time(8, 0)).replace(tzinfo=nyt)
        latest = wall.astimezone(timezone.utc)
      except Exception:
        latest = datetime.combine(d, time(13, 0), tzinfo=timezone.utc)  # ~08 NYT EDT
  if latest is not None:
    return latest
  return now_utc - timedelta(days=1)


def _compose_baseline_event_row(
  *, portfolio_id: int, basket: list[str], src_cfg: dict, anchor_ts: datetime,
  reconstructed_at: datetime,
) -> LiveEvent:
  """Build the baseline `phase2_evaluated_no_change` LiveEvent row.

  Uses the same Pydantic schema as live events so downstream readers
  see one coherent shape. `prior_basket == chosen_basket` and
  `basket_unchanged=True` because cold-save asserts no transition.
  """
  sf_snap = _build_session_filter_snapshot(src_cfg)

  # We don't have the original window batch_ids on hand here — derive
  # from PortfolioAsset rows in the calling function instead. For the
  # decision_inputs_hash we use an empty list as a stable sentinel
  # (cold-save events are explicitly synthetic; the hash distinguishes
  # them from real live events even on the same portfolio).
  decision_hash = compute_decision_inputs_hash(
    window_oos_start="",
    window_oos_end="",
    batch_ids=[],
    session_filter_snapshot=sf_snap,
    hybrid_min_hold=int(src_cfg.get("hybrid_min_hold", 0) or 0),
    hybrid_n_assets=int(src_cfg.get("hybrid_n_assets", 0) or 0),
    hybrid_n_trades=int(src_cfg.get("hybrid_n_trades", 0) or 0),
    hybrid_objective=src_cfg.get("hybrid_objective"),
    hybrid_scoring_method=src_cfg.get("hybrid_scoring_method"),
    hybrid_composite_weights=src_cfg.get("hybrid_composite_weights"),
    run_batch_map_fingerprint=fingerprint_run_batch_map({}),
  )

  details = Phase2EventDetails(
    decided_at_iso=anchor_ts.isoformat(),
    trigger="cold_save",
    recorder_version=RECORDER_VERSION,
    phase1_window_num=0,
    phase1_window_num_synth_for_stub=False,
    phase1_window_oos_start_iso="",
    phase1_window_oos_end_iso="",
    prior_basket=sorted(basket),
    chosen_basket=sorted(basket),
    added=[],
    removed=[],
    basket_unchanged=True,
    lock_size=int(src_cfg.get("hybrid_min_hold", 0) or 0),
    lock_progress={},
    session_filter_snapshot=sf_snap,
    decision_inputs_hash=decision_hash,
    latest_asset_scores={},
    kelly_allocation_id=None,
    reconstructed=True,
    reconstructed_at_iso=reconstructed_at.isoformat(),
    dispatch_outcome="success",  # baseline is purely audit; nothing to dispatch
    recorder_error=None,
  )

  return LiveEvent(
    portfolio_id=portfolio_id,
    event_type=EVENT_TYPE_NO_CHANGE,
    symbol="",
    details=details.model_dump_json(),
    created_at=anchor_ts,
  )


def cold_save_one(db, portfolio_id: int, *, dry_run: bool, now_utc: datetime | None = None) -> dict:
  """Backfill one portfolio. Returns a report dict."""
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if p is None:
    return {"portfolio_id": portfolio_id, "status": "missing", "reason": "portfolio not found"}

  if _has_existing_phase2_events(db, portfolio_id):
    return {
      "portfolio_id": portfolio_id, "portfolio_name": p.name,
      "status": "skipped", "reason": "phase-2 LiveEvent already exists",
    }

  try:
    src_cfg = json.loads(p.source_strategies) if p.source_strategies else {}
  except (ValueError, TypeError):
    src_cfg = {}

  basket = _current_basket_keys(db, portfolio_id)
  if not basket:
    return {
      "portfolio_id": portfolio_id, "portfolio_name": p.name,
      "status": "skipped", "reason": "no active PortfolioAsset rows — nothing to anchor",
    }

  anchor_ts = _resolve_anchor_timestamp(src_cfg, now_utc=now_utc or datetime.now(timezone.utc))
  reconstructed_at = now_utc or datetime.now(timezone.utc)

  row = _compose_baseline_event_row(
    portfolio_id=portfolio_id,
    basket=basket,
    src_cfg=src_cfg,
    anchor_ts=anchor_ts,
    reconstructed_at=reconstructed_at,
  )

  report = {
    "portfolio_id": portfolio_id,
    "portfolio_name": p.name,
    "status": "would-write" if dry_run else "written",
    "anchor_ts_utc": anchor_ts.isoformat(),
    "basket_size": len(basket),
    "basket_preview": basket[:3] + (["..."] if len(basket) > 3 else []),
  }
  if dry_run:
    return report

  db.add(row)
  db.commit()
  report["live_event_id"] = row.id
  return report


def cold_save_all(db, *, dry_run: bool, only_ids: list[int] | None = None) -> list[dict]:
  q = db.query(Portfolio).filter(
    Portfolio.is_deleted == False,  # noqa: E712
  )
  if only_ids:
    q = q.filter(Portfolio.id.in_(only_ids))
  ports = q.all()
  reports: list[dict] = []
  for p in ports:
    try:
      reports.append(cold_save_one(db, p.id, dry_run=dry_run))
    except Exception as e:
      log.exception("cold_save_one failed for pid=%s", p.id)
      reports.append({
        "portfolio_id": p.id, "portfolio_name": p.name,
        "status": "error", "reason": repr(e),
      })
  return reports


# ── T-002 v2 Item 3: Per-window cold-save ──────────────────────────

def _is_v1_baseline_event(e: LiveEvent) -> bool:
  """True iff `e` is a v1 single-row cold-save baseline (anchored at
  `max(oos_end)`, empty per-window window-iso fields). Used to decide
  when a portfolio's only existing cold-save row is safe to replace
  with the per-window reconstruction.

  Identifying signature:
    * event_type == phase2_evaluated_no_change
    * trigger == "cold_save"
    * reconstructed == True
    * phase1_window_oos_start_iso is "" or None (v1 used "" as sentinel)
  """
  if e.event_type != EVENT_TYPE_NO_CHANGE:
    return False
  try:
    d = json.loads(e.details or "{}")
  except (ValueError, TypeError):
    return False
  return (
    d.get("trigger") == "cold_save"
    and d.get("reconstructed") is True
    and (d.get("phase1_window_oos_start_iso") in ("", None))
  )


def _slice_records_to_window(
  records: list[dict], *, oos_start: str, oos_end: str,
) -> list[dict]:
  """Filter `records` to those with `dt` inside [oos_start, oos_end].

  Comparison is lexical on the ISO-8601 date portion (first 10 chars).
  ISO-8601 sorts lexically — safe for date and datetime strings alike.
  Records lacking `dt` are skipped (can't be sliced)."""
  out: list[dict] = []
  for rec in records:
    if not isinstance(rec, dict):
      continue
    dt_raw = rec.get("dt")
    if not dt_raw:
      continue
    dt_key = str(dt_raw)[:10]
    if dt_key < str(oos_start)[:10] or dt_key > str(oos_end)[:10]:
      continue
    out.append(rec)
  return out


def _basket_from_records(records: list[dict]) -> list[str]:
  """Derive `B{batch_id}_{symbol}_{Side}` basket keys from a list of
  `selected_records`. Mirrors live recorder's basket-key shape so the
  resolver treats reconstructed baskets identically. Records without
  `batch_id`, `symbol`, or a recognisable side are skipped."""
  keys: set[str] = set()
  for rec in records:
    sym = rec.get("symbol") or rec.get("asset")
    bid = rec.get("batch_id")
    side = rec.get("side") or rec.get("trade_side")
    if not sym or bid is None or not side:
      continue
    side_norm = str(side).strip().capitalize()
    if side_norm not in ("Long", "Short"):
      continue
    keys.add(f"B{int(bid)}_{sym}_{side_norm}")
  return sorted(keys)


def _anchor_window_oos_end_to_utc(oos_end: str) -> datetime:
  """Convert a window's `oos_end` date string to UTC datetime anchored
  at 08:00 NYT (matching the live nightly tick wall-clock)."""
  d = datetime.strptime(str(oos_end)[:10], "%Y-%m-%d").date()
  try:
    from zoneinfo import ZoneInfo
    nyt = ZoneInfo("America/New_York")
    wall = datetime.combine(d, time(8, 0)).replace(tzinfo=nyt)
    return wall.astimezone(timezone.utc)
  except Exception:
    # Fallback if zoneinfo isn't available; 13:00 UTC ≈ 08/09:00 NYT.
    return datetime.combine(d, time(13, 0), tzinfo=timezone.utc)


def _compose_per_window_event_row(
  *, portfolio_id: int, window_num: int, oos_start: str, oos_end: str,
  prior_basket: list[str], chosen_basket: list[str],
  src_cfg: dict, batch_ids: list[int], anchor_ts: datetime,
  reconstructed_at: datetime,
) -> LiveEvent:
  """Build one per-window reconstructed `phase2_evaluated_no_change`
  LiveEvent row.

  Differs from v1 baseline in three ways:
    * `phase1_window_oos_start_iso` / `phase1_window_oos_end_iso` carry
      THIS window's range (v1 used "" sentinel).
    * `decision_inputs_hash` is computed from the window's frozen
      inputs (v1 hashed the empty-string sentinel).
    * `prior_basket` is the previous window's `chosen_basket`, NOT a
      copy of the current basket (v1 set them equal).

  Lock state is genuinely lossy from `selected_records` alone — we
  set `lock_progress = {}`. The `reconstructed=True` badge is the
  user-visible honesty signal that this row's lock-residual data is
  empty by design, not by error.
  """
  sf_snap = _build_session_filter_snapshot(src_cfg)

  decision_hash = compute_decision_inputs_hash(
    window_oos_start=str(oos_start),
    window_oos_end=str(oos_end),
    batch_ids=sorted(batch_ids),
    session_filter_snapshot=sf_snap,
    hybrid_min_hold=int(src_cfg.get("hybrid_min_hold", 0) or 0),
    hybrid_n_assets=int(src_cfg.get("hybrid_n_assets", 0) or 0),
    hybrid_n_trades=int(src_cfg.get("hybrid_n_trades", 0) or 0),
    hybrid_objective=src_cfg.get("hybrid_objective"),
    hybrid_scoring_method=src_cfg.get("hybrid_scoring_method"),
    hybrid_composite_weights=src_cfg.get("hybrid_composite_weights"),
    run_batch_map_fingerprint=fingerprint_run_batch_map({}),
  )

  added = sorted(set(chosen_basket) - set(prior_basket))
  removed = sorted(set(prior_basket) - set(chosen_basket))
  unchanged = (not added) and (not removed)

  details = Phase2EventDetails(
    decided_at_iso=anchor_ts.isoformat(),
    trigger="cold_save",
    recorder_version=RECORDER_VERSION,
    phase1_window_num=int(window_num),
    phase1_window_num_synth_for_stub=False,
    phase1_window_oos_start_iso=str(oos_start),
    phase1_window_oos_end_iso=str(oos_end),
    prior_basket=sorted(prior_basket),
    chosen_basket=sorted(chosen_basket),
    added=added,
    removed=removed,
    basket_unchanged=unchanged,
    lock_size=int(src_cfg.get("hybrid_min_hold", 0) or 0),
    lock_progress={},
    session_filter_snapshot=sf_snap,
    decision_inputs_hash=decision_hash,
    latest_asset_scores={},
    kelly_allocation_id=None,
    reconstructed=True,
    reconstructed_at_iso=reconstructed_at.isoformat(),
    dispatch_outcome="success",
    recorder_error=None,
  )

  return LiveEvent(
    portfolio_id=portfolio_id,
    event_type=EVENT_TYPE_NO_CHANGE,
    symbol="",
    details=details.model_dump_json(),
    created_at=anchor_ts,
  )


def cold_save_one_per_window(
  db, portfolio_id: int, *, dry_run: bool,
  now_utc: datetime | None = None,
  replace_v1_baseline: bool = False,
) -> dict:
  """T-002 v2 Item 3 — per-window cold-save reconstruction.

  Walks `wfo_windows_meta` chronologically, slices `selected_records`
  to each window, derives the basket from the slice, and writes one
  reconstructed `phase2_evaluated_no_change` LiveEvent per window
  anchored at the window's `oos_end` 08:00 NYT.

  Closes the user-visible bug where pre-T-002 hybrid portfolios show
  TODAY'S basket on 6-month-old trades — the resolver now finds a
  per-window basket that genuinely reflects what was traded then.

  Idempotency
  -----------
  Default: skip if ANY phase-2 event already exists for the portfolio
  (matches v1 idempotency). When `replace_v1_baseline=True`, the
  function also detects a single v1-shape baseline row and deletes it
  before writing the per-window rows.

  Lossiness
  ---------
  Lock-state per asset is genuinely lossy from `selected_records`
  alone. `lock_progress={}` on every row, and `reconstructed=True`
  is the badge that flags this honestly to readers.
  """
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if p is None:
    return {"portfolio_id": portfolio_id, "status": "missing",
            "reason": "portfolio not found"}

  existing = (
    db.query(LiveEvent)
    .filter(
      LiveEvent.portfolio_id == portfolio_id,
      LiveEvent.event_type.in_(_PHASE2_EVENT_TYPES),
    )
    .order_by(LiveEvent.created_at.asc())
    .all()
  )
  if existing:
    is_only_v1 = (len(existing) == 1) and _is_v1_baseline_event(existing[0])
    if is_only_v1 and replace_v1_baseline:
      if not dry_run:
        db.delete(existing[0])
        db.flush()
    elif is_only_v1 and not replace_v1_baseline:
      return {
        "portfolio_id": portfolio_id, "portfolio_name": p.name,
        "status": "skipped",
        "reason": ("v1 baseline already exists — pass --replace-v1-baseline "
                   "to delete it and write per-window rows instead"),
        "v1_baseline_event_id": existing[0].id,
      }
    else:
      return {
        "portfolio_id": portfolio_id, "portfolio_name": p.name,
        "status": "skipped",
        "reason": "phase-2 LiveEvent already exists (not a v1 baseline)",
      }

  try:
    src_cfg = json.loads(p.source_strategies) if p.source_strategies else {}
  except (ValueError, TypeError):
    src_cfg = {}

  windows_meta = src_cfg.get("wfo_windows_meta") or []
  if not isinstance(windows_meta, list) or not windows_meta:
    return {"portfolio_id": portfolio_id, "portfolio_name": p.name,
            "status": "skipped", "reason": "no wfo_windows_meta — cannot reconstruct"}

  records: list[dict] = []
  step2 = src_cfg.get("step2_result") or {}
  if isinstance(step2, dict) and step2.get("selected_records"):
    records.extend(list(step2["selected_records"]))
  rsr = src_cfg.get("refresh_step2_result") or {}
  if isinstance(rsr, dict) and rsr.get("selected_records"):
    records.extend(list(rsr["selected_records"]))
  if not records:
    return {"portfolio_id": portfolio_id, "portfolio_name": p.name,
            "status": "skipped",
            "reason": "no selected_records to reconstruct from"}

  sorted_windows = sorted(
    (w for w in windows_meta if isinstance(w, dict)
     and w.get("oos_start") and w.get("oos_end")),
    key=lambda w: str(w.get("oos_start") or ""),
  )
  if not sorted_windows:
    return {"portfolio_id": portfolio_id, "portfolio_name": p.name,
            "status": "skipped",
            "reason": "wfo_windows_meta has no windows with valid oos_start/oos_end"}

  reconstructed_at = now_utc or datetime.now(timezone.utc)
  prior_basket: list[str] = []
  written_ids: list[int | None] = []
  per_window: list[dict] = []
  rows_to_add: list[LiveEvent] = []

  for w in sorted_windows:
    oos_start = str(w["oos_start"])
    oos_end = str(w["oos_end"])
    sliced = _slice_records_to_window(records, oos_start=oos_start, oos_end=oos_end)
    chosen_basket = _basket_from_records(sliced)
    batch_ids = sorted({
      int(r["batch_id"]) for r in sliced
      if isinstance(r.get("batch_id"), (int, float))
    })
    try:
      anchor_ts = _anchor_window_oos_end_to_utc(oos_end)
    except (ValueError, TypeError):
      continue
    row = _compose_per_window_event_row(
      portfolio_id=portfolio_id,
      window_num=int(w.get("window_num") or 0),
      oos_start=oos_start, oos_end=oos_end,
      prior_basket=prior_basket, chosen_basket=chosen_basket,
      src_cfg=src_cfg, batch_ids=batch_ids,
      anchor_ts=anchor_ts, reconstructed_at=reconstructed_at,
    )
    rows_to_add.append(row)
    per_window.append({
      "window_num": int(w.get("window_num") or 0),
      "oos_start": oos_start, "oos_end": oos_end,
      "basket_size": len(chosen_basket),
      "added": len(set(chosen_basket) - set(prior_basket)),
      "removed": len(set(prior_basket) - set(chosen_basket)),
    })
    prior_basket = chosen_basket

  if not rows_to_add:
    return {"portfolio_id": portfolio_id, "portfolio_name": p.name,
            "status": "skipped",
            "reason": "no usable windows after parsing"}

  if dry_run:
    return {
      "portfolio_id": portfolio_id, "portfolio_name": p.name,
      "status": "would-write",
      "windows_written": len(rows_to_add),
      "per_window_summary": per_window,
    }

  for row in rows_to_add:
    db.add(row)
  db.flush()
  written_ids = [row.id for row in rows_to_add]
  db.commit()

  return {
    "portfolio_id": portfolio_id, "portfolio_name": p.name,
    "status": "written",
    "windows_written": len(written_ids),
    "live_event_ids": written_ids,
    "per_window_summary": per_window,
  }


def cold_save_all_per_window(
  db, *, dry_run: bool, only_ids: list[int] | None = None,
  replace_v1_baseline: bool = False,
) -> list[dict]:
  """Run `cold_save_one_per_window` across portfolios. Same scoping
  semantics as `cold_save_all`."""
  q = db.query(Portfolio).filter(
    Portfolio.is_deleted == False,  # noqa: E712
  )
  if only_ids:
    q = q.filter(Portfolio.id.in_(only_ids))
  ports = q.all()
  reports: list[dict] = []
  for p in ports:
    try:
      reports.append(cold_save_one_per_window(
        db, p.id, dry_run=dry_run,
        replace_v1_baseline=replace_v1_baseline,
      ))
    except Exception as e:
      log.exception("cold_save_one_per_window failed for pid=%s", p.id)
      reports.append({
        "portfolio_id": p.id, "portfolio_name": p.name,
        "status": "error", "reason": repr(e),
      })
  return reports


# ── T-002 v3: Full-history three-source cold-save ─────────────────
#
# Closes the two gaps v2 left:
#   * Gap 1 — research-period per-swap fidelity (pre-`optimization_end_date`)
#     reconstructed from `source_strategies.phase2_selection_changes`.
#   * Gap 2 — trailing-live gap (`optimization_end_date` → first real
#     T-002 event) reconstructed from `KellyAllocation` rows (every
#     pre-T-002 nightly refresh wrote one of these).
#
# Strictly additive — does NOT touch v1 baseline or v2 per-window code
# paths. Operator opts in via `--full-history`.

_RECONSTRUCTED_FROM_SELECTION_CHANGES = "selection_changes"
_RECONSTRUCTED_FROM_KELLY_ALLOCATION = "kelly_allocation"


def _stable_sha256(parts: list[str]) -> str:
  """Stable, content-addressable hash over `parts` joined with `|`.

  Used for v3 dedup hashes — must NOT depend on DB-assigned ids so the
  hash survives migration / cross-DB import (ATLAS pushback #3)."""
  joined = "|".join(parts)
  return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _pass_a_decision_hash(
  *, swap_date_iso: str, window_num: int | None,
  added: list[str], removed: list[str], intra_day_seq: int,
) -> str:
  """Pass-A per-swap decision hash. ATLAS-approved spec:
  `(swap_date_iso, window_num, sorted(added), sorted(removed),
  intra_day_seq)`. Distinct per swap, stable across re-runs, content-
  addressable. Independent from v2's per-window hash."""
  return _stable_sha256([
    "pass_a",
    str(swap_date_iso),
    str(window_num if window_num is not None else "-"),
    ",".join(sorted(str(x) for x in added)),
    ",".join(sorted(str(x) for x in removed)),
    str(int(intra_day_seq)),
  ])


def _pass_b_decision_hash(
  *, base_hash: str, portfolio_id: int,
  computed_at_iso: str, chosen_basket: list[str],
) -> str:
  """Pass-B per-night decision hash. ATLAS-approved spec:
  `compute_decision_inputs_hash + (portfolio_id, computed_at_iso,
  sorted(chosen_basket))`. No `kelly_row.id` (DB auto-increment is
  non-portable across migrations / re-imports)."""
  return _stable_sha256([
    "pass_b",
    str(base_hash),
    str(int(portfolio_id)),
    str(computed_at_iso),
    ",".join(sorted(str(x) for x in chosen_basket)),
  ])


def _basket_from_kelly_row(kr: KellyAllocation) -> list[str]:
  """Read the canonical basket from a KellyAllocation row.

  Per ATLAS pushback #1 and `hybrid_portfolio_refresh.py:714-725`,
  `result_json["basket"]` is the canonical field — written explicitly
  by `_extract_basket()` (`:186-200`). `weights_json` is DERIVED from
  `basket`, not the other way around. Read result_json["basket"] first;
  fall back to `weights_json.keys()` only if missing/empty.

  Returns sorted basket keys. Empty list on parse failure or both
  fields missing."""
  if kr is None:
    return []

  result = kr.result_json
  if result:
    try:
      result_d = json.loads(result)
    except (ValueError, TypeError):
      result_d = None
    if isinstance(result_d, dict):
      basket = result_d.get("basket")
      if isinstance(basket, list) and basket:
        return sorted(str(x) for x in basket)

  return _basket_from_kelly_weights(kr.weights_json)


def _is_v2_per_window_event(e: LiveEvent) -> bool:
  """True iff `e` is a v2 per-window cold-save row.

  Identifying signature:
    * trigger == "cold_save"
    * reconstructed == True
    * phase1_window_oos_start_iso non-empty (distinguishes from v1)
    * details.reconstructed_from is missing (distinguishes from v3,
      which always writes the field)
  """
  if e.event_type not in _PHASE2_EVENT_TYPES:
    return False
  try:
    d = json.loads(e.details or "{}")
  except (ValueError, TypeError):
    return False
  return (
    d.get("trigger") == "cold_save"
    and d.get("reconstructed") is True
    and bool(d.get("phase1_window_oos_start_iso"))
    and "reconstructed_from" not in d
  )


def _is_v3_era1_event(e: LiveEvent) -> bool:
  """True iff `e` is a v3 Pass-A row (per-swap from
  `phase2_selection_changes`)."""
  if e.event_type not in _PHASE2_EVENT_TYPES:
    return False
  try:
    d = json.loads(e.details or "{}")
  except (ValueError, TypeError):
    return False
  return (
    d.get("trigger") == "cold_save"
    and d.get("reconstructed") is True
    and d.get("reconstructed_from") == _RECONSTRUCTED_FROM_SELECTION_CHANGES
  )


def _is_v3_era2_event(e: LiveEvent) -> bool:
  """True iff `e` is a v3 Pass-B row (per-night from
  `KellyAllocation`). Includes the legacy `kelly_inferred` value so a
  pre-AGREEMENT draft of v3 (if any rows leaked) is still recognised
  as v3 Era 2 and gets the same default-skip / rewrite handling."""
  if e.event_type not in _PHASE2_EVENT_TYPES:
    return False
  try:
    d = json.loads(e.details or "{}")
  except (ValueError, TypeError):
    return False
  return (
    d.get("trigger") == "cold_save"
    and d.get("reconstructed") is True
    and d.get("reconstructed_from") in (
      _RECONSTRUCTED_FROM_KELLY_ALLOCATION, "kelly_inferred",
    )
  )


def _is_v3_full_history_event(e: LiveEvent) -> bool:
  """True iff `e` is ANY v3 row (Era 1 OR Era 2). Convenience union
  used by the resolver-chain integration tests."""
  return _is_v3_era1_event(e) or _is_v3_era2_event(e)


def _is_real_t002_event(e: LiveEvent) -> bool:
  """True iff `e` is a real (non-cold-save) phase-2 event from the
  forward-period recorder."""
  if e.event_type not in _PHASE2_EVENT_TYPES:
    return False
  try:
    d = json.loads(e.details or "{}")
  except (ValueError, TypeError):
    return False
  return d.get("trigger") != "cold_save"


def _reconstruct_initial_basket(
  p: Portfolio, changes_research: list[dict],
) -> list[str]:
  """Rebuild the research-period starting basket.

  Prefers `source_strategies.phase2_initial_basket` if present. Falls
  back to reverse-replay from `is_active` PortfolioAsset rows (matches
  `_stitch_event_based_walkforward` in portfolio_analysis.py:797-807).
  """
  try:
    src = json.loads(p.source_strategies) if p.source_strategies else {}
  except (ValueError, TypeError):
    src = {}
  stored = src.get("phase2_initial_basket")
  if stored:
    return sorted(str(x) for x in stored)

  if not changes_research:
    return []

  final_assets = {
    f"{a.symbol}_{a.trade_side}"
    for a in p.assets
    if a.is_active and a.trade_side in ("Long", "Short")
  }
  reconstructed = set(final_assets)
  for c in sorted(
    changes_research, key=lambda x: str(x.get("date", "")), reverse=True,
  ):
    for a in (c.get("added") or []):
      reconstructed.discard(str(a))
    for a in (c.get("removed") or []):
      reconstructed.add(str(a))
  return sorted(reconstructed)


def _anchor_swap_date_to_utc(date_str: str, *, minute_offset: int = 0) -> datetime:
  """Convert a swap-date string to UTC anchored at 08:00 NYT + N minutes.

  `minute_offset` preserves intra-day swap order when multiple changes
  land on the same date (entry index in the original list)."""
  d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
  try:
    from zoneinfo import ZoneInfo
    nyt = ZoneInfo("America/New_York")
    wall = datetime.combine(d, time(8, 0)).replace(tzinfo=nyt)
    base = wall.astimezone(timezone.utc)
  except Exception:
    base = datetime.combine(d, time(13, 0), tzinfo=timezone.utc)
  return base + timedelta(minutes=minute_offset)


def _window_iso_for_swap(
  windows_meta: list[dict], window_num: int | None,
) -> tuple[str, str]:
  """Look up the (oos_start, oos_end) ISO range for the WFO window
  matching `window_num`. Returns ('', '') when the lookup fails — the
  swap row stays writable; resolver doesn't depend on these fields."""
  if window_num is None:
    return "", ""
  for w in windows_meta or []:
    if not isinstance(w, dict):
      continue
    if int(w.get("window_num") or 0) == int(window_num):
      return str(w.get("oos_start") or ""), str(w.get("oos_end") or "")
  return "", ""


def _compose_per_swap_event_row(
  *, portfolio_id: int, swap_date: str, window_num: int | None,
  oos_start: str, oos_end: str,
  prior_basket: list[str], chosen_basket: list[str],
  added: list[str], removed: list[str],
  src_cfg: dict, anchor_ts: datetime, reconstructed_at: datetime,
  intra_day_seq: int,
) -> LiveEvent:
  """Build one Pass-A reconstructed LiveEvent for a single swap entry
  in `phase2_selection_changes`.

  `intra_day_seq` is the 0-based ordinal of this swap within its
  `swap_date` (per ATLAS pushback #3 spec). 0 for single-swap days,
  1+ for additional same-day swaps. Distinguishes multiple swaps on
  the same date in the portable per-swap hash."""
  sf_snap = _build_session_filter_snapshot(src_cfg)

  decision_hash = _pass_a_decision_hash(
    swap_date_iso=str(swap_date)[:10],
    window_num=window_num,
    added=added, removed=removed,
    intra_day_seq=int(intra_day_seq),
  )

  unchanged = (not added) and (not removed)
  details = Phase2EventDetails(
    decided_at_iso=anchor_ts.isoformat(),
    trigger="cold_save",
    recorder_version=RECORDER_VERSION,
    phase1_window_num=int(window_num) if window_num is not None else 0,
    phase1_window_num_synth_for_stub=False,
    phase1_window_oos_start_iso=str(oos_start),
    phase1_window_oos_end_iso=str(oos_end),
    prior_basket=sorted(prior_basket),
    chosen_basket=sorted(chosen_basket),
    added=sorted(added),
    removed=sorted(removed),
    basket_unchanged=unchanged,
    lock_size=int(src_cfg.get("hybrid_min_hold", 0) or 0),
    lock_progress={},
    session_filter_snapshot=sf_snap,
    decision_inputs_hash=decision_hash,
    latest_asset_scores={},
    kelly_allocation_id=None,
    reconstructed=True,
    reconstructed_at_iso=reconstructed_at.isoformat(),
    dispatch_outcome="success",
    recorder_error=None,
  )
  payload = json.loads(details.model_dump_json())
  payload["reconstructed_from"] = _RECONSTRUCTED_FROM_SELECTION_CHANGES
  payload["swap_date"] = str(swap_date)[:10]
  payload["intra_day_seq"] = int(intra_day_seq)

  return LiveEvent(
    portfolio_id=portfolio_id,
    event_type=EVENT_TYPE_NO_CHANGE if unchanged else EVENT_TYPE_HYBRID_REFRESH,
    symbol="",
    details=json.dumps(payload),
    created_at=anchor_ts,
  )


def _compose_kelly_allocation_event_row(
  *, portfolio_id: int, kelly_row: KellyAllocation,
  prior_basket: list[str], chosen_basket: list[str],
  src_cfg: dict, oos_start_iso: str, oos_end_iso: str,
  reconstructed_at: datetime,
) -> LiveEvent:
  """Build one Pass-B reconstructed LiveEvent from a `KellyAllocation`
  row written by the pre-T-002 nightly refresh.

  `chosen_basket` is the canonical basket from
  `_basket_from_kelly_row(kelly_row)` — reads `result_json["basket"]`
  first (canonical, written by `_extract_basket()` at
  `hybrid_portfolio_refresh.py:186-200`), falls back to
  `weights_json.keys()` only if missing. Per ATLAS pushback #1.

  `prior_basket` is the previous Pass-B row's chosen_basket (or the last
  Pass-A basket / initial basket for the first Pass-B row).

  Known v3 limitation (ATLAS Era-2 Q1, accepted by the owner): manual user
  swaps recorded only in `Portfolio.swap_history` are NOT reflected in
  `KellyAllocation` rows. If a manual swap fell between two nightly
  Kelly refreshes, Pass B will miss it and the closing parity assertion
  in `cold_save_one_full_history` is the safety net — it surfaces the
  mismatch as a hard skip with a diff for the operator. If parity ever
  fires for this reason in production, a Pass D for `swap_history`
  becomes a follow-up. Not v3 scope."""
  sf_snap = _build_session_filter_snapshot(src_cfg)

  added = sorted(set(chosen_basket) - set(prior_basket))
  removed = sorted(set(prior_basket) - set(chosen_basket))
  unchanged = (not added) and (not removed)

  computed_at = kelly_row.computed_at
  if computed_at is None:
    computed_at = reconstructed_at
  if computed_at.tzinfo is None:
    computed_at = computed_at.replace(tzinfo=timezone.utc)

  base_hash = compute_decision_inputs_hash(
    window_oos_start=str(oos_start_iso),
    window_oos_end=str(oos_end_iso),
    batch_ids=[],
    session_filter_snapshot=sf_snap,
    hybrid_min_hold=int(src_cfg.get("hybrid_min_hold", 0) or 0),
    hybrid_n_assets=int(src_cfg.get("hybrid_n_assets", 0) or 0),
    hybrid_n_trades=int(src_cfg.get("hybrid_n_trades", 0) or 0),
    hybrid_objective=src_cfg.get("hybrid_objective"),
    hybrid_scoring_method=src_cfg.get("hybrid_scoring_method"),
    hybrid_composite_weights=src_cfg.get("hybrid_composite_weights"),
    run_batch_map_fingerprint=fingerprint_run_batch_map({}),
  )
  decision_hash = _pass_b_decision_hash(
    base_hash=base_hash, portfolio_id=portfolio_id,
    computed_at_iso=computed_at.isoformat(),
    chosen_basket=chosen_basket,
  )

  details = Phase2EventDetails(
    decided_at_iso=computed_at.isoformat(),
    trigger="cold_save",
    recorder_version=RECORDER_VERSION,
    phase1_window_num=0,
    phase1_window_num_synth_for_stub=False,
    phase1_window_oos_start_iso=str(oos_start_iso),
    phase1_window_oos_end_iso=str(oos_end_iso),
    prior_basket=sorted(prior_basket),
    chosen_basket=sorted(chosen_basket),
    added=added,
    removed=removed,
    basket_unchanged=unchanged,
    lock_size=int(src_cfg.get("hybrid_min_hold", 0) or 0),
    lock_progress={},
    session_filter_snapshot=sf_snap,
    decision_inputs_hash=decision_hash,
    latest_asset_scores={},
    kelly_allocation_id=int(kelly_row.id) if kelly_row.id else None,
    reconstructed=True,
    reconstructed_at_iso=reconstructed_at.isoformat(),
    dispatch_outcome="success",
    recorder_error=None,
  )
  payload = json.loads(details.model_dump_json())
  payload["reconstructed_from"] = _RECONSTRUCTED_FROM_KELLY_ALLOCATION
  payload["kelly_trigger"] = str(kelly_row.trigger or "")

  return LiveEvent(
    portfolio_id=portfolio_id,
    event_type=EVENT_TYPE_NO_CHANGE if unchanged else EVENT_TYPE_HYBRID_REFRESH,
    symbol="",
    details=json.dumps(payload),
    created_at=computed_at,
  )


def _basket_from_kelly_weights(weights_json: str | None) -> list[str]:
  """Extract sorted basket keys from a `KellyAllocation.weights_json`
  dict. Returns [] on parse failure or missing input."""
  if not weights_json:
    return []
  try:
    weights = json.loads(weights_json)
  except (ValueError, TypeError):
    return []
  if not isinstance(weights, dict):
    return []
  return sorted(str(k) for k in weights.keys())


def _classify_existing_events(
  rows: list[LiveEvent],
) -> dict[str, list[LiveEvent]]:
  """Group existing phase-2 events by reconstruction provenance so the
  full-history driver can decide what to delete vs. preserve.

  Per VISION delta #4 (four-class detector): v3 is split into
  `v3_era1` (Pass A, per-swap from `phase2_selection_changes`) and
  `v3_era2` (Pass B, per-night from `KellyAllocation`). The
  legacy/union bucket `v3_full_history` is also populated for any
  caller that doesn't care about the split. Both era buckets share
  rewrite/preserve semantics, but separate classification lets tests
  + diagnostics report them distinctly."""
  buckets: dict[str, list[LiveEvent]] = {
    "v1_baseline": [], "v2_per_window": [],
    "v3_era1": [], "v3_era2": [], "v3_full_history": [],
    "real_t002": [], "other": [],
  }
  for r in rows:
    if _is_v1_baseline_event(r):
      buckets["v1_baseline"].append(r)
    elif _is_v2_per_window_event(r):
      buckets["v2_per_window"].append(r)
    elif _is_v3_era1_event(r):
      buckets["v3_era1"].append(r)
      buckets["v3_full_history"].append(r)
    elif _is_v3_era2_event(r):
      buckets["v3_era2"].append(r)
      buckets["v3_full_history"].append(r)
    elif _is_real_t002_event(r):
      buckets["real_t002"].append(r)
    else:
      buckets["other"].append(r)
  return buckets


def _build_pass_a_rows(
  *, portfolio_id: int, p: Portfolio, src_cfg: dict,
  reconstructed_at: datetime,
) -> tuple[list[LiveEvent], list[str], list[dict]]:
  """Build Pass-A (per-swap) reconstructed rows.

  Returns (rows, final_basket_after_pass_a, summary_per_swap).
  `final_basket_after_pass_a` is the running basket the next pass
  should use as its `prior_basket` seed. Empty list when no swaps
  exist.

  Boundary rule (ATLAS pushback #2): swaps with `swap_date >
  optimization_end_date` are skipped — they belong to Era 2 and will
  be reconstructed by Pass B from `KellyAllocation` rows. Era 1 ends
  at OED (inclusive of OED date)."""
  changes = src_cfg.get("phase2_selection_changes") or []
  if not isinstance(changes, list) or not changes:
    return [], _reconstruct_initial_basket(p, []), []

  windows_meta = src_cfg.get("wfo_windows_meta") or []
  if not isinstance(windows_meta, list):
    windows_meta = []

  oed = p.optimization_end_date
  oed_date_iso: str | None = None
  if oed is not None:
    if oed.tzinfo is None:
      oed = oed.replace(tzinfo=timezone.utc)
    oed_date_iso = oed.date().isoformat()

  sorted_changes = sorted(changes, key=lambda c: str(c.get("date", "")))
  running_basket = _reconstruct_initial_basket(p, sorted_changes)

  rows: list[LiveEvent] = []
  summary: list[dict] = []
  by_date_minute: dict[str, int] = {}

  for entry in sorted_changes:
    if not isinstance(entry, dict):
      continue
    swap_date_raw = entry.get("date")
    if not swap_date_raw:
      continue
    swap_date = str(swap_date_raw)[:10]
    if oed_date_iso is not None and swap_date > oed_date_iso:
      continue
    added = sorted(str(x) for x in (entry.get("added") or []))
    removed = sorted(str(x) for x in (entry.get("removed") or []))
    window_num = entry.get("window_num")
    oos_start, oos_end = _window_iso_for_swap(windows_meta, window_num)

    prior = list(running_basket)
    new_basket = (set(running_basket) | set(added)) - set(removed)
    chosen = sorted(new_basket)

    intra_day_seq = by_date_minute.get(swap_date, 0)
    by_date_minute[swap_date] = intra_day_seq + 1
    anchor_ts = _anchor_swap_date_to_utc(swap_date, minute_offset=intra_day_seq)

    rows.append(_compose_per_swap_event_row(
      portfolio_id=portfolio_id, swap_date=swap_date, window_num=window_num,
      oos_start=oos_start, oos_end=oos_end,
      prior_basket=prior, chosen_basket=chosen,
      added=added, removed=removed,
      src_cfg=src_cfg, anchor_ts=anchor_ts,
      reconstructed_at=reconstructed_at,
      intra_day_seq=intra_day_seq,
    ))
    summary.append({
      "swap_date": swap_date, "window_num": window_num,
      "added": added, "removed": removed,
      "basket_size_after": len(chosen),
      "anchor_ts_utc": anchor_ts.isoformat(),
      "intra_day_seq": intra_day_seq,
    })
    running_basket = chosen

  return rows, running_basket, summary


def _build_pass_b_rows(
  db, *, portfolio_id: int, p: Portfolio, src_cfg: dict,
  pass_a_seed_basket: list[str], pass_a_last_anchor_utc: datetime | None,
  earliest_real_t002_utc: datetime | None,
  reconstructed_at: datetime,
) -> tuple[list[LiveEvent], list[dict]]:
  """Build Pass-B (per-night from KellyAllocation) reconstructed rows.

  Boundary rules (ATLAS pushback #2):
    * Lower bound = `optimization_end_date` ONLY. KellyAllocation rows
      with `computed_at <= oed` are skipped (Era 1 covers them via
      Pass A which now caps at OED). Dropping `pass_a_last_anchor_utc`
      from the candidates avoids a subtle bug where a Kelly row that
      ran the same morning as the last Pass-A swap would be silently
      excluded — Pass A and Pass B now share OED as the clean cut.
    * Upper bound = `earliest_real_t002_utc - 1 second` if any real
      T-002 event exists, else `now`. Avoids overlapping the forward
      recorder.

  `pass_a_last_anchor_utc` is retained in the signature for telemetry
  / future re-use but is no longer consulted for the boundary.
  """
  oed = p.optimization_end_date
  if oed is not None and oed.tzinfo is None:
    oed = oed.replace(tzinfo=timezone.utc)

  lower_bound = oed

  q = db.query(KellyAllocation).filter(
    KellyAllocation.portfolio_id == portfolio_id,
  )
  rows_kelly = q.order_by(KellyAllocation.computed_at.asc()).all()
  if not rows_kelly:
    return [], []

  oos_start_iso = oed.isoformat() if oed is not None else ""
  oos_end_iso = (
    earliest_real_t002_utc.isoformat()
    if earliest_real_t002_utc is not None
    else reconstructed_at.isoformat()
  )

  rows: list[LiveEvent] = []
  summary: list[dict] = []
  prior_basket = list(pass_a_seed_basket)

  for kr in rows_kelly:
    computed_at = kr.computed_at
    if computed_at is None:
      continue
    if computed_at.tzinfo is None:
      computed_at = computed_at.replace(tzinfo=timezone.utc)

    if lower_bound is not None and computed_at <= lower_bound:
      continue
    if (
      earliest_real_t002_utc is not None
      and computed_at >= earliest_real_t002_utc
    ):
      continue

    chosen = _basket_from_kelly_row(kr)
    if not chosen:
      continue

    rows.append(_compose_kelly_allocation_event_row(
      portfolio_id=portfolio_id, kelly_row=kr,
      prior_basket=prior_basket, chosen_basket=chosen,
      src_cfg=src_cfg,
      oos_start_iso=oos_start_iso, oos_end_iso=oos_end_iso,
      reconstructed_at=reconstructed_at,
    ))
    summary.append({
      "kelly_allocation_id": kr.id,
      "computed_at_utc": computed_at.isoformat(),
      "basket_size": len(chosen),
      "added": sorted(set(chosen) - set(prior_basket)),
      "removed": sorted(set(prior_basket) - set(chosen)),
    })
    prior_basket = chosen

  return rows, summary


def cold_save_one_full_history(
  db, portfolio_id: int, *, dry_run: bool,
  now_utc: datetime | None = None,
  replace_v1_baseline: bool = False,
  replace_v2_per_window: bool = False,
  rewrite_full_history: bool = False,
  skip_parity_check: bool = False,
) -> dict:
  """T-002 v3 — full-history three-source cold-save.

  Pass A: per-swap from `phase2_selection_changes`
          (Era 1 — research period).
  Pass B: per-night from `KellyAllocation` rows
          (Era 2 — post-save, pre-T-002 nightly history).
  Pass C: idempotency cleanup. Default skips on existing v3 rows;
          opt-in flags delete v1-baseline / v2-per-window / v3 rows
          before writing the new chain.

  All deletes + writes happen in the same SAVEPOINT so a failure in
  any pass rolls back the entire upgrade. The closing parity assertion
  (delta #6) compares the resolver basket from the just-written events
  to `portfolio.is_active` PortfolioAssets — mismatch raises
  `FullHistoryParityError`, the SAVEPOINT rolls back, and the report
  surfaces the diff. Set `skip_parity_check=True` to bypass (only used
  by tests where PortfolioAsset rows are intentionally not seeded —
  production CLI never sets this)."""
  p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if p is None:
    return {"portfolio_id": portfolio_id, "status": "missing",
            "reason": "portfolio not found"}

  existing = (
    db.query(LiveEvent)
    .filter(
      LiveEvent.portfolio_id == portfolio_id,
      LiveEvent.event_type.in_(_PHASE2_EVENT_TYPES),
    )
    .order_by(LiveEvent.created_at.asc())
    .all()
  )
  buckets = _classify_existing_events(existing)

  if buckets["other"]:
    return {
      "portfolio_id": portfolio_id, "portfolio_name": p.name,
      "status": "skipped",
      "reason": ("phase-2 LiveEvents present that aren't recognised "
                 "as v1/v2/v3 cold-save or real T-002 — refusing to "
                 "modify; inspect manually"),
      "unrecognised_event_count": len(buckets["other"]),
    }

  if buckets["v3_full_history"] and not rewrite_full_history:
    return {
      "portfolio_id": portfolio_id, "portfolio_name": p.name,
      "status": "skipped",
      "reason": ("v3 full-history rows already exist — pass "
                 "--rewrite-full-history to delete and rebuild"),
      "v3_event_count": len(buckets["v3_full_history"]),
    }

  if buckets["v1_baseline"] and not replace_v1_baseline:
    return {
      "portfolio_id": portfolio_id, "portfolio_name": p.name,
      "status": "skipped",
      "reason": ("v1 baseline present — pass --replace-v1-baseline to "
                 "delete and write the full-history chain"),
      "v1_baseline_count": len(buckets["v1_baseline"]),
    }
  if buckets["v2_per_window"] and not replace_v2_per_window:
    return {
      "portfolio_id": portfolio_id, "portfolio_name": p.name,
      "status": "skipped",
      "reason": ("v2 per-window rows present — pass "
                 "--replace-v2-per-window to delete and write the "
                 "full-history chain"),
      "v2_per_window_count": len(buckets["v2_per_window"]),
    }

  try:
    src_cfg = json.loads(p.source_strategies) if p.source_strategies else {}
  except (ValueError, TypeError):
    src_cfg = {}

  reconstructed_at = now_utc or datetime.now(timezone.utc)

  pass_a_rows, pass_a_seed, pass_a_summary = _build_pass_a_rows(
    portfolio_id=portfolio_id, p=p, src_cfg=src_cfg,
    reconstructed_at=reconstructed_at,
  )
  pass_a_last_anchor_utc: datetime | None = None
  if pass_a_rows:
    pass_a_last_anchor_utc = pass_a_rows[-1].created_at

  earliest_real_t002_utc: datetime | None = None
  if buckets["real_t002"]:
    ts = buckets["real_t002"][0].created_at
    if ts is not None:
      earliest_real_t002_utc = (
        ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
      )

  pass_b_rows, pass_b_summary = _build_pass_b_rows(
    db, portfolio_id=portfolio_id, p=p, src_cfg=src_cfg,
    pass_a_seed_basket=pass_a_seed,
    pass_a_last_anchor_utc=pass_a_last_anchor_utc,
    earliest_real_t002_utc=earliest_real_t002_utc,
    reconstructed_at=reconstructed_at,
  )

  total_rows = len(pass_a_rows) + len(pass_b_rows)
  if total_rows == 0:
    return {
      "portfolio_id": portfolio_id, "portfolio_name": p.name,
      "status": "skipped",
      "reason": ("no phase2_selection_changes AND no KellyAllocation "
                 "rows — nothing to reconstruct"),
    }

  to_delete: list[LiveEvent] = []
  if rewrite_full_history:
    to_delete.extend(buckets["v3_full_history"])
  if replace_v1_baseline:
    to_delete.extend(buckets["v1_baseline"])
  if replace_v2_per_window:
    to_delete.extend(buckets["v2_per_window"])

  report: dict = {
    "portfolio_id": portfolio_id, "portfolio_name": p.name,
    "status": "would-write" if dry_run else "written",
    "pass_a_rows": len(pass_a_rows),
    "pass_b_rows": len(pass_b_rows),
    "deletes_v1_baseline": len(buckets["v1_baseline"]) if replace_v1_baseline else 0,
    "deletes_v2_per_window": len(buckets["v2_per_window"]) if replace_v2_per_window else 0,
    "deletes_v3_existing": len(buckets["v3_full_history"]) if rewrite_full_history else 0,
    "preserved_real_t002": len(buckets["real_t002"]),
    "pass_a_summary": pass_a_summary,
    "pass_b_summary": pass_b_summary,
    "earliest_real_t002_utc": (
      earliest_real_t002_utc.isoformat() if earliest_real_t002_utc else None
    ),
  }

  if dry_run:
    return report

  # All deletes + writes + parity check go inside a SAVEPOINT (delta #6).
  # If the closing parity assertion fires, the SAVEPOINT rolls back so no
  # partial v3 chain is committed. The driver caller catches the error
  # and surfaces it in the per-portfolio report — other portfolios in
  # the batch keep running.
  savepoint = db.begin_nested()
  try:
    for row in to_delete:
      db.delete(row)
    if to_delete:
      db.flush()

    for row in pass_a_rows:
      db.add(row)
    for row in pass_b_rows:
      db.add(row)
    db.flush()
    written_ids = [r.id for r in pass_a_rows] + [r.id for r in pass_b_rows]

    if skip_parity_check:
      expected_basket = set()
      now_nyt = _utc_to_nyt_naive(now_utc or datetime.now(timezone.utc))
    else:
      segments = iter_basket_segments(db, portfolio_id)
      now_nyt = _utc_to_nyt_naive(now_utc or datetime.now(timezone.utc))
      expected_basket = resolve_basket_at_t(segments, now_nyt)
      got_basket = set(_current_basket_keys(db, portfolio_id))
      if expected_basket != got_basket:
        raise FullHistoryParityError(
          expected=expected_basket, got=got_basket,
        )

    savepoint.commit()
  except FullHistoryParityError as exc:
    savepoint.rollback()
    return {
      "portfolio_id": portfolio_id, "portfolio_name": p.name,
      "status": "parity-failed",
      "reason": ("closing parity assertion fired — resolver basket "
                 "from the just-written v3 chain does not match "
                 "portfolio.is_active. SAVEPOINT rolled back; no rows "
                 "committed. See `parity_diff` for the symbol-level "
                 "delta."),
      "parity_diff": {
        "expected_only": sorted(exc.expected - exc.got),
        "got_only": sorted(exc.got - exc.expected),
        "expected_size": len(exc.expected),
        "got_size": len(exc.got),
      },
      "pass_a_rows": len(pass_a_rows),
      "pass_b_rows": len(pass_b_rows),
    }
  except Exception:
    savepoint.rollback()
    raise

  db.commit()

  report["live_event_ids"] = written_ids
  report["parity_check"] = {
    "passed": True if not skip_parity_check else None,
    "skipped": bool(skip_parity_check),
    "basket_size": len(expected_basket),
    "checked_at_nyt": now_nyt.isoformat(),
  }
  return report


def cold_save_all_full_history(
  db, *, dry_run: bool, only_ids: list[int] | None = None,
  replace_v1_baseline: bool = False,
  replace_v2_per_window: bool = False,
  rewrite_full_history: bool = False,
  skip_parity_check: bool = False,
) -> list[dict]:
  """Run `cold_save_one_full_history` across portfolios. Same scoping
  semantics as the v1/v2 batch drivers."""
  q = db.query(Portfolio).filter(
    Portfolio.is_deleted == False,  # noqa: E712
  )
  if only_ids:
    q = q.filter(Portfolio.id.in_(only_ids))
  ports = q.all()
  reports: list[dict] = []
  for p in ports:
    try:
      reports.append(cold_save_one_full_history(
        db, p.id, dry_run=dry_run,
        replace_v1_baseline=replace_v1_baseline,
        replace_v2_per_window=replace_v2_per_window,
        rewrite_full_history=rewrite_full_history,
        skip_parity_check=skip_parity_check,
      ))
    except Exception as e:
      log.exception("cold_save_one_full_history failed for pid=%s", p.id)
      reports.append({
        "portfolio_id": p.id, "portfolio_name": p.name,
        "status": "error", "reason": repr(e),
      })
  return reports


def _parse_int_list(s: str) -> list[int]:
  return [int(x.strip()) for x in (s or "").split(",") if x.strip()]


def _run_cli(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Cold-save phase-2 LiveEvent rows for hybrid portfolios.",
  )
  parser.add_argument(
    "--ids", default=None,
    help="Optional comma-separated portfolio IDs to limit the run.",
  )
  parser.add_argument(
    "--dry-run", action="store_true",
    help="Print what would be written without committing. Default behavior is to write.",
  )
  parser.add_argument(
    "--per-window", action="store_true",
    help=("T-002 v2 Item 3 — write ONE reconstructed event per saved "
          "wfo_windows_meta entry, deriving each window's basket from "
          "the slice of selected_records inside that window. Without "
          "this flag, the legacy single-baseline behavior is used."),
  )
  parser.add_argument(
    "--replace-v1-baseline", action="store_true",
    help=("Meaningful with --per-window OR --full-history. When a "
          "portfolio already has a single v1-shape cold-save baseline "
          "(anchored at max(oos_end), empty per-window iso fields), "
          "delete that row and write the new rows in its place. "
          "Without this flag, such portfolios are skipped."),
  )
  parser.add_argument(
    "--full-history", action="store_true",
    help=("T-002 v3 — three-source reconstruction (Pass A: per-swap "
          "from phase2_selection_changes; Pass B: per-night from "
          "KellyAllocation rows for the trailing pre-T-002 gap). "
          "Higher fidelity than --per-window. Mutually exclusive with "
          "--per-window."),
  )
  parser.add_argument(
    "--replace-v2-per-window", action="store_true",
    help=("Only meaningful with --full-history. Delete pre-existing "
          "v2 per-window cold-save rows before writing the v3 chain. "
          "Without this flag, portfolios with v2 rows are skipped."),
  )
  parser.add_argument(
    "--rewrite-full-history", action="store_true",
    help=("Only meaningful with --full-history. Delete any existing "
          "v3 full-history rows for the portfolio before re-running. "
          "Without this flag, re-running on a portfolio that already "
          "has v3 rows is a no-op."),
  )
  parser.add_argument(
    "--mode", choices=["preserve", "upgrade", "rewrite"], default=None,
    help=("T-002 v3 unified mode flag (preferred over the individual "
          "--replace-* / --rewrite-* flags). Only meaningful with "
          "--full-history.\n"
          "  preserve (default) — write only when no rows exist; skip "
          "portfolios with v1/v2/v3.\n"
          "  upgrade — delete v1 + v2 rows and write v3; leave existing "
          "v3 chains untouched (idempotent re-run safe).\n"
          "  rewrite — delete v1 + v2 + v3 and rebuild the full v3 "
          "chain from scratch."),
  )
  args = parser.parse_args(argv)
  if args.per_window and args.full_history:
    parser.error("--per-window and --full-history are mutually exclusive")
  if args.mode is not None and not args.full_history:
    parser.error("--mode requires --full-history")
  if args.mode is not None and (
    args.replace_v1_baseline or args.replace_v2_per_window
    or args.rewrite_full_history
  ):
    parser.error(
      "--mode is mutually exclusive with --replace-v1-baseline, "
      "--replace-v2-per-window, --rewrite-full-history (pick one style)",
    )
  if args.mode == "preserve":
    args.replace_v1_baseline = False
    args.replace_v2_per_window = False
    args.rewrite_full_history = False
  elif args.mode == "upgrade":
    args.replace_v1_baseline = True
    args.replace_v2_per_window = True
    args.rewrite_full_history = False
  elif args.mode == "rewrite":
    args.replace_v1_baseline = True
    args.replace_v2_per_window = True
    args.rewrite_full_history = True
  only_ids = _parse_int_list(args.ids) if args.ids else None

  logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
  db = SessionLocal()
  try:
    if args.full_history:
      reports = cold_save_all_full_history(
        db, dry_run=args.dry_run, only_ids=only_ids,
        replace_v1_baseline=args.replace_v1_baseline,
        replace_v2_per_window=args.replace_v2_per_window,
        rewrite_full_history=args.rewrite_full_history,
      )
      for r in reports:
        tag = r.get("status", "?").upper()
        print(
          f"[{tag}] pid={r.get('portfolio_id')} "
          f"name={r.get('portfolio_name')!r} "
          f"reason={r.get('reason') or '-'} "
          f"pass_a={r.get('pass_a_rows', 0)} "
          f"pass_b={r.get('pass_b_rows', 0)} "
          f"deleted_v1={r.get('deletes_v1_baseline', 0)} "
          f"deleted_v2={r.get('deletes_v2_per_window', 0)} "
          f"deleted_v3={r.get('deletes_v3_existing', 0)}"
        )
    elif args.per_window:
      reports = cold_save_all_per_window(
        db, dry_run=args.dry_run, only_ids=only_ids,
        replace_v1_baseline=args.replace_v1_baseline,
      )
      for r in reports:
        tag = r.get("status", "?").upper()
        print(
          f"[{tag}] pid={r.get('portfolio_id')} name={r.get('portfolio_name')!r} "
          f"reason={r.get('reason') or '-'} "
          f"windows_written={r.get('windows_written', 0)}"
        )
    else:
      reports = cold_save_all(db, dry_run=args.dry_run, only_ids=only_ids)
      for r in reports:
        tag = r.get("status", "?").upper()
        print(
          f"[{tag}] pid={r.get('portfolio_id')} name={r.get('portfolio_name')!r} "
          f"reason={r.get('reason') or '-'} "
          f"basket_size={r.get('basket_size', 0)} "
          f"anchor_ts={r.get('anchor_ts_utc') or '-'}"
        )
    written = sum(1 for r in reports if r.get("status") == "written")
    skipped = sum(1 for r in reports if r.get("status") == "skipped")
    errs = sum(1 for r in reports if r.get("status") == "error")
    print(f"\nSummary: total={len(reports)} written={written} skipped={skipped} errors={errs}")
    return 0 if errs == 0 else 1
  finally:
    db.close()


if __name__ == "__main__":
  sys.exit(_run_cli())
