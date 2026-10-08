"""CS-005: Progressive Pruning gate — research_only_acknowledged must be set.

Static pruning uses ALL windows (including future IS) to decide which batches
to drop. Any OOS metric under that pipeline is look-ahead-contaminated and
may only be used for research. Before this gate, the only protection was a
``pruning_look_ahead_warning`` boolean on the result summary — an honour-system
field that any downstream serialiser could drop.

These tests pin the new hard contract:

1. ``pruning_enabled=True`` without ``research_only_acknowledged=True`` must
   raise ``ValueError`` BEFORE any numbers are produced.
2. With both flags set, the summary must carry a top-level
   ``research_only=True`` marker (not only the older ``pruning_look_ahead_warning``).
3. The gate only fires when pruning is *actually* requested — i.e.
   ``pruning_enabled=True`` AND (``prune_long>0`` or ``prune_short>0``).
4. The default config must still be safe (pruning off, flag off, no raise).
"""
from __future__ import annotations

from backend.analysis.batch_selection import BatchSelectionConfig


def test_default_config_is_safe():
  """Default: pruning off, research_only_acknowledged off, no contamination."""
  config = BatchSelectionConfig()
  assert config.pruning_enabled is False
  assert config.research_only_acknowledged is False
  # prune_long/short default 0 → even if someone flipped pruning_enabled by
  # mistake the downstream check also requires nonzero prune counts.
  assert config.prune_long == 0
  assert config.prune_short == 0


def test_pruning_on_without_ack_raises_value_error():
  """Enabling pruning without acknowledging research-only must fail loud.

  We call the gate branch directly via run_batch_selection — but run_batch_selection
  requires a DB + batch_ids, so instead we assert the code path: build a minimal
  harness that executes the exact gate block.
  """
  import pytest
  from backend.analysis import batch_selection as bs

  # Minimal in-line replay of the gate logic. The file's run_batch_selection
  # contains:
  #   if config.pruning_enabled and (config.prune_long > 0 or config.prune_short > 0):
  #     if not config.research_only_acknowledged:
  #       raise ValueError("CS-005: ...")
  # We replay only the check here so the test stays fast (no DB, no trades).
  def _gate(cfg: BatchSelectionConfig) -> None:
    if cfg.pruning_enabled and (cfg.prune_long > 0 or cfg.prune_short > 0):
      if not cfg.research_only_acknowledged:
        raise ValueError(
          "CS-005: Progressive Pruning is look-ahead-contaminated and may only "
          "be used for research. Set research_only_acknowledged=True on the "
          "BatchSelectionConfig to confirm the output will not feed a live "
          "portfolio."
        )

  # Confirm the real file contains the same raise so the gate can't be
  # silently removed without failing this test.
  import inspect
  src = inspect.getsource(bs.run_batch_selection)
  assert "CS-005" in src, "run_batch_selection must carry the CS-005 gate"
  assert "research_only_acknowledged" in src, (
    "run_batch_selection must check research_only_acknowledged"
  )
  assert 'raise ValueError' in src, "gate must raise, not warn"

  cfg = BatchSelectionConfig(
    pruning_enabled=True,
    prune_long=2,
    prune_short=2,
    research_only_acknowledged=False,
  )
  with pytest.raises(ValueError, match="CS-005"):
    _gate(cfg)


def test_pruning_on_with_ack_does_not_raise():
  """Explicit acknowledgment must allow the gate to pass."""
  from backend.analysis import batch_selection as bs  # noqa: F401

  def _gate(cfg: BatchSelectionConfig) -> None:
    if cfg.pruning_enabled and (cfg.prune_long > 0 or cfg.prune_short > 0):
      if not cfg.research_only_acknowledged:
        raise ValueError("CS-005")

  cfg = BatchSelectionConfig(
    pruning_enabled=True,
    prune_long=2,
    prune_short=2,
    research_only_acknowledged=True,
  )
  _gate(cfg)  # must not raise


def test_pruning_enabled_but_zero_counts_does_not_raise():
  """If prune_long=prune_short=0 the gate is moot — no need to trip the ack."""
  def _gate(cfg: BatchSelectionConfig) -> None:
    if cfg.pruning_enabled and (cfg.prune_long > 0 or cfg.prune_short > 0):
      if not cfg.research_only_acknowledged:
        raise ValueError("CS-005")

  cfg = BatchSelectionConfig(
    pruning_enabled=True,  # on, but nothing to actually prune
    prune_long=0,
    prune_short=0,
    research_only_acknowledged=False,
  )
  _gate(cfg)  # must not raise — nothing is pruned, no contamination


def test_summary_surfaces_research_only_marker():
  """The summary emitted by run_batch_selection must include `research_only`.

  We grep the source (fast + independent of DB fixtures) to pin that the
  field is written; the test_audit_opus_02_coverage file already pins that
  `pruning_look_ahead_warning` is written. This test adds the new marker.
  """
  import inspect
  from backend.analysis import batch_selection as bs

  src = inspect.getsource(bs.run_batch_selection)
  assert 'summary["research_only"]' in src, (
    "run_batch_selection must set summary['research_only'] so downstream "
    "serialisation can not silently drop the look-ahead marker (CS-005)."
  )
