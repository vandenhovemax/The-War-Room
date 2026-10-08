"""CS-032: Effective-risk feedback from PositionManager.calculate_position_size.

When sizing is capped (leverage cap, notional cap, or max-risk-per-trade),
the *requested* risk and *effective* risk diverge. Before the fix the result
dict only exposed ``risk_amount`` (effective), so the caller and UI could
not tell when the trade was capped down from the user's intent.

These tests pin the contract: every sizing result must include
``requested_risk``, ``effective_risk``, and ``risk_shortfall_usd`` so the
downstream UI can surface leverage-capped trades to the user.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.exchange.position_manager import PositionManager


def _build_pm(
  max_leverage: float = 100.0,
  lot_size: float = 0.001,
  min_qty: float = 0.001,
  wallet_balance: float = 10_000.0,
  available_balance: float = 10_000.0,
  max_risk_cap_usd: float = 0.0,
) -> PositionManager:
  pm = PositionManager.__new__(PositionManager)
  pm._client = MagicMock()
  pm._client.get_instrument_info.return_value = {
    "lot_size": lot_size,
    "max_leverage": max_leverage,
    "min_qty": min_qty,
  }
  pm._settings = SimpleNamespace(
    reserve_account_usd=0.0,
    total_equity_override=0.0,
    max_positions_total=10,
    margin_breathing_room_pct=0.0,
    max_position_pct=0.0,
    max_position_notional=0.0,
    default_leverage=1,
    max_risk_per_trade_usd=max_risk_cap_usd,
    max_risk_per_trade_pct=0.0,
  )
  pm.get_wallet_balance = MagicMock(return_value=wallet_balance)
  pm.get_available_balance = MagicMock(return_value=available_balance)
  return pm


def test_result_exposes_effective_risk_keys_on_happy_path():
  pm = _build_pm()
  result = pm.calculate_position_size(
    symbol="BTCUSDT",
    side="Buy",
    entry_price=50_000.0,
    sl_price=49_000.0,
    risk_amount=100.0,
  )
  assert "requested_risk" in result
  assert "effective_risk" in result
  assert "risk_shortfall_usd" in result
  assert result["requested_risk"] == 100.0
  # Uncapped path: effective ≈ requested (modulo lot rounding)
  assert abs(result["effective_risk"] - 100.0) < 1.0
  assert result["risk_shortfall_usd"] >= 0.0


def test_leverage_cap_reports_shortfall(caplog):
  """With 1x max leverage and tiny wallet, qty gets capped -> shortfall > 0."""
  pm = _build_pm(max_leverage=1.0, wallet_balance=10.0, available_balance=10.0)

  import logging
  with caplog.at_level(logging.WARNING, logger="backend.exchange.position_manager"):
    result = pm.calculate_position_size(
      symbol="BTCUSDT",
      side="Buy",
      entry_price=50_000.0,
      sl_price=49_000.0,
      risk_amount=500.0,
    )

  assert result["capped"] is True
  assert result["effective_risk"] < result["requested_risk"]
  assert result["risk_shortfall_usd"] > 0
  # Log must mention both numbers so a human can reason about the cap.
  msgs = " ".join(rec.message for rec in caplog.records)
  assert "requested risk" in msgs.lower() or "requested $" in msgs.lower()
  assert "actual" in msgs.lower() or "effective" in msgs.lower()


def test_shortfall_is_zero_when_not_capped():
  pm = _build_pm()
  result = pm.calculate_position_size(
    symbol="BTCUSDT",
    side="Buy",
    entry_price=50_000.0,
    sl_price=49_000.0,
    risk_amount=50.0,
  )
  # Tight lot-rounding tolerance
  assert result["risk_shortfall_usd"] < 1.0
  assert result["effective_risk"] == result["risk_amount"]


def test_risk_amount_preserved_for_backcompat():
  """CS-032 is additive; ``risk_amount`` must still equal effective_risk."""
  pm = _build_pm()
  result = pm.calculate_position_size(
    symbol="BTCUSDT",
    side="Buy",
    entry_price=50_000.0,
    sl_price=49_000.0,
    risk_amount=100.0,
  )
  assert result["risk_amount"] == result["effective_risk"]
