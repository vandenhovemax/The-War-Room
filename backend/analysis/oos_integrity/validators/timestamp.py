"""Timestamp Validator — temporal ordering, gaps, DST, and duplicates."""
from __future__ import annotations

from collections import Counter

import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner


class TimestampValidator(AbstractScanner):
  name = "timestamp_validator"
  category = "Timestamp Integrity"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    all_trades = ctx.get("is_trades", []) + ctx.get("oos_trades", [])

    if all_trades:
      findings.extend(self._check_monotonic(all_trades))
      findings.extend(self._check_future_dates(all_trades))
      findings.extend(self._check_duplicate_timestamps(all_trades))
      findings.extend(self._check_suspicious_gaps(all_trades))
      findings.extend(self._check_precision_consistency(all_trades))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_monotonic(self, trades):
    """Per-symbol: entry_time should precede exit_time."""
    bad = []
    for t in trades:
      if t.entry_time and t.exit_time:
        if pd.Timestamp(t.entry_time) > pd.Timestamp(t.exit_time):
          bad.append({"id": t.id, "symbol": t.symbol,
                      "entry": str(t.entry_time), "exit": str(t.exit_time)})
    if not bad:
      return []
    return [self._finding(
      "TS-001", "CRITICAL",
      f"{len(bad)} trades with entry_time > exit_time",
      "Trade timing is inverted — possible data corruption",
      evidence={"bad_trades": bad[:10]},
      affected_rows=len(bad),
      remediation="Investigate trade recording logic in engine.py",
    )]

  def _check_future_dates(self, trades):
    """No trade timestamps should be in the future."""
    now = pd.Timestamp.now()
    future = [
      {"id": t.id, "exit": str(t.exit_time)}
      for t in trades if t.exit_time and pd.Timestamp(t.exit_time) > now
    ]
    if not future:
      return []
    return [self._finding(
      "TS-002", "HIGH",
      f"{len(future)} trades have future exit timestamps",
      "Exit dates beyond current date — possibly synthetic or misconfigured data",
      evidence={"future_trades": future[:10]},
      affected_rows=len(future),
    )]

  def _check_duplicate_timestamps(self, trades):
    """Same (symbol, entry_time, exit_time) appearing multiple times."""
    keys = Counter(
      (t.symbol, str(t.entry_time), str(t.exit_time))
      for t in trades if t.entry_time and t.exit_time
    )
    dupes = {k: v for k, v in keys.items() if v > 1}
    if not dupes:
      return []
    total_dup = sum(v - 1 for v in dupes.values())
    return [self._finding(
      "TS-003", "MEDIUM",
      f"{total_dup} duplicate timestamp entries across {len(dupes)} unique keys",
      "Same symbol with identical entry/exit times — possible double-recording",
      evidence={"unique_duplicates": len(dupes), "total_extra": total_dup},
      affected_rows=total_dup,
    )]

  def _check_suspicious_gaps(self, trades):
    """Flag unusually long gaps between consecutive trades per symbol."""
    by_symbol: dict[str, list] = {}
    for t in trades:
      if t.exit_time:
        by_symbol.setdefault(t.symbol, []).append(pd.Timestamp(t.exit_time))
    suspicious = []
    for sym, times in by_symbol.items():
      if len(times) < 2:
        continue
      sorted_times = sorted(times)
      for i in range(1, len(sorted_times)):
        gap = (sorted_times[i] - sorted_times[i - 1]).days
        if gap > 180:
          suspicious.append({"symbol": sym, "gap_days": gap,
                             "from": str(sorted_times[i - 1].date()),
                             "to": str(sorted_times[i].date())})
          break
    if not suspicious:
      return []
    return [self._finding(
      "TS-004", "LOW",
      f"{len(suspicious)} symbols have 180+ day gaps between trades",
      "Large gaps may indicate missing data periods or strategy inactivity",
      evidence={"gaps": suspicious[:10]},
      confidence=0.4,
    )]

  def _check_precision_consistency(self, trades):
    """Timestamp precision should be consistent."""
    has_subsecond = 0
    has_date_only = 0
    for t in trades[:500]:
      if t.entry_time:
        ts = pd.Timestamp(t.entry_time)
        if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
          has_date_only += 1
        elif ts.microsecond > 0:
          has_subsecond += 1
    total = min(len(trades), 500)
    if total == 0:
      return []
    date_pct = has_date_only / total
    sub_pct = has_subsecond / total
    if date_pct > 0.1 and sub_pct > 0.1:
      return [self._finding(
        "TS-005", "MEDIUM",
        f"Mixed timestamp precision: {date_pct:.0%} date-only, {sub_pct:.0%} sub-second",
        "Inconsistent precision may indicate data from different sources",
        evidence={"date_only_pct": round(date_pct, 2), "subsecond_pct": round(sub_pct, 2)},
        confidence=0.5,
      )]
    return []
