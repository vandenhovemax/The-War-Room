"""CS-035: ``load_trades_lightweight`` must drop trades whose
``entry_time`` would end up ``None``.

The fix is surgical: after aggregating legs of a multi-leg trade,
if every leg lacks an entry timestamp, ``min(..., default=None)``
yields ``None`` — the loader then skips that trade with a debug log.

Without this filter, ``None`` leaks into every downstream consumer:

  * WFAS windowing: ``entry_time < window_start`` raises
    ``TypeError`` (None vs datetime).
  * Session-filter execution path: ``t.entry_time.hour`` crashes
    with ``AttributeError: 'NoneType' has no attribute 'hour'``.
  * Equity-curve build: ``pd.Timestamp(None)`` coerces to ``NaT``,
    silently shifting the trade to the start of the curve.

This test locks the invariant at source level so a revert surfaces
immediately in CI, and also asserts the control-flow is an explicit
``continue``/``skip``, not a ``fill with sentinel`` escape hatch.
"""
from __future__ import annotations

import inspect

from backend.analysis import batch_selection


def test_loader_skips_entry_time_none_rows_at_source_level():
  src = inspect.getsource(batch_selection.load_trades_lightweight)

  # The fix must explicitly check for entry_t is None.
  assert "entry_t is None" in src, (
    "CS-035 REGRESSION: load_trades_lightweight no longer checks "
    "``entry_t is None`` after multi-leg aggregation. Trades with "
    "legacy rows lacking entry timestamps will return "
    "LightTrade(entry_time=None), which crashes the WFAS exec-filter "
    "path with AttributeError: 'NoneType' has no attribute 'hour'."
  )

  # And it must skip, not fill. A sentinel fill (e.g.
  # ``default=pd.Timestamp(0)``) would silently move the trade to
  # 1970 and WFAS buckets it into the first window, corrupting the
  # result. Check for ``continue`` in the ~10 lines following the
  # guard.
  tail = src.split("entry_t is None", 1)[1][:600]
  assert "continue" in tail, (
    "CS-035 REGRESSION: the ``entry_t is None`` guard no longer "
    "skips with ``continue``. If the loader back-fills with a sentinel "
    "timestamp, the trade silently lands in the earliest WFAS window "
    "and corrupts IS/OOS boundaries for every downstream computation."
  )


def test_loader_does_not_emit_lighttrade_with_none_entry_time():
  """Behavioural bottom line: no code path may produce a LightTrade
  whose entry_time is None. Asserted by scanning the loader source
  for any ``LightTrade(`` constructor whose entry_time is plainly
  populated from ``entry_t`` AFTER the None-guard.
  """
  src = inspect.getsource(batch_selection.load_trades_lightweight)
  # The None-guard must come textually before the LightTrade
  # constructor it protects.
  guard_pos = src.find("entry_t is None")
  ctor_pos = src.find("LightTrade(")
  assert guard_pos != -1 and ctor_pos != -1, (
    "CS-035 FIXTURE BROKEN: cannot locate guard or constructor."
  )
  assert guard_pos < ctor_pos, (
    "CS-035 REGRESSION: the ``entry_t is None`` guard was moved AFTER "
    "the LightTrade(...) constructor. The guard no longer protects "
    "the emitted tuple — callers can receive entry_time=None again."
  )
