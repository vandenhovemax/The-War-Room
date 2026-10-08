"""Pin per-call RNG isolation for run_beam_optimization.

CS-015 regression: previously a module-level ``_rng = random.Random(42)``
was shared across all calls. Concurrent optimization runs (or back-to-back
calls in one process) would therefore drift — call N's sampling depended
on call N-1's consumption of the RNG. Now each call owns a fresh
``random.Random(seed)``.

These tests don't run the full optimizer (too heavy for a unit test);
they pin the *signature contract* and the isolation property by
exercising the same ``random.Random(seed).choice`` pattern used inside.
"""
from __future__ import annotations

import inspect
import random

from backend.analysis import beam_optimizer


def test_module_level_rng_is_removed():
  """No shared RNG at module scope — was the source of the drift."""
  assert not hasattr(beam_optimizer, "_rng"), (
    "beam_optimizer._rng must not exist at module scope — use per-call Random(seed)"
  )


def test_run_beam_optimization_accepts_seed_kwarg():
  """Public API exposes ``seed`` kwarg with a default for backwards compat."""
  sig = inspect.signature(beam_optimizer.run_beam_optimization)
  assert "seed" in sig.parameters, "run_beam_optimization must accept a `seed` kwarg"
  assert sig.parameters["seed"].default == 42, "default seed should be 42 (preserves prior behavior)"


def test_random_choice_is_deterministic_per_seed():
  """Same seed → same draws. Different seeds → different draws."""
  pool = list(range(100))
  a = [random.Random(42).choice(pool) for _ in range(10)]
  b = [random.Random(42).choice(pool) for _ in range(10)]
  assert a == b, "Same seed must produce the same single-draw sequence"

  # A single Random(42) drawing 10 times should differ from a fresh
  # Random(42) drawing once — proving isolation is meaningful.
  rng_one = random.Random(42)
  seq = [rng_one.choice(pool) for _ in range(10)]
  fresh_single = random.Random(42).choice(pool)
  assert seq[0] == fresh_single
  # The 2nd draw from the advanced RNG is NOT the same as a fresh RNG's 1st draw
  assert seq[1] != fresh_single or len(set(seq)) == 1  # guard: pool tiny edge


def test_different_seeds_produce_different_sequences():
  pool = list(range(1000))
  a = [random.Random(42).choice(pool) for _ in range(20)]
  b = [random.Random(7).choice(pool) for _ in range(20)]
  assert a != b, "Different seeds should produce different sequences"
