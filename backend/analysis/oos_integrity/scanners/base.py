"""Abstract base class for all OOS integrity scanners."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import Finding, ScanResult


class AbstractScanner(ABC):
  """Base scanner — subclasses implement scan() with domain-specific checks."""

  name: str = "base"
  category: str = "General"

  @abstractmethod
  def scan(self, context: dict, config: AuditConfig) -> ScanResult:
    """Run all checks and return findings.

    Args:
      context: dict with keys like is_trades, oos_trades, is_daily_pnl, etc.
      config: AuditConfig with thresholds and toggles.
    """
    ...

  def _run(self, context: dict, config: AuditConfig) -> ScanResult:
    """Timed wrapper around scan()."""
    t0 = time.perf_counter()
    result = self.scan(context, config)
    result.duration_ms = (time.perf_counter() - t0) * 1000
    return result

  def _finding(
    self, id: str, severity: str, title: str,
    description: str = "", evidence: dict | None = None,
    affected_columns: list[str] | None = None,
    affected_rows: int = 0, contamination_pct: float = 0.0,
    remediation: str = "", confidence: float = 1.0,
  ) -> Finding:
    """Helper to create a Finding with scanner metadata."""
    return Finding(
      id=id, scanner=self.name, severity=severity,
      category=self.category, title=title, description=description,
      evidence=evidence or {}, affected_columns=affected_columns or [],
      affected_rows=affected_rows, contamination_pct=contamination_pct,
      remediation=remediation, confidence=confidence,
    )
