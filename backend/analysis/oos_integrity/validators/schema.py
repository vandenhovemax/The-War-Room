"""Schema Validator — IS vs OOS structural consistency checks."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner


class SchemaValidator(AbstractScanner):
  name = "schema_validator"
  category = "Schema Consistency"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    is_pnl = ctx.get("is_daily_pnl")
    oos_pnl = ctx.get("oos_daily_pnl")

    if is_pnl is not None and oos_pnl is not None:
      if not is_pnl.empty and not oos_pnl.empty:
        findings.extend(self._check_column_match(is_pnl, oos_pnl))
        findings.extend(self._check_dtype_consistency(is_pnl, oos_pnl))
        findings.extend(self._check_value_range(is_pnl, oos_pnl))
        findings.extend(self._check_missing_patterns(is_pnl, oos_pnl))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_column_match(self, is_pnl, oos_pnl):
    """IS and OOS daily PnL should have the same column set."""
    is_cols = set(is_pnl.columns)
    oos_cols = set(oos_pnl.columns)
    only_is = is_cols - oos_cols
    only_oos = oos_cols - is_cols
    if not only_is and not only_oos:
      return []
    findings = []
    if only_is:
      findings.append(self._finding(
        "SCHEMA-001", "MEDIUM",
        f"{len(only_is)} columns in IS missing from OOS",
        "Assets present in IS but absent from OOS daily PnL",
        evidence={"missing_from_oos": sorted(only_is)[:20]},
        affected_columns=sorted(only_is),
      ))
    if only_oos:
      findings.append(self._finding(
        "SCHEMA-002", "HIGH",
        f"{len(only_oos)} columns in OOS not in IS",
        "OOS has assets not seen during IS — possible data contamination",
        evidence={"extra_in_oos": sorted(only_oos)[:20]},
        affected_columns=sorted(only_oos),
        remediation="Ensure OOS uses same asset universe as IS",
      ))
    return findings

  def _check_dtype_consistency(self, is_pnl, oos_pnl):
    """Column dtypes should match between IS and OOS."""
    shared = set(is_pnl.columns) & set(oos_pnl.columns)
    mismatched = []
    for col in shared:
      is_dtype = str(is_pnl[col].dtype)
      oos_dtype = str(oos_pnl[col].dtype)
      if is_dtype != oos_dtype:
        mismatched.append({"column": col, "is_dtype": is_dtype, "oos_dtype": oos_dtype})
    if not mismatched:
      return []
    return [self._finding(
      "SCHEMA-003", "MEDIUM",
      f"{len(mismatched)} columns have different dtypes IS vs OOS",
      "Type mismatches may indicate different data sources or processing",
      evidence={"mismatched": mismatched[:10]},
      affected_columns=[m["column"] for m in mismatched],
    )]

  def _check_value_range(self, is_pnl, oos_pnl):
    """Flag OOS values far outside IS range (per column)."""
    shared = set(is_pnl.columns) & set(oos_pnl.columns)
    extreme = []
    for col in shared:
      is_col = is_pnl[col].dropna()
      oos_col = oos_pnl[col].dropna()
      if is_col.empty or oos_col.empty:
        continue
      is_min, is_max = is_col.min(), is_col.max()
      is_range = is_max - is_min
      if is_range == 0:
        continue
      oos_min, oos_max = oos_col.min(), oos_col.max()
      # Flag if OOS extends beyond 3x the IS range
      if oos_max > is_max + 3 * is_range or oos_min < is_min - 3 * is_range:
        extreme.append({
          "column": col,
          "is_range": f"[{is_min:.4f}, {is_max:.4f}]",
          "oos_range": f"[{oos_min:.4f}, {oos_max:.4f}]",
        })
    if not extreme:
      return []
    return [self._finding(
      "SCHEMA-004", "LOW",
      f"{len(extreme)} columns have OOS values far outside IS range",
      "Extreme OOS values may indicate regime change or data issues",
      evidence={"extreme_columns": extreme[:10]},
      affected_columns=[e["column"] for e in extreme],
      confidence=0.4,
    )]

  def _check_missing_patterns(self, is_pnl, oos_pnl):
    """NaN patterns should be similar in IS and OOS."""
    shared = list(set(is_pnl.columns) & set(oos_pnl.columns))
    if not shared:
      return []
    is_nan_pct = is_pnl[shared].isnull().mean()
    oos_nan_pct = oos_pnl[shared].isnull().mean()
    big_diff = []
    for col in shared:
      diff = abs(is_nan_pct[col] - oos_nan_pct[col])
      if diff > 0.3:
        big_diff.append({
          "column": col,
          "is_nan_pct": round(float(is_nan_pct[col]), 3),
          "oos_nan_pct": round(float(oos_nan_pct[col]), 3),
        })
    if not big_diff:
      return []
    return [self._finding(
      "SCHEMA-005", "MEDIUM",
      f"{len(big_diff)} columns have very different NaN rates IS vs OOS",
      "Mismatched missing-data patterns suggest different data pipelines",
      evidence={"columns": big_diff[:10]},
      affected_columns=[d["column"] for d in big_diff],
      confidence=0.5,
    )]
