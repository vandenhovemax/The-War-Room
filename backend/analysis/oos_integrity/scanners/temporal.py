"""Temporal Leakage Scanner — detects time-based IS/OOS contamination.

The single most dangerous form of OOS contamination in trading systems.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner


class TemporalLeakageScanner(AbstractScanner):
  name = "temporal"
  category = "Temporal Leakage"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    is_trades = ctx.get("is_trades", [])
    oos_trades = ctx.get("oos_trades", [])
    is_end = ctx.get("is_end")
    is_pnl = ctx.get("is_daily_pnl")

    if is_end:
      findings.extend(self._check_direct_overlap(is_trades, oos_trades, is_end))
      findings.extend(self._check_entry_lookahead(is_trades, is_end))
      findings.extend(self._check_exit_boundary(is_trades, is_end))
      findings.extend(self._check_embargo(is_trades, oos_trades, is_end, config))
    if is_pnl is not None and not is_pnl.empty:
      findings.extend(self._check_pnl_temporal_leak(is_pnl, is_end))
      findings.extend(self._check_autocorrelation(is_pnl, config))
    if is_trades and oos_trades:
      findings.extend(self._check_temporal_gap(is_trades, oos_trades))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_direct_overlap(self, is_trades, oos_trades, is_end):
    """OOS trades with timestamps falling within IS range."""
    if not oos_trades:
      return []
    boundary = pd.Timestamp(is_end)
    violators = [
      t for t in oos_trades
      if t.exit_time and pd.Timestamp(t.exit_time) <= boundary
    ]
    if not violators:
      return []
    pct = len(violators) / len(oos_trades) * 100
    return [self._finding(
      "TEMP-001", "CRITICAL",
      f"{len(violators)} OOS trades fall within IS period",
      f"{pct:.1f}% of OOS trades have exit_time <= {is_end}",
      evidence={"count": len(violators), "pct": pct},
      affected_rows=len(violators), contamination_pct=pct,
      remediation="Re-filter OOS trades: exit_time > is_end",
    )]

  def _check_entry_lookahead(self, is_trades, is_end):
    """IS trades with entry signals from OOS candles."""
    boundary = pd.Timestamp(is_end) + pd.Timedelta(days=1)
    bad = [t for t in is_trades if t.entry_time and pd.Timestamp(t.entry_time) > boundary]
    if not bad:
      return []
    return [self._finding(
      "TEMP-002", "CRITICAL",
      f"{len(bad)} IS trades have entry_time in OOS period",
      "Entry signals computed from OOS candles — look-ahead bias",
      evidence={"count": len(bad), "first": str(bad[0].entry_time)},
      affected_rows=len(bad), contamination_pct=len(bad) / max(len(is_trades), 1) * 100,
      remediation="Ensure entry signals use only IS-period candle data",
    )]

  def _check_exit_boundary(self, is_trades, is_end):
    """IS trades exiting beyond IS boundary (straddling trades)."""
    boundary = pd.Timestamp(is_end) + pd.Timedelta(days=1)
    bad = [t for t in is_trades if t.exit_time and pd.Timestamp(t.exit_time) > boundary]
    if not bad:
      return []
    return [self._finding(
      "TEMP-003", "CRITICAL",
      f"{len(bad)} IS trades exit after IS boundary",
      "Trades entering IS but exiting in OOS period",
      evidence={"count": len(bad)},
      affected_rows=len(bad),
      remediation="Use filter_trades_strict() to exclude straddling trades",
    )]

  def _check_embargo(self, is_trades, oos_trades, is_end, config):
    """Validate embargo gap between IS and OOS."""
    if config.embargo_days <= 0 or not is_trades or not oos_trades:
      return []
    is_max = max(pd.Timestamp(t.exit_time) for t in is_trades if t.exit_time)
    oos_min = min(pd.Timestamp(t.entry_time) for t in oos_trades if t.entry_time)
    gap_days = (oos_min - is_max).days
    if gap_days >= config.embargo_days:
      return []
    return [self._finding(
      "TEMP-004", "MEDIUM",
      f"Embargo gap too short: {gap_days}d (need {config.embargo_days}d)",
      "Autocorrelation may leak IS information into early OOS trades",
      evidence={"gap_days": gap_days, "required": config.embargo_days},
      remediation=f"Add {config.embargo_days - gap_days}d embargo between IS and OOS",
    )]

  def _check_pnl_temporal_leak(self, is_pnl, is_end):
    """Daily PnL matrix rows beyond IS boundary."""
    if is_pnl.empty or not is_end:
      return []
    boundary = pd.Timestamp(is_end)
    beyond = is_pnl[is_pnl.index > boundary]
    if beyond.empty:
      return []
    return [self._finding(
      "TEMP-005", "CRITICAL",
      f"{len(beyond)} daily PnL rows exceed IS boundary",
      f"IS daily PnL extends to {is_pnl.index.max().date()}, beyond {is_end}",
      evidence={"rows_beyond": len(beyond), "max_date": str(is_pnl.index.max())},
      affected_rows=len(beyond),
      remediation="Truncate: daily_pnl = daily_pnl[daily_pnl.index <= is_end]",
    )]

  def _check_autocorrelation(self, is_pnl, config):
    """Measure daily PnL autocorrelation to recommend embargo width."""
    if is_pnl.empty or is_pnl.shape[1] == 0:
      return []
    combined = is_pnl.sum(axis=1)
    if len(combined) < config.max_autocorr_lag + 10:
      return []
    acf_values = [float(combined.autocorr(lag=i)) for i in range(1, min(config.max_autocorr_lag + 1, len(combined)))]
    significant_lags = sum(1 for a in acf_values if abs(a) > 2 / np.sqrt(len(combined)))
    if significant_lags == 0:
      return []
    return [self._finding(
      "TEMP-006", "LOW",
      f"{significant_lags} significant autocorrelation lags detected",
      f"Daily PnL has serial correlation — consider embargo of {significant_lags}+ days",
      evidence={"significant_lags": significant_lags, "acf_lag1": acf_values[0] if acf_values else 0},
      confidence=0.7,
      remediation=f"Set embargo_days >= {significant_lags} in config",
    )]

  def _check_temporal_gap(self, is_trades, oos_trades):
    """Flag suspicious gaps between IS and OOS (cherry-picking)."""
    if not is_trades or not oos_trades:
      return []
    is_max = max(pd.Timestamp(t.exit_time) for t in is_trades if t.exit_time)
    oos_min = min(pd.Timestamp(t.entry_time) for t in oos_trades if t.entry_time)
    gap_days = (oos_min - is_max).days
    if gap_days <= 90:
      return []
    return [self._finding(
      "TEMP-007", "MEDIUM",
      f"Large {gap_days}-day gap between IS and OOS",
      "Large gap may indicate cherry-picked evaluation period",
      evidence={"gap_days": gap_days, "is_max": str(is_max.date()), "oos_min": str(oos_min.date())},
      confidence=0.5,
      remediation="Verify gap is intentional (embargo) not cherry-picking",
    )]
