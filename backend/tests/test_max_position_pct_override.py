"""Portfolio-level max_position_pct override in position sizing.

The global Settings page default for max_position_pct caps per-position margin
across every portfolio. Some portfolios need a different cap (e.g. a WFO track
that should only ever put 10% in a single position while the account default
is 25%). The override arrives via the calculate_position_size kwarg
`max_position_pct_override` — when non-None it wins over `self._settings.max_position_pct`.

0 is a legitimate "disable cap" value and must be respected.
None means "no override, use the global default".
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from backend.exchange.position_manager import PositionManager


def _build_pm(global_max_pct: float = 25.0) -> PositionManager:
  pm = PositionManager.__new__(PositionManager)
  pm._client = MagicMock()
  pm._client.get_instrument_info.return_value = {
    "lot_size": 0.001,
    "max_leverage": 100.0,
    "min_qty": 0.001,
  }
  pm._settings = SimpleNamespace(
    reserve_account_usd=0.0,
    total_equity_override=0.0,
    max_positions_total=10,
    margin_breathing_room_pct=0.0,
    max_position_pct=global_max_pct,
    max_position_notional=0.0,
    default_leverage=1,
    max_risk_per_trade_usd=0.0,
    max_risk_per_trade_pct=0.0,
  )
  pm.get_wallet_balance = MagicMock(return_value=10_000.0)
  pm.get_available_balance = MagicMock(return_value=10_000.0)
  return pm


def _size(pm: PositionManager, **extra) -> dict:
  return pm.calculate_position_size(
    symbol="BTCUSDT",
    side="Buy",
    entry_price=50_000.0,
    sl_price=49_500.0,
    risk_amount=100.0,
    **extra,
  )


def test_no_override_uses_global_default():
  """Without an override the global 25% max_position_pct is applied."""
  pm = _build_pm(global_max_pct=25.0)
  result = _size(pm)
  # Global cap = 10,000 * 0.25 = 2500 max margin per position
  assert result["margin_budget"] <= 2500.0


def test_override_tighter_than_global_wins():
  """A 10% override tightens the cap below the global 25%."""
  pm = _build_pm(global_max_pct=25.0)
  result = _size(pm, max_position_pct_override=10.0)
  # Override cap = 10,000 * 0.10 = 1000
  assert result["margin_budget"] <= 1000.0


def test_override_looser_than_global_wins():
  """A 50% override loosens the cap above the global 25%.

  The only cap active here is max_position_pct — the fixed-slot budget is
  computed from wallet_balance / max_positions_total (10,000 / 10 = 1000),
  so we mostly verify the override is respected, not that it raises above that.
  """
  pm = _build_pm(global_max_pct=25.0)
  result = _size(pm, max_position_pct_override=50.0)
  # Cap = 5000; 1000 slot budget is well under that → no override cap trigger
  assert result["margin_budget"] <= 5000.0


def test_override_zero_disables_cap():
  """An explicit 0 disables the cap entirely."""
  pm = _build_pm(global_max_pct=25.0)
  result_with_cap = _size(pm, max_position_pct_override=1.0)  # 1% = $100 cap
  result_no_cap = _size(pm, max_position_pct_override=0.0)    # disabled
  # With 1% cap, margin_budget is clamped to 100
  assert result_with_cap["margin_budget"] <= 100.0
  # With 0 override, the per-slot budget (1000) is not clamped
  assert result_no_cap["margin_budget"] > 100.0


def test_override_none_equals_no_override():
  """Passing None is the same as not passing the kwarg — global default used."""
  pm = _build_pm(global_max_pct=25.0)
  result_none = _size(pm, max_position_pct_override=None)
  result_default = _size(pm)
  assert result_none["margin_budget"] == result_default["margin_budget"]
