"""CS-069: the portfolio branch of ``/monte-carlo`` must use the same
date-cutoff semantics as the batch and run branches.

Previous state — three divergent cutoffs for the same logical request:
  * portfolio: ``Entry Time <= pd.Timestamp(date_to)``  (midnight
    instant — drops trades entered later on date_to)
  * batch: ``_load_batch_trades(..., clip_boundary=True)``
  * run: ``entry_time < pd.Timestamp(date_to) + 1 day`` + clip_is_trades

The fix: portfolio branch now uses ``< date_to + 1 day`` and also
calls ``clip_is_trades`` — parity with the run branch.

This test locks the invariant at the source level so a revert to the
old ``<= pd.Timestamp(date_to)`` cutoff trips the build.
"""
from __future__ import annotations

import inspect

from backend.api.routes import analysis as analysis_mod


def test_monte_carlo_portfolio_branch_uses_midnight_plus_one_cutoff():
  """Source inspection: the portfolio branch must no longer use the
  old ``<= pd.Timestamp(req.date_to)`` comparison. The canonical form
  is ``< pd.Timestamp(req.date_to) + pd.Timedelta(days=1)``.
  """
  src = inspect.getsource(analysis_mod.monte_carlo)

  # Forbid the old broken cutoff.
  bad_cutoff = 'df["Entry Time"] <= pd.Timestamp(req.date_to)'
  assert bad_cutoff not in src, (
    "CS-069 REGRESSION: monte_carlo portfolio branch reverted to "
    "`<= pd.Timestamp(date_to)` cutoff. Trades entered on date_to "
    "after 00:00:00 are silently excluded — Monte Carlo stats will "
    "disagree with the batch and run branches for the same dataset."
  )

  # Require the canonical form.
  assert "pd.Timedelta(days=1)" in src, (
    "CS-069 REGRESSION: monte_carlo portfolio branch no longer adds "
    "a 1-day offset to the cutoff. The batch and run branches both "
    "treat date_to as the last WHOLE day included — the portfolio "
    "must too."
  )


def test_monte_carlo_portfolio_branch_calls_clip_is_trades():
  """Source inspection: the portfolio branch must call
  ``clip_is_trades`` for IS-straddling parity with the run branch.
  Without this, a trade entered before date_to and exiting after
  keeps its full OOS-leaking PnL in the Monte Carlo sample.
  """
  src = inspect.getsource(analysis_mod.monte_carlo)
  # The function must call clip_is_trades at least TWICE — once in the
  # run branch and once in the new portfolio branch (plus the import
  # elsewhere). Count the substring.
  call_count = src.count("clip_is_trades(")
  assert call_count >= 2, (
    f"CS-069 REGRESSION: monte_carlo only calls clip_is_trades "
    f"{call_count} times — expected at least 2 (run branch + "
    "portfolio branch). The portfolio branch is silently leaking OOS "
    "trade PnL into the IS Monte Carlo distribution."
  )


def test_fake_trade_class_carries_entry_time():
  """Behavioural: the _FakeTrade built for portfolio Monte Carlo must
  carry entry_time so clip_is_trades can detect straddles. Without
  it, the clip pass is a no-op because it compares t.entry_time
  against None.
  """
  src = inspect.getsource(analysis_mod.monte_carlo)
  # The FakeTrade constructor must set self.entry_time
  assert "self.entry_time" in src, (
    "CS-069 REGRESSION: _FakeTrade in monte_carlo no longer stores "
    "entry_time. clip_is_trades needs entry_time < cutoff < exit_time "
    "to detect straddling trades — without it, the clip pass silently "
    "no-ops and OOS PnL leaks in."
  )
  assert "entry_time=row.get(\"Entry Time\"" in src or \
         "entry_time = row.get(\"Entry Time\"" in src, (
    "CS-069 REGRESSION: _FakeTrade is no longer populated from the "
    "'Entry Time' column. The Monte Carlo clip cannot detect "
    "straddling positions without the entry-time stamp."
  )
