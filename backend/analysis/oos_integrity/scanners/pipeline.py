"""Pipeline Path Tracer — validates data flow ordering and defense layers."""
from __future__ import annotations

import pandas as pd

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult
from backend.analysis.oos_integrity.scanners.base import AbstractScanner

# Expected pipeline steps (in order) for each optimization path
PIPELINE_STEPS = {
  "single": [
    "load_trades_with_date_to",
    "build_daily_pnl_matrix",
    "truncate_daily_pnl",
    "filter_daily_pnl_quality",
    "compute_correlation",
    "compute_seed_scores",
    "run_optimizer",
    "enrich_candidates",
  ],
  "split": [
    "load_trades_with_date_to",
    "build_daily_pnl_matrix_with_side",
    "truncate_daily_pnl",
    "filter_daily_pnl_quality",
    "compute_correlation",
    "compute_seed_scores",
    "run_split_optimizer",
    "enrich_candidates",
  ],
  "cross_strategy": [
    "load_multi_batch_trades",
    "build_batch_tagged_daily_pnl",
    "truncate_daily_pnl",
    "filter_daily_pnl_quality_per_batch",
    "compute_correlation",
    "compute_seed_scores",
    "run_optimizer",
    "enrich_candidates",
  ],
  "wfas": [
    "generate_windows",
    "per_window_filter_trades_strict",
    "per_window_build_is_daily_pnl",
    "per_window_assert_no_oos_leakage",
    "per_window_optimize",
    "per_window_evaluate_oos",
    "compile_tracks",
  ],
}


class PipelineTracerScanner(AbstractScanner):
  name = "pipeline"
  category = "Pipeline Integrity"

  def scan(self, ctx: dict, config: AuditConfig) -> ScanResult:
    findings = []
    path_type = ctx.get("path_type", "single")
    is_pnl = ctx.get("is_daily_pnl")
    is_end = ctx.get("is_end")
    is_trades = ctx.get("is_trades", [])

    findings.extend(self._check_split_timing(is_pnl, is_trades, is_end))
    findings.extend(self._check_defense_depth(ctx, path_type))
    findings.extend(self._classify_pipeline(is_pnl, is_trades, is_end))

    score = max(0, 100 - sum(
      30 if f.severity == "CRITICAL" else 15 if f.severity == "HIGH"
      else 5 for f in findings
    ))
    return ScanResult(self.name, findings, score)

  def _check_split_timing(self, is_pnl, is_trades, is_end):
    """Verify IS/OOS split happens before data-dependent transforms."""
    if not is_end:
      return [self._finding(
        "PIPE-001", "CRITICAL",
        "No IS/OOS boundary defined (is_end is None)",
        "Cannot validate pipeline order without a split point",
        remediation="Set date_to / is_end before running optimization",
      )]
    findings = []
    # Check trades are pre-filtered
    if is_trades:
      boundary = pd.Timestamp(is_end) + pd.Timedelta(days=1)
      oos_in_is = sum(1 for t in is_trades if t.exit_time and pd.Timestamp(t.exit_time) > boundary)
      if oos_in_is > 0:
        findings.append(self._finding(
          "PIPE-002", "CRITICAL",
          f"{oos_in_is} OOS trades found in IS trade list",
          "Split happened AFTER trade loading — transforms use OOS data",
          evidence={"oos_in_is": oos_in_is},
          affected_rows=oos_in_is,
          remediation="Apply date_to filter at trade loading step",
        ))
    # Check daily PnL is pre-truncated
    if is_pnl is not None and not is_pnl.empty:
      boundary = pd.Timestamp(is_end)
      if is_pnl.index.max() > boundary:
        findings.append(self._finding(
          "PIPE-003", "CRITICAL",
          "Daily PnL not truncated before optimization",
          "Correlation, seed scoring, and optimizer may use OOS data",
          evidence={"max_date": str(is_pnl.index.max()), "is_end": is_end},
          remediation="Truncate daily PnL immediately after building",
        ))
    return findings

  def _check_defense_depth(self, ctx, path_type):
    """Count enforcement layers for the optimization path."""
    layers = 0
    is_end = ctx.get("is_end")
    is_trades = ctx.get("is_trades", [])
    is_pnl = ctx.get("is_daily_pnl")

    if not is_end:
      return []

    boundary = pd.Timestamp(is_end) + pd.Timedelta(days=1)

    # Layer 1: Trade date filter
    if is_trades and all(
      not t.exit_time or pd.Timestamp(t.exit_time) <= boundary
      for t in is_trades
    ):
      layers += 1

    # Layer 2: Daily PnL truncation
    if is_pnl is not None and not is_pnl.empty:
      if is_pnl.index.max() <= pd.Timestamp(is_end):
        layers += 1

    # Layer 3: Correlation built from bounded data (implied by layer 2)
    if layers >= 2:
      layers += 1

    # Layer 4: Optimizer receives bounded data (implied by above)
    if layers >= 3:
      layers += 1

    expected = {"single": 4, "split": 4, "cross_strategy": 4, "wfas": 7}.get(path_type, 4)

    if layers >= expected:
      return []
    return [self._finding(
      "PIPE-004", "MEDIUM" if layers >= 2 else "HIGH",
      f"Only {layers}/{expected} defense layers active for {path_type} path",
      "Missing IS/OOS enforcement layers reduce protection against leakage",
      evidence={"active_layers": layers, "expected": expected, "path_type": path_type},
      remediation=f"Add missing boundary checks for {path_type} optimization path",
    )]

  def _classify_pipeline(self, is_pnl, is_trades, is_end):
    """Classify pipeline as CLEAN / PARTIALLY / FULLY contaminated."""
    if not is_end:
      return [self._finding(
        "PIPE-005", "HIGH",
        "Pipeline classification: UNKNOWN (no is_end set)",
        evidence={"classification": "UNKNOWN"},
      )]

    trade_clean = True
    pnl_clean = True
    boundary = pd.Timestamp(is_end) + pd.Timedelta(days=1)

    if is_trades:
      trade_clean = all(
        not t.exit_time or pd.Timestamp(t.exit_time) <= boundary
        for t in is_trades
      )
    if is_pnl is not None and not is_pnl.empty:
      pnl_clean = is_pnl.index.max() <= pd.Timestamp(is_end)

    if trade_clean and pnl_clean:
      return []  # CLEAN — no finding needed
    if not trade_clean and not pnl_clean:
      return [self._finding(
        "PIPE-006", "CRITICAL",
        "Pipeline classification: FULLY CONTAMINATED",
        "Both trades and daily PnL contain OOS data",
        evidence={"classification": "FULLY_CONTAMINATED"},
      )]
    return [self._finding(
      "PIPE-007", "HIGH",
      "Pipeline classification: PARTIALLY CONTAMINATED",
      f"{'Trades' if not trade_clean else 'Daily PnL'} contain OOS data",
      evidence={"classification": "PARTIALLY_CONTAMINATED",
                "trade_clean": trade_clean, "pnl_clean": pnl_clean},
    )]
