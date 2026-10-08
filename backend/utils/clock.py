"""Deterministic clock provider for IS/OOS boundary math.

Per AUDIT_OPUS_02 W01/W02/B06/X03: several IS-boundary sites read
`date.today()` directly, making optimizer output non-reproducible between
conversations (different days → different final windows). Tests also can't
freeze time cleanly.

All IS-boundary time reads must go through `today()` / `now()` here so:
  - Tests can monkeypatch `set_today_provider()` to freeze to a known date
  - Production callers get wall-clock today without change
  - Live / journal sites (legitimate same-day usage) should keep using
    `date.today()` / `datetime.now()` directly — they are NOT IS-boundary reads.
"""
from __future__ import annotations
from collections.abc import Callable
from datetime import date, datetime, timezone

_today_provider: Callable[[], date] | None = None
_now_provider: Callable[[], datetime] | None = None


def set_today_provider(fn: Callable[[], date] | None) -> None:
  """Install a provider for today(). Pass None to restore wall clock."""
  global _today_provider
  _today_provider = fn


def set_now_provider(fn: Callable[[], datetime] | None) -> None:
  """Install a provider for now(). Pass None to restore wall clock."""
  global _now_provider
  _now_provider = fn


def today() -> date:
  """Return the current UTC date, or the injected test override."""
  if _today_provider is not None:
    return _today_provider()
  return datetime.now(timezone.utc).date()


def now() -> datetime:
  """Return the current UTC datetime, or the injected test override."""
  if _now_provider is not None:
    return _now_provider()
  return datetime.now(timezone.utc)
