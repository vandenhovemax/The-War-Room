"""Data Truthfulness Validator — Benford's law, impossible values, PnL consistency."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner


class TruthfulnessValidator(AbstractScanner):
  name = "truthfulness"
  category = "Data Truthfulness"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    all_trades = ctx.get("is_trades", []) + ctx.get("oos_trades", [])

    if all_trades:
      findings.extend(self._check_benfords_law(all_trades, config))
      findings.extend(self._check_impossible_values(all_trades))
      findings.extend(self._check_pnl_consistency(all_trades))
      findings.extend(self._check_mae_mfe_consistency(all_trades))
      findings.extend(self._check_round_number_bias(all_trades))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_benfords_law(self, trades, config):
    """First-digit distribution of net_profit should follow Benford's law."""
    values = [abs(t.net_profit) for t in trades if t.net_profit and t.net_profit != 0]
    if len(values) < 100:
      return []

    # Expected Benford's distribution
    expected = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
    observed = {d: 0 for d in range(1, 10)}
    for v in values:
      first_digit = int(str(f"{v:.10f}").lstrip("0").lstrip(".")[0])
      if 1 <= first_digit <= 9:
        observed[first_digit] += 1

    total = sum(observed.values())
    if total == 0:
      return []

    chi2 = sum(
      (observed[d] / total - expected[d]) ** 2 / expected[d]
      for d in range(1, 10)
    ) * total

    if chi2 <= config.benford_chi2_threshold:
      return []
    return [self._finding(
      "TRUTH-001", "MEDIUM",
      f"Benford's Law violation: chi2={chi2:.1f} (threshold {config.benford_chi2_threshold})",
      "First-digit distribution of net_profit deviates from natural data",
      evidence={"chi2": round(chi2, 2), "observed": observed, "n": total},
      confidence=0.6,
      remediation="Investigate trade PnL calculation — may indicate synthetic data",
    )]

  def _check_impossible_values(self, trades):
    """Flag trades with impossible field values."""
    bad = []
    for t in trades:
      reasons = []
      if t.entry_price is not None and t.entry_price <= 0:
        reasons.append("entry_price<=0")
      if t.exit_price is not None and t.exit_price <= 0:
        reasons.append("exit_price<=0")
      if t.quantity is not None and t.quantity <= 0:
        reasons.append("quantity<=0")
      if reasons:
        bad.append({"id": t.id, "symbol": t.symbol, "reasons": reasons})
    if not bad:
      return []
    return [self._finding(
      "TRUTH-002", "CRITICAL",
      f"{len(bad)} trades with impossible values",
      "Negative or zero prices/quantities indicate data corruption",
      evidence={"bad_trades": bad[:10]},
      affected_rows=len(bad),
    )]

  def _check_pnl_consistency(self, trades):
    """Verify net_profit is consistent with price/qty/side."""
    inconsistent = []
    for t in trades[:1000]:
      if not all([t.entry_price, t.exit_price, t.quantity, t.net_profit]):
        continue
      side = getattr(t, "side", "Long") or "Long"
      if side.lower() == "long":
        expected = (t.exit_price - t.entry_price) * t.quantity
      else:
        expected = (t.entry_price - t.exit_price) * t.quantity
      commission = getattr(t, "commission", 0) or 0
      expected -= commission
      if abs(expected) < 0.01:
        continue
      diff_pct = abs(t.net_profit - expected) / abs(expected) * 100
      if diff_pct > 20:
        inconsistent.append({
          "id": t.id, "symbol": t.symbol,
          "net_profit": round(t.net_profit, 2),
          "expected": round(expected, 2),
          "diff_pct": round(diff_pct, 1),
        })
    if not inconsistent:
      return []
    return [self._finding(
      "TRUTH-003", "MEDIUM" if len(inconsistent) < 10 else "HIGH",
      f"{len(inconsistent)} trades with >20% PnL discrepancy",
      "Net profit doesn't match (exit - entry) * qty - commission",
      evidence={"inconsistent": inconsistent[:10]},
      affected_rows=len(inconsistent),
      confidence=0.7,
      remediation="Check multi-position exits, partial fills, or slippage calculations",
    )]

  def _check_mae_mfe_consistency(self, trades):
    """MAE should be <= 0 for longs, MFE should be >= 0."""
    bad_mae = 0
    bad_mfe = 0
    for t in trades:
      side = getattr(t, "side", "Long") or "Long"
      mae = getattr(t, "mae", None)
      mfe = getattr(t, "mfe", None)
      if mae is not None and side.lower() == "long" and mae > 0.01:
        bad_mae += 1
      if mfe is not None and side.lower() == "long" and mfe < -0.01:
        bad_mfe += 1
    findings = []
    if bad_mae > 0:
      findings.append(self._finding(
        "TRUTH-004", "HIGH",
        f"{bad_mae} long trades with positive MAE (should be <= 0)",
        "MAE represents adverse excursion — positive for longs is impossible",
        evidence={"count": bad_mae},
        affected_rows=bad_mae,
      ))
    if bad_mfe > 0:
      findings.append(self._finding(
        "TRUTH-005", "HIGH",
        f"{bad_mfe} long trades with negative MFE (should be >= 0)",
        evidence={"count": bad_mfe},
        affected_rows=bad_mfe,
      ))
    return findings

  def _check_round_number_bias(self, trades):
    """Excessive clustering at round numbers."""
    prices = [t.net_profit for t in trades if t.net_profit is not None]
    if len(prices) < 50:
      return []
    round_count = sum(1 for p in prices if p == round(p))
    pct = round_count / len(prices) * 100
    if pct < 80:
      return []
    return [self._finding(
      "TRUTH-006", "LOW",
      f"{pct:.0f}% of net_profit values are whole numbers",
      "Excessive rounding may indicate simplified or synthetic data",
      evidence={"round_pct": round(pct, 1), "total": len(prices)},
      confidence=0.4,
    )]
