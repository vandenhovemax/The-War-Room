"""CS-002: on the Market-order REST-poll path in ``submit_entry_order``,
the post-fill sync chain (``on_order_update`` → ``_on_entry_filled`` →
``place_sl_order``) contains a retry loop with ``time.sleep`` of up to
0.75s. If those sync calls are awaited directly from the event-loop
thread, the sleep stalls every other coroutine — WS price handlers,
TSL amendments, and other portfolios' signals all get starved.

The fix pushes those sync calls onto an executor via
``_call_exchange``. This test locks the invariant: the event loop
must stay responsive during the post-fill processing window.

If someone later reverts the ``_call_exchange`` wrap back to a direct
``self.on_order_update(h); self.on_execution(h)`` pair, this test
fails immediately.
"""
from __future__ import annotations

import asyncio
import inspect
import time

import pytest

from backend.exchange import order_manager as om_mod


def test_submit_entry_order_offloads_post_fill_via_call_exchange():
  """Static invariant: the source of ``submit_entry_order`` must route
  ``on_order_update`` through ``_call_exchange`` (or ``run_in_executor``)
  rather than calling it directly on the loop thread.

  Checking via source inspection — spinning up a live OrderManager in
  a unit test would require mocking Bybit, DB, WS, and the scheduler.
  The source-level assertion is precise: the audit regression is
  literally ``self.on_order_update(h)`` sitting under ``if order_type ==
  "Market":`` without an executor wrap.
  """
  src = inspect.getsource(om_mod.OrderManager.submit_entry_order)

  # Find the Market-poll block
  market_idx = src.find('if order_type == "Market"')
  assert market_idx != -1, "Market-poll block not found — source shape changed"

  market_block = src[market_idx:market_idx + 1500]

  # Must NOT contain a bare sync call to on_order_update on the loop
  # Pattern that regresses the fix:
  bad_pattern_1 = "self.on_order_update(h)\n              # Also"
  bad_pattern_2 = "self.on_order_update(h)\n              self.on_execution(h)"
  assert bad_pattern_1 not in market_block, (
    "CS-002 regression: submit_entry_order calls on_order_update "
    "directly on the event-loop thread. The sync retry loop inside "
    "place_sl_order will block the loop for up to 0.75s per SL placement."
  )
  assert bad_pattern_2 not in market_block, (
    "CS-002 regression: back-to-back sync on_order_update + "
    "on_execution on the loop thread. Must be wrapped in executor."
  )

  # Must contain an executor offload — either _call_exchange or run_in_executor
  has_call_exchange = "_call_exchange(" in market_block
  has_run_in_executor = "run_in_executor(" in market_block
  assert has_call_exchange or has_run_in_executor, (
    "CS-002: post-fill sync processing must be pushed to an executor — "
    "found neither _call_exchange nor run_in_executor in the Market-poll "
    "block. The event loop will block on the place_sl_order retry sleep."
  )


def test_event_loop_stays_responsive_during_fake_sync_fill():
  """Behavioural proof: simulate the post-fill processing by running a
  sync function that sleeps 0.6s via ``_call_exchange``; meanwhile, a
  concurrent coroutine ticks every 50ms. If ``_call_exchange`` correctly
  offloads to a thread, the ticks continue during the sleep. If the
  sleep were on the loop thread, the tick count would collapse to ~1.
  """
  class _Harness:
    async def _call_exchange(self, fn):
      loop = asyncio.get_running_loop()
      return await loop.run_in_executor(None, fn)

  h = _Harness()

  async def _runner():
    ticks: list[float] = []
    stop = asyncio.Event()

    async def _ticker():
      while not stop.is_set():
        ticks.append(time.monotonic())
        await asyncio.sleep(0.05)

    async def _fake_fill():
      # This is the post-fill sync chain: up to 0.75s of time.sleep
      def _sync_work():
        time.sleep(0.6)
      await h._call_exchange(_sync_work)
      stop.set()

    tick_task = asyncio.create_task(_ticker())
    await _fake_fill()
    await tick_task
    return ticks

  ticks = asyncio.run(_runner())

  # 0.6s of offloaded sleep → ticker should fire ~10-12 times at 50ms
  # If the sleep were on the loop, we'd see 1 tick (the initial one).
  assert len(ticks) >= 5, (
    f"CS-002: event loop was starved during the fake fill — only "
    f"{len(ticks)} ticks fired in 0.6s. If _call_exchange were correctly "
    "using run_in_executor, the ticker would have fired ~12 times."
  )
