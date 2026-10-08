"""Report builder — JSON + Markdown output for OOS integrity audits."""
from __future__ import annotations

from dataclasses import asdict

from backend.analysis.oos_integrity.models import (
  SEVERITY_ORDER,
  AuditReport,
  Finding,
  IntegrityScore,
  ScanResult,
  status_from_score,
)

# --- Score computation ---

SCANNER_SCORE_MAP = {
  "temporal": "temporal",
  "identity": "identity",
  "feature": "feature",
  "preprocessing": "preprocessing",
  "distribution": "distribution",
  "statistical": "statistical",
  "pipeline": "pipeline",
  "group": "group",
  "wfo_fold": "wfo_fold",
  "quant_finance": "quant_finance",
  "truthfulness": "truthfulness",
  "timestamp_validator": "timestamp_val",
  "schema_validator": "schema_val",
}


def compute_integrity_score(scan_results: list[ScanResult]) -> IntegrityScore:
  """Build IntegrityScore from individual scanner results."""
  score = IntegrityScore()
  for result in scan_results:
    attr = SCANNER_SCORE_MAP.get(result.scanner_name)
    if attr and hasattr(score, attr):
      setattr(score, attr, result.score)

  # Overall = weighted average (critical scanners weighted higher)
  critical_scanners = ["temporal", "identity", "pipeline", "wfo_fold"]
  weights = {}
  for attr_name in SCANNER_SCORE_MAP.values():
    weights[attr_name] = 2.0 if attr_name in critical_scanners else 1.0
  total_w = sum(weights.values())
  weighted_sum = sum(getattr(score, attr) * w for attr, w in weights.items())
  score.overall = round(weighted_sum / total_w, 1)
  return score


def build_summary(score: IntegrityScore, findings: list[Finding]) -> str:
  """Build human-readable verdict summary."""
  status = status_from_score(score.overall)
  n_critical = sum(1 for f in findings if f.severity == "CRITICAL")
  n_high = sum(1 for f in findings if f.severity == "HIGH")
  n_medium = sum(1 for f in findings if f.severity == "MEDIUM")
  n_low = sum(1 for f in findings if f.severity == "LOW")
  n_info = sum(1 for f in findings if f.severity == "INFO")

  parts = [f"Overall score: {score.overall}/100 — {status}"]
  if n_critical:
    parts.append(f"{n_critical} CRITICAL finding(s) require immediate attention")
  if n_high:
    parts.append(f"{n_high} HIGH severity finding(s)")
  if n_medium:
    parts.append(f"{n_medium} MEDIUM severity finding(s)")
  if n_low + n_info:
    parts.append(f"{n_low} LOW + {n_info} INFO finding(s)")
  if not findings:
    parts.append("No issues detected — data integrity is pristine")
  return ". ".join(parts) + "."


def build_report(
  scan_results: list[ScanResult],
  context: str = "",
) -> AuditReport:
  """Assemble full audit report from scan results."""
  all_findings = []
  for result in scan_results:
    all_findings.extend(result.findings)
  # Sort by severity
  all_findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 4))

  score = compute_integrity_score(scan_results)
  summary = build_summary(score, all_findings)
  status = status_from_score(score.overall)

  return AuditReport(
    context=context,
    score=score,
    scan_results=scan_results,
    all_findings=all_findings,
    summary=summary,
    status=status,
  )


# --- Serialization ---

def report_to_json(report: AuditReport) -> dict:
  """Convert AuditReport to JSON-serializable dict."""
  return {
    "context": report.context,
    "timestamp": report.timestamp,
    "status": report.status,
    "summary": report.summary,
    "score": asdict(report.score),
    "findings_count": len(report.all_findings),
    "findings": [asdict(f) for f in report.all_findings],
    "scanner_results": [
      {
        "scanner": r.scanner_name,
        "score": r.score,
        "duration_ms": round(r.duration_ms, 1),
        "findings_count": len(r.findings),
        "worst_severity": r.worst_severity,
      }
      for r in report.scan_results
    ],
  }


def report_to_markdown(report: AuditReport) -> str:
  """Render AuditReport as Markdown."""
  lines = [
    "# OOS Integrity Audit Report",
    "",
    f"**Status:** {report.status} | **Score:** {report.score.overall}/100",
    f"**Timestamp:** {report.timestamp}",
    "",
    "## Summary",
    "",
    report.summary,
    "",
    "## Score Breakdown",
    "",
    "| Category | Score |",
    "|----------|-------|",
  ]
  score = report.score
  for label, attr in [
    ("Temporal", "temporal"), ("Identity", "identity"),
    ("Feature", "feature"), ("Preprocessing", "preprocessing"),
    ("Distribution", "distribution"), ("Statistical", "statistical"),
    ("Pipeline", "pipeline"), ("Group", "group"),
    ("WFO Fold", "wfo_fold"), ("Quant Finance", "quant_finance"),
    ("Truthfulness", "truthfulness"), ("Timestamp", "timestamp_val"),
    ("Schema", "schema_val"),
  ]:
    val = getattr(score, attr)
    icon = "+" if val >= 80 else "!" if val >= 60 else "X"
    lines.append(f"| {label} | {val} [{icon}] |")

  lines.extend(["", f"## Findings ({len(report.all_findings)})", ""])
  if not report.all_findings:
    lines.append("No issues detected.")
  for f in report.all_findings:
    lines.append(f"### [{f.severity}] {f.id}: {f.title}")
    lines.append(f"**Scanner:** {f.scanner} | **Category:** {f.category} | **Confidence:** {f.confidence}")
    if f.description:
      lines.append(f"\n{f.description}")
    if f.remediation:
      lines.append(f"\n**Remediation:** {f.remediation}")
    lines.append("")
  return "\n".join(lines)
