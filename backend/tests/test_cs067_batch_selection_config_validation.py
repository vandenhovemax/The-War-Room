"""CS-067: ``BatchSelectionConfig`` must validate its enum-shaped
fields at construction and raise on typos.

Before the fix there was no ``__post_init__``; a typo in any of
~12 enum-shaped string fields (``overfit_guard_mode``, ``l2_mode``,
``category_mode``, …) silently fell through the ``if x == "log":
... elif ...`` chain and produced a plausible-looking result in
the default branch. The project's non-coder-safety rule calls
this out by name: a user cannot visually detect a saved preset that
is running in the wrong branch.

The fix is additive — a ``__post_init__`` that enumerates the
allowed values and raises ``ValueError`` with a specific message.
This test locks the invariant for each cited field.
"""
from __future__ import annotations

import pytest


def test_default_config_still_constructs():
  """Sanity: the new validator must accept the defaults unchanged.
  A regression here would break every saved run.
  """
  from backend.analysis.batch_selection import BatchSelectionConfig
  cfg = BatchSelectionConfig()
  assert cfg.overfit_guard_mode == "log"
  assert cfg.l2_mode == "fixed"
  assert cfg.category_mode == "tertile"


def test_typo_in_overfit_guard_mode_raises():
  from backend.analysis.batch_selection import BatchSelectionConfig
  with pytest.raises(ValueError, match="overfit_guard_mode"):
    BatchSelectionConfig(overfit_guard_mode="logg")  # typo


def test_typo_in_l2_mode_raises():
  from backend.analysis.batch_selection import BatchSelectionConfig
  with pytest.raises(ValueError, match="l2_mode"):
    BatchSelectionConfig(l2_mode="fixed_50_50")  # common mistake


def test_typo_in_category_mode_raises():
  from backend.analysis.batch_selection import BatchSelectionConfig
  with pytest.raises(ValueError, match="category_mode"):
    BatchSelectionConfig(category_mode="streak")


def test_typo_in_category_influence_raises():
  from backend.analysis.batch_selection import BatchSelectionConfig
  with pytest.raises(ValueError, match="category_influence"):
    BatchSelectionConfig(category_influence="medium")


def test_typo_in_rank_metric_raises():
  from backend.analysis.batch_selection import BatchSelectionConfig
  with pytest.raises(ValueError, match="batch_rank_metric"):
    BatchSelectionConfig(batch_rank_metric="profit_factor")


def test_invalid_assets_per_batch_raises():
  from backend.analysis.batch_selection import BatchSelectionConfig
  with pytest.raises(ValueError, match="assets_per_batch"):
    BatchSelectionConfig(assets_per_batch=0)


def test_invalid_recency_weight_raises():
  from backend.analysis.batch_selection import BatchSelectionConfig
  with pytest.raises(ValueError, match="recency_weight"):
    BatchSelectionConfig(recency_weight=-0.5)


def test_invalid_rescue_quota_raises():
  from backend.analysis.batch_selection import BatchSelectionConfig
  with pytest.raises(ValueError, match="hybrid_rescue_quota_pct"):
    BatchSelectionConfig(hybrid_rescue_quota_pct=1.5)


def test_valid_non_default_enum_values_accepted():
  """Must not falsely reject legitimate non-default values."""
  from backend.analysis.batch_selection import BatchSelectionConfig
  cfg = BatchSelectionConfig(
    overfit_guard_mode="full",
    l2_mode="proportional",
    category_mode="streak_event",
    category_influence="high",
    batch_rank_metric="sortino",
    cal_rank_metric="pf",
    asset_rank_metric="payoff",
  )
  assert cfg.overfit_guard_mode == "full"
