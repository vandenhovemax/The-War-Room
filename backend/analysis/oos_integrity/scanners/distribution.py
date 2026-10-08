"""Distribution Shift Scanner — validates IS vs OOS statistical properties."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner
from backend.analysis.oos_integrity.utils.stats import (
  adversarial_validation,
  compute_psi,
  ks_test,
)


class DistributionShiftScanner(AbstractScanner):
  name = "distribution"
  category = "Distribution Shift"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    is_pnl = ctx.get("is_daily_pnl")
    oos_pnl = ctx.get("oos_daily_pnl")
    corr_matrix = ctx.get("corr_matrix")

    if is_pnl is not None and oos_pnl is not None:
      if not is_pnl.empty and not oos_pnl.empty:
        findings.extend(self._check_ks_per_asset(is_pnl, oos_pnl))
        findings.extend(self._check_psi_per_asset(is_pnl, oos_pnl, config))
        findings.extend(self._check_adversarial(is_pnl, oos_pnl, config))
        findings.extend(self._check_target_shift(is_pnl, oos_pnl))
        findings.extend(self._check_too_similar(is_pnl, oos_pnl))
        if corr_matrix is not None:
          findings.extend(self._check_corr_structure_shift(is_pnl, oos_pnl))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_ks_per_asset(self, is_pnl, oos_pnl):
    """Per-asset Kolmogorov-Smirnov test."""
    common = sorted(set(is_pnl.columns) & set(oos_pnl.columns))
    shifted = []
    for col in common:
      stat, p = ks_test(is_pnl[col], oos_pnl[col])
      if p < 0.01:
        shifted.append({"asset": col, "ks_stat": round(stat, 4), "p_value": round(p, 6)})
    if not shifted:
      return []
    return [self._finding(
      "DIST-001", "MEDIUM" if len(shifted) < len(common) * 0.5 else "HIGH",
      f"{len(shifted)}/{len(common)} assets show significant distribution shift (KS p<0.01)",
      "IS and OOS daily PnL distributions differ — may indicate regime change or data issue",
      evidence={"shifted_assets": shifted[:10], "total_checked": len(common)},
      affected_columns=[s["asset"] for s in shifted],
      confidence=0.8,
    )]

  def _check_psi_per_asset(self, is_pnl, oos_pnl, config):
    """Population Stability Index per asset."""
    common = sorted(set(is_pnl.columns) & set(oos_pnl.columns))
    high_psi = []
    for col in common:
      psi = compute_psi(is_pnl[col], oos_pnl[col])
      if psi > config.psi_threshold:
        high_psi.append({"asset": col, "psi": round(psi, 4)})
    if not high_psi:
      return []
    return [self._finding(
      "DIST-002", "HIGH" if len(high_psi) > 3 else "MEDIUM",
      f"{len(high_psi)} assets exceed PSI threshold ({config.psi_threshold})",
      "Significant population shift detected — OOS may not be representative",
      evidence={"high_psi_assets": high_psi[:10]},
      affected_columns=[h["asset"] for h in high_psi],
    )]

  def _check_adversarial(self, is_pnl, oos_pnl, config):
    """Train classifier to distinguish IS from OOS rows."""
    common = sorted(set(is_pnl.columns) & set(oos_pnl.columns))
    if len(common) < 2:
      return []
    auc = adversarial_validation(is_pnl[common], oos_pnl[common])
    if auc <= config.adversarial_auc_threshold:
      return []
    severity = "HIGH" if auc > 0.70 else "MEDIUM"
    return [self._finding(
      "DIST-003", severity,
      f"Adversarial validation AUC={auc:.3f} (threshold {config.adversarial_auc_threshold})",
      "A classifier can distinguish IS from OOS — distributions are meaningfully different",
      evidence={"auc": round(auc, 4), "threshold": config.adversarial_auc_threshold},
      confidence=0.85,
      remediation="Investigate whether shift is regime change (acceptable) or data leak",
    )]

  def _check_target_shift(self, is_pnl, oos_pnl):
    """Compare combined portfolio PnL distribution IS vs OOS."""
    is_combined = is_pnl.sum(axis=1)
    oos_combined = oos_pnl.sum(axis=1)
    is_mean, is_std = float(is_combined.mean()), float(is_combined.std())
    oos_mean, _oos_std = float(oos_combined.mean()), float(oos_combined.std())
    if is_std == 0:
      return []
    mean_shift = abs(oos_mean - is_mean) / max(is_std, 1e-10)
    if mean_shift < 1.0:
      return []
    return [self._finding(
      "DIST-004", "MEDIUM",
      f"Combined PnL mean shifted by {mean_shift:.1f} sigma",
      f"IS mean={is_mean:.2f} vs OOS mean={oos_mean:.2f}",
      evidence={"is_mean": round(is_mean, 2), "oos_mean": round(oos_mean, 2),
                "shift_sigma": round(mean_shift, 2)},
      confidence=0.6,
    )]

  def _check_too_similar(self, is_pnl, oos_pnl):
    """If IS and OOS are TOO similar, OOS may be recycled IS data."""
    common = sorted(set(is_pnl.columns) & set(oos_pnl.columns))
    if len(common) < 2:
      return []
    identical_count = 0
    for col in common:
      _, p = ks_test(is_pnl[col], oos_pnl[col])
      if p > 0.999:
        identical_count += 1
    if identical_count < len(common) * 0.8:
      return []
    return [self._finding(
      "DIST-005", "HIGH",
      f"{identical_count}/{len(common)} assets have identical IS/OOS distributions (p>0.999)",
      "OOS may be sampled from IS data rather than genuinely unseen",
      evidence={"identical_count": identical_count, "total": len(common)},
      contamination_pct=identical_count / len(common) * 100,
    )]

  def _check_corr_structure_shift(self, is_pnl, oos_pnl):
    """Compare IS vs OOS correlation matrices."""
    common = sorted(set(is_pnl.columns) & set(oos_pnl.columns))
    if len(common) < 3:
      return []
    is_corr = is_pnl[common].corr()
    oos_corr = oos_pnl[common].corr()
    diff = np.linalg.norm(is_corr.values - oos_corr.values, "fro")
    n = len(common)
    normalized = diff / n
    if normalized < 0.3:
      return []
    return [self._finding(
      "DIST-006", "MEDIUM",
      f"Correlation structure shift: Frobenius norm={diff:.2f} (normalized={normalized:.3f})",
      "IS and OOS correlation structures differ significantly",
      evidence={"frobenius": round(diff, 2), "normalized": round(normalized, 3), "n_assets": n},
      confidence=0.6,
    )]
