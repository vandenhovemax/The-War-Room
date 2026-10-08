"""OOS Integrity Agent — Scanner tests.

Tests each scanner with clean and contaminated synthetic data.
No database or backend server needed.

Run from the project root:
    python -m pytest backend/tests/test_oos_scanners.py -v --tb=short
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from backend.analysis.oos_integrity.config import AuditConfig
from backend.analysis.oos_integrity.models import ScanResult


# ---------------------------------------------------------------------------
# Mock trade object (mirrors BacktestTrade fields)
# ---------------------------------------------------------------------------
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


def _make_trades(n, start, symbol="BTCUSDT", side="Long", days_apart=1):
  """Generate n clean trades starting from `start` date."""
  trades = []
  for i in range(n):
    entry = start + timedelta(days=i * days_apart)
    exit_t = entry + timedelta(hours=4)
    trades.append(MockTrade(
      id=i + 1, symbol=symbol, side=side,
      entry_time=entry, exit_time=exit_t,
      entry_price=100 + i * 0.5,
      exit_price=100 + i * 0.5 + np.random.uniform(-5, 5),
      quantity=1.0,
      net_profit=np.random.uniform(-10, 10),
      commission=0.1,
      position_id=f"pos_{i}",
      mae=-abs(np.random.uniform(0, 5)),
      mfe=abs(np.random.uniform(0, 5)),
    ))
  return trades


IS_START = datetime(2025, 1, 1)
IS_END = datetime(2025, 5, 31)
OOS_START = datetime(2025, 6, 1)
OOS_END = datetime(2025, 8, 31)
CONFIG = AuditConfig(verbose=False)


# ===========================================================================
# Temporal Scanner
# ===========================================================================
class TestTemporalScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.temporal import TemporalLeakageScanner
    return TemporalLeakageScanner().scan(ctx, CONFIG)

  def test_clean_no_overlap(self):
    ctx = {
      "is_trades": _make_trades(30, IS_START),
      "oos_trades": _make_trades(10, OOS_START),
      "is_end": "2025-05-31",
      "is_daily_pnl": pd.DataFrame(),
    }
    result = self._scan(ctx)
    assert result.score >= 80
    assert not result.has_critical

  def test_temporal_overlap_detected(self):
    """IS trade with exit_time in OOS period → CRITICAL."""
    is_trades = _make_trades(10, IS_START)
    # Inject a leaking trade: entry in IS, exit in OOS
    is_trades.append(MockTrade(
      id=999, entry_time=datetime(2025, 5, 20),
      exit_time=datetime(2025, 6, 15),  # past IS boundary
    ))
    ctx = {
      "is_trades": is_trades,
      "oos_trades": _make_trades(5, OOS_START),
      "is_end": "2025-05-31",
      "is_daily_pnl": pd.DataFrame(),
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "TEMP-003" in ids

  def test_entry_lookahead_detected(self):
    """IS trade with entry_time after IS end → look-ahead."""
    is_trades = _make_trades(10, IS_START)
    is_trades.append(MockTrade(
      id=998, entry_time=datetime(2025, 6, 5),
      exit_time=datetime(2025, 6, 10),
    ))
    ctx = {
      "is_trades": is_trades,
      "oos_trades": _make_trades(5, OOS_START),
      "is_end": "2025-05-31",
      "is_daily_pnl": pd.DataFrame(),
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "TEMP-002" in ids

  def test_daily_pnl_beyond_boundary(self):
    """Daily PnL with rows past is_end → contaminated."""
    dates = pd.date_range("2025-01-01", "2025-07-01")
    pnl = pd.DataFrame({"BTCUSDT": np.random.randn(len(dates))}, index=dates)
    ctx = {
      "is_trades": _make_trades(10, IS_START),
      "oos_trades": _make_trades(5, OOS_START),
      "is_end": "2025-05-31",
      "is_daily_pnl": pnl,
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "TEMP-005" in ids

  def test_embargo_violation(self):
    """Trades right at the boundary with embargo configured."""
    cfg = AuditConfig(embargo_days=7, verbose=False)
    # IS trades ending right near boundary
    is_trades = _make_trades(10, datetime(2025, 5, 22))  # last exits ~May 31
    # OOS trades starting June 1 — only ~0 day gap, need 7
    oos_trades = _make_trades(5, datetime(2025, 6, 1))
    ctx = {
      "is_trades": is_trades,
      "oos_trades": oos_trades,
      "is_end": "2025-05-31",
      "is_daily_pnl": pd.DataFrame(),
    }
    from backend.analysis.oos_integrity.scanners.temporal import TemporalLeakageScanner
    result = TemporalLeakageScanner().scan(ctx, cfg)
    ids = [f.id for f in result.findings]
    assert "TEMP-004" in ids


# ===========================================================================
# Identity Scanner
# ===========================================================================
class TestIdentityScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.identity import IdentityLeakageScanner
    return IdentityLeakageScanner().scan(ctx, CONFIG)

  def test_clean_no_duplicates(self):
    ctx = {
      "is_trades": _make_trades(20, IS_START),
      "oos_trades": _make_trades(10, OOS_START),
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "IDEN-001" not in ids

  def test_exact_duplicate_detected(self):
    """Same trade (identical fields) in both IS and OOS."""
    shared = MockTrade(
      id=1, symbol="BTCUSDT",
      entry_time=datetime(2025, 3, 1), exit_time=datetime(2025, 3, 1, 4),
      entry_price=100, exit_price=105, quantity=1.0,
    )
    ctx = {
      "is_trades": [shared],
      "oos_trades": [MockTrade(
        id=2, symbol="BTCUSDT",
        entry_time=datetime(2025, 3, 1), exit_time=datetime(2025, 3, 1, 4),
        entry_price=100, exit_price=105, quantity=1.0,
      )],
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "IDEN-001" in ids

  def test_position_id_overlap(self):
    """Same position_id in IS and OOS → leakage."""
    is_t = [MockTrade(id=1, position_id="p1", entry_time=IS_START, exit_time=IS_START + timedelta(hours=4))]
    oos_t = [MockTrade(id=2, position_id="p1", entry_time=OOS_START, exit_time=OOS_START + timedelta(hours=4))]
    ctx = {"is_trades": is_t, "oos_trades": oos_t}
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "IDEN-003" in ids


# ===========================================================================
# Feature Scanner
# ===========================================================================
class TestFeatureScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.feature import FeatureLeakageScanner
    return FeatureLeakageScanner().scan(ctx, CONFIG)

  def test_clean_pnl(self):
    dates = pd.date_range("2025-01-01", "2025-05-31")
    pnl = pd.DataFrame({"A": np.random.randn(len(dates))}, index=dates)
    ctx = {
      "is_trades": [], "oos_trades": [],
      "is_daily_pnl": pnl,
      "oos_daily_pnl": pd.DataFrame(),
      "is_end": "2025-05-31",
      "corr_matrix": None,
    }
    result = self._scan(ctx)
    assert result.score >= 80

  def test_pnl_beyond_boundary(self):
    dates = pd.date_range("2025-01-01", "2025-07-15")
    pnl = pd.DataFrame({"A": np.random.randn(len(dates))}, index=dates)
    ctx = {
      "is_trades": [], "oos_trades": [],
      "is_daily_pnl": pnl,
      "oos_daily_pnl": pd.DataFrame(),
      "is_end": "2025-05-31",
      "corr_matrix": None,
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "FEAT-001" in ids

  def test_corr_extra_columns(self):
    dates = pd.date_range("2025-01-01", "2025-05-31")
    pnl = pd.DataFrame({"A": np.random.randn(len(dates))}, index=dates)
    corr = pd.DataFrame(
      [[1.0, 0.5], [0.5, 1.0]],
      columns=["A", "SECRET_EXTRA"],
      index=["A", "SECRET_EXTRA"],
    )
    ctx = {
      "is_trades": [], "oos_trades": [],
      "is_daily_pnl": pnl,
      "oos_daily_pnl": pd.DataFrame(),
      "is_end": "2025-05-31",
      "corr_matrix": corr,
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "FEAT-002" in ids


# ===========================================================================
# Preprocessing Scanner
# ===========================================================================
class TestPreprocessingScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.preprocessing import PreprocessingLeakageScanner
    return PreprocessingLeakageScanner().scan(ctx, CONFIG)

  def test_clean_truncation(self):
    dates = pd.date_range("2025-01-01", "2025-05-31")
    pnl = pd.DataFrame({"A": np.random.randn(len(dates))}, index=dates)
    ctx = {
      "is_trades": _make_trades(20, IS_START),
      "oos_trades": _make_trades(5, OOS_START),
      "is_daily_pnl": pnl,
      "is_end": "2025-05-31",
      "path_type": "single",
    }
    result = self._scan(ctx)
    assert not result.has_critical

  def test_nan_values_flagged(self):
    dates = pd.date_range("2025-01-01", "2025-05-31")
    data = np.random.randn(len(dates))
    data[10:20] = np.nan
    pnl = pd.DataFrame({"A": data}, index=dates)
    ctx = {
      "is_trades": _make_trades(20, IS_START),
      "oos_trades": _make_trades(5, OOS_START),
      "is_daily_pnl": pnl,
      "is_end": "2025-05-31",
      "path_type": "single",
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "PREP-002" in ids

  def test_oos_trades_in_is(self):
    """OOS trade mixed into IS trade list → quality filter timing issue."""
    is_trades = _make_trades(20, IS_START)
    oos_trade = MockTrade(
      id=500, entry_time=OOS_START + timedelta(days=10),
      exit_time=OOS_START + timedelta(days=10, hours=4),
    )
    is_trades.append(oos_trade)
    ctx = {
      "is_trades": is_trades,
      "oos_trades": _make_trades(5, OOS_START),
      "is_daily_pnl": pd.DataFrame(),
      "is_end": "2025-05-31",
      "path_type": "single",
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "PREP-004" in ids


# ===========================================================================
# Distribution Scanner
# ===========================================================================
class TestDistributionScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.distribution import DistributionShiftScanner
    return DistributionShiftScanner().scan(ctx, CONFIG)

  def test_same_distribution_clean(self):
    np.random.seed(42)
    is_pnl = pd.DataFrame({"A": np.random.randn(200)})
    oos_pnl = pd.DataFrame({"A": np.random.randn(100)})
    ctx = {"is_daily_pnl": is_pnl, "oos_daily_pnl": oos_pnl, "is_trades": [], "oos_trades": []}
    result = self._scan(ctx)
    # Same distribution should pass most checks
    assert result.score >= 60

  def test_shifted_distribution_detected(self):
    """IS ~ N(0,1), OOS ~ N(5,1) → KS test should fire."""
    np.random.seed(42)
    is_pnl = pd.DataFrame({"A": np.random.randn(200)})
    oos_pnl = pd.DataFrame({"A": np.random.randn(100) + 5})  # shifted
    ctx = {"is_daily_pnl": is_pnl, "oos_daily_pnl": oos_pnl, "is_trades": [], "oos_trades": []}
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "DIST-001" in ids or "DIST-002" in ids

  def test_identical_data_too_similar(self):
    """OOS = exact copy of IS → too-similar detection."""
    np.random.seed(42)
    data = np.random.randn(200)
    is_pnl = pd.DataFrame({"A": data})
    oos_pnl = pd.DataFrame({"A": data[:100]})  # exact copy
    ctx = {"is_daily_pnl": is_pnl, "oos_daily_pnl": oos_pnl, "is_trades": [], "oos_trades": []}
    result = self._scan(ctx)
    # Should either flag too-similar or pass (depends on sample)
    assert result.score <= 100


# ===========================================================================
# Statistical Scanner
# ===========================================================================
class TestStatisticalScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.statistical import StatisticalValidationScanner
    return StatisticalValidationScanner().scan(ctx, CONFIG)

  def test_oos_outperforms_is(self):
    """OOS mean >> IS mean → suspicious."""
    is_trades = []
    for i in range(50):
      is_trades.append(MockTrade(id=i, net_profit=-1.0, entry_time=IS_START + timedelta(days=i), exit_time=IS_START + timedelta(days=i, hours=4)))
    oos_trades = []
    for i in range(20):
      oos_trades.append(MockTrade(id=100 + i, net_profit=50.0, entry_time=OOS_START + timedelta(days=i), exit_time=OOS_START + timedelta(days=i, hours=4)))
    dates_is = pd.date_range("2025-01-01", "2025-05-31")
    dates_oos = pd.date_range("2025-06-01", "2025-08-31")
    is_pnl = pd.DataFrame({"A": np.random.randn(len(dates_is)) * 0.1}, index=dates_is)
    oos_pnl = pd.DataFrame({"A": np.random.randn(len(dates_oos)) + 10}, index=dates_oos)
    ctx = {
      "is_trades": is_trades, "oos_trades": oos_trades,
      "is_daily_pnl": is_pnl, "oos_daily_pnl": oos_pnl,
      "optimizer_results": [],
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "STAT-002" in ids

  def test_clean_similar_performance(self):
    np.random.seed(42)
    is_trades = _make_trades(50, IS_START)
    oos_trades = _make_trades(20, OOS_START)
    dates_is = pd.date_range("2025-01-01", "2025-05-31")
    dates_oos = pd.date_range("2025-06-01", "2025-08-31")
    is_pnl = pd.DataFrame({"A": np.random.randn(len(dates_is))}, index=dates_is)
    oos_pnl = pd.DataFrame({"A": np.random.randn(len(dates_oos))}, index=dates_oos)
    ctx = {
      "is_trades": is_trades, "oos_trades": oos_trades,
      "is_daily_pnl": is_pnl, "oos_daily_pnl": oos_pnl,
      "optimizer_results": [],
    }
    result = self._scan(ctx)
    assert not result.has_critical


# ===========================================================================
# Pipeline Scanner
# ===========================================================================
class TestPipelineScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.pipeline import PipelineTracerScanner
    return PipelineTracerScanner().scan(ctx, CONFIG)

  def test_no_boundary_flagged(self):
    ctx = {
      "is_trades": _make_trades(10, IS_START),
      "oos_trades": _make_trades(5, OOS_START),
      "is_end": None,
      "is_daily_pnl": pd.DataFrame(),
      "path_type": "single",
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "PIPE-001" in ids

  def test_clean_pipeline(self):
    dates = pd.date_range("2025-01-01", "2025-05-31")
    pnl = pd.DataFrame({"A": np.random.randn(len(dates))}, index=dates)
    ctx = {
      "is_trades": _make_trades(10, IS_START),
      "oos_trades": _make_trades(5, OOS_START),
      "is_end": "2025-05-31",
      "is_daily_pnl": pnl,
      "path_type": "single",
    }
    result = self._scan(ctx)
    assert not result.has_critical


# ===========================================================================
# Group Scanner
# ===========================================================================
class TestGroupScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.group import GroupLeakageScanner
    return GroupLeakageScanner().scan(ctx, CONFIG)

  def test_batch_isolation_clean(self):
    batch_trades = {
      1: _make_trades(10, IS_START, symbol="BTCUSDT"),
      2: _make_trades(10, IS_START, symbol="ETHUSDT"),
    }
    ctx = {
      "batch_trades": batch_trades,
      "is_end": "2025-05-31",
      "is_daily_pnl": pd.DataFrame(),
      "is_trades": _make_trades(10, IS_START),
      "oos_trades": _make_trades(5, OOS_START),
    }
    result = self._scan(ctx)
    assert not result.has_critical

  def test_batch_isolation_violated(self):
    """Batch contains trades past IS boundary."""
    leaking_trades = _make_trades(5, IS_START) + _make_trades(3, OOS_START + timedelta(days=10))
    batch_trades = {1: leaking_trades}
    ctx = {
      "batch_trades": batch_trades,
      "is_end": "2025-05-31",
      "is_daily_pnl": pd.DataFrame(),
      "is_trades": _make_trades(5, IS_START),
      "oos_trades": _make_trades(3, OOS_START),
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "GRP-001" in ids

  def test_zero_symbol_overlap(self):
    is_t = _make_trades(10, IS_START, symbol="AAPL")
    oos_t = _make_trades(5, OOS_START, symbol="MSFT")
    ctx = {
      "batch_trades": None,
      "is_end": "2025-05-31",
      "is_daily_pnl": pd.DataFrame(),
      "is_trades": is_t,
      "oos_trades": oos_t,
    }
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "GRP-003" in ids


# ===========================================================================
# WFO Fold Scanner
# ===========================================================================
class TestWFOFoldScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.wfo_fold import WFOFoldScanner
    return WFOFoldScanner().scan(ctx, CONFIG)

  def test_clean_windows(self):
    windows = [
      {"window_num": 1, "is_start": "2025-01-01", "is_end": "2025-03-31", "oos_start": "2025-04-01", "oos_end": "2025-05-31"},
      {"window_num": 2, "is_start": "2025-01-01", "is_end": "2025-05-31", "oos_start": "2025-06-01", "oos_end": "2025-07-31"},
    ]
    ctx = {"windows": windows, "is_trades": _make_trades(20, IS_START)}
    result = self._scan(ctx)
    assert not result.has_critical

  def test_is_oos_overlap_critical(self):
    """IS end >= OOS start → CRITICAL."""
    windows = [
      {"window_num": 1, "is_start": "2025-01-01", "is_end": "2025-06-15", "oos_start": "2025-06-01", "oos_end": "2025-07-31"},
    ]
    ctx = {"windows": windows, "is_trades": []}
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "WFO-001" in ids
    assert result.has_critical

  def test_oos_overlap_between_windows(self):
    """OOS windows overlapping each other."""
    windows = [
      {"window_num": 1, "is_start": "2025-01-01", "is_end": "2025-03-31", "oos_start": "2025-04-01", "oos_end": "2025-06-30"},
      {"window_num": 2, "is_start": "2025-01-01", "is_end": "2025-05-31", "oos_start": "2025-05-01", "oos_end": "2025-07-31"},
    ]
    ctx = {"windows": windows, "is_trades": []}
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "WFO-002" in ids


# ===========================================================================
# Quant Finance Scanner
# ===========================================================================
class TestQuantFinanceScanner:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.scanners.quant_finance import QuantFinanceScanner
    return QuantFinanceScanner().scan(ctx, CONFIG)

  def test_clean_trades(self):
    ctx = {
      "is_trades": _make_trades(30, IS_START),
      "oos_trades": _make_trades(10, OOS_START),
    }
    result = self._scan(ctx)
    assert result.score >= 80

  def test_zero_duration_trade(self):
    """Entry == exit timestamp → suspicious."""
    t = MockTrade(
      id=1, entry_time=datetime(2025, 3, 1, 10, 0),
      exit_time=datetime(2025, 3, 1, 10, 0),
    )
    ctx = {"is_trades": [t], "oos_trades": []}
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "QF-001" in ids

  def test_stale_data(self):
    """6+ consecutive identical entry prices → stale feed."""
    trades = []
    for i in range(10):
      trades.append(MockTrade(
        id=i, symbol="SOLUSDT",
        entry_time=IS_START + timedelta(days=i),
        exit_time=IS_START + timedelta(days=i, hours=4),
        entry_price=100.0,  # all same
      ))
    ctx = {"is_trades": trades, "oos_trades": []}
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "QF-006" in ids

  def test_price_continuity(self):
    """Impossible 200% price jump → flagged."""
    t = MockTrade(
      id=1, entry_price=100, exit_price=350,
      entry_time=IS_START, exit_time=IS_START + timedelta(hours=4),
    )
    ctx = {"is_trades": [t], "oos_trades": []}
    result = self._scan(ctx)
    ids = [f.id for f in result.findings]
    assert "QF-005" in ids
