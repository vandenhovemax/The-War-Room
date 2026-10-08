"""Simple in-memory TTL cache for expensive computations."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

_DEFAULT_TTL = 300  # 5 minutes
_MAX_ENTRIES = 128  # Evict oldest when full


class TTLCache:
  """Thread-safe LRU + TTL cache keyed by hashable tuples."""

  def __init__(self, ttl: int = _DEFAULT_TTL, max_entries: int = _MAX_ENTRIES):
    self._ttl = ttl
    self._max = max_entries
    self._store: OrderedDict[tuple, tuple[float, object]] = OrderedDict()
    self._lock = threading.Lock()

  def get(self, key: tuple) -> object | None:
    with self._lock:
      entry = self._store.get(key)
      if entry is None:
        return None
      ts, val = entry
      if time.monotonic() - ts > self._ttl:
        self._store.pop(key, None)
        return None
      self._store.move_to_end(key)
      return val

  def set(self, key: tuple, value: object) -> None:
    with self._lock:
      self._store[key] = (time.monotonic(), value)
      self._store.move_to_end(key)
      while len(self._store) > self._max:
        self._store.popitem(last=False)

  def invalidate(self, key: tuple) -> None:
    with self._lock:
      self._store.pop(key, None)

  def clear(self) -> None:
    with self._lock:
      self._store.clear()


# Shared instance for metrics results
metrics_cache = TTLCache()
