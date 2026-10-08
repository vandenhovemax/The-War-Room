"""Background jobs: event polling and health monitoring."""
from __future__ import annotations

import logging
import time

from telegram.ext import ContextTypes

from telegram_bot.api_client import WarRoomAPI
from telegram_bot.formatters import format_event_notification

logger = logging.getLogger("telegram_bot")

api = WarRoomAPI()

# Config — overridden by bot.py from settings
NOTIFICATIONS_ENABLED: bool = True

# Grace window before firing the "Backend unreachable" alert. Avoids pings
# for transient blips (process restart, brief network hiccup) that recover
# within ~1 minute. If the backend recovers inside this window, NEITHER
# the unreachable nor the restored message is sent.
_BACKEND_DOWN_GRACE_SECONDS: float = 60.0

# State tracking
_last_event_id: int = 0
_backend_was_down: bool = False           # True only after grace window elapsed and alert was sent
_backend_down_since: float | None = None  # monotonic timestamp of first failed health check
_backend_was_degraded: bool = False
_backoff_seconds: float = 10.0
_MAX_BACKOFF: float = 120.0


async def poll_events(ctx: ContextTypes.DEFAULT_TYPE) -> None:
  """Poll /live/events for new events and push notifications.

  Runs via the JobQueue at the configured interval.
  """
  global _last_event_id, _backoff_seconds

  if not NOTIFICATIONS_ENABLED:
    return

  chat_id = ctx.job.data
  events = await api.get("/live/events", {"limit": 50})

  if "error" in events:
    return  # Health monitor handles backend-down notifications

  if not isinstance(events, list) or not events:
    return

  # Initialize on first run: set marker to latest event, don't spam history
  if _last_event_id == 0:
    _last_event_id = max(e.get("id", 0) for e in events)
    return

  # Find new events (ID > last seen)
  new_events = [e for e in events if e.get("id", 0) > _last_event_id]
  if not new_events:
    return

  # Update marker
  _last_event_id = max(e.get("id", 0) for e in new_events)

  # Format and send notifications
  messages = []
  for event in sorted(new_events, key=lambda e: e.get("id", 0)):
    msg = format_event_notification(event)
    if msg:
      messages.append(msg)

  if not messages:
    return

  # Batch into one message if multiple
  if len(messages) <= 5:
    text = "\n\n".join(messages)
  else:
    text = f"{len(messages)} events:\n\n" + "\n".join(f"- {m}" for m in messages[:10])
    if len(messages) > 10:
      text += f"\n... and {len(messages) - 10} more"

  try:
    await ctx.bot.send_message(chat_id=chat_id, text=text)
  except Exception as e:
    logger.error("Failed to send event notification: %s", e)


async def health_check(ctx: ContextTypes.DEFAULT_TYPE) -> None:
  """Poll /health periodically. Alert on backend down/restored AND on
  component-level degradation (candle_sync stuck, engine stuck, etc.).

  Edge-triggered: only fires on state transitions, not every poll.

  Runs via the JobQueue at the configured interval.
  """
  global _backend_was_down, _backend_down_since, _backend_was_degraded, _backoff_seconds

  if not NOTIFICATIONS_ENABLED:
    return

  chat_id = ctx.job.data
  result = await api.get("/health")
  is_down = "error" in result

  if is_down:
    now = time.monotonic()
    # First failure → start the grace timer, don't alert yet
    if _backend_down_since is None:
      _backend_down_since = now
      logger.info("Backend health check failed — entering %ss grace window", _BACKEND_DOWN_GRACE_SECONDS)
      return
    # Inside the grace window → still don't alert
    if not _backend_was_down and (now - _backend_down_since) < _BACKEND_DOWN_GRACE_SECONDS:
      return
    # Grace window elapsed and we haven't alerted yet → fire the alert
    if not _backend_was_down:
      _backend_was_down = True
      try:
        await ctx.bot.send_message(
          chat_id=chat_id,
          text=f"Backend unreachable: {result.get('error', '?')}",
        )
      except Exception as e:
        logger.error("Failed to send health alert: %s", e)
    return

  # Backend is reachable
  if _backend_was_down:
    # Alert was sent earlier → send the recovery message
    _backend_was_down = False
    _backend_down_since = None
    try:
      await ctx.bot.send_message(chat_id=chat_id, text="Backend restored.")
    except Exception as e:
      logger.error("Failed to send recovery alert: %s", e)
  elif _backend_down_since is not None:
    # Recovered inside the grace window → silently reset, no messages
    logger.info("Backend recovered within grace window — no alert sent")
    _backend_down_since = None

  # Component-level degradation (only check when backend is reachable).
  if is_down:
    return

  is_degraded = result.get("status") == "degraded"

  if is_degraded and not _backend_was_degraded:
    _backend_was_degraded = True
    stale = [
      f"{name} ({info.get('age_seconds', '?')}s old, max {info.get('max_age_seconds', '?')}s)"
      for name, info in (result.get("components") or {}).items()
      if info.get("status") == "stale"
    ]
    msg = "Backend degraded — stale heartbeat:\n" + "\n".join(f"- {s}" for s in stale)
    try:
      await ctx.bot.send_message(chat_id=chat_id, text=msg)
    except Exception as e:
      logger.error("Failed to send degraded alert: %s", e)

  elif not is_degraded and _backend_was_degraded:
    _backend_was_degraded = False
    try:
      await ctx.bot.send_message(chat_id=chat_id, text="Backend recovered — all components healthy.")
    except Exception as e:
      logger.error("Failed to send recovery alert: %s", e)
