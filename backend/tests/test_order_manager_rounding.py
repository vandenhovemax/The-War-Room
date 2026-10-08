"""Order manager lot/tick rounding regression tests.

Pins the contract for ``OrderManager._round_qty`` and ``_round_price``:
  - Round DOWN (never over-size vs. available margin or exchange cap)
  - Never lose a full lot to float artifacts (e.g. 0.3 // 0.1 → 2.0)
  - Return integer string when step >= 1, else a decimal string with
    the same number of decimals as the step.

CS-037 regression: before the Decimal fix, ``_round_qty(0.3, 0.1)``
returned ``"0.2"`` — one whole lot (33%) short of the intended size.
"""
from __future__ import annotations

from backend.exchange.order_manager import OrderManager


# --- CS-037 regression cases -------------------------------------------

def test_round_qty_canonical_cs037_case():
  """0.3 // 0.1 == 2.0 in float → bug dropped a full lot. Now: full 0.3."""
  assert OrderManager._round_qty(0.3, 0.1) == "0.3"


def test_round_qty_btc_style_003_over_0001():
  """Smaller-scale version of the same float artifact."""
  assert OrderManager._round_qty(0.003, 0.001) == "0.003"


def test_round_qty_07_over_01():
  assert OrderManager._round_qty(0.7, 0.1) == "0.7"


# --- General rounding contract -----------------------------------------

def test_round_qty_rounds_down():
  """Partial lot excess is discarded; never over-sizes."""
  assert OrderManager._round_qty(0.35, 0.1) == "0.3"
  assert OrderManager._round_qty(0.39, 0.1) == "0.3"


def test_round_qty_exact_multiple_preserved():
  assert OrderManager._round_qty(1.0, 0.001) == "1.000"
  assert OrderManager._round_qty(2.5, 0.5) == "2.5"


def test_round_qty_sub_lot_rounds_to_zero():
  assert OrderManager._round_qty(0.0005, 0.001) == "0.000"


def test_round_qty_integer_lot_returns_integer_string():
  assert OrderManager._round_qty(7.9, 1.0) == "7"
  assert OrderManager._round_qty(1234.7, 1.0) == "1234"


def test_round_qty_preserves_decimal_count_from_lot_size():
  assert OrderManager._round_qty(0.3, 0.001) == "0.300"
  assert OrderManager._round_qty(0.125, 0.0001) == "0.1250"


# --- _round_price: same contract ---------------------------------------

def test_round_price_cs037_case():
  assert OrderManager._round_price(0.3, 0.1) == "0.3"


def test_round_price_rounds_down():
  # str(0.10) == "0.1" → 1 decimal digit in output
  assert OrderManager._round_price(50000.27, 0.10) == "50000.2"


def test_round_price_small_tick_stablecoin():
  assert OrderManager._round_price(1.2345, 0.0001) == "1.2345"
  assert OrderManager._round_price(1.23456, 0.0001) == "1.2345"


def test_round_price_integer_tick():
  assert OrderManager._round_price(100.7, 1.0) == "100"
