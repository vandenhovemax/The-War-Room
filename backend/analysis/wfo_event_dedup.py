"""Bulletproof cross-window dedup + rebuild for event-based WFAS (Hybrid Step 2).

Extracted from backend/api/routes/wfo.py so it can be unit-tested in isolation.
Single source of truth for every downstream aggregate when OOS windows overlap.

Design goals (parity with time-based WFAS protection):
  • Per-trade records deduped by (asset, dt)
  • Equity curve / pnl deltas rebuilt from deduped records (Issue A)
  • asset_frequency rebuilt: exact `selected` per asset, proportional `eligible` (Issue B)
  • Ancillary per-window counters (steps, rebalances, all_trade, warmup) scaled (Issue C)
  • Runs unconditionally, not gated on an overlap flag (Issue D)
  • Invariant checks: pnls/records length match, curve sum == pnls sum (fix #4)
  • Asset parity invariant: record["asset"] must equal the canonical scoring key
    derived from record["batch_id"]/symbol/side (2026-04-19 — defends against
    re-introduction of the asset loop-variable leak in run_event_wfo).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


_ASSET_KEY_RE = re.compile(r"^(?:B(?P<bid>\d+)_)?(?P<sym>.+)_(?P<side>Long|Short)$")


def _canonical_asset_key(rec: dict) -> str:
  """Rebuild the scoring key a record *should* have, from its trade fields.

  Mirrors `event_wfo._scoring_key`: when batch_id is present the asset is
  prefixed with `B{bid}_`, otherwise it is bare. Side is title-cased
  (`Long`/`Short`), symbol is upper-cased.
  """
  bid = rec.get("batch_id", 0) or 0
  sym = (rec.get("symbol") or "").upper()
  side = (rec.get("side") or "").lower()
  if side == "long":
    side_cap = "Long"
  elif side == "short":
    side_cap = "Short"
  else:
    side_cap = side.capitalize()
  if bid:
    return f"B{bid}_{sym}_{side_cap}"
  return f"{sym}_{side_cap}"


def _record_asset_is_consistent(rec: dict) -> tuple[bool, str]:
  """Check whether record['asset'] is internally consistent with trade fields.

  The 2026-04-19 asset loop-variable leak manifests as a symbol/side mismatch
  between the asset string and the record's own symbol/side fields (because
  `asset` was overwritten by a shadowing for-loop and now points at a
  different portfolio member). This function is tolerant of three benign
  variations seen in synthetic test fixtures:
    • batch_id may be None/0 even when the asset has a `B{n}_` prefix
      (fixture convention for dedup unit tests).
    • symbol may be stored in mixed case; we compare case-insensitive.
    • side may be "long"/"Long"/"LONG"; we compare case-insensitive.

  The hard checks — which catch the leak — are:
    1. Asset string must parse as `[B{n}_]{SYMBOL}_{Side}`.
    2. The symbol component must match rec['symbol'] (case-insensitive).
    3. The side component must match rec['side'] (case-insensitive).
    4. If both rec['batch_id'] > 0 AND the asset has a B{n}_ prefix, the
       numeric batch ids must agree.

  Returns (ok, reason). `reason` is a short diagnostic when ok=False.
  """
  asset = rec.get("asset", "") or ""
  m = _ASSET_KEY_RE.match(asset)
  if not m:
    return False, f"asset {asset!r} does not match `[B{{n}}_]{{SYMBOL}}_{{Side}}`"

  asset_bid = m.group("bid")
  asset_sym = m.group("sym").upper()
  asset_side = m.group("side")  # "Long" or "Short" by regex

  rec_sym = (rec.get("symbol") or "").upper()
  rec_side = (rec.get("side") or "").lower()
  rec_side_cap = (
    "Long" if rec_side == "long"
    else "Short" if rec_side == "short"
    else rec_side.capitalize()
  )

  if asset_sym != rec_sym:
    return False, f"symbol mismatch: asset={asset_sym!r} vs record={rec_sym!r}"
  if asset_side != rec_side_cap:
    return False, f"side mismatch: asset={asset_side!r} vs record={rec_side_cap!r}"

  rec_bid = rec.get("batch_id") or 0
  if rec_bid and asset_bid is not None and int(asset_bid) != int(rec_bid):
    return False, f"batch_id mismatch: asset=B{asset_bid} vs record={rec_bid}"

  return True, ""


def _assert_record_asset_parity(records: list[dict]) -> int:
  """Verify every record's `asset` is internally consistent with its trade fields.

  Returns the number of mismatches. When ≥1 mismatch is detected:
    • Always logs at ERROR with a bounded sample (to avoid log flooding).
    • Raises AssertionError — the asset leak is a silent accuracy killer;
      failing loud is the only defensible behaviour.

  Opt-out: set env `WFAS_SKIP_ASSET_PARITY=1` for emergency recovery only
  (explicit, not the default) so operators can still view historical data
  while a diagnosis is in flight. Test suites never set this flag.
  """
  if not records:
    return 0

  mismatches: list[tuple[int, str, str, dict]] = []
  for i, r in enumerate(records):
    ok, reason = _record_asset_is_consistent(r)
    if not ok:
      mismatches.append((i, r.get("asset", ""), reason, r))

  n = len(mismatches)
  if n == 0:
    return 0

  sample_n = min(5, n)
  log.error(
    "Hybrid Step 2 asset-parity invariant VIOLATED: %d/%d records have "
    "record['asset'] inconsistent with their own (symbol, side, batch_id). "
    "This is the classic `run_event_wfo` loop-variable leak — check whether "
    "any inner for-loop inside the rebalance block is using `asset` as its "
    "loop target (it must not). Sample (first %d): %s",
    n, len(records), sample_n,
    [
      {
        "idx": idx, "recorded_asset": actual, "reason": reason,
        "batch_id": rec.get("batch_id"), "symbol": rec.get("symbol"),
        "side": rec.get("side"), "dt": rec.get("dt"),
      }
      for idx, actual, reason, rec in mismatches[:sample_n]
    ],
  )

  if os.environ.get("WFAS_SKIP_ASSET_PARITY") == "1":
    log.error(
      "WFAS_SKIP_ASSET_PARITY=1 is set — not raising despite %d mismatches. "
      "Results are UNRELIABLE. This flag must never be set in production.", n,
    )
    return n

  first = mismatches[0]
  raise AssertionError(
    f"Hybrid Step 2 asset-parity invariant violated: {n}/{len(records)} "
    f"records have record['asset'] inconsistent with their own trade fields. "
    f"See ERROR log for sample. First mismatch: recorded_asset={first[1]!r} "
    f"reason={first[2]} (batch_id={first[3].get('batch_id')}, "
    f"symbol={first[3].get('symbol')}, side={first[3].get('side')}). "
    f"This indicates the asset loop-variable leak in run_event_wfo has been "
    f"re-introduced. Fix the shadowing `for asset, ... in ...:` loop."
  )


@dataclass
class EventAggregateState:
  """Mutable bag of aggregates that flow through the dedup/rebuild step."""
  selected_records: list[dict] = field(default_factory=list)
  selected_pnls: list[float] = field(default_factory=list)
  equity_curve: list[float] = field(default_factory=list)
  equity_dates: list[str] = field(default_factory=list)
  pnl_deltas: list[float] = field(default_factory=list)
  asset_freq: dict[str, dict] = field(default_factory=dict)  # {asset: {selected, eligible}}
  total_steps: int = 0
  total_trades: int = 0
  rebalance_count: int = 0
  all_trade_count: int = 0
  warmup_trades: int = 0
  running_equity: float = 0.0


def dedup_and_rebuild(state: EventAggregateState, overlap_flag: bool = False) -> int:
  """Dedup per-trade records and rebuild every derived aggregate in-place.

  Returns the number of duplicate trades dropped. Runs unconditionally — if no
  duplicates exist, it's a no-op except for the guaranteed invariant assertions.
  """
  if not state.selected_records:
    return 0

  pre_trades = len(state.selected_records)

  # ── Dedup by (asset, dt) ──
  seen: set = set()
  deduped_recs: list[dict] = []
  deduped_pnls: list[float] = []
  for r in state.selected_records:
    k = (r.get("asset", ""), str(r.get("dt", "")))
    if k in seen:
      continue
    seen.add(k)
    deduped_recs.append(r)
    deduped_pnls.append(float(r.get("pnl", 0) or 0))

  dropped = pre_trades - len(deduped_recs)
  if dropped > 0:
    log.warning(
      "Hybrid Step 2 dedup: dropped %d/%d duplicated trades (overlap=%s)",
      dropped, pre_trades, overlap_flag,
    )

  state.selected_records = deduped_recs
  state.selected_pnls = deduped_pnls
  state.total_trades = len(deduped_pnls)

  # ── Issue A: rebuild equity curve from deduped records ──
  if dropped > 0:
    sorted_recs = sorted(state.selected_records, key=lambda x: str(x.get("dt", "")))
    state.equity_curve = []
    state.equity_dates = []
    state.pnl_deltas = []
    running = 0.0
    for r in sorted_recs:
      p = float(r.get("pnl", 0) or 0)
      running += p
      state.pnl_deltas.append(round(p, 6))
      state.equity_curve.append(round(running, 2))
      dt_raw = str(r.get("dt", ""))
      state.equity_dates.append(dt_raw[:10] if dt_raw else "")
    state.running_equity = running

  # ── Issue B: rebuild asset_frequency ──
  kept_ratio = (len(deduped_recs) / pre_trades) if pre_trades > 0 else 1.0
  rebuilt_freq: dict[str, dict] = {}
  for r in state.selected_records:
    a = r.get("asset", "")
    if not a:
      continue
    slot = rebuilt_freq.setdefault(a, {"selected": 0, "eligible": 0})
    slot["selected"] += 1
  for a, fd in state.asset_freq.items():
    slot = rebuilt_freq.setdefault(a, {"selected": 0, "eligible": 0})
    slot["eligible"] = int(round(int(fd.get("eligible", 0) or 0) * kept_ratio))
  for slot in rebuilt_freq.values():
    if slot["eligible"] < slot["selected"]:
      slot["eligible"] = slot["selected"]
  state.asset_freq = rebuilt_freq

  # ── Issue C: scale ancillary counters ──
  if dropped > 0:
    state.total_steps = int(round(state.total_steps * kept_ratio))
    state.rebalance_count = int(round(state.rebalance_count * kept_ratio))
    state.all_trade_count = int(round(state.all_trade_count * kept_ratio))
    state.warmup_trades = int(round(state.warmup_trades * kept_ratio))

  # ── Invariants ──
  assert len(state.selected_pnls) == len(state.selected_records), (
    f"dedup invariant violated: pnls={len(state.selected_pnls)} "
    f"records={len(state.selected_records)}"
  )
  curve_sum = round(sum(state.pnl_deltas), 2) if state.pnl_deltas else 0.0
  pnls_sum = round(sum(state.selected_pnls), 2)
  if dropped > 0 and abs(curve_sum - pnls_sum) > 0.01:
    log.error(
      "Hybrid Step 2 invariant: curve sum (%.2f) != pnls sum (%.2f) — rebuild bug",
      curve_sum, pnls_sum,
    )

  # ── Asset parity invariant (2026-04-19 regression guard) ──
  # Raises AssertionError if any record's stored asset disagrees with the
  # canonical key rebuilt from its own (batch_id, symbol, side). See the
  # module docstring and `_assert_record_asset_parity` for context.
  _assert_record_asset_parity(state.selected_records)

  return dropped
