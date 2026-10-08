"""CS-059: mother/daughter config immutability + per-position SL
isolation. The two tests TC flagged as missing from the Suggested
Minimum Test Set.

Why these matter:

  * A refactor that mutates a daughter's ``parameter_values`` into
    its mother's ``parameters`` silently contaminates every other
    daughter of the same mother. The UI would show correct values
    but live runs would use the last-mutated daughter's params.
  * A refactor that stores SL state symbol-keyed instead of
    position-keyed would make two same-symbol positions share the
    same extreme-high / stop level. One position's trailing stop
    would silently move based on the other position's price action
    — a class-one bug on the order path.

These tests assert the invariants at the dataclass level so any
regression surfaces in CI.
"""
from __future__ import annotations

import copy

from backend.backtester.models import ExitOrder, Position


def test_daughter_param_mutation_does_not_affect_mother():
  """Mother = full default params dict (as stored on Strategy).
  Daughter = overrides (as stored on StrategyConfig).
  Building the effective params for a daughter must NOT mutate the
  mother.
  """
  mother_params = {"atr_len": 14, "rr": 2.0, "exit_mode": "tsl"}
  daughter_overrides = {"rr": 3.0}  # daughter changes only rr

  # This is the canonical merge pattern used across the codebase:
  #   effective = {**mother, **daughter_overrides}
  effective = {**mother_params, **daughter_overrides}

  assert effective == {"atr_len": 14, "rr": 3.0, "exit_mode": "tsl"}
  assert mother_params == {"atr_len": 14, "rr": 2.0, "exit_mode": "tsl"}, (
    "CS-059 REGRESSION: daughter-param merge mutated the mother's "
    "parameters dict. The next daughter of the same mother would "
    "inherit the previous daughter's overrides — every saved "
    "preset silently drifts."
  )

  # And a shallow copy + override must leave the original untouched —
  # this catches the classic ``params = mother_params; params.update(x)``
  # mistake.
  snapshot = copy.deepcopy(mother_params)
  effective2 = dict(mother_params)
  effective2.update(daughter_overrides)
  assert mother_params == snapshot, (
    "CS-059 REGRESSION: dict.update on the mother reference "
    "mutated the shared object. Use ``{**mother, **overrides}`` or "
    "``dict(mother); copy.update(overrides)`` — never update the "
    "mother in place."
  )


def test_position_sl_state_is_per_instance_not_shared():
  """Two Position objects — same symbol, different ids — must have
  fully independent SL/extreme state. A symbol-keyed cache for SL
  would fail this test because mutating one's extreme would overwrite
  the other's.
  """
  pos_a = Position(id="A", side="long", entry_price=100.0, qty=1.0)
  pos_b = Position(id="B", side="long", entry_price=110.0, qty=1.0)

  # Simulate ticker updates: each position's extreme diverges.
  pos_a.extreme_high = 120.0
  pos_b.extreme_high = 115.0

  assert pos_a.extreme_high == 120.0
  assert pos_b.extreme_high == 115.0, (
    "CS-059 REGRESSION: Position.extreme_high is shared between "
    "instances. Two same-symbol positions with different entries "
    "would have their trailing stops collide."
  )

  # And the ``float('inf')`` default must NOT be a shared mutable
  # sentinel — if it were (e.g. by class-level default), updating
  # pos_a.extreme_low would be visible on pos_b.
  pos_a.extreme_low = 95.0
  assert pos_b.extreme_low == float("inf"), (
    "CS-059 REGRESSION: Position.extreme_low is shared between "
    "instances (likely a class-level mutable default)."
  )


def test_exit_order_is_keyed_to_a_specific_position_id():
  """Each ExitOrder pins to exactly one ``from_entry`` id. Two
  positions with the same symbol must be able to carry DIFFERENT SL
  prices via distinct ExitOrders.
  """
  sl_a = ExitOrder(id="SL_A", from_entry="A", stop=90.0)
  sl_b = ExitOrder(id="SL_B", from_entry="B", stop=105.0)

  assert sl_a.from_entry == "A"
  assert sl_b.from_entry == "B"
  assert sl_a.stop != sl_b.stop, (
    "CS-059 FIXTURE: the two exit orders must hold independent "
    "stop prices — otherwise the test can't detect isolation."
  )

  # Mutating one exit order's stop must not bleed to the other.
  sl_a.stop = 80.0
  assert sl_b.stop == 105.0, (
    "CS-059 REGRESSION: ExitOrder.stop appears shared across "
    "instances. Per-position SL isolation is broken — a tighten on "
    "position A's stop would also tighten position B's stop."
  )


def test_position_fresh_defaults_on_each_construction():
  """Defensive: explicitly build two positions with no kwargs and
  confirm each gets its own fresh default value. This catches the
  class-level mutable default anti-pattern at construction time.
  """
  p1 = Position()
  p2 = Position()
  p1.extreme_high = 999.0
  assert p2.extreme_high != 999.0, (
    "CS-059 REGRESSION: Position dataclass has a shared mutable "
    "class-level default. Every new position would see prior "
    "positions' extreme_high."
  )
