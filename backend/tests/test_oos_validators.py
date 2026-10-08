"""OOS Integrity Agent — Validator tests.

Tests timestamp, truthfulness, and schema validators.
No database or backend server needed.

Run from the project root:
    python -m pytest backend/tests/test_oos_validators.py -v --tb=short
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from backend.analysis.oos_integrity.config import AuditConfig


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


CONFIG = AuditConfig(verbose=False)
IS_START = datetime(2025, 1, 1)


# ===========================================================================
# Timestamp Validator
# ===========================================================================
class TestTimestampValidator:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.validators.timestamp import TimestampValidator
    return TimestampValidator().scan(ctx, CONFIG)

  def test_clean_timestamps(self):
    trades = [
      MockTrade(id=i, entry_time=IS_START + timedelta(days=i),
                exit_time=IS_START + timedelta(days=i, hours=4))
      for i in range(20)
    ]
    result = self._scan({"is_trades": trades, "oos_trades": []})
    assert not result.has_critical

  def test_inverted_entry_exit(self):
    """entry_time > exit_time → CRITICAL."""
    t = MockTrade(
      id=1,
      entry_time=datetime(2025, 3, 10, 14, 0),
      exit_time=datetime(2025, 3, 10, 10, 0),  # before entry
    )
    result = self._scan({"is_trades": [t], "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TS-001" in ids
    assert result.has_critical

  def test_future_dates(self):
    """Exit time in the future → flagged."""
    t = MockTrade(
      id=1,
      entry_time=datetime(2025, 1, 1),
      exit_time=datetime(2099, 1, 1),  # far future
    )
    result = self._scan({"is_trades": [t], "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TS-002" in ids

  def test_duplicate_timestamps(self):
    """Same (symbol, entry, exit) appearing twice."""
    t1 = MockTrade(id=1, entry_time=datetime(2025, 3, 1), exit_time=datetime(2025, 3, 1, 4))
    t2 = MockTrade(id=2, entry_time=datetime(2025, 3, 1), exit_time=datetime(2025, 3, 1, 4))
    result = self._scan({"is_trades": [t1, t2], "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TS-003" in ids

  def test_suspicious_gap(self):
    """200-day gap between trades → flagged."""
    t1 = MockTrade(id=1, entry_time=datetime(2025, 1, 1), exit_time=datetime(2025, 1, 2))
    t2 = MockTrade(id=2, entry_time=datetime(2025, 8, 1), exit_time=datetime(2025, 8, 2))
    result = self._scan({"is_trades": [t1, t2], "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TS-004" in ids

  def test_mixed_precision(self):
    """Mix of date-only and sub-second timestamps."""
    trades = []
    for i in range(100):
      if i < 30:
        # date-only (midnight)
        entry = datetime(2025, 1, 1) + timedelta(days=i)
        exit_t = entry
      else:
        # sub-second with non-midnight hour so validator sees the microsecond
        entry = datetime(2025, 1, 1, 10, 30, 0, 123456) + timedelta(days=i)
        exit_t = entry + timedelta(hours=4, microseconds=654321)
      trades.append(MockTrade(id=i, entry_time=entry, exit_time=exit_t))
    result = self._scan({"is_trades": trades, "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TS-005" in ids


# ===========================================================================
# Truthfulness Validator
# ===========================================================================
class TestTruthfulnessValidator:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.validators.truthfulness import TruthfulnessValidator
    return TruthfulnessValidator().scan(ctx, CONFIG)

  def test_clean_trades(self):
    trades = [
      MockTrade(
        id=i, entry_price=100 + i * 0.3, exit_price=105 + i * 0.3,
        quantity=1.0, net_profit=(105 - 100) * 1.0 - 0.1,
        commission=0.1, mae=-2.0, mfe=8.0,
        entry_time=IS_START + timedelta(days=i),
        exit_time=IS_START + timedelta(days=i, hours=4),
      )
      for i in range(30)
    ]
    result = self._scan({"is_trades": trades, "oos_trades": []})
    assert not result.has_critical

  def test_impossible_values(self):
    """Negative entry price → CRITICAL."""
    t = MockTrade(id=1, entry_price=-100, exit_price=50, quantity=1.0)
    result = self._scan({"is_trades": [t], "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TRUTH-002" in ids
    assert result.has_critical

  def test_negative_quantity(self):
    """Negative quantity → CRITICAL."""
    t = MockTrade(id=1, entry_price=100, exit_price=110, quantity=-5)
    result = self._scan({"is_trades": [t], "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TRUTH-002" in ids

  def test_pnl_consistency(self):
    """net_profit wildly different from expected → flagged."""
    t = MockTrade(
      id=1, entry_price=100, exit_price=110,
      quantity=1.0, side="Long",
      net_profit=500,  # expected ~10
      commission=0,
    )
    result = self._scan({"is_trades": [t], "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TRUTH-003" in ids

  def test_positive_mae_long(self):
    """Positive MAE for a long trade → impossible."""
    t = MockTrade(id=1, side="Long", mae=10.0, mfe=5.0)
    result = self._scan({"is_trades": [t], "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TRUTH-004" in ids

  def test_negative_mfe_long(self):
    """Negative MFE for a long trade → impossible."""
    t = MockTrade(id=1, side="Long", mae=-5.0, mfe=-10.0)
    result = self._scan({"is_trades": [t], "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TRUTH-005" in ids

  def test_round_number_bias(self):
    """All net_profit values are whole numbers."""
    trades = [
      MockTrade(id=i, net_profit=float(i * 10))
      for i in range(60)
    ]
    result = self._scan({"is_trades": trades, "oos_trades": []})
    ids = [f.id for f in result.findings]
    assert "TRUTH-006" in ids


# ===========================================================================
# Schema Validator
# ===========================================================================
class TestSchemaValidator:
  def _scan(self, ctx):
    from backend.analysis.oos_integrity.validators.schema import SchemaValidator
    return SchemaValidator().scan(ctx, CONFIG)

  def test_matching_schemas(self):
    dates_is = pd.date_range("2025-01-01", "2025-05-31")
    dates_oos = pd.date_range("2025-06-01", "2025-08-31")
    is_pnl = pd.DataFrame({"A": np.random.randn(len(dates_is)), "B": np.random.randn(len(dates_is))}, index=dates_is)
    oos_pnl = pd.DataFrame({"A": np.random.randn(len(dates_oos)), "B": np.random.randn(len(dates_oos))}, index=dates_oos)
    result = self._scan({"is_daily_pnl": is_pnl, "oos_daily_pnl": oos_pnl})
    assert result.score >= 80

  def test_missing_columns(self):
    """OOS missing a column from IS."""
    dates_is = pd.date_range("2025-01-01", "2025-05-31")
    dates_oos = pd.date_range("2025-06-01", "2025-08-31")
    is_pnl = pd.DataFrame({"A": np.random.randn(len(dates_is)), "B": np.random.randn(len(dates_is))}, index=dates_is)
    oos_pnl = pd.DataFrame({"A": np.random.randn(len(dates_oos))}, index=dates_oos)  # missing B
    result = self._scan({"is_daily_pnl": is_pnl, "oos_daily_pnl": oos_pnl})
    ids = [f.id for f in result.findings]
    assert "SCHEMA-001" in ids

  def test_extra_oos_columns(self):
    """OOS has columns not in IS → suspicious."""
    dates_is = pd.date_range("2025-01-01", "2025-05-31")
    dates_oos = pd.date_range("2025-06-01", "2025-08-31")
    is_pnl = pd.DataFrame({"A": np.random.randn(len(dates_is))}, index=dates_is)
    oos_pnl = pd.DataFrame({"A": np.random.randn(len(dates_oos)), "SECRET": np.random.randn(len(dates_oos))}, index=dates_oos)
    result = self._scan({"is_daily_pnl": is_pnl, "oos_daily_pnl": oos_pnl})
    ids = [f.id for f in result.findings]
    assert "SCHEMA-002" in ids

  def test_nan_pattern_mismatch(self):
    """Wildly different NaN rates between IS and OOS."""
    dates_is = pd.date_range("2025-01-01", "2025-05-31")
    dates_oos = pd.date_range("2025-06-01", "2025-08-31")
    is_data = np.random.randn(len(dates_is))
    oos_data = np.random.randn(len(dates_oos))
    oos_data[:int(len(oos_data) * 0.5)] = np.nan  # 50% NaN in OOS
    is_pnl = pd.DataFrame({"A": is_data}, index=dates_is)
    oos_pnl = pd.DataFrame({"A": oos_data}, index=dates_oos)
    result = self._scan({"is_daily_pnl": is_pnl, "oos_daily_pnl": oos_pnl})
    ids = [f.id for f in result.findings]
    assert "SCHEMA-005" in ids
