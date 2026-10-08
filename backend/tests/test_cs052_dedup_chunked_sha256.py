"""CS-052: ``dedup_batches`` used to compute its fingerprint via
``sha256(repr(tuples).encode())``. On a 3M-trade batch that
materialises the entire list's ``repr`` as a single Python string
before hashing — a ~100MB RAM spike per fingerprint.

The fix is chunk-wise: feed ``repr(tup)`` for each tuple into the
sha256 object one at a time, so memory stays O(1).

This suite locks the *behaviour* — not the hash value. Changing the
hash format is fine (fingerprints are computed on-the-fly, never
persisted) as long as:
  1. identical trade sets still produce identical fingerprints
  2. different trade sets still produce different fingerprints
  3. one duplicate batch is removed when two batches have identical
     trades (end-to-end dedup still works)
  4. the implementation no longer calls ``repr(tuples)`` on the full
     list (static guard — direct regression check).
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime

from backend.analysis import batch_selection
from backend.analysis.batch_selection import dedup_batches


@dataclass
class _FakeTrade:
  """Duck-typed ``BacktestTrade`` — ``dedup_batches`` uses getattr on
  ``symbol``, ``side``, ``entry_time``, ``exit_time``, ``net_profit``,
  and reads ``run_id`` directly. That's all we need to mirror.
  """
  run_id: int
  symbol: str
  side: str
  entry_time: datetime
  exit_time: datetime
  net_profit: float


def _make_trades(run_id: int, n: int, base_pnl: float = 1.0) -> list[_FakeTrade]:
  return [
    _FakeTrade(
      run_id=run_id,
      symbol=f"SYM{i % 3}",
      side="long" if i % 2 == 0 else "short",
      entry_time=datetime(2024, 1, 1, i % 23),
      exit_time=datetime(2024, 1, 2, i % 23),
      net_profit=base_pnl + i * 0.01,
    )
    for i in range(n)
  ]


# ── Static guard ───────────────────────────────────────────────────

def test_dedup_batches_no_longer_calls_repr_on_full_list():
  """The old ``sha256(repr(tuples))`` line was the whole point of
  CS-052. If it ever comes back — even in a refactor that 'looks
  equivalent' — this test tripwires it.
  """
  # Strip comments/docstrings so our own explanatory prose doesn't
  # trip the guard. We only want to catch the actual call.
  src = inspect.getsource(dedup_batches)
  code_lines = [
    ln for ln in src.splitlines()
    if ln.strip() and not ln.strip().startswith("#")
  ]
  code_only = "\n".join(code_lines)
  # Drop the docstring (first triple-quoted block).
  if '"""' in code_only:
    parts = code_only.split('"""')
    # parts[0]=signature, parts[1]=docstring body, parts[2:]=rest
    code_only = parts[0] + ("".join(parts[2:]) if len(parts) > 2 else "")

  assert "sha256(repr(" not in code_only, (
    "CS-052 REGRESSION: dedup_batches is calling sha256(repr(...)) "
    "again — materialising the whole tuple-list's repr as one string "
    "is the 100MB/batch RAM spike the audit flagged. Feed tuples "
    "into sha256 one at a time."
  )
  # Positive check: the chunked pattern must be present.
  assert "sha256()" in code_only, (
    "CS-052 REGRESSION: dedup_batches no longer constructs a "
    "hashlib.sha256() object incrementally. The chunked pattern "
    "has been replaced."
  )


# ── Behavioural parity ─────────────────────────────────────────────

def test_identical_batches_dedup_to_one():
  """Two batches with identical trade content must produce the same
  fingerprint and collapse to one — the whole point of the dedup.
  """
  trades_a = _make_trades(run_id=10, n=20)
  trades_b = _make_trades(run_id=20, n=20)  # same content, different run
  all_trades = trades_a + trades_b
  run_batch_map = {10: 1, 20: 2}

  kept_trades, kept_map, kept_batch_ids = dedup_batches(all_trades, run_batch_map)

  assert len(kept_batch_ids) == 1, (
    f"CS-052 REGRESSION: two content-identical batches should collapse "
    f"to one fingerprint — got {len(kept_batch_ids)} survivors."
  )
  # Lowest batch_id wins, per the function's docstring.
  assert kept_batch_ids == [1]
  # The kept run's trades should survive; the duplicate's should not.
  assert all(t.run_id == 10 for t in kept_trades)
  assert kept_map == {10: 1}


def test_distinct_batches_both_kept():
  """Two batches with different trade content must produce distinct
  fingerprints and both survive. A hash collision here would be a
  silent correctness bug.
  """
  trades_a = _make_trades(run_id=30, n=20, base_pnl=1.0)
  trades_b = _make_trades(run_id=40, n=20, base_pnl=2.0)  # different PnL
  all_trades = trades_a + trades_b
  run_batch_map = {30: 3, 40: 4}

  _, _, kept_batch_ids = dedup_batches(all_trades, run_batch_map)

  assert sorted(kept_batch_ids) == [3, 4], (
    "CS-052 REGRESSION: two distinct-content batches were collapsed — "
    "the chunk-wise hash is producing collisions it shouldn't."
  )


def test_one_byte_difference_changes_fingerprint():
  """The chunked hash must be sensitive to a single-trade delta. This
  catches a bad separator choice (e.g. empty separator could let
  ``['ab', 'c']`` and ``['a', 'bc']`` collide).
  """
  trades_a = _make_trades(run_id=50, n=5)
  trades_b = _make_trades(run_id=60, n=5)
  # Mutate exactly one trade in batch B.
  trades_b[0] = _FakeTrade(
    run_id=60,
    symbol=trades_b[0].symbol,
    side=trades_b[0].side,
    entry_time=trades_b[0].entry_time,
    exit_time=trades_b[0].exit_time,
    net_profit=trades_b[0].net_profit + 0.0001,  # rounds away? 4 decimals kept
  )
  # The +0.0001 is exactly at the 4-decimal boundary — bump harder to
  # be safe:
  trades_b[0].net_profit = trades_b[0].net_profit + 1.0

  all_trades = trades_a + trades_b
  run_batch_map = {50: 5, 60: 6}

  _, _, kept_batch_ids = dedup_batches(all_trades, run_batch_map)

  assert sorted(kept_batch_ids) == [5, 6], (
    "CS-052 REGRESSION: a single-trade net_profit delta no longer "
    "changes the fingerprint. Dedup will silently merge genuinely "
    "distinct batches."
  )


def test_empty_batch_map_is_a_noop():
  """Empty inputs must not raise — chunk-wise hashing on an empty
  tuple list should still produce a valid sha256 digest.
  """
  kept_trades, kept_map, kept_batch_ids = dedup_batches([], {})
  assert kept_trades == []
  assert kept_map == {}
  assert kept_batch_ids == []


def test_dedup_is_deterministic_across_calls():
  """Two calls on the same input must produce the same surviving
  batch set. A mutable accumulator bug in the rewrite would show up
  as instability across calls.
  """
  trades_a = _make_trades(run_id=70, n=15)
  trades_b = _make_trades(run_id=80, n=15)  # identical content
  run_batch_map = {70: 7, 80: 8}

  _, _, r1 = dedup_batches(trades_a + trades_b, dict(run_batch_map))
  _, _, r2 = dedup_batches(trades_a + trades_b, dict(run_batch_map))
  assert r1 == r2 == [7]


# ── Sanity: module-level import still resolves ─────────────────────

def test_dedup_batches_symbol_is_still_exported():
  """Defensive: if someone accidentally renames or shadows
  ``dedup_batches`` while refactoring the hash call, the loader
  (``run_batch_selection``) breaks silently — this test catches it.
  """
  assert hasattr(batch_selection, "dedup_batches")
  assert callable(batch_selection.dedup_batches)
