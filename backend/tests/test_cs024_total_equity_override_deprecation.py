"""CS-024: total_equity_override deprecation warning path.

The setting still works (additive-safe — no live account surprise), but
anyone using it now gets a DEPRECATED warning at every read so they know
to migrate to ``reserve_account_usd`` before the setting is removed.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.exchange.position_manager import PositionManager


def _build_pm(
  reserve: float = 0.0,
  wallet: float = 0.0,
  override: float = 0.0,
) -> PositionManager:
  pm = PositionManager.__new__(PositionManager)
  pm._client = MagicMock()
  pm._client.get_instrument_info.return_value = {
    "lot_size": 0.001, "max_leverage": 100.0, "min_qty": 0.001,
  }
  pm._settings = SimpleNamespace(
    reserve_account_usd=reserve,
    total_equity_override=override,
    max_positions_total=10,
    margin_breathing_room_pct=0.0,
    max_position_pct=25.0,  # needs non-zero to exercise cap_equity read
    max_position_notional=0.0,
    default_leverage=1,
    max_risk_per_trade_usd=0.0,
    max_risk_per_trade_pct=0.0,
  )
  pm.get_wallet_balance = MagicMock(return_value=wallet)
  pm.get_available_balance = MagicMock(return_value=max(wallet, 1.0))
  return pm


def test_override_triggers_deprecation_warning(caplog):
  """Non-zero override + zero wallet/reserve → warning fires."""
  pm = _build_pm(reserve=0.0, wallet=0.0, override=5000.0)

  with caplog.at_level(logging.WARNING, logger="backend.exchange.position_manager"):
    # Set wallet to >0 so margin budget path can complete; override is only
    # consulted when cap_equity (reserve+wallet) is <=0. Force that by using
    # a separate path: set get_wallet_balance to 0 for cap_equity line but >0
    # for margin_budget line — not simple. Easier: assert the branch by calling
    # get_wallet_balance=0 and expect failure, then check the warning in the
    # partial execution.
    pm.get_wallet_balance = MagicMock(return_value=0.0)
    pm.get_available_balance = MagicMock(return_value=0.0)
    with pytest.raises(ValueError):
      pm.calculate_position_size(
        symbol="BTCUSDT", side="Buy",
        entry_price=50_000.0, sl_price=49_000.0, risk_amount=100.0,
      )

  msgs = [r.message for r in caplog.records]
  assert any("CS-024 DEPRECATED" in m for m in msgs), msgs


def test_zero_override_no_warning(caplog):
  """override=0 → no deprecation noise."""
  pm = _build_pm(reserve=0.0, wallet=10_000.0, override=0.0)

  with caplog.at_level(logging.WARNING, logger="backend.exchange.position_manager"):
    pm.calculate_position_size(
      symbol="BTCUSDT", side="Buy",
      entry_price=50_000.0, sl_price=49_000.0, risk_amount=100.0,
    )

  deprec = [r for r in caplog.records if "CS-024 DEPRECATED" in r.message]
  assert not deprec, f"Unexpected deprecation log: {deprec}"
