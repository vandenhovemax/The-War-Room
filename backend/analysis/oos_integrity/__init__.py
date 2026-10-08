"""OOS Integrity Agent — comprehensive data contamination detection.

Usage:
  from backend.analysis.oos_integrity import OOSIntegrityAgent, AuditConfig
  agent = OOSIntegrityAgent()
  report = agent.audit(is_trades=..., oos_trades=..., is_end="2025-05-31")
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

from backend.analysis.oos_integrity.audit_prompt import generate_audit_prompt
from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import AuditReport, ScanResult
from backend.analysis.oos_integrity.report import build_report, report_to_json, report_to_markdown
from backend.analysis.oos_integrity.scanners import ALL_SCANNERS
from backend.analysis.oos_integrity.validators import ALL_VALIDATORS

if TYPE_CHECKING:
  from backend.models.backtest import BacktestTrade

log = logging.getLogger(__name__)

# Scanner name → config toggle attribute
_TOGGLE_MAP = {
  "temporal": "enable_temporal",
  "identity": "enable_identity",
  "feature": "enable_feature",
  "preprocessing": "enable_preprocessing",
  "distribution": "enable_distribution",
  "statistical": "enable_statistical",
  "pipeline": "enable_pipeline",
  "group": "enable_group",
  "wfo_fold": "enable_wfo_fold",
  "quant_finance": "enable_quant_finance",
  "truthfulness": "enable_truthfulness",
  "timestamp_validator": "enable_timestamp",
  "schema_validator": "enable_schema",
}


class OOSIntegrityAgent:
  """Orchestrates all scanners + validators for a full OOS integrity audit."""

  def __init__(self, config: AuditConfig | None = None):
    self.config = config or AuditConfig()
    self._scanners = [cls() for cls in ALL_SCANNERS]
    self._validators = [cls() for cls in ALL_VALIDATORS]

  def audit(
    self,
    is_trades: list[BacktestTrade] | None = None,
    oos_trades: list[BacktestTrade] | None = None,
    is_daily_pnl: pd.DataFrame | None = None,
    oos_daily_pnl: pd.DataFrame | None = None,
    is_end: str | None = None,
    corr_matrix: pd.DataFrame | None = None,
    windows: list[dict] | None = None,
    batch_trades: dict | None = None,
    optimizer_results: list[dict] | None = None,
    portfolio_id: int | None = None,
    path_type: str = "single",
  ) -> AuditReport:
    """Run all enabled scanners and validators, return full report.

    Args:
      is_trades: In-sample trades
      oos_trades: Out-of-sample trades
      is_daily_pnl: IS daily PnL DataFrame (rows=dates, cols=assets)
      oos_daily_pnl: OOS daily PnL DataFrame
      is_end: IS boundary date string (YYYY-MM-DD)
      corr_matrix: Pre-computed correlation matrix (optional)
      windows: WFO window definitions (optional)
      batch_trades: Dict of batch_id → trade list (cross-strategy)
      optimizer_results: Optimizer seed results (optional)
      portfolio_id: Portfolio ID being validated
      path_type: Optimization path type (single/split/cross/wfas)
    """
    # Build shared context dict for all scanners
    ctx = {
      "is_trades": is_trades or [],
      "oos_trades": oos_trades or [],
      "is_daily_pnl": is_daily_pnl if is_daily_pnl is not None else pd.DataFrame(),
      "oos_daily_pnl": oos_daily_pnl if oos_daily_pnl is not None else pd.DataFrame(),
      "is_end": is_end,
      "corr_matrix": corr_matrix,
      "windows": windows or [],
      "batch_trades": batch_trades,
      "optimizer_results": optimizer_results or [],
      "portfolio_id": portfolio_id,
      "path_type": path_type,
    }

    scan_results: list[ScanResult] = []

    # Run scanners
    for scanner in self._scanners:
      toggle = _TOGGLE_MAP.get(scanner.name)
      if toggle and not getattr(self.config, toggle, True):
        continue
      try:
        result = scanner._run(ctx, self.config)
        scan_results.append(result)
        if self.config.verbose and result.findings:
          log.info(
            "%s: %d finding(s), score=%.0f (%.1fms)",
            scanner.name, len(result.findings), result.score, result.duration_ms,
          )
      except Exception as exc:
        log.warning("Scanner %s failed: %s", scanner.name, exc)
        scan_results.append(ScanResult(scanner.name, [], 100.0))

    # Run validators
    for validator in self._validators:
      toggle = _TOGGLE_MAP.get(validator.name)
      if toggle and not getattr(self.config, toggle, True):
        continue
      try:
        result = validator._run(ctx, self.config)
        scan_results.append(result)
        if self.config.verbose and result.findings:
          log.info(
            "%s: %d finding(s), score=%.0f (%.1fms)",
            validator.name, len(result.findings), result.score, result.duration_ms,
          )
      except Exception as exc:
        log.warning("Validator %s failed: %s", validator.name, exc)
        scan_results.append(ScanResult(validator.name, [], 100.0))

    context_str = f"portfolio={portfolio_id}" if portfolio_id else path_type
    return build_report(scan_results, context=context_str)

  def quick_audit(
    self,
    is_trades: list[BacktestTrade] | None = None,
    oos_trades: list[BacktestTrade] | None = None,
    is_end: str | None = None,
  ) -> AuditReport:
    """Minimal audit — temporal + identity + schema only."""
    quick_config = AuditConfig(
      enable_temporal=True,
      enable_identity=True,
      enable_schema=True,
      enable_feature=False,
      enable_preprocessing=False,
      enable_distribution=False,
      enable_statistical=False,
      enable_pipeline=False,
      enable_group=False,
      enable_wfo_fold=False,
      enable_quant_finance=False,
      enable_truthfulness=False,
      enable_timestamp=True,
      verbose=False,
    )
    saved = self.config
    self.config = quick_config
    try:
      return self.audit(is_trades=is_trades, oos_trades=oos_trades, is_end=is_end)
    finally:
      self.config = saved

  @staticmethod
  def generate_prompt(
    target: str | None = None,
    files: list[str] | None = None,
    reference: str | None = None,
    focus_areas: list[str] | None = None,
    path_type: str | None = None,
  ) -> str:
    """Generate a filled-in OOS contamination audit prompt.

    Returns a structured prompt for manual or AI-assisted code-level tracing
    of every data pathway to verify OOS never leaks into IS calculations.

    Args:
      target: What to audit (e.g. "Single Optimizer"). Defaults to all paths.
      files: Files to audit. Defaults to all War Room data flow files.
      reference: Known-clean reference file. Defaults to wfo_boundary.py.
      focus_areas: Specific concerns to highlight.
      path_type: Filter to one path (single/split/cross/wfas/portfolio/all).
    """
    return generate_audit_prompt(
      target=target,
      files=files,
      reference=reference,
      focus_areas=focus_areas,
      path_type=path_type,
    )
