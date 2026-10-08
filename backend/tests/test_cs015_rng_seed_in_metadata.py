"""CS-015: RNG seed must be surfaced on every beam-optimizer result.

Per-call RNG isolation was fixed earlier (see test_beam_seed_isolation). The
audit's full remedy also requires the seed to be **declared in the result
metadata** so that:

* Two reruns of the same preset can be compared bit-identical.
* A silently-changed seed (caller override, refactor default change) can be
  detected by diffing stored result dicts.

These tests pin the contract without running the full optimizer (too heavy
for unit tests); instead they exercise ``_build_results`` and the return
shape of ``run_beam_optimization``.
"""
from __future__ import annotations

import inspect

import pandas as pd

from backend.analysis import beam_optimizer


def test_run_beam_optimization_source_writes_rng_seed():
  """Every result dict written by run_beam_optimization carries `rng_seed`."""
  src = inspect.getsource(beam_optimizer.run_beam_optimization)
  assert 'r["rng_seed"]' in src, (
    "run_beam_optimization must attach rng_seed to every result (CS-015)"
  )


def test_rng_seed_is_int_coerced():
  """Seed is int-coerced — prevents accidental str/float drift in metadata."""
  src = inspect.getsource(beam_optimizer.run_beam_optimization)
  assert "int(seed)" in src, (
    "run_beam_optimization must int-coerce seed before serialising to metadata"
  )


def test_seed_kwarg_signature_unchanged():
  """Signature and default must remain stable across the fix."""
  sig = inspect.signature(beam_optimizer.run_beam_optimization)
  assert "seed" in sig.parameters
  assert sig.parameters["seed"].default == 42


def test_build_results_shape_is_not_broken():
  """Sanity: _build_results still returns dicts with assets + metrics.

  CS-015 touched the caller, not _build_results. This test pins that the
  base shape still exists — any future change that renames the metrics dict
  or drops assets will fail here before it breaks the UI.
  """
  scored = [
    {"assets": ["A", "B"], "score": 0.0},
    {"assets": ["C"], "score": 1.0},
  ]
  idx = pd.date_range("2024-01-01", periods=5)
  daily = pd.DataFrame(
    {"A": [1, -1, 2, 0, 1], "B": [0, 1, 0, -1, 1], "C": [-1, 0, 1, 1, 0]},
    index=idx,
  )
  out = beam_optimizer._build_results(scored, daily)
  assert len(out) == 2
  for r in out:
    assert "assets" in r
    assert "metrics" in r
    assert "net_profit" in r["metrics"]
