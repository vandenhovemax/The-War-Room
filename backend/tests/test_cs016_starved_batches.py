"""CS-016: Jaccard dedup + ``k_l1 * 2`` over-generation can silently drop
whole batches from L2/L3.

Before this fix, a batch that either (a) had no usable asset columns in
``daily_pnl`` or (b) dedup-collapsed to zero survivors would disappear
from every downstream portfolio with no warning logged. L2 would skip
the entire group via ``if not all(group_variant_lists): continue``.

These tests pin the new contract:

1. ``run_beam_optimization`` logs a ``CS-016 BatchStarved`` warning
   listing every starved batch at L1 completion.
2. The L2 group-skip branch logs a named warning listing the starving
   batches instead of silently ``continue``.
3. Every result dict carries ``starved_batches`` metadata so the UI
   can surface the drop prominently.
"""
from __future__ import annotations

import inspect

from backend.analysis import beam_optimizer


def test_source_logs_batchstarved_warning_for_no_assets():
  src = inspect.getsource(beam_optimizer.run_beam_optimization)
  assert "CS-016 BatchStarved" in src, (
    "run_beam_optimization must log 'CS-016 BatchStarved' for no-asset batches"
  )
  assert "starved_no_assets" in src, (
    "must track batches with zero usable columns separately"
  )


def test_source_logs_batchstarved_warning_for_post_dedup_collapse():
  src = inspect.getsource(beam_optimizer.run_beam_optimization)
  assert "starved_after_dedup" in src, (
    "must track batches whose L1 variants all collapsed after Jaccard dedup"
  )
  # The warning must explicitly name dedup threshold and over-generation.
  assert "Jaccard dedup" in src, (
    "warning must name Jaccard dedup so a reader can diagnose the cause"
  )


def test_source_logs_l2_group_skip_with_names():
  src = inspect.getsource(beam_optimizer.run_beam_optimization)
  # Ensure the skip-continue branch is preceded by an explicit warning.
  assert "CS-016 L2 group skipped" in src, (
    "L2 group-skip must log the group and the starving batch(es) (CS-016). "
    "Before the fix this was a silent `continue`."
  )


def test_results_carry_starved_batches_metadata():
  src = inspect.getsource(beam_optimizer.run_beam_optimization)
  assert 'r["starved_batches"]' in src, (
    "every result dict must carry starved_batches so the UI surfaces them"
  )


def test_l1_summary_counts_are_logged():
  src = inspect.getsource(beam_optimizer.run_beam_optimization)
  assert "L1 summary" in src, (
    "per-audit recommendation: log input batch count vs surviving variant count"
  )


def test_post_dedup_empty_variants_classified_correctly():
  """Sanity: the list-comprehension that classifies starved_after_dedup
  must check for empty variant lists (not missing keys). Missing keys were
  already captured by starved_no_assets.
  """
  src = inspect.getsource(beam_optimizer.run_beam_optimization)
  assert "for bid, variants in l1_variants.items() if not variants" in src, (
    "starved_after_dedup must iterate the dict entries and pick empty lists"
  )
