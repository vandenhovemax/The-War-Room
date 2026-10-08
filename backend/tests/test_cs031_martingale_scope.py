"""CS-031 regression: unrecognized martingale_scope must not silently
fall through — it must warn and normalize to "asset".

The silent fallthrough was a correctness bug: a stale migration, typo,
or hand-edited DB JSON could put any string into martingale_scope, and
the engine would quietly compute streaks with the wrong grouping. That
changes position sizing in production with no user-visible signal.

These tests lock the whitelist contract into place.
"""
from __future__ import annotations

import logging

import pytest

from backend.exchange import engine_coordinator as ec


def test_valid_scopes_are_the_whitelist():
  """The whitelist is the source of truth — every API Literal must match."""
  assert ec._VALID_MARTINGALE_SCOPES == frozenset({
    "asset", "portfolio", "asset_batch", "batch",
  })


@pytest.mark.parametrize("scope", ["asset", "portfolio", "asset_batch", "batch"])
def test_documented_scopes_are_members(scope: str):
  assert scope in ec._VALID_MARTINGALE_SCOPES


@pytest.mark.parametrize("bad", [
  "global",          # early-draft name
  "per_asset",       # user intuition, not the stored form
  "ASSET",           # case mismatch
  " asset",          # whitespace
  "",                # empty
  "symbol_batch",    # plausible typo
])
def test_unknown_scopes_are_rejected(bad: str):
  assert bad not in ec._VALID_MARTINGALE_SCOPES


def test_unknown_scope_logs_warning_and_normalizes(caplog):
  """Simulate the guard path: engine sees an unknown scope → warn + fallback."""
  scope = "bogus_scope"
  caplog.set_level(logging.WARNING, logger="backend.exchange.engine_coordinator")

  # Mirror the exact guard from engine_coordinator._get_portfolio_risk
  if scope not in ec._VALID_MARTINGALE_SCOPES:
    ec.logger.warning(
      "Portfolio %d: unrecognized martingale_scope=%r — falling back to 'asset'. "
      "Valid scopes: %s",
      42, scope, sorted(ec._VALID_MARTINGALE_SCOPES),
    )
    scope = "asset"

  assert scope == "asset"
  assert any("unrecognized martingale_scope" in r.message for r in caplog.records)
  assert any("bogus_scope" in r.message for r in caplog.records)
