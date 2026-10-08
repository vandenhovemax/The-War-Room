"""Identity/Row Leakage Scanner — detects duplicate and near-duplicate trades."""
from __future__ import annotations

import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner
from backend.analysis.oos_integrity.utils.hashing import (
  find_exact_duplicates,
  hash_trade,
  hash_trades_bulk,
)


class IdentityLeakageScanner(AbstractScanner):
  name = "identity"
  category = "Identity Leakage"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    is_trades = ctx.get("is_trades", [])
    oos_trades = ctx.get("oos_trades", [])

    if is_trades and oos_trades:
      findings.extend(self._check_exact_duplicates(is_trades, oos_trades))
      findings.extend(self._check_near_duplicates(is_trades, oos_trades, config))
      findings.extend(self._check_position_id_overlap(is_trades, oos_trades))
      findings.extend(self._check_symbol_split_type(is_trades, oos_trades))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_exact_duplicates(self, is_trades, oos_trades):
    """Hash every trade — flag any collision between IS and OOS."""
    is_hashes = {hash_trade(t) for t in is_trades}
    oos_hashes = {hash_trade(t) for t in oos_trades}
    dupes = find_exact_duplicates(is_hashes, oos_hashes)
    if not dupes:
      return []
    pct = len(dupes) / len(oos_hashes) * 100
    return [self._finding(
      "IDEN-001", "CRITICAL",
      f"{len(dupes)} exact duplicate trades across IS/OOS",
      f"{pct:.1f}% of OOS trades are identical copies of IS trades",
      evidence={"duplicate_count": len(dupes), "pct": pct, "sample_hashes": list(dupes)[:5]},
      affected_rows=len(dupes), contamination_pct=pct,
      remediation="Remove duplicate trades from OOS set — use exit_time filtering",
    )]

  def _check_near_duplicates(self, is_trades, oos_trades, config):
    """Find trades with very similar timestamps and prices."""
    near_dupes = 0
    sample_pairs = []
    for oos_t in oos_trades[:200]:
      if not oos_t.entry_time or not oos_t.exit_time:
        continue
      oos_entry = pd.Timestamp(oos_t.entry_time)
      oos_exit = pd.Timestamp(oos_t.exit_time)
      for is_t in is_trades[:500]:
        if not is_t.entry_time or not is_t.exit_time:
          continue
        if is_t.symbol != oos_t.symbol:
          continue
        is_entry = pd.Timestamp(is_t.entry_time)
        is_exit = pd.Timestamp(is_t.exit_time)
        entry_diff = abs((oos_entry - is_entry).total_seconds())
        exit_diff = abs((oos_exit - is_exit).total_seconds())
        price_sim = 1.0
        if is_t.entry_price and oos_t.entry_price:
          price_sim = min(is_t.entry_price, oos_t.entry_price) / max(is_t.entry_price, oos_t.entry_price)
        if entry_diff < 3600 and exit_diff < 3600 and price_sim > config.similarity_threshold:
          near_dupes += 1
          if len(sample_pairs) < 3:
            sample_pairs.append({"is_id": is_t.id, "oos_id": oos_t.id, "symbol": oos_t.symbol})
          break
    if near_dupes == 0:
      return []
    return [self._finding(
      "IDEN-002", "HIGH",
      f"{near_dupes} near-duplicate trades detected",
      "OOS trades nearly identical to IS trades (same symbol, similar time/price)",
      evidence={"count": near_dupes, "samples": sample_pairs},
      affected_rows=near_dupes,
      remediation="Verify split is purely temporal — near-duplicates suggest data recycling",
    )]

  def _check_position_id_overlap(self, is_trades, oos_trades):
    """Same position_id in both IS and OOS."""
    is_pids = {t.position_id for t in is_trades if t.position_id}
    oos_pids = {t.position_id for t in oos_trades if t.position_id}
    overlap = is_pids & oos_pids
    if not overlap:
      return []
    return [self._finding(
      "IDEN-003", "HIGH",
      f"{len(overlap)} position IDs appear in both IS and OOS",
      "Same position split across IS/OOS — position entered in IS, exited in OOS",
      evidence={"count": len(overlap), "samples": list(overlap)[:5]},
      affected_rows=len(overlap),
      remediation="Position-level split should assign entire position to IS or OOS",
    )]

  def _check_symbol_split_type(self, is_trades, oos_trades):
    """Verify split is temporal (same symbols expected) not random."""
    is_symbols = {t.symbol for t in is_trades}
    oos_symbols = {t.symbol for t in oos_trades}
    overlap = is_symbols & oos_symbols
    only_oos = oos_symbols - is_symbols
    overlap_pct = len(overlap) / max(len(is_symbols | oos_symbols), 1) * 100
    findings = []
    if only_oos:
      findings.append(self._finding(
        "IDEN-004", "LOW",
        f"{len(only_oos)} symbols only in OOS (not in IS)",
        "New symbols in OOS may have no IS training history",
        evidence={"oos_only": sorted(only_oos)[:10], "overlap_pct": overlap_pct},
        confidence=0.6,
      ))
    return findings
