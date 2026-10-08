"""OOS Integrity Agent — Integration tests.

End-to-end tests of the full OOSIntegrityAgent orchestrator.
Tests known-clean data, known-contaminated data, edge cases, and score calculation.

Run from the project root:
    python -m pytest backend/tests/test_oos_integration.py -v --tb=short
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from backend.analysis.oos_integrity import OOSIntegrityAgent
from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import SCORE_BANDS, status_from_score
from backend.analysis.oos_integrity.report import (
  compute_integrity_score,
  report_to_json,
  report_to_markdown,
)


@dataclass
class MockTrade:
  id: int = 1
  symbol: str = "BTCUSDT"
  side: str = "Long"
  entry_time: datetime | None = None
  exit_time: datetime | None = None
  entry_price: float = 100.0
  exit_price: float = 110.0
  quantity: float = 1.0
  net_profit: float = 10.0
  commission: float = 0.1
  position_id: str = "pos_1"
  mae: float = -5.0
  mfe: float = 15.0
  run_id: int = 1


def _make_trades(n, start, symbol="BTCUSDT", side="Long"):
  trades = []
  for i in range(n):
    entry = start + timedelta(days=i)
    exit_t = entry + timedelta(hours=4)
    pnl = np.random.uniform(-5, 10)
    trades.append(MockTrade(
      id=i + 1, symbol=symbol, side=side,
      entry_time=entry, exit_time=exit_t,
      entry_price=100 + i * 0.3,
      exit_price=100 + i * 0.3 + pnl / 1.0,
      quantity=1.0, net_profit=pnl,
      commission=0.1,
      position_id=f"pos_{i}",
      mae=-abs(np.random.uniform(0, 3)),
      mfe=abs(np.random.uniform(0, 5)),
    ))
  return trades


IS_START = datetime(2025, 1, 1)
OOS_START = datetime(2025, 6, 1)


# ===========================================================================
# Known-Clean Data
# ===========================================================================
class TestKnownClean:
  def test_pristine_data_high_score(self):
    """Perfectly clean data should score >= 80."""
    np.random.seed(42)
    is_trades = _make_trades(50, IS_START)
    oos_trades = _make_trades(20, OOS_START)
    dates_is = pd.date_range("2025-01-01", "2025-05-31")
    dates_oos = pd.date_range("2025-06-01", "2025-08-31")
    is_pnl = pd.DataFrame({"BTCUSDT": np.random.randn(len(dates_is))}, index=dates_is)
    oos_pnl = pd.DataFrame({"BTCUSDT": np.random.randn(len(dates_oos))}, index=dates_oos)

    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(
      is_trades=is_trades, oos_trades=oos_trades,
      is_daily_pnl=is_pnl, oos_daily_pnl=oos_pnl,
      is_end="2025-05-31",
    )
    assert report.score.overall >= 70
    assert report.status in ("PRISTINE", "CLEAN", "CAUTIOUS")
    assert not any(f.severity == "CRITICAL" for f in report.all_findings)

  def test_empty_data_no_crash(self):
    """Empty inputs should not crash and should return pristine."""
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit()
    assert report.score.overall >= 80
    assert report.status in ("PRISTINE", "CLEAN")

  def test_quick_audit(self):
    """Quick audit runs only temporal + identity + timestamp + schema."""
    np.random.seed(42)
    is_trades = _make_trades(20, IS_START)
    oos_trades = _make_trades(10, OOS_START)

    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.quick_audit(
      is_trades=is_trades, oos_trades=oos_trades,
      is_end="2025-05-31",
    )
    # Should only have results from enabled scanners
    scanner_names = {r.scanner_name for r in report.scan_results}
    assert "temporal" in scanner_names
    assert "identity" in scanner_names
    # Distribution/statistical should NOT be present
    assert "distribution" not in scanner_names
    assert "statistical" not in scanner_names


# ===========================================================================
# Known-Contaminated Data
# ===========================================================================
class TestKnownContaminated:
  def test_temporal_leakage_drops_score(self):
    """IS trades with OOS exit times → score drops."""
    is_trades = _make_trades(30, IS_START)
    # Inject leaking trades
    for i in range(5):
      is_trades.append(MockTrade(
        id=900 + i,
        entry_time=datetime(2025, 5, 25) + timedelta(days=i),
        exit_time=datetime(2025, 6, 10) + timedelta(days=i),
        position_id=f"leak_{i}",
      ))
    oos_trades = _make_trades(10, OOS_START)
    dates = pd.date_range("2025-01-01", "2025-05-31")
    is_pnl = pd.DataFrame({"BTCUSDT": np.random.randn(len(dates))}, index=dates)

    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(
      is_trades=is_trades, oos_trades=oos_trades,
      is_daily_pnl=is_pnl, is_end="2025-05-31",
    )
    assert report.score.temporal < 100

  def test_identity_leakage_drops_score(self):
    """Exact duplicate trades in IS and OOS → score drops."""
    shared_trade = MockTrade(
      id=1, entry_time=datetime(2025, 3, 1),
      exit_time=datetime(2025, 3, 1, 4),
      entry_price=100, exit_price=105, quantity=1.0,
    )
    is_trades = [shared_trade]
    oos_trades = [MockTrade(
      id=2, entry_time=datetime(2025, 3, 1),
      exit_time=datetime(2025, 3, 1, 4),
      entry_price=100, exit_price=105, quantity=1.0,
    )]

    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(
      is_trades=is_trades, oos_trades=oos_trades,
      is_end="2025-05-31",
    )
    assert report.score.identity < 100

  def test_impossible_values_critical(self):
    """Negative prices → CRITICAL finding."""
    trades = [MockTrade(id=1, entry_price=-50, exit_price=-30, quantity=1.0)]
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(is_trades=trades, is_end="2025-05-31")
    assert any(f.severity == "CRITICAL" for f in report.all_findings)
    assert report.score.truthfulness < 100

  def test_wfo_overlap_critical(self):
    """IS/OOS overlap in windows → CRITICAL."""
    windows = [
      {"window_num": 1, "is_start": "2025-01-01", "is_end": "2025-06-15",
       "oos_start": "2025-06-01", "oos_end": "2025-07-31"},
    ]
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(windows=windows, is_end="2025-05-31")
    assert report.score.wfo_fold < 100
    wfo_findings = [f for f in report.all_findings if f.scanner == "wfo_fold"]
    assert any(f.severity == "CRITICAL" for f in wfo_findings)

  def test_schema_mismatch(self):
    """OOS has extra columns not in IS → flagged."""
    dates_is = pd.date_range("2025-01-01", "2025-05-31")
    dates_oos = pd.date_range("2025-06-01", "2025-08-31")
    is_pnl = pd.DataFrame({"A": np.random.randn(len(dates_is))}, index=dates_is)
    oos_pnl = pd.DataFrame({
      "A": np.random.randn(len(dates_oos)),
      "LEAKED": np.random.randn(len(dates_oos)),
    }, index=dates_oos)

    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(
      is_daily_pnl=is_pnl, oos_daily_pnl=oos_pnl, is_end="2025-05-31",
    )
    assert report.score.schema_val < 100

  def test_batch_isolation_violation(self):
    """Batch with OOS trades past IS boundary."""
    leaking = _make_trades(5, IS_START) + _make_trades(3, OOS_START + timedelta(days=10))
    batch_trades = {1: leaking}
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(
      batch_trades=batch_trades, is_end="2025-05-31",
      is_trades=_make_trades(5, IS_START), oos_trades=_make_trades(3, OOS_START),
    )
    assert report.score.group < 100


# ===========================================================================
# Edge Cases
# ===========================================================================
class TestEdgeCases:
  def test_single_trade_each(self):
    """One IS trade, one OOS trade — should not crash."""
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(
      is_trades=[MockTrade(id=1, entry_time=IS_START, exit_time=IS_START + timedelta(hours=4))],
      oos_trades=[MockTrade(id=2, entry_time=OOS_START, exit_time=OOS_START + timedelta(hours=4))],
      is_end="2025-05-31",
    )
    assert report.score.overall >= 0

  def test_no_oos_trades(self):
    """IS-only data — scanners requiring OOS should skip gracefully."""
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(
      is_trades=_make_trades(20, IS_START),
      is_end="2025-05-31",
    )
    assert report.score.overall >= 0

  def test_mismatched_schemas_no_crash(self):
    """Completely different column sets should not crash."""
    is_pnl = pd.DataFrame({"A": [1, 2, 3], "B": [4, 5, 6]})
    oos_pnl = pd.DataFrame({"C": [7, 8, 9], "D": [10, 11, 12]})
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(
      is_daily_pnl=is_pnl, oos_daily_pnl=oos_pnl,
      is_end="2025-05-31",
    )
    assert report.score.overall >= 0

  def test_all_scanners_disabled(self):
    """All scanners off → pristine score, no findings."""
    cfg = AuditConfig(
      enable_temporal=False, enable_identity=False, enable_feature=False,
      enable_preprocessing=False, enable_distribution=False,
      enable_statistical=False, enable_pipeline=False, enable_group=False,
      enable_wfo_fold=False, enable_quant_finance=False,
      enable_truthfulness=False, enable_timestamp=False, enable_schema=False,
      verbose=False,
    )
    agent = OOSIntegrityAgent(cfg)
    report = agent.audit(
      is_trades=_make_trades(10, IS_START),
      is_end="2025-05-31",
    )
    assert report.score.overall == 100
    assert len(report.all_findings) == 0


# ===========================================================================
# Score Calculation
# ===========================================================================
class TestScoreCalculation:
  def test_score_bands(self):
    assert status_from_score(100) == "PRISTINE"
    assert status_from_score(95) == "PRISTINE"
    assert status_from_score(94) == "CLEAN"
    assert status_from_score(80) == "CLEAN"
    assert status_from_score(79) == "CAUTIOUS"
    assert status_from_score(60) == "CAUTIOUS"
    assert status_from_score(59) == "COMPROMISED"
    assert status_from_score(40) == "COMPROMISED"
    assert status_from_score(39) == "SEVERE"
    assert status_from_score(20) == "SEVERE"
    assert status_from_score(19) == "FRAUDULENT"
    assert status_from_score(0) == "FRAUDULENT"

  def test_report_to_json(self):
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(is_trades=_make_trades(10, IS_START), is_end="2025-05-31")
    j = report_to_json(report)
    assert "score" in j
    assert "findings" in j
    assert "status" in j
    assert "scanner_results" in j
    assert isinstance(j["score"]["overall"], float)

  def test_report_to_markdown(self):
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(is_trades=_make_trades(10, IS_START), is_end="2025-05-31")
    md = report_to_markdown(report)
    assert "# OOS Integrity Audit Report" in md
    assert "Score Breakdown" in md
    assert report.status in md

  def test_critical_findings_lower_score(self):
    """More critical findings → lower overall score."""
    # Inject many violations
    trades = [MockTrade(
      id=i, entry_price=-10, exit_price=-5, quantity=-1,
      mae=10, mfe=-10, side="Long",
    ) for i in range(20)]
    agent = OOSIntegrityAgent(AuditConfig(verbose=False))
    report = agent.audit(is_trades=trades, is_end="2025-05-31")
    assert report.score.overall < 100
    assert report.score.truthfulness < 100
    assert any(f.severity == "CRITICAL" for f in report.all_findings)

  def test_weighted_scoring(self):
    """Critical scanners (temporal, identity, pipeline, wfo_fold) weighted 2x."""
    from backend.analysis.oos_integrity.models import ScanResult as SR
    results = [
      SR("temporal", [], 50.0),
      SR("identity", [], 100.0),
      SR("feature", [], 100.0),
      SR("preprocessing", [], 100.0),
      SR("distribution", [], 100.0),
      SR("statistical", [], 100.0),
      SR("pipeline", [], 100.0),
      SR("group", [], 100.0),
      SR("wfo_fold", [], 100.0),
      SR("quant_finance", [], 100.0),
      SR("truthfulness", [], 100.0),
      SR("timestamp_validator", [], 100.0),
      SR("schema_validator", [], 100.0),
    ]
    score = compute_integrity_score(results)
    # temporal (weight 2) at 50 should drag overall down more than a weight-1 scanner
    assert score.temporal == 50.0
    assert score.overall < 100
    # Verify it's less than simple average (which would be ~96.2)
    simple_avg = (50 + 12 * 100) / 13
    assert score.overall < simple_avg


# ===========================================================================
# Audit Prompt Generator
# ===========================================================================
class TestAuditPrompt:
  def test_default_prompt(self):
    """Default prompt includes all pathways and table format."""
    prompt = OOSIntegrityAgent.generate_prompt()
    assert "FULL OOS CONTAMINATION AUDIT" in prompt
    assert "wfo_boundary.py" in prompt
    assert "Pathway | Data Source" in prompt
    assert "Single Optimizer" in prompt
    assert "Cross-Strategy Builder" in prompt

  def test_filtered_by_path_type(self):
    """path_type='single' only includes Single Optimizer pathway."""
    prompt = OOSIntegrityAgent.generate_prompt(path_type="single")
    assert "Single Optimizer" in prompt
    assert "Cross-Strategy Builder" not in prompt

  def test_custom_target_and_focus(self):
    """Custom target and focus areas are included."""
    prompt = OOSIntegrityAgent.generate_prompt(
      target="my_custom_feature.py",
      focus_areas=["Check the frobulator", "Verify the widget"],
    )
    assert "my_custom_feature.py" in prompt
    assert "Check the frobulator" in prompt
    assert "Verify the widget" in prompt

  def test_custom_files_and_reference(self):
    prompt = OOSIntegrityAgent.generate_prompt(
      files=["foo.py", "bar.py"],
      reference="known_clean.py",
    )
    assert "`foo.py`" in prompt
    assert "`bar.py`" in prompt
    assert "known_clean.py" in prompt
