"""Statistical Validation Scanner — performance gap and consistency checks."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner
from backend.analysis.oos_integrity.utils.stats import permutation_test


class StatisticalValidationScanner(AbstractScanner):
  name = "statistical"
  category = "Statistical Validation"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    is_pnl = ctx.get("is_daily_pnl")
    oos_pnl = ctx.get("oos_daily_pnl")
    optimizer_results = ctx.get("optimizer_results")

    if is_pnl is not None and oos_pnl is not None:
      if not is_pnl.empty and not oos_pnl.empty:
        findings.extend(self._check_performance_gap(is_pnl, oos_pnl, config))
        findings.extend(self._check_oos_better_than_is(is_pnl, oos_pnl))
        findings.extend(self._check_stability_consistency(is_pnl, oos_pnl))
        findings.extend(self._check_pf_consistency(is_pnl, oos_pnl))
        findings.extend(self._check_permutation(is_pnl, oos_pnl))
    if optimizer_results:
      findings.extend(self._check_wfe_sanity(optimizer_results))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_performance_gap(self, is_pnl, oos_pnl, config):
    """IS-OOS performance suspiciously close = possible leakage."""
    is_combined = is_pnl.sum(axis=1)
    oos_combined = oos_pnl.sum(axis=1)
    is_sharpe = self._daily_sharpe(is_combined)
    oos_sharpe = self._daily_sharpe(oos_combined)
    if is_sharpe == 0:
      return []
    gap = abs(is_sharpe - oos_sharpe) / max(abs(is_sharpe), 0.01)
    if gap >= config.performance_gap_alert:
      return []
    return [self._finding(
      "STAT-001", "MEDIUM",
      f"IS-OOS Sharpe gap suspiciously small: {gap:.4f} (threshold {config.performance_gap_alert})",
      f"IS Sharpe={is_sharpe:.3f}, OOS Sharpe={oos_sharpe:.3f}",
      evidence={"is_sharpe": round(is_sharpe, 4), "oos_sharpe": round(oos_sharpe, 4), "gap": round(gap, 4)},
      confidence=0.5,
      remediation="May indicate IS/OOS contamination — investigate data boundaries",
    )]

  def _check_oos_better_than_is(self, is_pnl, oos_pnl):
    """OOS outperforming IS is a strong leakage signal."""
    is_combined = is_pnl.sum(axis=1)
    oos_combined = oos_pnl.sum(axis=1)
    is_sharpe = self._daily_sharpe(is_combined)
    oos_sharpe = self._daily_sharpe(oos_combined)
    if oos_sharpe <= is_sharpe or is_sharpe <= 0:
      return []
    improvement = (oos_sharpe - is_sharpe) / max(abs(is_sharpe), 0.01)
    if improvement < 0.3:
      return []
    return [self._finding(
      "STAT-002", "HIGH",
      f"OOS Sharpe ({oos_sharpe:.3f}) exceeds IS ({is_sharpe:.3f}) by {improvement:.0%}",
      "OOS outperforming IS by >30% is a strong indicator of data leakage",
      evidence={"is_sharpe": round(is_sharpe, 4), "oos_sharpe": round(oos_sharpe, 4),
                "improvement": round(improvement, 4)},
      remediation="Carefully review all data boundaries — likely contamination",
    )]

  def _check_stability_consistency(self, is_pnl, oos_pnl):
    """Compare IS vs OOS equity curve stability."""
    from backend.analysis.scoring import calculate_stability
    is_stab = calculate_stability(is_pnl.sum(axis=1))
    oos_stab = calculate_stability(oos_pnl.sum(axis=1))
    diff = abs(is_stab - oos_stab)
    if diff < 0.3:
      return []
    return [self._finding(
      "STAT-003", "MEDIUM",
      f"Stability divergence: IS={is_stab:.3f}, OOS={oos_stab:.3f} (diff={diff:.3f})",
      "Large stability gap suggests different market regimes or data issues",
      evidence={"is_stability": round(is_stab, 4), "oos_stability": round(oos_stab, 4)},
      confidence=0.6,
    )]

  def _check_pf_consistency(self, is_pnl, oos_pnl):
    """Per-asset profit factor comparison IS vs OOS."""
    common = sorted(set(is_pnl.columns) & set(oos_pnl.columns))
    large_divergence = []
    for col in common:
      is_pf = self._profit_factor(is_pnl[col])
      oos_pf = self._profit_factor(oos_pnl[col])
      if is_pf > 0 and oos_pf > 0:
        ratio = max(is_pf, oos_pf) / max(min(is_pf, oos_pf), 0.01)
        if ratio > 3.0:
          large_divergence.append({"asset": col, "is_pf": round(is_pf, 2), "oos_pf": round(oos_pf, 2)})
    if not large_divergence:
      return []
    return [self._finding(
      "STAT-004", "LOW",
      f"{len(large_divergence)} assets have >3x PF divergence IS vs OOS",
      "Large PF divergence may indicate overfitting to IS period",
      evidence={"divergent": large_divergence[:10]},
      affected_columns=[d["asset"] for d in large_divergence],
      confidence=0.5,
    )]

  def _check_permutation(self, is_pnl, oos_pnl):
    """Permutation test: shuffle IS/OOS labels, check if gap is significant."""
    common = sorted(set(is_pnl.columns) & set(oos_pnl.columns))
    if not common or len(common) < 2:
      return []
    is_vals = is_pnl[common].sum(axis=1).values
    oos_vals = oos_pnl[common].sum(axis=1).values
    p_value = permutation_test(is_vals, oos_vals, n_perms=200)
    if p_value > 0.05:
      return []
    return [self._finding(
      "STAT-005", "LOW",
      f"Permutation test: significant IS/OOS difference (p={p_value:.4f})",
      "IS and OOS combined PnL means differ significantly — may be regime change",
      evidence={"p_value": round(p_value, 4)},
      confidence=0.6,
    )]

  def _check_wfe_sanity(self, results):
    """Walk-Forward Efficiency values out of sane range."""
    findings = []
    for i, r in enumerate(results):
      wfe = r.get("oos_wfe") or r.get("wfe")
      if wfe is None:
        continue
      if wfe > 1.5:
        findings.append(self._finding(
          f"STAT-006-{i}", "HIGH",
          f"WFE={wfe:.2f} exceeds 1.5 — OOS outperforming IS per-day",
          "Walk-Forward Efficiency > 1.5 is suspicious — possible leakage",
          evidence={"wfe": wfe, "result_index": i},
        ))
      elif wfe < -0.5:
        findings.append(self._finding(
          f"STAT-007-{i}", "MEDIUM",
          f"WFE={wfe:.2f} is very negative — strategy failed dramatically OOS",
          evidence={"wfe": wfe, "result_index": i},
          confidence=0.7,
        ))
    return findings

  @staticmethod
  def _daily_sharpe(series: pd.Series) -> float:
    mean = float(series.mean())
    std = float(series.std())
    return mean / std * np.sqrt(365) if std > 0 else 0.0

  @staticmethod
  def _profit_factor(series: pd.Series) -> float:
    gp = float(series[series > 0].sum())
    gl = abs(float(series[series < 0].sum()))
    return min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
