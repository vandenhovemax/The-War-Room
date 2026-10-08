"""Audit prompt generator — produces a filled-in contamination audit prompt.

Generates a structured prompt for manual or AI-assisted code-level IS/OOS
contamination tracing, pre-filled with War Room file paths and pathways.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# War Room data pathways (all known IS/OOS data flows)
# ---------------------------------------------------------------------------
WAR_ROOM_PATHWAYS = [
  {
    "name": "Single Optimizer",
    "route": "backend/api/routes/analysis.py → POST /api/analysis/optimize",
    "data_files": [
      "backend/analysis/optimizer_split.py",
      "backend/analysis/wfo_boundary.py",
      "backend/analysis/full_metrics.py",
    ],
    "boundary": "date_to parameter → filter_trades_strict() → build_is_daily_pnl()",
    "concerns": "Trade exit filtering, daily PnL truncation, seed scoring inputs",
  },
  {
    "name": "L/S Split Optimizer",
    "route": "backend/api/routes/analysis.py → POST /api/analysis/optimize-split",
    "data_files": [
      "backend/analysis/optimizer_split.py",
      "backend/analysis/wfo_boundary.py",
    ],
    "boundary": "date_to → separate Long/Short trade pools → each filtered independently",
    "concerns": "Both sides must respect same IS boundary, combined PnL matrix",
  },
  {
    "name": "Cross-Strategy Builder",
    "route": "backend/api/routes/analysis.py → POST /api/analysis/optimize-cross",
    "data_files": [
      "backend/analysis/optimizer_split.py",
      "backend/analysis/wfo_boundary.py",
      "backend/analysis/beam_optimizer.py",
    ],
    "boundary": "date_to → per-batch trade filtering → batch-tagged PnL columns",
    "concerns": "Each batch must be independently filtered, correlation matrix uses IS-only data",
  },
  {
    "name": "WFO / WFAS",
    "route": "backend/api/routes/analysis.py → POST /api/analysis/wfas",
    "data_files": [
      "backend/analysis/wfo_aggregation.py",
      "backend/analysis/wfo_boundary.py",
      "backend/analysis/wfe.py",
      "backend/analysis/event_wfo.py",
    ],
    "boundary": "Per-window: is_end/oos_start from window gen → filter_trades_strict() per window",
    "concerns": "Window IS/OOS overlap, expanding IS monotonicity, OOS non-overlap between windows",
  },
  {
    "name": "Portfolio Save",
    "route": "backend/api/routes/analysis.py → POST /api/portfolios/save",
    "data_files": [
      "backend/api/routes/analysis.py",
      "backend/analysis/full_metrics.py",
    ],
    "boundary": "optimization_end_date stored → must be used when recalculating metrics",
    "concerns": "Metrics recalculated with ALL trades if end_date missing (known gap)",
  },
  {
    "name": "Seed Scoring",
    "route": "Called within optimizer_split.py",
    "data_files": [
      "backend/analysis/optimizer_split.py",
    ],
    "boundary": "Sortino/recency/benchmark/event scores must use IS trades only",
    "concerns": "External scores or benchmark data may include OOS period",
  },
  {
    "name": "Correlation Matrix",
    "route": "Built inside optimizer for portfolio construction",
    "data_files": [
      "backend/analysis/optimizer_split.py",
    ],
    "boundary": "Correlation computed from IS daily PnL matrix (already truncated)",
    "concerns": "Extra columns from other sources, PnL matrix not truncated",
  },
  {
    "name": "WFE Calculation",
    "route": "backend/analysis/wfe.py",
    "data_files": [
      "backend/analysis/wfe.py",
      "backend/analysis/wfo_aggregation.py",
    ],
    "boundary": "IS PnL/day vs OOS PnL/day — each must use correct date range",
    "concerns": "WFE > 1.5 or < -0.5 may indicate boundary errors",
  },
]

# ---------------------------------------------------------------------------
# Key reference files for the War Room
# ---------------------------------------------------------------------------
WAR_ROOM_FILES = {
  "boundary_enforcement": "backend/analysis/wfo_boundary.py",
  "integrity_agent": "backend/analysis/integrity_agent.py",
  "optimizer": "backend/analysis/optimizer_split.py",
  "wfo_aggregation": "backend/analysis/wfo_aggregation.py",
  "wfe": "backend/analysis/wfe.py",
  "beam_optimizer": "backend/analysis/beam_optimizer.py",
  "full_metrics": "backend/analysis/full_metrics.py",
  "event_wfo": "backend/analysis/event_wfo.py",
  "api_routes": "backend/api/routes/analysis.py",
}

# ---------------------------------------------------------------------------
# The prompt template
# ---------------------------------------------------------------------------
AUDIT_PROMPT_TEMPLATE = """\
# FULL OOS CONTAMINATION AUDIT

I need you to trace EVERY data pathway in {target} to verify that OOS data \
never leaks into IS calculations.

## Rules for this audit:

1. Do NOT use subagents or delegate. Read every file yourself.
2. For EVERY function that receives data, trace the data BACKWARDS to its \
source. Don't stop at the variable name — follow it through every assignment, \
every function call, every transformation until you reach the raw data load.
3. For EVERY filter/boundary (date clips, quality filters, IS/OOS splits), \
verify it is applied BEFORE the data reaches the function, not assumed.
4. Check EVERY pathway — not just the main optimizer call. Check seed scoring, \
recency, benchmark, WFE, correlation matrix, PnL matrix, trade counts, \
external scores. Every input.
5. For each pathway, write out the full chain:
   - Raw data loaded at [file:line]
   - Filtered at [file:line] by [condition]
   - Passed to [function] at [file:line]
   - Used for [purpose]
   - VERDICT: CLEAN / CONTAMINATED (with proof)
6. Do NOT tell me "it looks fine" or "it should be clean." Show me the exact \
line numbers that prove it. If you can't find the proof, say "UNVERIFIED" not \
"clean."
7. If you find something that LOOKS like a bug, trace it fully before reporting. \
Don't flag false positives.
8. Compare against {reference} which is known-working, line by line, for every \
shared function call.
9. Present results as a table: Pathway | Data Source | IS Boundary Applied? | \
Line Proof | Verdict

## Files to audit:
{files_list}

## Reference (known clean):
{reference}

## Focus areas:
{focus_areas}

## Known data pathways to trace:
{pathways_section}

## IS/OOS Boundary Enforcement Layers (must be present for each pathway):
1. **DB Query**: date_to parameter filters trades at query time
2. **Daily PnL Truncation**: build_is_daily_pnl() clips rows to <= is_end
3. **Assert Guard**: assert_no_oos_leakage() raises OOSContaminationError
4. **Filter Safety Net**: filter_trades_strict() removes straddling trades

## Output format:
For each pathway, produce a row in this table:

| Pathway | Data Source | IS Boundary Applied? | Line Proof | Verdict |
|---------|-------------|---------------------|------------|---------|
| [name]  | [file:line] | Yes/No at [file:line] | [evidence] | CLEAN/CONTAMINATED/UNVERIFIED |

Then summarize:
- Total pathways audited
- CLEAN count
- CONTAMINATED count (with details)
- UNVERIFIED count (with what's missing)
- Overall verdict: PASS / FAIL / NEEDS REVIEW
"""


def generate_audit_prompt(
  target: str | None = None,
  files: list[str] | None = None,
  reference: str | None = None,
  focus_areas: list[str] | None = None,
  path_type: str | None = None,
) -> str:
  """Generate a filled-in OOS contamination audit prompt.

  Args:
    target: What to audit (e.g., "Single Optimizer" or a specific file).
            Defaults to "all optimization pathways".
    files: List of files to audit. Defaults to all War Room data flow files.
    reference: Known-clean reference file. Defaults to wfo_boundary.py.
    focus_areas: Specific concerns to highlight.
    path_type: If set, filter pathways to just this type
               (single/split/cross/wfas/portfolio/all).
  """
  # Target
  if not target:
    target = "all optimization pathways in The War Room"

  # Reference
  if not reference:
    reference = WAR_ROOM_FILES["boundary_enforcement"]

  # Files to audit
  if not files:
    seen = set()
    files = []
    for pw in WAR_ROOM_PATHWAYS:
      for f in pw["data_files"]:
        if f not in seen:
          seen.add(f)
          files.append(f)
  files_list = "\n".join(f"- `{f}`" for f in files)

  # Focus areas
  if not focus_areas:
    focus_areas = [
      "Trade exit_time filtering (straddling trades)",
      "Daily PnL matrix truncation at IS boundary",
      "Seed scoring inputs (sortino, recency, benchmark, event)",
      "Correlation matrix source data",
      "WFE calculation IS/OOS split",
      "Cross-batch isolation in cross-strategy builder",
      "Portfolio save metrics recalculation",
    ]
  focus_section = "\n".join(f"- {fa}" for fa in focus_areas)

  # Pathways
  pathways = WAR_ROOM_PATHWAYS
  if path_type and path_type != "all":
    type_map = {
      "single": "Single Optimizer",
      "split": "L/S Split Optimizer",
      "cross": "Cross-Strategy Builder",
      "wfas": "WFO / WFAS",
      "portfolio": "Portfolio Save",
    }
    target_name = type_map.get(path_type)
    if target_name:
      pathways = [pw for pw in pathways if pw["name"] == target_name]

  pathway_lines = []
  for pw in pathways:
    pathway_lines.append(f"### {pw['name']}")
    pathway_lines.append(f"- **Route**: `{pw['route']}`")
    pathway_lines.append(f"- **Boundary**: {pw['boundary']}")
    pathway_lines.append(f"- **Concerns**: {pw['concerns']}")
    pathway_lines.append(f"- **Files**: {', '.join(f'`{f}`' for f in pw['data_files'])}")
    pathway_lines.append("")
  pathways_section = "\n".join(pathway_lines)

  return AUDIT_PROMPT_TEMPLATE.format(
    target=target,
    files_list=files_list,
    reference=reference,
    focus_areas=focus_section,
    pathways_section=pathways_section,
  )
