"""L3.2 — Step-1 refresh worker (Ship 2).

Triggered by the OOS boundary detector (oos_boundary.check_and_enqueue)
or from the UI ('Force Rebalance'). Re-runs Step-1 (batch selection) on
the portfolio's fresh unified trade log, diffs the new survivor basket
against the current live basket, and writes a RebalanceProposal row
with status='pending'.

This worker NEVER mutates Portfolio / PortfolioAsset / KellyAllocation.
The only write it performs is the proposal row itself.

Contract:
  rebuild_and_propose(portfolio_id, trigger_type, db=None) -> {
    ok: bool,
    proposal_id: int | None,
    reason: str | None,
    diff: {added, removed, unchanged} | None,
  }

Tests monkey-patch `run_batch_selection` so we can verify diff + proposal
persistence without spinning up the optimizer.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.analysis.batch_selection import (
  BatchSelectionConfig,
  run_batch_selection,
)
from backend.analysis.trade_log_unified import load_unified_trades
from backend.db.database import SessionLocal
from backend.db.models import (
  LiveEvent,
  Portfolio,
  PortfolioAsset,
  RebalanceProposal,
)

log = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────

def _asset_key(symbol: str, side: str) -> str:
  """Canonical '{SYMBOL}_{Long|Short}' key used end-to-end in the
  Kelly/executor/proposal paths."""
  side_norm = (side or "").strip().lower()
  suffix = "Long" if side_norm in ("long", "l") else "Short"
  return f"{symbol.upper()}_{suffix}"


def _current_basket_keys(db, portfolio_id: int) -> list[str]:
  rows = (
    db.query(PortfolioAsset)
    .filter(
      PortfolioAsset.portfolio_id == portfolio_id,
      PortfolioAsset.is_active == True,  # noqa: E712
    )
    .all()
  )
  keys = {_asset_key(r.symbol, r.trade_side) for r in rows}
  return sorted(keys)


def _batch_ids(db, portfolio_id: int) -> list[int]:
  rows = (
    db.query(PortfolioAsset)
    .filter(PortfolioAsset.portfolio_id == portfolio_id)
    .all()
  )
  return sorted({r.batch_id for r in rows if r.batch_id is not None})


def _build_bs_config(src: dict) -> BatchSelectionConfig:
  """Project the portfolio's saved source_strategies onto a
  BatchSelectionConfig. Unknown keys fall through to dataclass defaults.

  New portfolios save the full bs_config under source_strategies.bs_config;
  legacy portfolios fall back to defaults (and the user sees the default
  behavior in the proposal — they can always reject).
  """
  bs = dict(src.get("bs_config") or {})
  # Filter to fields actually on BatchSelectionConfig to avoid TypeError.
  allowed = set(BatchSelectionConfig.__dataclass_fields__.keys())
  safe = {k: v for k, v in bs.items() if k in allowed}
  return BatchSelectionConfig(**safe)


def _extract_new_basket(result: dict) -> list[str]:
  """Pull the final survivor basket out of a run_batch_selection() result.

  The canonical location is result['summary']['portfolio'] (a list of
  asset keys). Fallbacks cover older shapes. Keys are normalized to the
  '{SYMBOL}_{Long|Short}' convention.
  """
  summary = result.get("summary") or {}
  candidates = (
    summary.get("portfolio")
    or summary.get("final_portfolio")
    or summary.get("survivors")
    or result.get("final_portfolio")
    or []
  )
  normalized: list[str] = []
  for item in candidates:
    if isinstance(item, str):
      normalized.append(item.strip())
    elif isinstance(item, dict):
      sym = item.get("symbol") or item.get("asset")
      side = item.get("side") or item.get("trade_side")
      if sym:
        normalized.append(_asset_key(sym, side or "long"))
  return sorted(set(normalized))


def _diff(current: list[str], proposed: list[str]) -> dict[str, list[str]]:
  cur, prop = set(current), set(proposed)
  return {
    "added":     sorted(prop - cur),
    "removed":   sorted(cur - prop),
    "unchanged": sorted(cur & prop),
  }


# ── Main entry point ────────────────────────────────────────────

def rebuild_and_propose(
  portfolio_id: int,
  trigger_type: str = "oos_boundary",
  db: Any | None = None,
) -> dict:
  """Re-run Step-1 on fresh data and write a RebalanceProposal.

  Returns: {ok, proposal_id, reason, diff}
  Never raises — logs + returns {ok: False, reason: ...}.
  """
  own_session = db is None
  if own_session:
    db = SessionLocal()

  try:
    p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if p is None or p.is_deleted:
      return {"ok": False, "proposal_id": None, "reason": "portfolio_missing", "diff": None}

    # Guard against duplicate pending proposals.
    dup = (
      db.query(RebalanceProposal)
      .filter(
        RebalanceProposal.portfolio_id == portfolio_id,
        RebalanceProposal.status == "pending",
      )
      .first()
    )
    if dup is not None:
      return {"ok": False, "proposal_id": dup.id, "reason": "already_pending", "diff": None}

    current = _current_basket_keys(db, portfolio_id)
    bids = _batch_ids(db, portfolio_id)

    try:
      src = json.loads(p.source_strategies or "{}") or {}
    except Exception:
      src = {}

    # Load unified trades restricted to this portfolio's batch IDs so the
    # Step-1 re-run scores the SAME universe the portfolio is drawn from.
    cutoff = p.optimization_end_date
    trades, rbm, batch_info = load_unified_trades(
      db, batch_ids=bids or None, cutoff=cutoff, portfolio_id=portfolio_id,
    )

    # Windows — for Ship 2 the worker expects the original windows config
    # on source_strategies.wfo_windows. If absent we emit a loud LiveEvent
    # so the user can see on the dashboard why auto-rotation produced no
    # diff (instead of a silent "nothing changed"). Manual rotation still
    # works — they can re-save the portfolio to populate wfo_windows.
    windows = src.get("wfo_windows") or []
    if not windows:
      log.warning(
        "step1_refresh_worker: portfolio %d has no wfo_windows in "
        "source_strategies — auto-rotation will produce an empty diff. "
        "Re-save the portfolio from the optimizer to populate this.",
        portfolio_id,
      )
      try:
        db.add(LiveEvent(
          portfolio_id=portfolio_id,
          event_type="rebalance_skipped_no_windows",
          details=json.dumps({
            "trigger_type": trigger_type,
            "reason": "source_strategies.wfo_windows missing or empty",
          }),
        ))
        db.commit()
      except Exception as _exc:
        log.debug("swallowed live_event write failure: %s", _exc)

    config = _build_bs_config(src)

    try:
      result = run_batch_selection(
        trades, windows, config, rbm, batch_info=batch_info, db=db,
      )
    except Exception as e:
      log.warning("step1_refresh_worker: run_batch_selection failed pid=%d: %s", portfolio_id, e)
      return {"ok": False, "proposal_id": None, "reason": f"step1_failed: {e}", "diff": None}

    proposed = _extract_new_basket(result)
    diff = _diff(current, proposed)

    proposal = RebalanceProposal(
      portfolio_id=portfolio_id,
      trigger_type=trigger_type,
      status="pending",
      current_basket_json=json.dumps(current),
      proposed_basket_json=json.dumps(proposed),
      diff_json=json.dumps(diff),
      metrics_preview_json=json.dumps({
        "summary": (result.get("summary") or {}),
        "generated_at": datetime.now(timezone.utc).isoformat(),
      }, default=str),
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    log.info(
      "step1_refresh_worker: proposal #%d for portfolio %d — +%d / -%d / =%d",
      proposal.id, portfolio_id,
      len(diff["added"]), len(diff["removed"]), len(diff["unchanged"]),
    )
    return {
      "ok": True,
      "proposal_id": proposal.id,
      "reason": None,
      "diff": diff,
    }
  finally:
    if own_session:
      db.close()
