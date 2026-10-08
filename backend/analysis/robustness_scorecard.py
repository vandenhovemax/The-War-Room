"""Robustness scorecard — letter grades from analysis modules.

Grading thresholds:
  Sequence (median DD% of capital): A < 15% | B < 25% | C < 40% | F >= 40%
  Structural (fragility ratio): A >= 0.85 | B >= 0.70 | C >= 0.50 | F < 0.50
  Edge (Z-score): A >= 3.0 | B >= 2.0 | C >= 1.0 | F < 1.0
"""
from __future__ import annotations


def _grade_sequence(dd_pct: float) -> str:
    if dd_pct < 15:
        return "A"
    if dd_pct < 25:
        return "B"
    if dd_pct < 40:
        return "C"
    return "F"


def _grade_structural(ratio: float) -> str:
    if ratio >= 0.85:
        return "A"
    if ratio >= 0.70:
        return "B"
    if ratio >= 0.50:
        return "C"
    return "F"


def _grade_edge(z_score: float) -> str:
    if z_score >= 3.0:
        return "A"
    if z_score >= 2.0:
        return "B"
    if z_score >= 1.0:
        return "C"
    return "F"


def compute_grades(
    mc_median_dd: float | None = None,
    initial_capital: float = 10_000.0,
    fragility_ratio: float | None = None,
    z_score: float | None = None,
    # Legacy alias — callers that still pass mc_worst_dd are accepted
    mc_worst_dd: float | None = None,
) -> dict:
    """Compute A/B/C/F grades + verdict from module results.

    Any parameter left as None means that module wasn't run.
    Verdict only considers modules that were actually run.
    """
    seq_grade: str | None = None
    struct_grade: str | None = None
    edge_grade: str | None = None

    # Prefer median DD; fall back to legacy worst-DD kwarg
    dd_value = mc_median_dd if mc_median_dd is not None else mc_worst_dd
    if dd_value is not None and initial_capital > 0:
        dd_pct = abs(dd_value) / initial_capital * 100
        seq_grade = _grade_sequence(dd_pct)

    if fragility_ratio is not None:
        struct_grade = _grade_structural(fragility_ratio)

    if z_score is not None:
        edge_grade = _grade_edge(z_score)

    # Verdict from whatever modules were run
    grades = [g for g in (seq_grade, struct_grade, edge_grade) if g is not None]
    if not grades:
        verdict = "Run at least one module"
        is_robust = False
    elif "F" in grades:
        verdict = "Fragile: Performance relies too heavily on trade sequence/luck."
        is_robust = False
    elif sum(1 for g in grades if g in ("A", "B")) >= 2:
        verdict = "Robust: Strategy shows structural edge and survives outlier removal."
        is_robust = True
    elif all(g in ("A", "B") for g in grades):
        verdict = "Robust: Strategy shows structural edge and survives outlier removal."
        is_robust = True
    else:
        verdict = "Marginal: Mixed signals — some resilience but not fully proven."
        is_robust = False

    return {
        "sequence_grade": seq_grade,
        "structural_grade": struct_grade,
        "edge_grade": edge_grade,
        "verdict": verdict,
        "is_robust": is_robust,
    }
