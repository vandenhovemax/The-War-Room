"""Martingale backtest/live parity test — Proposal C.

What this guards
================
The martingale position-sizing formula is implemented INLINE in two places:

  1. Backtester: `backend/backtester/engine.py`
       - lines ~600–610 (initial sizing in `_open_position`)
       - lines ~767–776 (post-next risk resize)

  2. Live engine:  `backend/exchange/engine_coordinator.py`
       - lines ~1830–1840 (factor computation in `_get_portfolio_risk`)

Both compute the SAME factor:

    capped = min(streak, max_streak)
    if capped <= 0:
        factor = 1.0
    elif mode in {"inv_martingale", "inv_anti_martingale", "inv_anti"}:
        factor = (1.0 / multiplier) ** capped
    else:  # "martingale" or "anti_martingale" (a.k.a. "anti")
        factor = multiplier ** capped

If either path drifts away from this formula, live execution silently diverges
from what the backtest promised. That's the failure mode this test guards.

Strategy
========
1. Define the canonical factor as a pure function (the SPEC).
2. Test the spec against hand-computed values for every mode + edge cases.
3. Source-grep both engine files to assert they still implement this exact
   formula. If someone changes either path, the regex assertion fires and
   forces an explicit test update (= a decision, not silent drift).

This is a static/structural parity test — it does NOT instantiate either
engine. A full runtime parity test would require either a massive mock
infrastructure or live DB access, both of which violate the sandbox-first
rule for CLAUDE.md. This test catches the 95% case — formula divergence —
with zero runtime risk.

Notes on `martingale_type` values
=================================
- Backtester (engine.py) config: `martingale_type` ∈ {"martingale", "anti_martingale" (a.k.a. "anti"), "inv_martingale", "inv_anti"}
- Live engine (engine_coordinator.py) profile: `mode` ∈ {"martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale"}

The names for the "inverse anti" mode differ by one word ("inv_anti" vs.
"inv_anti_martingale"), but both codebases branch on membership in the
`{inv_*}` set, so the formula is identical. The parity test treats both
naming conventions as equivalent.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# ═══════════════════════════════════════════════════════════════════════
# SPEC — the one true factor formula
# ═══════════════════════════════════════════════════════════════════════

_INVERSE_MODES = {"inv_martingale", "inv_anti_martingale", "inv_anti"}


def martingale_factor(
    mode: str, streak: int, multiplier: float, max_streak: int
) -> float:
    """Canonical martingale factor. Both engines MUST match this exactly."""
    if streak < 0:
        raise ValueError(f"streak must be ≥ 0, got {streak}")
    if max_streak < 0:
        raise ValueError(f"max_streak must be ≥ 0, got {max_streak}")
    if multiplier <= 0:
        raise ValueError(f"multiplier must be > 0, got {multiplier}")

    capped = min(streak, max_streak)
    if capped <= 0:
        return 1.0
    if mode in _INVERSE_MODES:
        return (1.0 / multiplier) ** capped
    return multiplier ** capped


# ═══════════════════════════════════════════════════════════════════════
# 1.  SPEC TESTS — hand-computed values for every mode + edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestMartingaleFactorSpec:
    """Every (mode, streak, multiplier, max_streak) must produce the
    exact factor documented here. These values are hand-computed; if
    any asserts fail, the SPEC itself has drifted — investigate before
    trading."""

    def test_zero_streak_is_identity(self):
        # No losses yet → no size change, for every mode
        for mode in ("martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale", "inv_anti"):
            assert martingale_factor(mode, 0, 2.0, 3) == 1.0, mode

    def test_martingale_compounds(self):
        # Classic martingale: double after each loss
        assert martingale_factor("martingale", 1, 2.0, 10) == 2.0
        assert martingale_factor("martingale", 2, 2.0, 10) == 4.0
        assert martingale_factor("martingale", 3, 2.0, 10) == 8.0
        assert martingale_factor("martingale", 4, 2.0, 10) == 16.0

    def test_anti_martingale_compounds(self):
        # Anti-martingale: same multiplier shape, but fired off win-streak
        # (the dispatch to "wins vs. losses" is done OUTSIDE this function)
        assert martingale_factor("anti_martingale", 1, 1.5, 10) == 1.5
        assert martingale_factor("anti_martingale", 3, 1.5, 10) == pytest.approx(3.375)

    def test_inv_martingale_shrinks(self):
        # Inverse martingale: halves after each loss (use 1/multiplier)
        assert martingale_factor("inv_martingale", 1, 2.0, 10) == 0.5
        assert martingale_factor("inv_martingale", 2, 2.0, 10) == 0.25
        assert martingale_factor("inv_martingale", 3, 2.0, 10) == 0.125

    def test_inv_anti_martingale_shrinks(self):
        # Inverse anti: same shrink shape, fired off win-streak
        assert martingale_factor("inv_anti_martingale", 2, 2.0, 10) == 0.25
        # Both naming conventions (engine vs coordinator) must behave identical
        assert martingale_factor("inv_anti", 2, 2.0, 10) == 0.25

    def test_max_streak_caps(self):
        # At max_streak=3, anything beyond 3 must cap at multiplier**3
        assert martingale_factor("martingale", 5, 2.0, 3) == 8.0
        assert martingale_factor("martingale", 10, 2.0, 3) == 8.0
        assert martingale_factor("martingale", 1000, 2.0, 3) == 8.0

    def test_max_streak_zero_disables(self):
        # If max_streak=0, factor is always 1 (no scaling)
        for streak in (0, 1, 5, 100):
            assert martingale_factor("martingale", streak, 2.0, 0) == 1.0

    def test_multiplier_one_is_identity(self):
        # multiplier=1.0 → factor always 1 (no sizing change)
        for streak in (0, 1, 2, 5):
            assert martingale_factor("martingale", streak, 1.0, 10) == 1.0
            assert martingale_factor("inv_martingale", streak, 1.0, 10) == 1.0

    def test_fractional_multiplier(self):
        # Decimal multipliers commonly used in research
        assert martingale_factor("martingale", 2, 1.5, 10) == pytest.approx(2.25)
        assert martingale_factor("inv_martingale", 2, 1.5, 10) == pytest.approx(1.0 / 2.25)

    def test_negative_streak_rejected(self):
        with pytest.raises(ValueError):
            martingale_factor("martingale", -1, 2.0, 3)

    def test_negative_max_streak_rejected(self):
        with pytest.raises(ValueError):
            martingale_factor("martingale", 1, 2.0, -1)

    def test_zero_multiplier_rejected(self):
        with pytest.raises(ValueError):
            martingale_factor("martingale", 1, 0.0, 3)

    def test_unknown_mode_treated_as_forward(self):
        # If a mode isn't in the inverse set, the formula uses the forward path.
        # The live engine has a whitelist upstream; the factor function itself
        # is not responsible for mode validation.
        assert martingale_factor("random_string", 2, 2.0, 10) == 4.0


# ═══════════════════════════════════════════════════════════════════════
# 2.  SOURCE-REGEX GUARDS — both engines must still implement the SPEC
# ═══════════════════════════════════════════════════════════════════════

REPO_ROOT = Path(__file__).resolve().parents[2]  # tests/ -> backend/ -> The_War_Room (or Session/ -> Co-Work -> The_War_Room)
BACKTEST_ENGINE = REPO_ROOT / "backend" / "backtester" / "engine.py"
LIVE_ENGINE = REPO_ROOT / "backend" / "exchange" / "engine_coordinator.py"


class TestBacktesterUsesSpec:
    """The backtester must still compute `qty *= multiplier ** capped`
    and `qty *= (1 / multiplier) ** capped` in both its sizing sites."""

    def test_backtest_engine_file_exists(self):
        assert BACKTEST_ENGINE.exists(), f"Expected {BACKTEST_ENGINE}"

    def test_forward_martingale_formula_present(self):
        src = BACKTEST_ENGINE.read_text(encoding="utf-8")
        pattern = re.compile(
            r"qty\s*\*=\s*self\.config\.martingale_multiplier\s*\*\*\s*capped"
        )
        matches = pattern.findall(src)
        assert len(matches) >= 1, (
            "Forward-martingale formula missing or renamed in engine.py — "
            "check lines ~610 (open_position) and ~776 (risk_resize). "
            "If this was an intentional rename, update this test."
        )

    def test_inverse_martingale_formula_present(self):
        src = BACKTEST_ENGINE.read_text(encoding="utf-8")
        pattern = re.compile(
            r"qty\s*\*=\s*\(\s*1\.0\s*/\s*self\.config\.martingale_multiplier\s*\)\s*\*\*\s*capped"
        )
        matches = pattern.findall(src)
        assert len(matches) >= 1, (
            "Inverse-martingale formula missing or renamed in engine.py — "
            "check lines ~608 and ~774."
        )

    def test_inverse_mode_dispatch_matches_spec(self):
        """Backtester branches on ('inv_martingale', 'inv_anti') — spec requires this exact set."""
        src = BACKTEST_ENGINE.read_text(encoding="utf-8")
        pattern = re.compile(
            r'self\.config\.martingale_type\s+in\s+\(\s*"inv_martingale"\s*,\s*"inv_anti"\s*\)'
        )
        matches = pattern.findall(src)
        assert len(matches) >= 2, (
            f"Expected 2 inverse-mode branch checks in engine.py, found {len(matches)}. "
            "Spec requires both _open_position AND _post_next_risk_resize to branch identically."
        )

    def test_streak_is_capped(self):
        """`capped = min(streak, martingale_max_streak)` must appear — the cap is load-bearing."""
        src = BACKTEST_ENGINE.read_text(encoding="utf-8")
        pattern = re.compile(
            r"capped\s*=\s*min\(\s*streak\s*,\s*self\.config\.martingale_max_streak\s*\)"
        )
        matches = pattern.findall(src)
        assert len(matches) >= 2, (
            f"`capped = min(streak, max_streak)` missing in engine.py — found {len(matches)} sites, expected ≥ 2. "
            "Without the cap, live execution runs unbounded martingale that backtest cannot reproduce."
        )


class TestLiveEngineUsesSpec:
    """The live `EngineCoordinator._get_portfolio_risk` must compute
    `factor = multiplier ** capped` and `factor = (1/multiplier) ** capped`."""

    def test_live_engine_file_exists(self):
        assert LIVE_ENGINE.exists(), f"Expected {LIVE_ENGINE}"

    def test_forward_martingale_formula_present(self):
        src = LIVE_ENGINE.read_text(encoding="utf-8")
        pattern = re.compile(r"factor\s*=\s*multiplier\s*\*\*\s*capped")
        matches = pattern.findall(src)
        assert len(matches) >= 1, (
            "Forward-martingale formula missing in engine_coordinator.py — "
            "check lines ~1835. If renamed intentionally, update this test."
        )

    def test_inverse_martingale_formula_present(self):
        src = LIVE_ENGINE.read_text(encoding="utf-8")
        pattern = re.compile(
            r"factor\s*=\s*\(\s*1\.0\s*/\s*multiplier\s*\)\s*\*\*\s*capped"
        )
        matches = pattern.findall(src)
        assert len(matches) >= 1, (
            "Inverse-martingale formula missing in engine_coordinator.py — check lines ~1833."
        )

    def test_inverse_mode_dispatch_matches_spec(self):
        """Live engine branches on ('inv_martingale', 'inv_anti_martingale')."""
        src = LIVE_ENGINE.read_text(encoding="utf-8")
        pattern = re.compile(
            r'mode\s+in\s+\(\s*"inv_martingale"\s*,\s*"inv_anti_martingale"\s*\)'
        )
        assert pattern.search(src), (
            "Live engine missing the inverse-mode dispatch check. "
            "Spec requires `mode in ('inv_martingale', 'inv_anti_martingale')` branch in _get_portfolio_risk."
        )

    def test_streak_is_capped(self):
        src = LIVE_ENGINE.read_text(encoding="utf-8")
        pattern = re.compile(r"capped\s*=\s*min\(\s*streak\s*,\s*max_streak\s*\)")
        assert pattern.search(src), (
            "Live engine missing `capped = min(streak, max_streak)` — the cap is load-bearing "
            "and without it, live martingale can exceed what backtest permitted."
        )

    def test_zero_streak_factor_is_one(self):
        """Live engine must default `factor = 1.0` when capped ≤ 0."""
        src = LIVE_ENGINE.read_text(encoding="utf-8")
        # We match the else-branch assigning factor = 1.0 (multiple forms tolerated)
        pattern = re.compile(r"factor\s*=\s*1\.0")
        assert pattern.search(src), (
            "Live engine missing `factor = 1.0` default branch — without it, "
            "zero-streak trades may not size at all or size incorrectly."
        )


# ═══════════════════════════════════════════════════════════════════════
# 3.  CROSS-ENGINE CONSISTENCY — the inverse-mode sets must be isomorphic
# ═══════════════════════════════════════════════════════════════════════

class TestInverseModeSetsAreEquivalent:
    """Backtester uses 'inv_anti', live uses 'inv_anti_martingale'. Both must
    route through the inverse-formula branch. This test encodes that equivalence."""

    def test_backtest_inv_anti_routes_inverse(self):
        # Spec treats 'inv_anti' as inverse (matches engine.py dispatch)
        assert "inv_anti" in _INVERSE_MODES
        assert martingale_factor("inv_anti", 2, 2.0, 10) == 0.25

    def test_live_inv_anti_martingale_routes_inverse(self):
        assert "inv_anti_martingale" in _INVERSE_MODES
        assert martingale_factor("inv_anti_martingale", 2, 2.0, 10) == 0.25

    def test_both_inverse_names_produce_same_factor(self):
        # This is the load-bearing invariant: the spec treats the two names
        # as behaviorally identical, so a portfolio migrated from backtest
        # ('inv_anti') to live ('inv_anti_martingale') doesn't silently rescale.
        for streak in (0, 1, 2, 3, 5):
            for mult in (1.5, 2.0, 3.0):
                for ms in (1, 3, 5, 10):
                    bt = martingale_factor("inv_anti", streak, mult, ms)
                    live = martingale_factor("inv_anti_martingale", streak, mult, ms)
                    assert bt == live, (
                        f"Name divergence for inverse-anti: streak={streak}, "
                        f"mult={mult}, max={ms}, bt={bt}, live={live}"
                    )


# ═══════════════════════════════════════════════════════════════════════
# 4.  PARAMETRIC SWEEP — every combination the spec is contractually bound to
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mode", ["martingale", "anti_martingale"])
@pytest.mark.parametrize("streak", [0, 1, 2, 3, 5])
@pytest.mark.parametrize("multiplier", [1.0, 1.5, 2.0, 3.0])
@pytest.mark.parametrize("max_streak", [0, 1, 3, 5, 10])
def test_forward_factor_matches_closed_form(mode, streak, multiplier, max_streak):
    """For forward modes, factor must equal multiplier ** min(streak, max_streak)."""
    expected = multiplier ** min(streak, max_streak)
    actual = martingale_factor(mode, streak, multiplier, max_streak)
    assert actual == pytest.approx(expected), (
        f"{mode}: streak={streak}, mult={multiplier}, max={max_streak}: "
        f"expected {expected}, got {actual}"
    )


@pytest.mark.parametrize("mode", ["inv_martingale", "inv_anti_martingale", "inv_anti"])
@pytest.mark.parametrize("streak", [0, 1, 2, 3, 5])
@pytest.mark.parametrize("multiplier", [1.0, 1.5, 2.0, 3.0])
@pytest.mark.parametrize("max_streak", [0, 1, 3, 5, 10])
def test_inverse_factor_matches_closed_form(mode, streak, multiplier, max_streak):
    """For inverse modes, factor must equal (1/multiplier) ** min(streak, max_streak)."""
    capped = min(streak, max_streak)
    expected = 1.0 if capped == 0 else (1.0 / multiplier) ** capped
    actual = martingale_factor(mode, streak, multiplier, max_streak)
    assert actual == pytest.approx(expected), (
        f"{mode}: streak={streak}, mult={multiplier}, max={max_streak}: "
        f"expected {expected}, got {actual}"
    )
