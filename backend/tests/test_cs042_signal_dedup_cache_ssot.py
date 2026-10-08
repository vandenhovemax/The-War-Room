"""CS-042: per-signal dedup must short-circuit on the coordinator's
in-memory cache, not open a fresh DB session per candidate signal.

Prior state:

  * ``StrategyExecutor._has_open_position`` unconditionally opened a
    DB session and ran a ``Position`` ORM query on every entry
    signal. Under the 40-symbol M30 burst that is up to 40 sessions
    in the first second after bar close.
  * ``EngineCoordinator._handle_entry_signal`` already owns an
    O(1) cache (``_open_position_keys``) for the same decision. The
    coordinator-side check was a silent duplicate of the executor's
    work, and the two paths could briefly disagree mid-commit.

Fix: wire the coordinator's cache into the executor via an optional
``position_key_check`` callback. When present, the executor trusts
the cache (O(1)) and skips the DB query. Stale-sweep in the
coordinator keeps the cache consistent; the DB fallback is retained
for standalone-executor use (tests).
"""
from __future__ import annotations

import inspect


def test_strategy_executor_accepts_position_key_check_callback():
  from backend.exchange.strategy_executor import StrategyExecutor
  sig = inspect.signature(StrategyExecutor.__init__)
  assert "position_key_check" in sig.parameters, (
    "CS-042 REGRESSION: StrategyExecutor.__init__ no longer accepts "
    "``position_key_check``. The coordinator cannot wire its O(1) "
    "open-position cache into the executor, so every candidate entry "
    "signal would re-open a DB session on the hot path."
  )


def test_coordinator_wires_cache_callback_into_executor():
  from backend.exchange import engine_coordinator as ec_mod
  src = inspect.getsource(ec_mod)
  # The constructor call must pass position_key_check.
  assert "position_key_check=self._is_position_key_open" in src, (
    "CS-042 REGRESSION: EngineCoordinator no longer passes its "
    "cache-lookup callback into StrategyExecutor(...). Without that "
    "wiring, the executor falls back to the DB path — exactly the "
    "O(signals/bar) session churn the audit flagged."
  )
  # And the helper must resolve direction → side the same way the
  # cache was populated (long→Buy, short→Sell).
  assert '"Buy" if direction == "long" else "Sell"' in src, (
    "CS-042 REGRESSION: _is_position_key_open no longer maps "
    "direction→side. A mismatch here silently makes every lookup miss "
    "the cache, re-enabling the DB-per-signal regression."
  )


def test_executor_uses_callback_before_db_query():
  from backend.exchange import strategy_executor as se_mod
  src = inspect.getsource(se_mod.StrategyExecutor._has_open_position)
  # Callback branch must come BEFORE the db_factory branch.
  cb_pos = src.find("self._position_key_check")
  db_pos = src.find("self._db_factory(")
  assert cb_pos != -1, (
    "CS-042 REGRESSION: _has_open_position no longer consults the "
    "``position_key_check`` callback. Drops back to the DB path."
  )
  assert db_pos != -1, (
    "CS-042 FIXTURE BROKEN: DB fallback path is missing — should be "
    "kept for standalone executor tests."
  )
  assert cb_pos < db_pos, (
    "CS-042 REGRESSION: the DB fallback now runs before (or instead "
    "of) the cache callback. On the hot path we must short-circuit "
    "on the O(1) cache."
  )


def test_callback_short_circuits_and_skips_db():
  """Behavioural: wiring a cache-lookup callback must cause the
  executor's dedup to answer without touching the db_factory.
  """
  from backend.exchange.strategy_executor import StrategyExecutor

  db_hits = {"count": 0}

  def _db_factory():
    db_hits["count"] += 1
    raise AssertionError(
      "CS-042 REGRESSION: DB factory called even though the cache "
      "callback was supplied. The hot path must be O(1)."
    )

  cached_keys = {(1, "BTCUSDT", "long")}
  def _cache_check(pid: int, sym: str, direction: str) -> bool:
    return (pid, sym, direction) in cached_keys

  ex = StrategyExecutor(
    candle_aggregator=None,  # not used on this path
    db_factory=_db_factory,
    on_signal=None,
    position_key_check=_cache_check,
  )

  # Cache hit: return True, zero DB sessions.
  assert ex._has_open_position(1, "BTCUSDT", "long") is True
  # Cache miss: return False, still zero DB sessions (trust the cache).
  assert ex._has_open_position(1, "ETHUSDT", "short") is False
  assert db_hits["count"] == 0, (
    "CS-042 REGRESSION: DB factory was called %d times — must be 0 "
    "when a cache callback is wired." % db_hits["count"]
  )
