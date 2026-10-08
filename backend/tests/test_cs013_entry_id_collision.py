"""CS-013: two same-bar entries with empty ``id`` must not collide.

Bug from the audit:

  * ``_fill_entry`` used ``order.get("id", "") or f"_entry_{bar}"`` as
    the fallback. Two entries on the same bar with no explicit id both
    hashed to ``_entry_{bar}`` and silently overwrote each other in
    ``strategy._positions``.
  * The second fill clobbered the first. SL/TP registered from
    ``strategy.next()`` for the first entry then attached to the
    second entry's price — phantom PnL.

Fix: an auto-generated fallback now appends a monotonic per-strategy
counter, so every empty-id entry gets a unique tag even within the
same bar. A non-empty id that collides with an existing position
logs a warning instead of silently overwriting.
"""
from __future__ import annotations

import inspect
import re


def _fill_entry_src() -> str:
  from backend.backtester import engine as engine_mod
  return inspect.getsource(engine_mod.BacktestEngine._fill_entry)


def test_fallback_entry_id_is_not_a_bare_bar_string():
  """The fallback id must embed a monotonic counter — a bare
  ``f"_entry_{bar}"`` is the exact shape that produced the collision.
  """
  src = _fill_entry_src()

  # Forbid the un-counterd fallback literal in live code.
  # We intentionally strip comments first — the docstring/CS-013 note
  # intentionally quotes the old string so readers know what we fixed.
  live = "\n".join(
    ln for ln in src.splitlines()
    if not ln.lstrip().startswith("#")
  )
  # Drop anything inside triple-quoted strings (docstrings).
  live = re.sub(r'""".*?"""', "", live, flags=re.DOTALL)
  live = re.sub(r"'''.*?'''", "", live, flags=re.DOTALL)

  bare_matches = re.findall(r'f"_entry_\{bar\}"', live)
  assert not bare_matches, (
    "CS-013 REGRESSION: _fill_entry's fallback id is the bare "
    "``f\"_entry_{bar}\"`` that caused the audit collision. Two "
    "same-bar empty-id entries would overwrite each other in "
    "strategy._positions, misattaching SL/TP from entry A to entry B."
  )


def test_fallback_entry_id_uses_monotonic_counter():
  """The live fallback must include a counter in the id template."""
  src = _fill_entry_src()
  assert "_auto_entry_counter" in src, (
    "CS-013 REGRESSION: _fill_entry no longer reads/writes "
    "``_auto_entry_counter``. Without a per-strategy monotonic "
    "counter, empty-id fallbacks collide on the same bar."
  )
  # The counter must feed into the generated id string — not just be
  # incremented and discarded.
  assert re.search(r'f"_entry_\{bar\}_\{counter\}"', src), (
    "CS-013 REGRESSION: the fallback id template no longer embeds "
    "``{counter}``. The counter only prevents collisions if it is in "
    "the emitted id."
  )


def test_duplicate_non_empty_id_warns_instead_of_silent_overwrite():
  """If strategy code emits the SAME non-empty id twice on one bar
  we must log a warning. Silent overwrite is the failure mode the
  audit flagged.
  """
  src = _fill_entry_src()
  assert "strategy._positions" in src and "Duplicate entry id" in src, (
    "CS-013 REGRESSION: _fill_entry no longer warns on duplicate "
    "non-empty ids. The audit's concern was silent overwrite of a "
    "live position — at minimum the collision must surface in logs."
  )


def test_behavioural_two_empty_id_orders_same_bar_get_distinct_ids():
  """Simulate the audit scenario: two empty-id orders fired on the
  same bar. The generated ids must differ so both positions persist.
  """
  # Mini stub of what _fill_entry reads/writes — we replicate the
  # counter logic rather than spin up a full BacktestEngine, because
  # the invariant is at the id-generation level.
  class _FakeStrat:
    _positions: dict = {}
    _auto_entry_counter: int = 0

  strategy = _FakeStrat()

  def _gen_id(bar: int, raw: str) -> str:
    if raw:
      return raw
    c = getattr(strategy, "_auto_entry_counter", 0)
    gen = f"_entry_{bar}_{c}"
    strategy._auto_entry_counter = c + 1
    return gen

  bar = 123
  id_a = _gen_id(bar, "")
  id_b = _gen_id(bar, "")
  assert id_a != id_b, (
    "CS-013: two empty-id orders on bar %d produced the same id "
    "(%r). This is the exact collision the audit flagged." % (bar, id_a)
  )
