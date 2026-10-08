"""Feature-Level Contamination Scanner — detects feature leakage in daily PnL."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner


class FeatureLeakageScanner(AbstractScanner):
  name = "feature"
  category = "Feature Leakage"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    is_pnl = ctx.get("is_daily_pnl")
    oos_pnl = ctx.get("oos_daily_pnl")
    is_end = ctx.get("is_end")
    corr_matrix = ctx.get("corr_matrix")

    if is_pnl is not None and not is_pnl.empty:
      findings.extend(self._check_pnl_boundary(is_pnl, is_end))
      if corr_matrix is not None:
        findings.extend(self._check_corr_source(is_pnl, corr_matrix, is_end))
      findings.extend(self._check_suspiciously_predictive(is_pnl))
    if is_pnl is not None and oos_pnl is not None:
      findings.extend(self._check_column_consistency(is_pnl, oos_pnl))
      findings.extend(self._check_global_aggregate_leak(is_pnl, oos_pnl))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_pnl_boundary(self, is_pnl, is_end):
    """Verify daily PnL doesn't extend beyond IS."""
    if not is_end or is_pnl.empty:
      return []
    boundary = pd.Timestamp(is_end)
    max_date = is_pnl.index.max()
    if max_date <= boundary:
      return []
    beyond = int((is_pnl.index > boundary).sum())
    return [self._finding(
      "FEAT-001", "CRITICAL",
      f"IS daily PnL extends {beyond} rows past IS boundary",
      f"Max date {max_date.date()} > IS end {is_end}",
      evidence={"max_date": str(max_date), "beyond_rows": beyond},
      affected_rows=beyond,
      remediation="Truncate daily_pnl before computing correlation/optimization",
    )]

  def _check_corr_source(self, is_pnl, corr_matrix, is_end):
    """Verify correlation matrix was built from IS-only data."""
    if is_pnl.empty or corr_matrix.empty:
      return []
    is_cols = set(is_pnl.columns)
    corr_cols = set(corr_matrix.columns)
    extra = corr_cols - is_cols
    if extra:
      return [self._finding(
        "FEAT-002", "HIGH",
        f"{len(extra)} correlation columns not in IS daily PnL",
        "Correlation matrix may include OOS-derived assets",
        evidence={"extra_cols": sorted(extra)[:10]},
        affected_columns=sorted(extra),
        remediation="Rebuild correlation from IS-only daily PnL",
      )]
    return []

  def _check_suspiciously_predictive(self, is_pnl):
    """Flag features with suspicious correlation to combined portfolio."""
    if is_pnl.shape[1] < 2:
      return []
    combined = is_pnl.sum(axis=1)
    findings = []
    suspicious = []
    for col in is_pnl.columns:
      corr = float(is_pnl[col].corr(combined))
      if abs(corr) > 0.98:
        suspicious.append({"asset": col, "corr": round(corr, 4)})
    if suspicious:
      findings.append(self._finding(
        "FEAT-003", "MEDIUM",
        f"{len(suspicious)} assets with >0.98 correlation to combined portfolio",
        "May indicate a single asset dominates portfolio (not necessarily leakage)",
        evidence={"suspicious": suspicious[:5]},
        affected_columns=[s["asset"] for s in suspicious],
        confidence=0.5,
      ))
    return findings

  def _check_column_consistency(self, is_pnl, oos_pnl):
    """IS and OOS daily PnL should have the same columns."""
    is_cols = set(is_pnl.columns)
    oos_cols = set(oos_pnl.columns)
    only_oos = oos_cols - is_cols
    findings = []
    if only_oos:
      findings.append(self._finding(
        "FEAT-004", "MEDIUM",
        f"{len(only_oos)} columns only in OOS daily PnL",
        "OOS has assets not present in IS — optimizer never saw these",
        evidence={"oos_only": sorted(only_oos)[:10]},
        affected_columns=sorted(only_oos),
      ))
    return findings

  def _check_global_aggregate_leak(self, is_pnl, oos_pnl):
    """Check if combined IS+OOS stats differ from IS-only stats."""
    if is_pnl.empty or oos_pnl.empty:
      return []
    is_mean = float(is_pnl.values.mean())
    combined = pd.concat([is_pnl, oos_pnl])
    combined_mean = float(combined.values.mean())
    if is_mean == 0:
      return []
    drift = abs(combined_mean - is_mean) / abs(is_mean)
    if drift < 0.15:
      return []
    return [self._finding(
      "FEAT-005", "LOW",
      f"IS vs combined mean differs by {drift:.1%}",
      "If global stats were computed on combined data, IS features are contaminated",
      evidence={"is_mean": round(is_mean, 4), "combined_mean": round(combined_mean, 4), "drift": round(drift, 4)},
      confidence=0.4,
      remediation="Ensure all aggregate statistics (mean, std) computed on IS-only data",
    )]
