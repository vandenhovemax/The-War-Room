"""Preprocessing Leakage Scanner — detects transform ordering violations."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner


class PreprocessingLeakageScanner(AbstractScanner):
  name = "preprocessing"
  category = "Preprocessing Leakage"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    is_pnl = ctx.get("is_daily_pnl")
    is_end = ctx.get("is_end")
    is_trades = ctx.get("is_trades", [])

    if is_pnl is not None and not is_pnl.empty:
      findings.extend(self._check_truncation_complete(is_pnl, is_end))
      findings.extend(self._check_zero_fill(is_pnl))
      findings.extend(self._check_nan_contamination(is_pnl))
    if is_trades:
      findings.extend(self._check_quality_filter_timing(is_trades, is_end))
    findings.extend(self._check_pipeline_order(ctx))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_truncation_complete(self, is_pnl, is_end):
    """Verify daily PnL truncation is complete."""
    if not is_end or is_pnl.empty:
      return []
    boundary = pd.Timestamp(is_end)
    if is_pnl.index.max() <= boundary:
      return []
    return [self._finding(
      "PREP-001", "CRITICAL",
      "Daily PnL truncation incomplete",
      f"Max date {is_pnl.index.max().date()} exceeds IS boundary {is_end}",
      evidence={"max_date": str(is_pnl.index.max()), "is_end": is_end},
      remediation="Apply date_to truncation BEFORE correlation/optimization",
    )]

  def _check_zero_fill(self, is_pnl):
    """Non-trading days should be 0.0, not NaN (affects correlation)."""
    nan_count = int(is_pnl.isna().sum().sum())
    if nan_count == 0:
      return []
    total_cells = is_pnl.shape[0] * is_pnl.shape[1]
    pct = nan_count / total_cells * 100
    return [self._finding(
      "PREP-002", "HIGH" if pct > 5 else "MEDIUM",
      f"{nan_count} NaN values in IS daily PnL ({pct:.1f}%)",
      "NaN values distort correlation matrix and optimizer scoring",
      evidence={"nan_count": nan_count, "pct": round(pct, 2), "total_cells": total_cells},
      remediation="Fill NaN with 0.0: daily_pnl.fillna(0, inplace=True)",
    )]

  def _check_nan_contamination(self, is_pnl):
    """Columns that are entirely NaN (dead assets)."""
    dead_cols = [c for c in is_pnl.columns if is_pnl[c].isna().all() or (is_pnl[c] == 0).all()]
    if not dead_cols:
      return []
    return [self._finding(
      "PREP-003", "MEDIUM",
      f"{len(dead_cols)} dead columns (all NaN or all zero)",
      "Dead columns waste optimizer capacity and skew correlation",
      evidence={"dead_columns": dead_cols[:10]},
      affected_columns=dead_cols,
      remediation="Remove zero-trade columns before optimization",
    )]

  def _check_quality_filter_timing(self, is_trades, is_end):
    """Verify quality filters use IS-only trade counts."""
    if not is_end:
      return []
    boundary = pd.Timestamp(is_end) + pd.Timedelta(days=1)
    oos_trades_in_is = [t for t in is_trades if t.exit_time and pd.Timestamp(t.exit_time) > boundary]
    if not oos_trades_in_is:
      return []
    return [self._finding(
      "PREP-004", "CRITICAL",
      f"{len(oos_trades_in_is)} OOS trades in IS trade list",
      "Quality filter (min_trades, min_pf) computed on contaminated data",
      evidence={"oos_in_is": len(oos_trades_in_is)},
      affected_rows=len(oos_trades_in_is),
      remediation="Apply date_to filter BEFORE quality filtering",
    )]

  def _check_pipeline_order(self, ctx):
    """Verify expected pipeline ordering markers."""
    findings = []
    # Check if daily PnL was built from pre-filtered trades
    is_pnl = ctx.get("is_daily_pnl")
    is_end = ctx.get("is_end")
    if is_pnl is not None and not is_pnl.empty and is_end:
      boundary = pd.Timestamp(is_end)
      if is_pnl.index.max() <= boundary:
        return []  # Pipeline order is correct
      findings.append(self._finding(
        "PREP-005", "HIGH",
        "Pipeline ordering may be wrong",
        "Daily PnL built BEFORE date truncation — transforms may use OOS data",
        evidence={"max_pnl_date": str(is_pnl.index.max()), "is_end": is_end},
        remediation="Build daily PnL from pre-filtered trades, then truncate",
      ))
    return findings
