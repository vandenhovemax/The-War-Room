"""CS-041: session filter activation must honour the enabled-flag.

Portfolio rows persist ``session_filter_exclude_hours`` and
``session_filter_exclude_days`` even when the user toggles the feature
off. The old code consulted the lists whenever they were non-empty,
which contradicted user intent — a disabled filter with leftover
``[21, 22, 23]`` still silently excluded those NYT hours from live
trading.

The fix: the boolean ``session_filter_enabled`` is authoritative.
Only consult the lists when ``enabled is True``.

This test locks the invariant at the source level (so an accidental
revert to ``if _sf_enabled or _excl_h_raw:`` trips the build).
"""
from __future__ import annotations

import inspect

from backend.exchange.strategy_executor import StrategyExecutor


def test_activate_portfolio_respects_session_filter_enabled():
  """Source inspection: the enabled-flag must gate the exclude-hours
  set construction; ORing with the list-non-empty check is a
  regression.
  """
  src = inspect.getsource(StrategyExecutor)

  # The canonical gate: only populate the set when enabled is True.
  # Expect either
  #   {int(h) for h in _excl_h_raw} if _sf_enabled else set()
  # or an equivalent guard block.
  has_correct_guard = (
    "if _sf_enabled else set()" in src
    or "if _sf_enabled:" in src
  )
  assert has_correct_guard, (
    "CS-041 REGRESSION: StrategyExecutor no longer gates "
    "session_filter on the enabled-flag. A user who disables the "
    "filter but left hours populated from a prior save will still "
    "have those hours excluded — silent contradiction of intent."
  )

  # Specifically forbid the old OR-pattern that caused the bug.
  bad_pattern = "if (_sf_enabled or _excl_h_raw"
  assert bad_pattern not in src, (
    "CS-041 REGRESSION: the OR pattern `_sf_enabled or _excl_h_raw` "
    "is back. That contradicts the user-controlled enable flag — "
    "non-empty leftover hours activate even when the user disabled "
    "the filter."
  )
