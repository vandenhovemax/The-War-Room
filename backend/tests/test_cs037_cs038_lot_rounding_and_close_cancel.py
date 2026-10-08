"""CS-037 & CS-038: two surgical live-order invariants.

**CS-037** — ``PositionManager._floor_to_step`` and
``OrderManager._round_qty`` / ``_round_price`` must not drop a full lot
to float-division imprecision. ``math.floor(0.3 / 0.1) == 2.0`` because
``0.3 / 0.1 == 2.9999...`` in IEEE-754 — the live qty silently under-
sizes by one lot, under-risking every trade.

**CS-038** — ``_cancel_other_exits`` must include ``role == "close"``.
When an SL fires at the same moment an emergency ``market_close`` is
in-flight, both can execute and the position flips through zero (double-
close → unintended reverse).
"""
from __future__ import annotations

import inspect

from backend.exchange.order_manager import OrderManager
from backend.exchange.position_manager import PositionManager


# ── CS-037 ────────────────────────────────────────────────────────────

def test_position_manager_floor_to_step_does_not_drop_lot():
  """Classic float trap: 0.3 / 0.1 == 2.9999..., math.floor → 2.0 →
  result 0.2 when the intended floor is 0.3.
  """
  result = PositionManager._floor_to_step(0.3, 0.1)
  assert result == 0.3, (
    f"CS-037 REGRESSION: _floor_to_step(0.3, 0.1) returned {result}, "
    "dropping a full lot. Live position sizing under-risks every trade "
    "that happens to hit this float boundary."
  )


def test_position_manager_floor_to_step_handles_other_float_traps():
  """A few other values that bite float division."""
  # 0.6 / 0.2 == 2.9999... on some platforms
  assert PositionManager._floor_to_step(0.6, 0.2) == 0.6
  # 0.9 / 0.3 may also drop
  assert PositionManager._floor_to_step(0.9, 0.3) == 0.9
  # 1.2 / 0.4
  assert PositionManager._floor_to_step(1.2, 0.4) == 1.2


def test_position_manager_floor_to_step_still_floors_non_aligned():
  """Values that legitimately need flooring still floor."""
  # 0.35 should floor to 0.3 (step 0.1)
  result = PositionManager._floor_to_step(0.35, 0.1)
  assert 0.29 <= result <= 0.30, (
    f"_floor_to_step(0.35, 0.1) returned {result} — expected ~0.3"
  )


def test_order_manager_round_qty_does_not_drop_lot():
  """_round_qty returns a string; the numeric value must match _floor_to_step."""
  # 0.3 with lot_size 0.1 — string output
  out = OrderManager._round_qty(0.3, 0.1)
  assert float(out) == 0.3, (
    f"CS-037: OrderManager._round_qty(0.3, 0.1) returned {out!r} — "
    "same lot-drop bug as _floor_to_step."
  )


def test_order_manager_round_price_does_not_drop_tick():
  """Same guarantee on tick rounding for SL/TP prices."""
  out = OrderManager._round_price(0.3, 0.1)
  assert float(out) == 0.3


# ── CS-038 ────────────────────────────────────────────────────────────

def test_cancel_other_exits_includes_close_role():
  """_cancel_other_exits must enumerate ``close`` in its role filter.
  Otherwise an in-flight emergency close can race with an SL fire and
  both execute → double-close.
  """
  src = inspect.getsource(OrderManager._cancel_other_exits)

  # Filter line must mention 'close'
  assert '"close"' in src or "'close'" in src, (
    "CS-038 REGRESSION: _cancel_other_exits no longer references "
    "role 'close'. An in-flight market_close can race an SL fire and "
    "double-close the position. Re-add 'close' to the role.in_() list."
  )
  # Sanity: the other exit roles should still be there
  for role in ("sl", "tp"):
    assert f'"{role}"' in src or f"'{role}'" in src, (
      f"CS-038: _cancel_other_exits no longer references role "
      f"'{role}'. The SL/TP cancellation path has regressed."
    )
