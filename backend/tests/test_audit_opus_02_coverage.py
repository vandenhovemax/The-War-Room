"""Tier 5 coverage tests for AUDIT_OPUS_02 findings.

Each test here corresponds to a coverage gap identified in AUDIT_OPUS_02.md
section 13.5. If one fails later, the matching audit finding has regressed.

  Test 19 — `_asset_pf` strict-zero parity across non-legacy callers (X01)
  Test 20 — Three-way OOS unification: cost/scale order + helper (L01/L04)
  Test 21 — Deterministic clock provider freezes final-window end (B06/W01)
  Test 22 — Exec-filter propagation in WFO OOS path (MEMORY.md rule)
  Test 23 — Progressive Pruning look-ahead gate surfaces a warning (B01)
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import pytest


# --------------------------------------------------------------------------
# Test 19 — _asset_pf strict-zero parity
# --------------------------------------------------------------------------

class TestAssetPfParity:
  """All non-legacy optimizer modules must expose the same `_asset_pf`.

  Shared implementation lives at backend.analysis.shared._asset_pf using the
  strict-zero convention. Any optimizer file that still defines its own local
  copy is a regression of Tier 1.3 / X01.
  """

  NON_LEGACY_MODULES = [
    "backend.analysis.optimizer",
    "backend.analysis.optimizer_split",
    "backend.analysis.optimizer_ensemble",
    "backend.analysis.optimizer_quality_first",
    "backend.analysis.optimizer_tournament",
    "backend.analysis.optimizer_marginal_sharpe",
    "backend.analysis.optimizer_genetic",
    "backend.analysis.hrp_optimizer",
    "backend.analysis.pca_optimizer",
    "backend.analysis.asset_contribution",
  ]

  def test_shared_uses_strict_zero(self):
    from backend.analysis.shared import _asset_pf
    s = pd.Series([1.0, 2.0, -0.5, -1.0])
    # Strict zero: gp=3, gl=1.5 → 2.0
    assert _asset_pf(s) == pytest.approx(2.0, abs=1e-9)

  def test_all_modules_share_same_function(self):
    import importlib
    from backend.analysis.shared import _asset_pf as canonical
    for mod_name in self.NON_LEGACY_MODULES:
      mod = importlib.import_module(mod_name)
      assert hasattr(mod, "_asset_pf"), f"{mod_name} must expose _asset_pf"
      assert mod._asset_pf is canonical, (
        f"{mod_name}._asset_pf is not the shared implementation — "
        f"Tier 1.3 X01 regression (got {mod._asset_pf!r})"
      )


# --------------------------------------------------------------------------
# Test 20 — Three-way OOS unification (L01 cost/scale order + L04 helper)
# --------------------------------------------------------------------------

class TestThreeWayOosUnification:
  """Portfolio OOS paths must agree on order-of-operations and date lookup.

  L01: _load_portfolio_trades must apply risk scaling BEFORE stress costs
       (matching cross_strategy.py's optimizer order).
  L04: A single _resolve_oos_end_date helper must be the source of truth.
  L03: portfolio_live_views must alias `since_optimized` to the clipped
       `oos` view, not to `oos_full`.
  """

  def test_resolve_oos_end_date_exists_and_has_request_override(self):
    from backend.api.routes.portfolio_analysis import _resolve_oos_end_date

    class _FakePortfolio:
      source_strategies = json.dumps({"oos_end_date": "2025-01-01"})
      created_at = datetime(2025, 6, 1)

    p = _FakePortfolio()
    assert _resolve_oos_end_date(p) == "2025-01-01"
    assert _resolve_oos_end_date(p, req_override="2025-03-15") == "2025-03-15"

  def test_resolve_oos_end_date_falls_back_to_created_at(self):
    from backend.api.routes.portfolio_analysis import _resolve_oos_end_date

    class _FakePortfolio:
      source_strategies = json.dumps({})  # no oos_end_date, no data_end_date
      created_at = datetime(2025, 9, 30)

    assert _resolve_oos_end_date(_FakePortfolio()) == "2025-09-30"

  def test_load_portfolio_trades_applies_scale_before_cost(self):
    """Regression test for L01. The optimizer scales first, then deducts
    cost. Saved-portfolio path must match: scanning the source code for the
    in-order appearance of the two markers is sufficient — any future swap
    would re-introduce the streak-tracking contamination.
    """
    import inspect
    from backend.api.routes import portfolio_analysis
    src = inspect.getsource(portfolio_analysis._load_portfolio_trades)
    scale_idx = src.find("_apply_risk_scaling")
    cost_idx = src.find("_apply_stress_slippage")
    assert scale_idx > 0 and cost_idx > 0, (
      "Expected both scaling and cost calls in _load_portfolio_trades"
    )
    assert scale_idx < cost_idx, (
      "Tier 1.1 L01 regression: cost stress must run AFTER risk scaling "
      "to match cross_strategy.py's optimizer order. Current order swapped."
    )

  def test_since_optimized_aliases_clipped_oos_view(self):
    """Regression test for L03: the dashboard's legacy `since_optimized`
    view must equal the clipped `oos` view, not the unclipped `oos_full`.
    """
    import inspect
    from backend.api.routes import portfolio_analysis
    src = inspect.getsource(portfolio_analysis.portfolio_live_views)
    assert 'views["since_optimized"] = views["oos"]' in src, (
      "Tier 1.1 L03 regression: since_optimized must alias clipped oos"
    )


# --------------------------------------------------------------------------
# Test 21 — Deterministic clock provider
# --------------------------------------------------------------------------

class TestDeterministicClock:
  """IS-boundary math must be reproducible under a frozen clock.

  generate_rolling_windows uses clock.today() for the final IS-only window.
  Test 21 exercises the provider swap so two runs at different wall-clock
  times produce identical final-window ends.
  """

  def test_set_today_provider_freezes_final_window(self):
    from backend.utils import clock
    from backend.analysis.wfo_window_gen import generate_rolling_windows

    frozen = date(2025, 11, 5)
    try:
      clock.set_today_provider(lambda: frozen)
      windows = generate_rolling_windows(
        is_months=12,
        oos_months=3,
        last_oos_end="2025-06-30",
        data_start="2022-01-01",
      )
      final = windows[-1]
      assert final["oos_start"] is None
      assert final["is_end"] == frozen.isoformat()
    finally:
      clock.set_today_provider(None)

  def test_clock_today_matches_provider(self):
    from backend.utils import clock

    try:
      clock.set_today_provider(lambda: date(2024, 7, 4))
      assert clock.today() == date(2024, 7, 4)
    finally:
      clock.set_today_provider(None)
    # After restoration, today() is close to the real UTC date
    assert abs((clock.today() - datetime.utcnow().date()).days) <= 1


# --------------------------------------------------------------------------
# Test 22 — Exec-filter presence on WFO OOS path
# --------------------------------------------------------------------------

class TestExecFilterOnWfo:
  """MEMORY.md rule: WFAS must always be evaluated with an execution filter.

  batch_selection surfaces `has_exec_filter` on its summary output; this
  test asserts the key is always present so dashboards can flag runs that
  accidentally skipped the filter.
  """

  def test_has_exec_filter_key_is_summary_contract(self):
    import inspect
    from backend.analysis import batch_selection
    src = inspect.getsource(batch_selection)
    assert 'summary["has_exec_filter"]' in src, (
      "MEMORY.md rule: every batch_selection summary must expose "
      "has_exec_filter so the UI can flag unfiltered WFAS evaluations."
    )


# --------------------------------------------------------------------------
# Test 23 — Progressive Pruning look-ahead warning
# --------------------------------------------------------------------------

class TestProgressivePruningWarning:
  """Tier 1.2 B01: Progressive Pruning is static — any OOS result under
  pruning_enabled=True uses future-window information. The gate surfaces
  `pruning_look_ahead_warning` on the summary so downstream code (and the
  UI) can refuse to save or prominently warn.
  """

  def test_warning_flag_documented_on_summary(self):
    import inspect
    from backend.analysis import batch_selection
    src = inspect.getsource(batch_selection)
    assert 'summary["pruning_look_ahead_warning"]' in src, (
      "Tier 1.2 B01: batch_selection summary must expose "
      "pruning_look_ahead_warning when pruning runs"
    )

  def test_docstring_calls_out_look_ahead(self):
    from backend.analysis.batch_selection import _progressive_prune
    doc = _progressive_prune.__doc__ or ""
    assert "LOOK-AHEAD" in doc.upper(), (
      "Tier 1.2 B01: _progressive_prune docstring must prominently "
      "warn about look-ahead bias"
    )


# --------------------------------------------------------------------------
# Test 24 — Walk-forward live-event label compatibility
# --------------------------------------------------------------------------

class TestWalkForwardLiveEventLabels:
  """The walk-forward timeline must consume the canonical T-002 event-type
  set and remain backward-compat with pre-T-002 legacy labels.

  Post-T-003 Delta 1 contract:
    - `_stitch_event_based_walkforward` filters LiveEvents by an
      `event_types` parameter (no longer a hardcoded list).
    - Default = `DEFAULT_STITCHER_EVENT_TYPES`, which is the canonical
      T-002 set imported from `phase2_recorder.BASKET_EVENT_TYPES`
      UNION the pre-T-002 legacy labels (`hybrid_nightly_refresh`,
      `basket_restore`).
    - The canonical set is the single source of truth; future event-type
      additions land in `phase2_recorder` only.
  """

  def test_stitcher_imports_canonical_event_types_from_phase2_recorder(self):
    """The stitcher must import `BASKET_EVENT_TYPES` from
    `phase2_recorder` (the single source of truth) rather than hardcoding
    its own list. This kills the v1 bug where the stitcher missed
    `phase2_evaluated_no_change` and `hybrid_rotation` event types.
    """
    import inspect
    from backend.api.routes import portfolio_analysis

    src = inspect.getsource(portfolio_analysis)
    assert "from backend.services.phase2_recorder import BASKET_EVENT_TYPES" in src, (
      "Stitcher must import BASKET_EVENT_TYPES from phase2_recorder — "
      "single source of truth for canonical T-002 event types."
    )

  def test_default_stitcher_event_types_includes_t002_and_legacy(self):
    """The default filter must cover both the canonical T-002 set AND
    the pre-T-002 legacy labels. This guarantees backward-compat with
    older DBs that contain `hybrid_nightly_refresh` rows.
    """
    from backend.api.routes.portfolio_analysis import DEFAULT_STITCHER_EVENT_TYPES
    from backend.services.phase2_recorder import BASKET_EVENT_TYPES

    # Canonical T-002 set must be a subset of the default
    for et in BASKET_EVENT_TYPES:
      assert et in DEFAULT_STITCHER_EVENT_TYPES, f"missing canonical T-002 event_type: {et}"

    # Pre-T-002 legacy labels must remain in the default for back-compat
    for legacy in ("hybrid_nightly_refresh", "basket_restore"):
      assert legacy in DEFAULT_STITCHER_EVENT_TYPES, f"missing legacy event_type: {legacy}"

  def test_stitcher_uses_parameterised_event_types_filter(self):
    """The live-events query inside the stitcher must use the
    `event_types` parameter, not a hardcoded list literal. This pins the
    T-003 Delta 1 contract: callers can opt into narrower / wider sets.
    """
    import inspect
    from backend.api.routes import portfolio_analysis

    src = inspect.getsource(portfolio_analysis._stitch_event_based_walkforward)
    # Hardcoded literal must be gone from the function body
    assert '["hybrid_refresh", "hybrid_nightly_refresh", "basket_restore"]' not in src, (
      "Hardcoded event_type list must be replaced with the `event_types` parameter."
    )
    # Parameter must be wired to the .in_() filter
    assert "event_type.in_(list(event_types))" in src, (
      "Stitcher must filter live events via the parameterised `event_types`, "
      "not a hardcoded list."
    )


# --------------------------------------------------------------------------
# Test 25 — Live entry guard cleanup on all failure paths
# --------------------------------------------------------------------------

class TestEntryGuardCleanup:
  """Every early-exit path in EngineCoordinator._handle_entry_signal must
  clear the in-flight entry guard. Leaks cause a symbol/side pair to be
  blocked for up to 120s (the stale-sweep timeout) after any failure.

  The test reads the function source and asserts that the cleanup call
  `self._in_flight_entries.pop(flight_key, None)` appears *after* the guard
  is set, and at least once per known failure-log marker.
  """

  def test_all_failure_paths_clear_flight_key(self):
    import inspect
    from backend.exchange.engine_coordinator import EngineCoordinator
    src = inspect.getsource(EngineCoordinator._handle_entry_signal)

    set_idx = src.find("self._in_flight_entries[flight_key] = now")
    assert set_idx > 0, "guard must be set via flight_key assignment"

    # Each of these failure log markers must be followed by a cleanup call
    # before the next return/raise. Markers are chosen to match the log
    # message at the failure site (not comments referring to the check).
    markers = [
      "Safety check blocked entry",           # safety_monitor block
      "Max positions reached",                 # hard position cap
      "exceeds max $%.2f",                     # post-sizing notional guardrail
      "Failed to process entry signal",        # generic exception handler
    ]
    for marker in markers:
      m_idx = src.find(marker)
      assert m_idx > set_idx, f"failure marker {marker!r} not found after guard set"
      # Look forward from the marker for the cleanup pop within a reasonable window
      window = src[m_idx: m_idx + 400]
      assert "self._in_flight_entries.pop(flight_key, None)" in window, (
        f"Failure path {marker!r} does not clear the in-flight guard — "
        f"entry will stay blocked for up to 120s until the stale sweep runs."
      )
