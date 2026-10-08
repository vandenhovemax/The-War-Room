"""T-003 Delta 11 — Reader-parity audit.

Purpose:
  After the T-003 read-path unification, every hybrid portfolio detail
  view sources its DataFrame from the SAME pair of functions:

      df = load_wfas_replay(settings, db)
         + load_wfas_forward_append(p, settings, db)
        = get_portfolio_detail_dataframe(p, settings, db)

  The legacy `_stitch_event_based_walkforward` is retired for hybrid
  portfolios (`step2_result.selected_records` present). It remains a
  fallback for pre-T-002 portfolios only.

  This module verifies that the two readers AGREE for hybrid portfolios.
  If they ever diverge, that's a regression — either the unified reader
  dropped a trade the stitcher correctly attributes, or the stitcher
  picked up a phantom segment. Surfaced as a defect on the Activity feed
  for human investigation.

Design safety (PURE AUDIT, mirrors phase2_audit / parity_canary):
  * Never writes a trade, never mutates a portfolio.
  * Only writes:
      - One `LiveEvent(event_type='reader_parity_audit')` row when
        divergence is found, so the Activity feed surfaces it.
  * Never raises — per-portfolio failures are caught and counted under
    `errors`. One broken portfolio cannot kill the audit.

Scope and carve-outs:
  * Hybrid-only. Non-hybrid portfolios skip — the legacy stitcher is the
    intended path for those, so divergence is meaningless.
  * `basket_restore` carve-out: portfolios that have any LiveEvent rows
    with `event_type='basket_restore'` are EXCLUDED from the audit.
    `basket_restore` is a pre-T-002 legacy event_type that the legacy
    stitcher consumes (via `DEFAULT_STITCHER_EVENT_TYPES`) but the
    unified reader's `iter_basket_segments` does not — comparing the
    two readers on those portfolios would surface a known, intentional
    divergence and pollute the audit signal. ATLAS T-003 spec: see the
    Delta 11 carve-out note.

Trade-key contract:
  Trades are diffed by canonical key `(entry_time_iso, symbol, side,
  round(net_profit, 2))`. Robust to dict ordering and float jitter.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from backend.db.models import LiveEvent, Portfolio

log = logging.getLogger(__name__)


READER_PARITY_AUDIT_KIND = "reader_parity_audit"


# ── Helpers ─────────────────────────────────────────────────────────


def _trade_keys(df: pd.DataFrame) -> set[tuple]:
  """Canonical identity set for diff matching.

  Tolerant of either reader's column shape — both producers emit the
  same standard columns (entry_time, symbol, side, net_profit) but
  call sites can hand in DataFrames missing one of them after a join,
  so we coerce missing columns to empty strings / 0 rather than KeyError.
  """
  if df is None or df.empty:
    return set()
  out: set[tuple] = set()
  for _, row in df.iterrows():
    et = row.get("entry_time") if hasattr(row, "get") else row["entry_time"]
    if isinstance(et, datetime):
      et_str = et.isoformat()
    else:
      et_str = str(et) if et is not None else ""
    sym = str(row.get("symbol") if hasattr(row, "get") else row.get("symbol", "")).strip()
    side = str(row.get("side") if hasattr(row, "get") else "").strip().lower()
    np_raw = row.get("net_profit") if hasattr(row, "get") else 0.0
    try:
      np = round(float(np_raw or 0.0), 2)
    except (ValueError, TypeError):
      np = 0.0
    out.add((et_str, sym, side, np))
  return out


def _diff_reader_outputs(unified: pd.DataFrame, legacy: pd.DataFrame) -> dict:
  """Diff two reader outputs by canonical trade key.

  Returns a dict suitable for embedding in the audit report:
    {
      unified_count, legacy_count,
      unified_only_count, legacy_only_count,
      unified_pnl, legacy_pnl, pnl_delta,
      has_diff,
      unified_only_sample, legacy_only_sample,   # capped at 20
    }
  """
  u_keys = _trade_keys(unified)
  l_keys = _trade_keys(legacy)
  unified_only = sorted(u_keys - l_keys)
  legacy_only = sorted(l_keys - u_keys)

  def _sum_pnl(df: pd.DataFrame) -> float:
    if df is None or df.empty or "net_profit" not in df.columns:
      return 0.0
    try:
      return float(df["net_profit"].fillna(0.0).sum())
    except Exception:
      return 0.0

  u_pnl = _sum_pnl(unified)
  l_pnl = _sum_pnl(legacy)
  pnl_delta = round(u_pnl - l_pnl, 4)

  has_diff = bool(unified_only) or bool(legacy_only) or abs(pnl_delta) > 0.01

  return {
    "unified_count": int(0 if unified is None else len(unified)),
    "legacy_count": int(0 if legacy is None else len(legacy)),
    "unified_only_count": len(unified_only),
    "legacy_only_count": len(legacy_only),
    "unified_pnl": round(u_pnl, 4),
    "legacy_pnl": round(l_pnl, 4),
    "pnl_delta": pnl_delta,
    "has_diff": has_diff,
    "unified_only_sample": [list(k) for k in unified_only[:20]],
    "legacy_only_sample": [list(k) for k in legacy_only[:20]],
  }


def _portfolio_settings(p: Portfolio) -> dict:
  """Pull the saved settings JSON used by both readers as input."""
  try:
    return json.loads(p.source_strategies) if p.source_strategies else {}
  except (ValueError, TypeError):
    return {}


def _has_basket_restore_carveout(db, portfolio_id: int) -> bool:
  """Carve-out: legacy `basket_restore` events make divergence inevitable.

  These pre-T-002 rows are consumed by the legacy stitcher's
  `DEFAULT_STITCHER_EVENT_TYPES` but never by `iter_basket_segments`.
  Any portfolio with even one such row is excluded from this audit.
  """
  hit = (
    db.query(LiveEvent.id)
    .filter(LiveEvent.portfolio_id == portfolio_id)
    .filter(LiveEvent.event_type == "basket_restore")
    .first()
  )
  return hit is not None


# ── Per-portfolio comparator ────────────────────────────────────────


def compare_readers_for_portfolio(p: Portfolio, db) -> dict:
  """Compare unified vs legacy reader outputs for one portfolio.

  Returns: {
    portfolio_id, ok, reason,            # set when ok=False
    skipped, skip_reason,                # set when skipped (carve-out)
    has_diff, diff,                      # only populated when ok=True
  }

  Never raises. Hybrid-only — non-hybrid portfolios return ok=False
  with reason='not_hybrid'. `basket_restore` carve-out portfolios
  return skipped=True with skip_reason='basket_restore_carveout'.
  """
  # Late imports — the modules are heavy and the audit may run on a
  # narrow shard where we never need either path.
  from backend.analysis.portfolio_wfo_replay import (
    get_effective_step2,
    get_portfolio_detail_dataframe,
  )
  from backend.api.routes.portfolio_analysis import (
    _stitch_event_based_walkforward,
  )

  pid = p.id
  settings = _portfolio_settings(p)

  # Hybrid gate — same condition used by `get_portfolio_detail_dataframe`.
  if not get_effective_step2(settings).get("selected_records"):
    return {
      "portfolio_id": pid,
      "ok": False,
      "reason": "not_hybrid",
      "skipped": False,
      "skip_reason": None,
      "has_diff": False,
      "diff": {},
    }

  # Carve-out: skip portfolios with legacy `basket_restore` events.
  if _has_basket_restore_carveout(db, pid):
    return {
      "portfolio_id": pid,
      "ok": True,
      "reason": None,
      "skipped": True,
      "skip_reason": "basket_restore_carveout",
      "has_diff": False,
      "diff": {},
    }

  # Resolve batch_ids for the legacy stitcher fallback. The unified
  # reader pulls them from settings internally, but the stitcher accepts
  # them as a positional arg.
  batch_ids_fallback: list[int] = []
  try:
    raw = settings.get("batch_ids") or []
    batch_ids_fallback = [int(b) for b in raw if b is not None]
  except (ValueError, TypeError):
    batch_ids_fallback = []

  # Run both readers under broad except — never raise from an audit.
  try:
    unified_df = get_portfolio_detail_dataframe(p, settings, db)
  except Exception as e:
    log.warning("reader_parity_audit: unified reader failed pid=%d: %s", pid, e)
    return {
      "portfolio_id": pid, "ok": False,
      "reason": f"unified_failed: {type(e).__name__}",
      "skipped": False, "skip_reason": None,
      "has_diff": False, "diff": {},
    }

  if unified_df is None:
    # `get_portfolio_detail_dataframe` returns None for non-hybrid; we
    # already gated on hybrid above, so this is a defensive catch.
    return {
      "portfolio_id": pid, "ok": False,
      "reason": "unified_returned_none",
      "skipped": False, "skip_reason": None,
      "has_diff": False, "diff": {},
    }

  try:
    legacy_df, _segments = _stitch_event_based_walkforward(
      settings, p, batch_ids_fallback, db,
    )
  except Exception as e:
    log.warning("reader_parity_audit: legacy stitcher failed pid=%d: %s", pid, e)
    return {
      "portfolio_id": pid, "ok": False,
      "reason": f"legacy_failed: {type(e).__name__}",
      "skipped": False, "skip_reason": None,
      "has_diff": False, "diff": {},
    }

  diff = _diff_reader_outputs(unified_df, legacy_df)
  return {
    "portfolio_id": pid,
    "ok": True,
    "reason": None,
    "skipped": False,
    "skip_reason": None,
    "has_diff": diff["has_diff"],
    "diff": diff,
  }


# ── Orchestration ──────────────────────────────────────────────────


def run_reader_parity_audit(
  *, portfolio_ids: list[int] | None = None, db=None,
) -> dict:
  """Audit entry point. Iterate every hybrid portfolio (or the supplied
  subset), compare both readers, surface any divergence as a LiveEvent.

  Returns: {
    run_date, total_portfolios, portfolios_with_diff,
    skipped, errors,
    portfolios_with_diff_ids: [...],
    report: { portfolios: [{...}, ...] },
  }

  Never raises. Per-portfolio failures are caught and counted.
  """
  own_session = db is None
  if own_session:
    from backend.db.database import SessionLocal
    db = SessionLocal()

  started = datetime.now(timezone.utc)
  run_date = started.strftime("%Y-%m-%d")
  report: dict[str, Any] = {
    "started_at": started.isoformat(),
    "ended_at": None,
    "portfolios": [],
  }

  try:
    q = db.query(Portfolio).filter(
      Portfolio.is_deleted == False,  # noqa: E712
    )
    if portfolio_ids:
      q = q.filter(Portfolio.id.in_(portfolio_ids))
    portfolios = q.all()

    total = 0
    with_diff = 0
    skipped = 0
    errors = 0
    diff_ids: list[int] = []

    for p in portfolios:
      total += 1
      try:
        res = compare_readers_for_portfolio(p, db)
      except Exception as e:
        errors += 1
        log.warning("reader_parity_audit: portfolio %d threw: %s", p.id, e)
        report["portfolios"].append({
          "portfolio_id": p.id, "ok": False, "has_diff": False,
          "reason": f"exc_{type(e).__name__}",
          "skipped": False, "skip_reason": None,
        })
        continue

      if res.get("skipped"):
        skipped += 1
      if not res.get("ok"):
        errors += 1
      if res.get("has_diff"):
        with_diff += 1
        diff_ids.append(p.id)
      report["portfolios"].append(res)

    report["ended_at"] = datetime.now(timezone.utc).isoformat()

    # Activity-feed surface — ONE LiveEvent per portfolio with divergence.
    # Mirrors the parity_canary pattern (one row, easy to query later).
    if with_diff > 0:
      try:
        for pid in diff_ids:
          db.add(LiveEvent(
            portfolio_id=pid,
            event_type=READER_PARITY_AUDIT_KIND,
            details=json.dumps({
              "kind": READER_PARITY_AUDIT_KIND,
              "run_date": run_date,
              "verdict": "DIVERGENCE",
            }),
          ))
        db.commit()
      except Exception as e:
        log.warning("reader_parity_audit: LiveEvent persist failed: %s", e)
        try:
          db.rollback()
        except Exception:
          pass

    log.info(
      "reader_parity_audit: date=%s total=%d with_diff=%d skipped=%d errors=%d",
      run_date, total, with_diff, skipped, errors,
    )
    return {
      "run_date": run_date,
      "total_portfolios": total,
      "portfolios_with_diff": with_diff,
      "skipped": skipped,
      "errors": errors,
      "portfolios_with_diff_ids": diff_ids,
      "report": report,
    }
  finally:
    if own_session:
      try:
        db.close()
      except Exception:
        pass
