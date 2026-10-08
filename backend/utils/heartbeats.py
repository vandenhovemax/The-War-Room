"""File-based heartbeats for long-running background processes.

Each component that needs to be watched (candle_sync, engine_coordinator
reconciliation loop, etc.) calls `beat(name)` at the top of every cycle.
The file's mtime is the heartbeat — if it stops advancing, the component
is stuck or dead.

The `/health` endpoint reads these and reports degraded status; the
Telegram bot's existing health_check job then alerts Max.

Format: one line ISO-8601 UTC timestamp. Human-readable on disk.

Why a file (not a DB row / in-memory dict):
  - candle_sync.py is a separate process from the API. Shared memory
    would not see its beats.
  - The DB is not always healthy when we most need to detect a stuck
    engine — the filesystem is a more robust signal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BASE_DIR.parent
HEARTBEAT_DIR = _PROJECT_ROOT / "data" / "heartbeats"


def _path_for(name: str) -> Path:
  safe = "".join(c for c in name if c.isalnum() or c in ("_", "-"))
  if not safe:
    raise ValueError(f"invalid heartbeat name: {name!r}")
  return HEARTBEAT_DIR / f"{safe}.hb"


def beat(name: str) -> None:
  """Record a heartbeat for component `name`. Never raises."""
  try:
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    _path_for(name).write_text(ts, encoding="utf-8")
  except Exception as e:
    logger.warning("heartbeat write failed for %s: %s", name, e)


def read(name: str) -> datetime | None:
  """Return last heartbeat time for `name`, or None if missing/unreadable."""
  p = _path_for(name)
  if not p.exists():
    return None
  try:
    raw = p.read_text(encoding="utf-8").strip()
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
      ts = ts.replace(tzinfo=timezone.utc)
    return ts
  except Exception as e:
    logger.warning("heartbeat read failed for %s: %s", name, e)
    return None


def age_seconds(name: str) -> float | None:
  """Seconds since last beat, or None if never beaten."""
  ts = read(name)
  if ts is None:
    return None
  return (datetime.now(timezone.utc) - ts).total_seconds()


def is_stale(name: str, max_age_seconds: float) -> bool:
  """True if the heartbeat is missing or older than `max_age_seconds`."""
  age = age_seconds(name)
  return age is None or age > max_age_seconds
