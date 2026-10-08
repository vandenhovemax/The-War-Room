"""Jaccard similarity + dedup invariants.

Guards the two optimizer paths that rely on Jaccard overlap:
  1. ``beam_optimizer._deduplicate_variants`` — post-beam dedup with
     a 0.7 default threshold; variant is dropped if it shares >70% of
     assets with any already-kept variant. First-wins ordering.
  2. ``diversification._diverse_select`` — greedy picker that enforces
     an overlap ceiling (<=max_overlap) against every already-selected
     portfolio. max_overlap=None is a passthrough.

Contracts tested:
  - empty/empty → 1.0 (documented edge case)
  - identical sets → 1.0
  - fully disjoint → 0.0
  - strict inequality at threshold (0.7 default means EXACTLY 0.7 is kept)
  - first-wins ordering preserved
  - duplicate assets inside a single list don't inflate similarity
"""
from __future__ import annotations

import pytest

from backend.analysis.beam_optimizer import (
  _deduplicate_variants,
  _jaccard_similarity,
)
from backend.analysis.diversification import _diverse_select


# --- _jaccard_similarity -------------------------------------------------

def test_jaccard_identical_sets_is_one():
  assert _jaccard_similarity(["A", "B", "C"], ["A", "B", "C"]) == 1.0


def test_jaccard_disjoint_sets_is_zero():
  assert _jaccard_similarity(["A", "B"], ["C", "D"]) == 0.0


def test_jaccard_half_overlap():
  # {A,B,C,D} & {C,D,E,F} = {C,D} → 2/6 = 0.333...
  v = _jaccard_similarity(["A", "B", "C", "D"], ["C", "D", "E", "F"])
  assert v == pytest.approx(1 / 3)


def test_jaccard_one_shared_two_items_each():
  # {A,B} & {B,C} = {B} → 1/3
  v = _jaccard_similarity(["A", "B"], ["B", "C"])
  assert v == pytest.approx(1 / 3)


def test_jaccard_empty_lists_return_one():
  """Both-empty returns 1.0 (union empty → documented sentinel)."""
  assert _jaccard_similarity([], []) == 1.0


def test_jaccard_one_empty_returns_zero():
  """One empty, one non-empty → intersection empty, union non-empty."""
  assert _jaccard_similarity([], ["A", "B"]) == 0.0
  assert _jaccard_similarity(["A"], []) == 0.0


def test_jaccard_ignores_duplicates_within_list():
  """set() conversion means duplicates don't inflate numerator or denominator."""
  # ["A","A","B"] → {A,B}; ["A","B","B"] → {A,B} ⇒ identical sets ⇒ 1.0
  assert _jaccard_similarity(["A", "A", "B"], ["A", "B", "B"]) == 1.0


def test_jaccard_order_independent():
  assert _jaccard_similarity(["A", "B", "C"], ["C", "B", "A"]) == 1.0


# --- _deduplicate_variants ----------------------------------------------

def _v(assets: list[str]) -> dict:
  return {"assets": assets}


def test_dedup_drops_above_threshold():
  """Variant with >0.7 overlap against a kept variant is dropped."""
  # {A..J} vs {A..H,I,K} → intersection 8, union 11 → 8/11 ≈ 0.727 > 0.7
  first = _v(["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"])
  near_dupe = _v(["A", "B", "C", "D", "E", "F", "G", "H", "I", "K"])

  out = _deduplicate_variants([first, near_dupe])

  assert out == [first]


def test_dedup_keeps_below_threshold():
  """Variant with <=0.7 overlap is kept (strict > comparison)."""
  # {A,B,C,D,E} vs {A,B,C,D,F} → 4/6 ≈ 0.667 < 0.7 → both kept
  a = _v(["A", "B", "C", "D", "E"])
  b = _v(["A", "B", "C", "D", "F"])

  out = _deduplicate_variants([a, b])

  assert out == [a, b]


def test_dedup_strict_gt_at_exact_threshold():
  """At exactly threshold, the variant is KEPT (comparison is strict >)."""
  # {A,B,C,D,E,F,G} vs {A,B,C,D,E,F,Z} → 6/8 = 0.75 > 0.7 (drops)
  # For exact 0.7 we'd need 7/10; use custom threshold to test the boundary.
  a = _v(["A", "B", "C", "D", "E"])
  # {A..E} vs {A..E} = 1.0 exactly → kept only if threshold >= 1.0
  b_identical = _v(["A", "B", "C", "D", "E"])

  # At threshold=1.0, identical variants are NOT dropped (1.0 > 1.0 is False)
  out = _deduplicate_variants([a, b_identical], threshold=1.0)
  assert out == [a, b_identical]

  # At threshold=0.99, identical variants ARE dropped (1.0 > 0.99 is True)
  out = _deduplicate_variants([a, b_identical], threshold=0.99)
  assert out == [a]


def test_dedup_first_wins_ordering():
  """Earlier variants are kept; later near-dupes drop against them."""
  first = _v(["A", "B", "C", "D", "E"])
  second = _v(["A", "B", "C", "D", "E", "F"])  # superset → high Jaccard

  out = _deduplicate_variants([first, second])

  assert len(out) == 1
  assert out[0] is first


def test_dedup_empty_list_returns_empty():
  assert _deduplicate_variants([]) == []


def test_dedup_preserves_dissimilar_variants():
  """Three fully disjoint sets should all survive."""
  a = _v(["A", "B"])
  b = _v(["C", "D"])
  c = _v(["E", "F"])

  out = _deduplicate_variants([a, b, c])

  assert out == [a, b, c]


# --- _diverse_select -----------------------------------------------------

def test_diverse_select_passthrough_when_max_overlap_none():
  cands = [_v(["A", "B"]), _v(["A", "B"]), _v(["C", "D"])]
  out = _diverse_select(cands, n_portfolios=10, max_overlap=None)
  assert out == cands


def test_diverse_select_rejects_above_ceiling():
  """With max_overlap=0.5, later candidate with >0.5 overlap is skipped."""
  a = _v(["A", "B", "C", "D"])
  b = _v(["A", "B", "C", "E"])  # 3/5 = 0.6 > 0.5 → skipped
  c = _v(["X", "Y", "Z"])       # disjoint → accepted

  out = _diverse_select([a, b, c], n_portfolios=3, max_overlap=0.5)

  # Fallback fills to n_portfolios if possible; first pick + disjoint kept,
  # then fallback fills with b (no unique-enough remaining → fill allowed).
  assert a in out
  assert c in out


def test_diverse_select_zero_overlap_requires_disjoint():
  a = _v(["A", "B"])
  b = _v(["B", "C"])  # shares B → rejected
  c = _v(["D", "E"])  # disjoint → accepted

  out = _diverse_select([a, b, c], n_portfolios=2, max_overlap=0.0)

  assert out == [a, c]


def test_diverse_select_respects_n_portfolios_cap():
  cands = [_v(["A"]), _v(["B"]), _v(["C"]), _v(["D"])]
  out = _diverse_select(cands, n_portfolios=2, max_overlap=0.5)
  assert len(out) == 2


def test_diverse_select_single_candidate_shortcut():
  """Single candidate path returns early (len<=1 shortcut)."""
  only = _v(["A", "B"])
  out = _diverse_select([only], n_portfolios=5, max_overlap=0.0)
  assert out == [only]
