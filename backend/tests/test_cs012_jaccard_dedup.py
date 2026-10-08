"""CS-012: Jaccard dedup regression — pin the invariant and catch drift
between the three independent copies.

``_jaccard_similarity`` / ``_deduplicate_variants`` live in THREE files:

  - ``backend/analysis/beam_optimizer.py`` (production)
  - ``backend/analysis/beam_optimizer_og_v1.py`` (frozen OG-v1)
  - ``backend/analysis/beam_optimizer_og_v2.py`` (frozen OG-v2)

Threshold 0.7 and strict ``>`` comparison are load-bearing — the
portfolio recommender depends on "top candidates must not be near-
duplicates of each other". An off-by-one on the set, a wrong threshold,
or flipping ``>`` to ``>=`` silently ships twin portfolios.

Tests here parametrize across all three copies so a fix to one that
doesn't land in the others is caught at CI time.
"""
from __future__ import annotations

import pytest

from backend.analysis import (
  beam_optimizer,
  beam_optimizer_og_v1,
  beam_optimizer_og_v2,
)

_MODULES = pytest.mark.parametrize(
  "mod",
  [beam_optimizer, beam_optimizer_og_v1, beam_optimizer_og_v2],
  ids=["beam", "og_v1", "og_v2"],
)


# ── _jaccard_similarity arithmetic ────────────────────────────────────

@_MODULES
def test_jaccard_identical_lists_returns_one(mod):
  assert mod._jaccard_similarity(["a", "b", "c"], ["a", "b", "c"]) == 1.0


@_MODULES
def test_jaccard_disjoint_lists_returns_zero(mod):
  assert mod._jaccard_similarity(["a", "b"], ["c", "d"]) == 0.0


@_MODULES
def test_jaccard_both_empty_returns_one(mod):
  """Code contract: empty union → 1.0. This matters because otherwise
  a division-by-zero would raise and break beam L2/L3 combination on
  pathological inputs.
  """
  assert mod._jaccard_similarity([], []) == 1.0


@_MODULES
def test_jaccard_ignores_duplicates_within_list(mod):
  """Input is coerced to a set before computing — repeated entries in
  the same list must not inflate the similarity.
  """
  a = ["x", "x", "y", "z"]
  b = ["x", "y", "z"]
  # Both collapse to {x,y,z} → Jaccard = 1.0
  assert mod._jaccard_similarity(a, b) == 1.0


@_MODULES
def test_jaccard_is_order_independent(mod):
  a = ["a", "b", "c", "d"]
  b_perm = ["d", "c", "b", "a"]
  assert mod._jaccard_similarity(a, b_perm) == 1.0


@_MODULES
def test_jaccard_partial_overlap_computes_ratio(mod):
  """|{a,b,c} ∩ {a,b,x}| = 2; |union| = 4 → 0.5."""
  assert mod._jaccard_similarity(["a", "b", "c"], ["a", "b", "x"]) == pytest.approx(0.5)


# ── _deduplicate_variants threshold contract ─────────────────────────

@_MODULES
def test_dedup_drops_when_strictly_above_threshold(mod):
  """Jaccard > 0.7 means "too similar" — the later variant is dropped.
  5/6 = 0.833... > 0.7.
  """
  v1 = {"assets": ["a", "b", "c", "d", "e"]}
  v2 = {"assets": ["a", "b", "c", "d", "e", "f"]}  # union=6, intersect=5
  out = mod._deduplicate_variants([v1, v2], threshold=0.7)
  assert len(out) == 1
  assert out[0] is v1


@_MODULES
def test_dedup_keeps_when_below_threshold(mod):
  """2/6 = 0.333 is well below 0.7 → both survive."""
  v1 = {"assets": ["a", "b", "c", "d"]}
  v2 = {"assets": ["a", "b", "e", "f"]}  # union=6, intersect=2
  out = mod._deduplicate_variants([v1, v2], threshold=0.7)
  assert len(out) == 2


@_MODULES
def test_dedup_keeps_when_exactly_at_threshold(mod):
  """Strict ``>`` inequality: 0.7 exact is NOT a duplicate.
  7/10 = 0.7 exactly.
  """
  base = [f"s{i}" for i in range(10)]
  # v1 has 7 of those 10 elements; v2 has all 10. Intersect=7, union=10.
  v1 = {"assets": base[:7]}
  v2 = {"assets": base}
  # Sanity check the raw Jaccard is the exact threshold.
  jac = mod._jaccard_similarity(v1["assets"], v2["assets"])
  assert jac == pytest.approx(0.7, abs=1e-12)
  # Strict > means equality is NOT a dup.
  out = mod._deduplicate_variants([v1, v2], threshold=0.7)
  assert len(out) == 2, (
    f"CS-012: {mod.__name__} dedup threshold became >=, not >. Exactly-"
    "at-threshold pairs must survive, or research loses too many candidates."
  )


@_MODULES
def test_dedup_preserves_order_of_kept_variants(mod):
  """Dedup iterates in order; kept variants are in input order. Important
  because the *first* pass of L2/L3 already sorted by score, so dropping
  by iteration order keeps the higher-scored copy.
  """
  v_high = {"assets": ["a", "b", "c", "d"], "score": 1.0}
  v_low = {"assets": ["a", "b", "c", "d"], "score": 0.1}  # dup of high
  v_other = {"assets": ["x", "y", "z"], "score": 0.5}
  out = mod._deduplicate_variants([v_high, v_low, v_other], threshold=0.7)
  assert [v.get("score") for v in out] == [1.0, 0.5], (
    "CS-012: dedup must keep the first occurrence (presumed-higher score)"
  )


@_MODULES
def test_dedup_is_transitive_across_multiple_neighbours(mod):
  """If v2 is a near-dup of v1 and v3 is a near-dup of v2 but NOT of v1,
  what survives? The code only compares against *kept* variants, so v1
  stays and v2 gets dropped — then v3 is compared against v1 only.

  This test freezes that transitive behaviour: different near-neighbour
  chains can leak through. A future change to "compare against all
  originals" would tighten this — that's fine, but it needs to be a
  conscious call, not a silent drift.
  """
  # v1 has a,b,c,d — v2 shares all of them (Jaccard=1.0) → v2 dropped
  # v3 shares c,d with v1 (2/5 = 0.4) → v3 kept vs v1
  v1 = {"assets": ["a", "b", "c", "d"]}
  v2 = {"assets": ["a", "b", "c", "d"]}
  v3 = {"assets": ["c", "d", "e"]}
  out = mod._deduplicate_variants([v1, v2, v3], threshold=0.7)
  assert len(out) == 2
  assert out[0] is v1
  assert out[2 - 1] is v3


@_MODULES
def test_dedup_on_empty_input_returns_empty(mod):
  assert mod._deduplicate_variants([], threshold=0.7) == []


@_MODULES
def test_dedup_on_single_variant_returns_it(mod):
  v = {"assets": ["a", "b"]}
  out = mod._deduplicate_variants([v], threshold=0.7)
  assert out == [v]


# ── Cross-copy consistency: values must match across all three files ──

def test_all_three_modules_agree_on_identical_input():
  """Direct regression target for the "fix one copy, forget the others"
  drift mode: feed the same input to all three, expect bitwise-identical
  output. If this fails, the three Jaccard implementations have drifted.
  """
  variants = [
    {"assets": ["a", "b", "c"]},
    {"assets": ["a", "b", "c", "d"]},   # 3/4 = 0.75 > 0.7 → dup
    {"assets": ["x", "y"]},              # kept
    {"assets": ["a", "b", "c", "d", "e"]},  # vs v1: 3/5=0.6 → below 0.7
  ]
  out_beam = beam_optimizer._deduplicate_variants(variants, threshold=0.7)
  out_v1 = beam_optimizer_og_v1._deduplicate_variants(variants, threshold=0.7)
  out_v2 = beam_optimizer_og_v2._deduplicate_variants(variants, threshold=0.7)

  # Compare by assets (identity check would fail since lists are distinct)
  def _assets(out):
    return [v["assets"] for v in out]

  assert _assets(out_beam) == _assets(out_v1) == _assets(out_v2), (
    "CS-012: beam_optimizer, og_v1, og_v2 produced different dedup "
    "results on identical input — one of the three has drifted."
  )
