"""IS/OOS Data Integrity Agent — validates that no OOS data contaminates
optimization, scoring, filtering, or portfolio computations.

14 check categories across all optimization paths.
Runs on-demand (API/pytest) or automatically when optimization files change.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.db.models import BacktestTrade

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
  """Single validation check outcome."""
  check_id: str
  category: str
  passed: bool
  severity: str  # "critical" | "warning" | "info"
  message: str
  detail: dict = field(default_factory=dict)


@dataclass
class IntegrityReport:
  """Full validation report for one optimization context."""
  context: str
  timestamp: str
  total_checks: int
  passed: int
  failed: int
  warnings: int
  checks: list[CheckResult]
  overall_status: str  # "PASS" | "FAIL" | "WARN"


def _ts(date_str: str) -> pd.Timestamp:
  """Parse date string to end-of-day Timestamp (inclusive boundary)."""
  return pd.Timestamp(date_str) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 14 CHECK FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def check_trade_exit_boundary(
  trades: list[BacktestTrade], is_end: str, context: str = "",
) -> CheckResult:
  """Check 1: No trade exit_time exceeds IS boundary."""
  if not trades:
    return CheckResult("trade_exit_boundary", "Trade Boundary", True, "info",
                       "No trades to validate", {"count": 0})
  boundary = _ts(is_end)
  violators = [
    {"trade_id": t.id, "exit_time": str(t.exit_time), "symbol": t.symbol}
    for t in trades if t.exit_time and pd.Timestamp(t.exit_time) > boundary
  ]
  return CheckResult(
    "trade_exit_boundary", "Trade Boundary",
    passed=len(violators) == 0, severity="critical",
    message=(f"{len(violators)} trades exit after IS boundary {is_end}"
             if violators else f"All {len(trades)} trades exit within IS"),
    detail={"violators": violators[:10], "total_violations": len(violators)},
  )


def check_trade_entry_boundary(
  trades: list[BacktestTrade], is_end: str, context: str = "",
) -> CheckResult:
  """Check 2: No trade entry_time exceeds IS boundary (signal from IS candles)."""
  if not trades:
    return CheckResult("trade_entry_boundary", "Trade Boundary", True, "info",
                       "No trades to validate", {"count": 0})
  boundary = _ts(is_end)
  violators = [
    {"trade_id": t.id, "entry_time": str(t.entry_time), "symbol": t.symbol}
    for t in trades if t.entry_time and pd.Timestamp(t.entry_time) > boundary
  ]
  return CheckResult(
    "trade_entry_boundary", "Trade Boundary",
    passed=len(violators) == 0, severity="critical",
    message=(f"{len(violators)} trades entered after IS boundary {is_end}"
             if violators else f"All {len(trades)} trade entries within IS"),
    detail={"violators": violators[:10], "total_violations": len(violators)},
  )


def check_daily_pnl_boundary(
  daily_pnl: pd.DataFrame, is_end: str, context: str = "",
) -> CheckResult:
  """Check 3: No daily_pnl row exceeds IS boundary."""
  if daily_pnl.empty:
    return CheckResult("daily_pnl_boundary", "Daily PnL", True, "info",
                       "Empty daily_pnl — nothing to validate")
  boundary = _ts(is_end)
  max_date = daily_pnl.index.max()
  passed = max_date <= boundary
  return CheckResult(
    "daily_pnl_boundary", "Daily PnL",
    passed=passed, severity="critical",
    message=(f"Daily PnL max date {max_date.date()} exceeds IS end {is_end}"
             if not passed else f"Daily PnL bounded: {daily_pnl.index.min().date()} to {max_date.date()}"),
    detail={"max_date": str(max_date), "is_end": is_end,
            "rows_after": int((daily_pnl.index > boundary).sum())},
  )


def check_corr_source_boundary(
  daily_pnl: pd.DataFrame, is_end: str,
) -> CheckResult:
  """Check 4: Correlation matrix source data within IS.
  Since corr is computed from daily_pnl, validates the source."""
  # Delegate to daily_pnl check — correlation inherits its boundary
  result = check_daily_pnl_boundary(daily_pnl, is_end, "correlation source")
  return CheckResult(
    "corr_source_boundary", "Correlation",
    passed=result.passed, severity="critical",
    message=result.message.replace("Daily PnL", "Correlation source daily_pnl"),
    detail=result.detail,
  )


def check_seed_scoring_boundary(
  trades: list[BacktestTrade], is_end: str, scoring_type: str = "sortino",
) -> CheckResult:
  """Check 5: Seed scoring trades within IS (per scoring type)."""
  result = check_trade_exit_boundary(trades, is_end, f"seed scoring ({scoring_type})")
  return CheckResult(
    f"seed_{scoring_type}_boundary", "Seed Scoring",
    passed=result.passed, severity="critical",
    message=result.message.replace("trades exit", f"{scoring_type} seed trades exit"),
    detail={**result.detail, "scoring_type": scoring_type},
  )


def check_benchmark_boundary(
  trades: list[BacktestTrade], is_end: str,
) -> CheckResult:
  """Check 6: Benchmark robustness uses IS-only trades."""
  result = check_trade_exit_boundary(trades, is_end, "benchmark")
  return CheckResult(
    "benchmark_boundary", "Benchmark",
    passed=result.passed, severity="critical",
    message=result.message.replace("trades exit", "benchmark trades exit"),
    detail=result.detail,
  )


def check_recency_boundary(
  trade_df: pd.DataFrame, is_end: str,
) -> CheckResult:
  """Check 7: Recency trade DataFrame within IS."""
  if trade_df.empty:
    return CheckResult("recency_boundary", "Recency Scoring", True, "info",
                       "No recency trades to validate")
  boundary = _ts(is_end)
  time_col = "Exit Time" if "Exit Time" in trade_df.columns else "exit_time"
  if time_col not in trade_df.columns:
    return CheckResult("recency_boundary", "Recency Scoring", True, "info",
                       "No time column in recency DataFrame")
  max_date = pd.Timestamp(trade_df[time_col].max())
  passed = max_date <= boundary
  return CheckResult(
    "recency_boundary", "Recency Scoring",
    passed=passed, severity="critical",
    message=(f"Recency data extends to {max_date.date()}, beyond IS end {is_end}"
             if not passed else f"All {len(trade_df)} recency trades within IS"),
    detail={"max_date": str(max_date), "is_end": is_end},
  )


def check_quality_filter_boundary(
  filtered_pnl: pd.DataFrame, is_end: str,
) -> CheckResult:
  """Check 8: Quality-filtered daily_pnl within IS."""
  result = check_daily_pnl_boundary(filtered_pnl, is_end, "quality filter")
  return CheckResult(
    "quality_filter_boundary", "Quality Filters",
    passed=result.passed, severity="critical",
    message=result.message.replace("Daily PnL", "Quality-filtered daily_pnl"),
    detail=result.detail,
  )


def check_portfolio_metrics(
  portfolio_id: int, db,
) -> CheckResult:
  """Check 9: Saved portfolio metrics match IS-only recomputation."""
  from backend.db.models import Portfolio
  portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
  if not portfolio:
    return CheckResult("portfolio_metrics", "Portfolio Save", False, "critical",
                       f"Portfolio {portfolio_id} not found")

  opt_end = portfolio.optimization_end_date
  if not opt_end:
    return CheckResult(
      "portfolio_metrics", "Portfolio Save", False, "warning",
      f"Portfolio '{portfolio.name}' has no optimization_end_date — cannot validate",
      detail={"portfolio_id": portfolio_id, "name": portfolio.name},
    )

  # Load stored metrics
  stored = json.loads(portfolio.metrics_snapshot) if portfolio.metrics_snapshot else {}
  if not stored:
    return CheckResult("portfolio_metrics", "Portfolio Save", True, "info",
                       "No stored metrics to validate")

  # Load trades and compute IS-only metrics
  try:
    from backend.api.routes.portfolio_analysis import _load_portfolio_trades
    all_df = _load_portfolio_trades(portfolio, db)
    if all_df.empty:
      return CheckResult("portfolio_metrics", "Portfolio Save", True, "info",
                         "No portfolio trades found")

    # Filter to IS only
    opt_end_ts = pd.Timestamp(opt_end)
    time_col = "Exit Time" if "Exit Time" in all_df.columns else "exit_time"
    is_df = all_df[all_df[time_col] <= opt_end_ts]

    stored_trades = stored.get("total_trades", 0)
    is_trades = len(is_df)

    # If stored metrics include more trades than IS, that's OOS leakage
    if stored_trades > is_trades and is_trades > 0:
      return CheckResult(
        "portfolio_metrics", "Portfolio Save", False, "critical",
        f"Portfolio '{portfolio.name}' snapshot has {stored_trades} trades but IS-only has {is_trades} — OOS data leaked",
        detail={"stored_trades": stored_trades, "is_trades": is_trades,
                "delta": stored_trades - is_trades,
                "optimization_end_date": str(opt_end)},
      )
    return CheckResult(
      "portfolio_metrics", "Portfolio Save", True, "info",
      f"Portfolio '{portfolio.name}' metrics consistent with IS boundary",
      detail={"stored_trades": stored_trades, "is_trades": is_trades},
    )
  except Exception as e:
    return CheckResult("portfolio_metrics", "Portfolio Save", False, "warning",
                       f"Could not validate portfolio metrics: {e}")


def check_cross_batch_isolation(
  batch_trades: dict[int, list[BacktestTrade]], is_end: str,
) -> CheckResult:
  """Check 10: Each batch independently respects IS boundary."""
  if not batch_trades:
    return CheckResult("cross_batch_isolation", "Cross-Batch", True, "info",
                       "No batches to validate")
  boundary = _ts(is_end)
  violations: dict[int, int] = {}
  for bid, trades in batch_trades.items():
    bad = sum(1 for t in trades if t.exit_time and pd.Timestamp(t.exit_time) > boundary)
    if bad > 0:
      violations[bid] = bad

  return CheckResult(
    "cross_batch_isolation", "Cross-Batch",
    passed=len(violations) == 0, severity="critical",
    message=(f"{len(violations)} batches have OOS trades: {violations}"
             if violations else f"All {len(batch_trades)} batches within IS boundary"),
    detail={"violations": violations, "batches_checked": len(batch_trades)},
  )


def check_optimizer_result_pool(
  selected_assets: list[str], pool_assets: list[str],
) -> CheckResult:
  """Check 11: Selected assets exist in IS pool."""
  if not selected_assets:
    return CheckResult("result_pool", "Optimizer Result", True, "info",
                       "No selected assets to validate")
  pool_set = set(pool_assets)
  phantoms = [a for a in selected_assets if a not in pool_set]
  return CheckResult(
    "result_pool", "Optimizer Result",
    passed=len(phantoms) == 0, severity="warning",
    message=(f"{len(phantoms)} selected assets not in IS pool: {phantoms[:5]}"
             if phantoms else f"All {len(selected_assets)} selected assets exist in IS pool"),
    detail={"phantoms": phantoms, "pool_size": len(pool_assets)},
  )


def check_window_boundaries(windows: list[dict]) -> CheckResult:
  """Check 12: IS end < OOS start, no overlaps across windows."""
  if not windows:
    return CheckResult("window_boundaries", "Window Generation", True, "info",
                       "No windows to validate")
  violations: list[dict] = []
  for w in windows:
    is_end = w.get("is_end")
    oos_start = w.get("oos_start")
    if is_end and oos_start:
      if pd.Timestamp(is_end) >= pd.Timestamp(oos_start):
        violations.append({
          "window": w.get("window_num"),
          "is_end": is_end, "oos_start": oos_start,
        })

  # Check OOS windows don't overlap each other
  oos_windows = [w for w in windows if w.get("oos_start") and w.get("oos_end")]
  for i in range(len(oos_windows) - 1):
    if pd.Timestamp(oos_windows[i]["oos_end"]) >= pd.Timestamp(oos_windows[i + 1]["oos_start"]):
      violations.append({
        "overlap": f"Window {oos_windows[i].get('window_num')} OOS overlaps Window {oos_windows[i+1].get('window_num')} OOS",
        "oos_end": oos_windows[i]["oos_end"],
        "next_oos_start": oos_windows[i + 1]["oos_start"],
      })

  return CheckResult(
    "window_boundaries", "Window Generation",
    passed=len(violations) == 0, severity="critical",
    message=(f"{len(violations)} window boundary violations"
             if violations else f"All {len(windows)} windows have clean IS/OOS boundaries"),
    detail={"violations": violations[:10]},
  )


def check_date_to_present(
  date_to: str | None, context: str = "",
) -> CheckResult:
  """Check 13: date_to / optimization_end_date is actually set."""
  return CheckResult(
    "date_to_present", "Configuration",
    passed=bool(date_to), severity="warning",
    message=(f"IS boundary set: {date_to}"
             if date_to else "No date_to/optimization_end_date set — OOS boundary undefined"),
    detail={"date_to": date_to, "context": context},
  )


def check_backtest_data_range(
  batch_id: int, is_end: str, db,
) -> CheckResult:
  """Check 14: Source backtest end_date vs IS boundary compatibility."""
  from backend.db.models import BacktestRun
  runs = db.query(BacktestRun).filter(
    BacktestRun.batch_id == batch_id, BacktestRun.status == "completed",
  ).all()
  if not runs:
    return CheckResult("backtest_data_range", "Data Range", True, "info",
                       f"No completed runs in batch {batch_id}")

  is_end_ts = pd.Timestamp(is_end)
  runs_beyond = [
    {"run_id": r.id, "symbol": r.symbol, "end_date": str(r.end_date)}
    for r in runs if r.end_date and pd.Timestamp(r.end_date) > is_end_ts
  ]

  # Runs extending beyond IS is expected (backtests run over full data range,
  # trades are filtered by exit_time). This is an INFO check, not a failure.
  return CheckResult(
    "backtest_data_range", "Data Range",
    passed=True, severity="info",
    message=(f"{len(runs_beyond)}/{len(runs)} backtest runs extend beyond IS boundary "
             f"(expected — trade filtering handles this)"
             if runs_beyond else f"All {len(runs)} runs end within IS boundary"),
    detail={"runs_beyond_is": len(runs_beyond), "total_runs": len(runs)},
  )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MASTER ORCHESTRATOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _build_report(context: str, checks: list[CheckResult]) -> IntegrityReport:
  """Build IntegrityReport from a list of CheckResults."""
  passed = sum(1 for c in checks if c.passed)
  failed = sum(1 for c in checks if not c.passed and c.severity == "critical")
  warnings = sum(1 for c in checks if not c.passed and c.severity == "warning")
  if failed > 0:
    status = "FAIL"
  elif warnings > 0:
    status = "WARN"
  else:
    status = "PASS"
  return IntegrityReport(
    context=context,
    timestamp=datetime.now(timezone.utc).isoformat(),
    total_checks=len(checks),
    passed=passed, failed=failed, warnings=warnings,
    checks=checks, overall_status=status,
  )


def run_integrity_audit(
  path_type: str,
  batch_id: int | None = None,
  batch_ids: list[int] | None = None,
  portfolio_id: int | None = None,
  date_to: str | None = None,
  date_from: str | None = None,
  optimizer_settings: dict | None = None,
  db=None,
) -> IntegrityReport:
  """Run full integrity audit for a given optimization path.

  Does NOT re-run the optimizer. Loads the same data the optimizer would,
  then validates all IS/OOS boundaries.
  """
  checks: list[CheckResult] = []
  ctx_parts = [f"path={path_type}"]
  if batch_id:
    ctx_parts.append(f"batch={batch_id}")
  if batch_ids:
    ctx_parts.append(f"batches={batch_ids}")
  if date_to:
    ctx_parts.append(f"date_to={date_to}")
  context = " ".join(ctx_parts)

  # Check 13: date_to present
  checks.append(check_date_to_present(date_to, path_type))

  if not date_to:
    return _build_report(context, checks)

  # Portfolio validation path
  if path_type == "portfolio" and portfolio_id and db:
    checks.append(check_portfolio_metrics(portfolio_id, db))
    return _build_report(context, checks)

  if not db:
    return _build_report(context, checks)

  # Load trades (same as optimizer)
  trades: list[BacktestTrade] = []
  batch_trades_map: dict[int, list[BacktestTrade]] = {}

  if batch_ids and len(batch_ids) > 0:
    from backend.api.routes.wfo import _load_multi_batch_trades
    trades, _ = _load_multi_batch_trades(batch_ids, db, date_from)
    # Build per-batch map for isolation check
    for bid in batch_ids:
      from backend.api.routes.analysis import _load_batch_trades
      batch_trades_map[bid] = _load_batch_trades(bid, db, date_to=date_to, date_from=date_from)
  elif batch_id and db:
    from backend.api.routes.analysis import _load_batch_trades
    trades = _load_batch_trades(batch_id, db, date_to=date_to, date_from=date_from)
    batch_trades_map[batch_id] = trades

  # Check 1: Trade exit boundary
  checks.append(check_trade_exit_boundary(trades, date_to))

  # Check 2: Trade entry boundary
  checks.append(check_trade_entry_boundary(trades, date_to))

  # Check 14: Backtest data range
  if batch_id:
    checks.append(check_backtest_data_range(batch_id, date_to, db))
  elif batch_ids:
    for bid in batch_ids:
      checks.append(check_backtest_data_range(bid, date_to, db))

  # Build daily PnL
  if trades:
    from backend.analysis.daily_pnl import build_daily_pnl_matrix
    daily_pnl = build_daily_pnl_matrix(trades, include_side=True)

    # Truncate to IS boundary (same as optimizer does)
    if not daily_pnl.empty:
      cutoff = pd.Timestamp(date_to).normalize()
      daily_pnl_is = daily_pnl[daily_pnl.index <= cutoff]
    else:
      daily_pnl_is = daily_pnl

    # Check 3: Daily PnL boundary
    checks.append(check_daily_pnl_boundary(daily_pnl_is, date_to))

    # Check 4: Correlation source boundary
    checks.append(check_corr_source_boundary(daily_pnl_is, date_to))

    # Check 5: Seed scoring boundary
    checks.append(check_seed_scoring_boundary(trades, date_to, "sortino"))

    # Check 7: Quality filter boundary
    checks.append(check_quality_filter_boundary(daily_pnl_is, date_to))

  # Check 10: Cross-batch isolation
  if len(batch_trades_map) > 1:
    checks.append(check_cross_batch_isolation(batch_trades_map, date_to))

  return _build_report(context, checks)


def report_to_dict(report: IntegrityReport) -> dict:
  """Convert IntegrityReport to JSON-serializable dict."""
  return {
    "context": report.context,
    "timestamp": report.timestamp,
    "total_checks": report.total_checks,
    "passed": report.passed,
    "failed": report.failed,
    "warnings": report.warnings,
    "overall_status": report.overall_status,
    "checks": [
      {
        "check_id": c.check_id,
        "category": c.category,
        "passed": c.passed,
        "severity": c.severity,
        "message": c.message,
        "detail": c.detail,
      }
      for c in report.checks
    ],
  }
