"""CS-021: async routes must not hand-roll an event loop.

``asyncio.new_event_loop()`` + ``loop.run_until_complete(...)`` +
``loop.close()`` is a foot-gun:

  * If an exception escapes between ``new_event_loop()`` and the
    ``finally``, the loop is leaked.
  * If the function is ever called from a context where a loop is
    already running, ``run_until_complete`` deadlocks silently (rather
    than raising).
  * Closing the loop does not cancel pending tasks — they can leak
    into the next run.

``asyncio.run(coro)`` handles all three correctly: it cancels
pending tasks, closes the loop in ``finally``, and raises
``RuntimeError`` cleanly if called from inside a running loop.

This test asserts that the three audit-flagged call sites no longer
contain the manual pattern.
"""
from __future__ import annotations

import inspect

from backend.api.routes import batch_backtest as bb_mod


def test_batch_backtest_does_not_hand_roll_event_loops():
  """Source inspection: ``batch_backtest.py`` must not contain the
  ``asyncio.new_event_loop() ... run_until_complete ... loop.close()``
  pattern anywhere.
  """
  src = inspect.getsource(bb_mod)

  # Forbid the foot-gun.
  assert "asyncio.new_event_loop()" not in src, (
    "CS-021 REGRESSION: batch_backtest.py reintroduces "
    "``asyncio.new_event_loop()``. Prefer ``asyncio.run(coro)`` — it "
    "handles cleanup on exception and raises RuntimeError cleanly if "
    "ever called from inside a running loop instead of deadlocking."
  )
  assert ".run_until_complete(" not in src, (
    "CS-021 REGRESSION: batch_backtest.py reintroduces "
    "``loop.run_until_complete(...)``. Prefer ``asyncio.run(coro)``."
  )

  # Require the canonical form (we know three sites use it post-fix).
  call_count = src.count("asyncio.run(")
  assert call_count >= 3, (
    f"CS-021 REGRESSION: batch_backtest.py only calls asyncio.run "
    f"{call_count} times — expected at least 3 (two in "
    "``_download_missing_tf_data`` and one in the refresh-download "
    "background worker). Has a site been reverted to the manual "
    "new_event_loop / run_until_complete / close pattern?"
  )
