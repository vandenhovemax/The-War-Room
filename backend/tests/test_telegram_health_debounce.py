"""Regression test for the Telegram bot's "Backend unreachable" grace window.

User pain point: a brief blip (process restart, network hiccup) was producing
two back-to-back TG messages within ~1 minute:
  - "Backend unreachable: ..."
  - "Backend restored."

Fix: require the backend to fail health checks for >= 60 seconds before
sending the unreachable alert. If it recovers inside the grace window,
neither message fires.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram_bot import notifications


def _reset_state():
  notifications._backend_was_down = False
  notifications._backend_down_since = None
  notifications._backend_was_degraded = False


def _make_ctx(send_mock: AsyncMock) -> MagicMock:
  ctx = MagicMock()
  ctx.bot.send_message = send_mock
  ctx.job.data = 12345  # chat_id
  return ctx


@pytest.mark.asyncio
async def test_brief_blip_inside_grace_window_sends_no_messages():
  """Backend goes down at t=0, recovers at t=30s (< 60s grace) → ZERO messages.
  This is the exact scenario from the user's report."""
  _reset_state()
  send = AsyncMock()
  ctx = _make_ctx(send)

  # First poll: backend is down (process restarting)
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"error": "down"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=0.0):
      await notifications.health_check(ctx)
  assert send.await_count == 0, "Should NOT send alert on first failed check"
  assert notifications._backend_down_since == 0.0
  assert notifications._backend_was_down is False

  # Second poll 30s later: backend recovered
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"status": "ok"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=30.0):
      await notifications.health_check(ctx)

  assert send.await_count == 0, "Should NOT send 'restored' since 'unreachable' was never sent"
  assert notifications._backend_down_since is None
  assert notifications._backend_was_down is False


@pytest.mark.asyncio
async def test_sustained_outage_past_grace_sends_unreachable_then_restored():
  """Backend goes down and stays down past 60s → sends unreachable.
  Then recovers → sends restored."""
  _reset_state()
  send = AsyncMock()
  ctx = _make_ctx(send)

  # t=0: first failure
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"error": "down"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=0.0):
      await notifications.health_check(ctx)
  assert send.await_count == 0

  # t=70s: still down (past grace) → should fire unreachable
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"error": "down"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=70.0):
      await notifications.health_check(ctx)
  assert send.await_count == 1
  call_text = send.await_args.kwargs["text"]
  assert "Backend unreachable" in call_text

  # t=130s: still down → should NOT re-fire (edge-triggered)
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"error": "down"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=130.0):
      await notifications.health_check(ctx)
  assert send.await_count == 1, "Should not re-send unreachable on repeated failures"

  # t=180s: recovered → should fire restored
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"status": "ok"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=180.0):
      await notifications.health_check(ctx)
  assert send.await_count == 2
  assert send.await_args.kwargs["text"] == "Backend restored."
  assert notifications._backend_was_down is False
  assert notifications._backend_down_since is None


@pytest.mark.asyncio
async def test_recovery_then_new_outage_works_independently():
  """After a full down-and-recovered cycle, a NEW outage should also
  honor the grace window (no leftover state poisoning the next cycle)."""
  _reset_state()
  send = AsyncMock()
  ctx = _make_ctx(send)

  # First cycle: full outage (down past grace, then recovers)
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"error": "down"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=0.0):
      await notifications.health_check(ctx)
    with patch("telegram_bot.notifications.time.monotonic", return_value=70.0):
      await notifications.health_check(ctx)
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"status": "ok"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=100.0):
      await notifications.health_check(ctx)
  assert send.await_count == 2  # unreachable + restored

  # Second cycle: brief blip again, should NOT alert
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"error": "down"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=200.0):
      await notifications.health_check(ctx)
  with patch.object(notifications.api, "get", new=AsyncMock(return_value={"status": "ok"})):
    with patch("telegram_bot.notifications.time.monotonic", return_value=210.0):
      await notifications.health_check(ctx)

  assert send.await_count == 2, "Brief blip in second cycle should produce no new messages"
