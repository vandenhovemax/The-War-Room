"""Regression guard: `_try_spot_replace` must honour `scoring_method="composite"`.

Bug summary:
  Before this fix, `_try_spot_replace` ranked both the incumbent and its
  challenger by a single `objective` metric regardless of the run's
  `scoring_method`. Under `scoring_method="composite"`, the configured
  `composite_weights` were silently ignored during spot_single / spot_batch
  rebalances, so the `hybrid_objective` knob secretly drove swap decisions
  even when the user believed composite was in charge.

  These tests set up a pair of asset buffers whose ordering **flips**
  between "best by sortino" and "best by PF". If composite_weights are
  respected, the PF-biased composite picks the PF-winner; the legacy single
  path picks the sortino-winner. Running `_try_spot_replace` with the same
  `objective="sortino"` but different `scoring_method` must produce
  different swap outcomes — otherwise the fix regressed.
"""
from __future__ import annotations

from datetime import datetime

from backend.analysis.event_wfo import _try_spot_replace, _compute_event_metrics


# ── Fixture: two buffers where sortino- and PF-ranks disagree ────────────
#
# Both buffers are exactly 10 trades so the `buf[-n_trades:]` slice with
# n_trades=10 keeps every value intact. Both stay BELOW the PF cap of 10
# (event_wfo._compute_event_metrics caps pf at 10.0) so PF comparisons are
# meaningful.
#
# SORTINO_WINNER: 3 big wins + 7 unit losses.
#   gp=30, gl=7, PF = 30/7 ≈ 4.29
#   mean = 23/10 = 2.3, downside = [-1]*7, ds_dev = 1.0 → sortino = 2.3
#
# PF_WINNER: 8 small wins + 2 unit losses.
#   gp=16, gl=2, PF = 8.0
#   mean = 14/10 = 1.4, downside = [-1]*2, ds_dev = 1.0 → sortino = 1.4
#
# Divergence: SORTINO_WINNER has sortino 2.3 vs 1.4 (wins sortino by 0.9).
#             PF_WINNER has PF 8.0 vs 4.29 (wins PF by ~3.7).
SORTINO_WINNER_BUF = [10.0, 10.0, 10.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0]
PF_WINNER_BUF      = [2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, -1.0, -1.0]


def _sanity_check_fixture():
  """Verify the fixture actually produces divergent rankings."""
  m_pf = _compute_event_metrics(PF_WINNER_BUF)
  m_so = _compute_event_metrics(SORTINO_WINNER_BUF)
  # PF_WINNER must win on PF (no losses → capped 10), SORTINO_WINNER wins on sortino
  assert m_pf["pf"] >= m_so["pf"], f"fixture broken: PF order inverted ({m_pf['pf']} vs {m_so['pf']})"
  assert m_so["sortino"] > m_pf["sortino"], \
    f"fixture broken: sortino order inverted ({m_so['sortino']} vs {m_pf['sortino']})"
  return m_pf, m_so


def test_fixture_is_divergent():
  """Guard against future edits breaking the crafted ranking divergence."""
  _sanity_check_fixture()


def _build_args(incumbent_key: str, contender_key: str,
                incumbent_buf: list[float], contender_buf: list[float]):
  """Common scaffold for calling `_try_spot_replace`."""
  ev_time = datetime(2026, 1, 15, 12, 0)
  # Populate asset_trade_times uniformly — days_span is irrelevant for
  # non-calmar metrics, but must be non-empty for _compute_event_metrics.
  times = [datetime(2026, 1, d + 1, 12, 0) for d in range(max(len(incumbent_buf), len(contender_buf)))]
  asset_buffers = {incumbent_key: list(incumbent_buf), contender_key: list(contender_buf)}
  asset_trade_times = {incumbent_key: times[:len(incumbent_buf)], contender_key: times[:len(contender_buf)]}
  asset_last_time = {incumbent_key: ev_time, contender_key: ev_time}
  current_portfolio = {incumbent_key}
  asset_locks: dict[str, int] = {}
  return dict(
    check_assets={incumbent_key},
    asset_buffers=asset_buffers,
    asset_last_time=asset_last_time,
    asset_trade_times=asset_trade_times,
    current_portfolio=current_portfolio,
    trade_time=ev_time,
    n_trades=10,  # match buffer length so the full fixture is scored
    staleness_days=0,
    min_pf=0.0,
    min_wr=0.0,
    objective="sortino",  # single-mode default — should be IGNORED under composite
    min_hold=0,
    asset_locks=asset_locks,
  )


# ── Test 1: single-mode reads `objective="sortino"` → keeps sortino winner ──

def test_spot_replace_single_mode_uses_objective():
  """Under scoring_method='single' with objective='sortino', the incumbent
  should not be swapped out because the PF_WINNER has a lower sortino."""
  _sanity_check_fixture()
  args = _build_args("INC", "CON",
                     incumbent_buf=SORTINO_WINNER_BUF,
                     contender_buf=PF_WINNER_BUF)
  swaps = _try_spot_replace(**args, scoring_method="single", composite_weights=None)
  assert swaps == 0, "single/sortino: incumbent has higher sortino — should stay"
  assert args["current_portfolio"] == {"INC"}


def test_spot_replace_single_mode_swaps_when_objective_prefers_contender():
  """Flip buffers — contender now wins on sortino — swap must fire."""
  args = _build_args("INC", "CON",
                     incumbent_buf=PF_WINNER_BUF,
                     contender_buf=SORTINO_WINNER_BUF)
  swaps = _try_spot_replace(**args, scoring_method="single", composite_weights=None)
  assert swaps == 1
  assert args["current_portfolio"] == {"CON"}


# ── Test 2: composite mode with PF-heavy weights → picks PF winner, ignoring objective ──

def test_spot_replace_composite_uses_weights_not_objective():
  """The bug: under composite, `_try_spot_replace` was still ranking by
  `objective`. This test sets `objective='sortino'` (which would KEEP the
  incumbent) but `composite_weights={'pf': 1.0}` (which SWAPS to the
  PF winner). If the fix is correct, composite wins and the swap fires."""
  _sanity_check_fixture()
  args = _build_args("INC", "CON",
                     incumbent_buf=SORTINO_WINNER_BUF,   # sortino-favoured
                     contender_buf=PF_WINNER_BUF)         # PF-favoured
  swaps = _try_spot_replace(
    **args,
    scoring_method="composite",
    composite_weights={"pf": 1.0},
  )
  assert swaps == 1, (
    "composite with pf-only weights should swap to PF_WINNER, "
    "but spot_replace apparently still ranks by objective='sortino'"
  )
  assert args["current_portfolio"] == {"CON"}


def test_spot_replace_composite_keeps_incumbent_when_composite_prefers_it():
  """Inverse check: under composite with sortino-heavy weights, the sortino
  winner should be kept even though the contender dominates on PF."""
  args = _build_args("INC", "CON",
                     incumbent_buf=SORTINO_WINNER_BUF,
                     contender_buf=PF_WINNER_BUF)
  swaps = _try_spot_replace(
    **args,
    scoring_method="composite",
    composite_weights={"sortino": 1.0},
  )
  assert swaps == 0
  assert args["current_portfolio"] == {"INC"}


# ── Test 3: default args preserve legacy behaviour ──

def test_spot_replace_defaults_match_single_mode():
  """Callers that never pass `scoring_method` / `composite_weights` must
  behave exactly as before (single-mode with the given `objective`)."""
  args = _build_args("INC", "CON",
                     incumbent_buf=SORTINO_WINNER_BUF,
                     contender_buf=PF_WINNER_BUF)
  swaps = _try_spot_replace(**args)  # no new kwargs
  assert swaps == 0  # same as explicit single-mode above
