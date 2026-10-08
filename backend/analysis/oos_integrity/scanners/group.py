"""Group/Entity Leakage Scanner — batch isolation and symbol-level checks."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner


class GroupLeakageScanner(AbstractScanner):
  name = "group"
  category = "Group Leakage"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    batch_trades = ctx.get("batch_trades")
    is_end = ctx.get("is_end")
    is_pnl = ctx.get("is_daily_pnl")
    is_trades = ctx.get("is_trades", [])
    oos_trades = ctx.get("oos_trades", [])

    if batch_trades and is_end:
      findings.extend(self._check_batch_isolation(batch_trades, is_end))
      findings.extend(self._check_batch_tag_consistency(batch_trades, is_pnl))
    if is_trades and oos_trades:
      findings.extend(self._check_symbol_overlap(is_trades, oos_trades))
    if is_pnl is not None and not is_pnl.empty:
      findings.extend(self._check_hierarchical_correlation(is_pnl))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_batch_isolation(self, batch_trades, is_end):
    """Each batch must independently respect IS boundary."""
    boundary = pd.Timestamp(is_end) + pd.Timedelta(days=1)
    violations = {}
    for bid, trades in batch_trades.items():
      bad = sum(1 for t in trades if t.exit_time and pd.Timestamp(t.exit_time) > boundary)
      if bad > 0:
        violations[bid] = bad
    if not violations:
      return []
    return [self._finding(
      "GRP-001", "CRITICAL",
      f"{len(violations)} batches have OOS trades past IS boundary",
      f"Batch violations: {violations}",
      evidence={"violations": violations, "batches_checked": len(batch_trades)},
      remediation="Apply date_to filter per-batch before merging",
    )]

  def _check_batch_tag_consistency(self, batch_trades, is_pnl):
    """Verify batch tags in daily PnL match actual batches."""
    if is_pnl is None or is_pnl.empty:
      return []
    tagged_cols = [c for c in is_pnl.columns if "~B" in c]
    if not tagged_cols:
      return []  # No batch tags = single-batch mode, OK
    batch_ids_in_pnl = set()
    for col in tagged_cols:
      if "~B" in col:
        tag = col.split("~B")[1].split("_")[0]
        try:
          batch_ids_in_pnl.add(int(tag))
        except ValueError:
          pass
    batch_ids_in_trades = set(batch_trades.keys())
    missing = batch_ids_in_pnl - batch_ids_in_trades
    if not missing:
      return []
    return [self._finding(
      "GRP-002", "HIGH",
      f"Batch tags in daily PnL reference {len(missing)} unknown batches",
      f"Batch IDs {missing} in PnL columns but not in loaded trades",
      evidence={"missing_batches": sorted(missing)},
      remediation="Ensure all batch IDs in daily PnL have corresponding loaded trades",
    )]

  def _check_symbol_overlap(self, is_trades, oos_trades):
    """Quantify symbol overlap between IS and OOS."""
    is_symbols = {t.symbol for t in is_trades}
    oos_symbols = {t.symbol for t in oos_trades}
    overlap = is_symbols & oos_symbols
    # For trading: overlap is expected (same symbols, different time periods)
    # Flag only if overlap is zero (suspicious for temporal split)
    if len(overlap) > 0:
      return []
    return [self._finding(
      "GRP-003", "MEDIUM",
      "Zero symbol overlap between IS and OOS",
      "No shared symbols — this is unusual for a temporal split",
      evidence={"is_symbols": len(is_symbols), "oos_symbols": len(oos_symbols)},
      confidence=0.6,
      remediation="Verify split logic — temporal splits should have same symbols in both sets",
    )]

  def _check_hierarchical_correlation(self, is_pnl):
    """Flag highly correlated asset pairs (shared factor risk)."""
    if is_pnl.shape[1] < 3:
      return []
    corr = is_pnl.corr()
    mask = np.triu(np.ones(corr.shape), k=1).astype(bool)
    upper = corr.where(mask).stack()
    high_corr = upper[upper.abs() > 0.85]
    if high_corr.empty:
      return []
    pairs = [
      {"pair": f"{i[0]} / {i[1]}", "corr": round(float(v), 3)}
      for (i, v) in high_corr.items()
    ][:10]
    return [self._finding(
      "GRP-004", "LOW",
      f"{len(high_corr)} highly correlated asset pairs (|r|>0.85)",
      "Highly correlated assets may share factors — diversification limited",
      evidence={"pairs": pairs, "count": len(high_corr)},
      confidence=0.5,
    )]
