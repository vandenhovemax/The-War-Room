"""OOS Integrity Agent — data models for findings and reports."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

SCORE_BANDS = [
  (95, "PRISTINE"),
  (80, "CLEAN"),
  (60, "CAUTIOUS"),
  (40, "COMPROMISED"),
  (20, "SEVERE"),
  (0, "FRAUDULENT"),
]


def status_from_score(score: float) -> str:
  """Map 0-100 score to status band."""
  for threshold, label in SCORE_BANDS:
    if score >= threshold:
      return label
  return "FRAUDULENT"


@dataclass
class Finding:
  """Single contamination finding with evidence and remediation."""
  id: str
  scanner: str
  severity: str
  category: str
  title: str
  description: str
  evidence: dict = field(default_factory=dict)
  affected_columns: list[str] = field(default_factory=list)
  affected_rows: int = 0
  contamination_pct: float = 0.0
  remediation: str = ""
  confidence: float = 1.0


@dataclass
class ScanResult:
  """Result from a single scanner run."""
  scanner_name: str
  findings: list[Finding] = field(default_factory=list)
  score: float = 100.0
  duration_ms: float = 0.0

  @property
  def has_critical(self) -> bool:
    return any(f.severity == "CRITICAL" for f in self.findings)

  @property
  def worst_severity(self) -> str:
    if not self.findings:
      return "INFO"
    return min(self.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 4)).severity


@dataclass
class IntegrityScore:
  """Breakdown of integrity scores by category."""
  overall: float = 100.0
  temporal: float = 100.0
  identity: float = 100.0
  feature: float = 100.0
  preprocessing: float = 100.0
  distribution: float = 100.0
  statistical: float = 100.0
  pipeline: float = 100.0
  group: float = 100.0
  wfo_fold: float = 100.0
  quant_finance: float = 100.0
  truthfulness: float = 100.0
  timestamp_val: float = 100.0
  schema_val: float = 100.0


@dataclass
class AuditReport:
  """Full audit report with scores, findings, and verdict."""
  context: str = ""
  timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
  score: IntegrityScore = field(default_factory=IntegrityScore)
  scan_results: list[ScanResult] = field(default_factory=list)
  all_findings: list[Finding] = field(default_factory=list)
  summary: str = ""
  status: str = "PRISTINE"
