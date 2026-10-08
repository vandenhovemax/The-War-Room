"""CS-010: audit recommended migrating every raw ``BacktestTrade`` caller
to ``load_trades_lightweight``, with an explicit exception — callers that
need ORM semantics (multi-leg raw rows, unscaled PnL, None-entry rows
preserved) must document the exemption in the module header and continue
to use raw ORM.

Two live-critical paths qualify:

  * ``kelly_allocation._load_portfolio_daily_pnl`` — Kelly's daily PnL
    correlation matrix keys on each leg's ``exit_time``. Collapsing legs
    into ``max(leg_exits)`` shifts Tuesday PnL into Friday and moves the
    correlation matrix → silent drift of live Kelly weights. Also
    sensitive to ``pnl_multiplier`` scaling (doubles the scale on the
    live-sizing math).

  * ``services.parity_canary`` — this IS the parity baseline. Reading
    through the loader would compare "loader-scaled, leg-aggregated
    view" against "raw engine output" and fire false-positives on every
    scaled or multi-leg run.

This test locks both exemptions. If someone silently re-writes either
path to call ``load_trades_lightweight``, the test fails with a pointer
to the audit comment.
"""
from __future__ import annotations

import inspect

from backend.api.routes import kelly_allocation as kelly_mod
from backend.services import parity_canary as canary_mod


def test_kelly_daily_pnl_loader_stays_on_raw_orm():
  """Kelly's portfolio daily-PnL builder must NOT silently switch to
  ``load_trades_lightweight``. The function must either stay on raw ORM
  or carry a dated exemption comment referencing CS-010.
  """
  src = inspect.getsource(kelly_mod)

  # The exemption comment must be present and reference CS-010
  assert "CS-010" in src, (
    "CS-010: kelly_allocation lost its exemption comment. If migration "
    "was intentional, add a dated note (see audit comment at lines "
    "242-258 of kelly_allocation.py) explaining how multi-leg exit_time "
    "collapsing and pnl_multiplier scaling were neutralised."
  )

  # If the loader is imported, it must NOT be called inside
  # _load_portfolio_is_data — that's the live-Kelly path the audit
  # protects (loads per-leg trades + builds daily PnL matrix).
  fn_src = inspect.getsource(kelly_mod._load_portfolio_is_data)
  assert "load_trades_lightweight(" not in fn_src, (
    "CS-010 REGRESSION: _load_portfolio_daily_pnl now calls "
    "load_trades_lightweight. This silently shifts live Kelly weights "
    "because the loader collapses multi-leg exit_times and applies "
    "pnl_multiplier. Read the exemption comment at kelly_allocation.py "
    "(search 'CS-010 (intentionally NOT migrated') before changing this."
  )


def test_parity_canary_stays_on_raw_orm():
  """The parity canary must read raw ``BacktestTrade`` rows. Its whole
  job is to verify stored trades match cold re-runs — the loader would
  rewrite one side of the comparison.
  """
  src = inspect.getsource(canary_mod)

  assert "CS-010" in src, (
    "CS-010: parity_canary lost its exemption comment. The canary MUST "
    "read raw BacktestTrade rows — read the module header for the "
    "full list of reasons (multi-leg aggregation, pnl_multiplier "
    "scaling, entry_time=None drop)."
  )

  # The per-run comparator must not migrate
  fn_src = inspect.getsource(canary_mod._stored_trades)
  assert "load_trades_lightweight(" not in fn_src, (
    "CS-010 REGRESSION: parity_canary._stored_trades now calls "
    "load_trades_lightweight. This routes the parity baseline through "
    "a transform that aggregates multi-leg exits and applies "
    "pnl_multiplier — every scaled or multi-leg run will report false "
    "divergence. Read the exemption in parity_canary.py's module "
    "docstring before changing this."
  )

  # Confirm the raw query shape is still present
  assert "db.query(BacktestTrade)" in fn_src, (
    "CS-010: parity_canary._stored_trades no longer issues a raw "
    "BacktestTrade query. If the query shape moved, update this test "
    "to point at the new accessor — but it MUST still be raw ORM."
  )
