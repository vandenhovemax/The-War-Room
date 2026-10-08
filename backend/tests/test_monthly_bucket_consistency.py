"""Regression tests for monthly-matrix bucketing consistency in
`backend/api/routes/portfolio_analysis.py`.

Bug: For WFAS replay portfolios (i.e. those with `wfo_per_window` +
`wfo_windows_meta`), the IS/OOS split in `portfolio_oos_date` and
`portfolio_full_metrics_wfo` uses `Entry Time`, but the monthly
matrix returned by those same routes used to bucket by `Exit Time`.
A trade that entered in March and exited in April was placed in
March by the IS/OOS split (Entry-Time-based) but in April by the
monthly matrix (Exit-Time-based) — same row in the table showed
different values than the modal drilldown which buckets by Entry
Time.

The fix forces Entry-Time bucketing on the monthly matrix for WFAS
replay portfolios, matching:
  * `portfolio_full_metrics` line 1032 (already Entry Time for replay)
  * `portfolio_month_trades` line 2060 (already Entry Time for replay)
  * `portfolio_monthly_filtered` line 2225 (already Entry Time for replay)

These tests pin the contract: a trade with Entry Time in month X and
Exit Time in month X+1 MUST be attributed to month X for WFAS
replay portfolios.
"""
from __future__ import annotations

import pandas as pd


def _build_monthly_matrix_entry_bucketed(src_df: pd.DataFrame, bucket_col: str) -> dict:
  """Re-implementation of the fixed _build_monthly_matrix shape for testing.

  Mirrors the production code in portfolio_oos_date._build_monthly_matrix
  after the fix. We don't import the route function directly because it's
  embedded inside an HTTP handler; the lambda here pins the same logic.
  """
  result: dict[str, dict[str, dict]] = {}
  if src_df.empty:
    return result
  tmp = src_df.copy()
  tmp[bucket_col] = pd.to_datetime(tmp[bucket_col])
  tmp["Year"] = tmp[bucket_col].dt.year
  tmp["Month"] = tmp[bucket_col].dt.month
  for (yr, mo), grp in tmp.groupby(["Year", "Month"]):
    yr_str = str(int(yr))
    if yr_str not in result:
      result[yr_str] = {}
    total = len(grp)
    wins = len(grp[grp["Net Profit"] > 0])
    result[yr_str][str(int(mo))] = {
      "pnl": round(float(grp["Net Profit"].sum()), 2),
      "trades": total,
      "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
    }
  return result


def test_cross_month_trade_bucketed_by_entry_time_for_wfas_replay():
  """The exact bug case: a trade entered in March 2026 but exited in
  April 2026. Must land in March (Entry-Time bucket) for WFAS replay.
  """
  df = pd.DataFrame([
    {"Entry Time": "2026-03-31 10:30:00", "Exit Time": "2026-04-01 14:30:00",
     "Net Profit": 500.0},
    {"Entry Time": "2026-03-15 10:00:00", "Exit Time": "2026-03-20 12:00:00",
     "Net Profit": 200.0},
    {"Entry Time": "2026-04-05 11:00:00", "Exit Time": "2026-04-06 14:00:00",
     "Net Profit": -100.0},
  ])
  matrix = _build_monthly_matrix_entry_bucketed(df, bucket_col="Entry Time")

  # March 2026: 2 trades (the cross-month one + the all-March one), pnl = $700
  assert matrix["2026"]["3"]["trades"] == 2
  assert matrix["2026"]["3"]["pnl"] == 700.0

  # April 2026: 1 trade (the all-April one only), pnl = -$100
  assert matrix["2026"]["4"]["trades"] == 1
  assert matrix["2026"]["4"]["pnl"] == -100.0


def test_old_exit_time_bucketing_would_misattribute():
  """Negative control: bucketing by Exit Time WOULD have placed the
  cross-month trade in April, producing a different (and inconsistent)
  result than the IS/OOS split which uses Entry Time.
  """
  df = pd.DataFrame([
    {"Entry Time": "2026-03-31 10:30:00", "Exit Time": "2026-04-01 14:30:00",
     "Net Profit": 500.0},
    {"Entry Time": "2026-03-15 10:00:00", "Exit Time": "2026-03-20 12:00:00",
     "Net Profit": 200.0},
    {"Entry Time": "2026-04-05 11:00:00", "Exit Time": "2026-04-06 14:00:00",
     "Net Profit": -100.0},
  ])
  matrix_exit = _build_monthly_matrix_entry_bucketed(df, bucket_col="Exit Time")

  # Under Exit Time: March 2026 only has 1 trade (pnl $200), the cross-
  # month trade landed in April. This is the OLD (buggy) shape.
  assert matrix_exit["2026"]["3"]["trades"] == 1
  assert matrix_exit["2026"]["3"]["pnl"] == 200.0
  # April under Exit Time has 2 trades (the cross-month + the all-April).
  assert matrix_exit["2026"]["4"]["trades"] == 2
  assert matrix_exit["2026"]["4"]["pnl"] == 400.0  # 500 + (-100)

  # The mismatch between Entry-Time and Exit-Time bucketing is what
  # caused the table-vs-popup divergence.


def test_same_month_trades_unchanged_by_bucketing_choice():
  """Sanity: when Entry Time and Exit Time are in the same month,
  the monthly matrix is identical regardless of bucket column. This
  ensures the fix is invisible for the common case.
  """
  df = pd.DataFrame([
    {"Entry Time": "2026-03-05 10:00:00", "Exit Time": "2026-03-06 14:00:00",
     "Net Profit": 100.0},
    {"Entry Time": "2026-03-15 11:00:00", "Exit Time": "2026-03-20 09:00:00",
     "Net Profit": -50.0},
  ])
  matrix_entry = _build_monthly_matrix_entry_bucketed(df, bucket_col="Entry Time")
  matrix_exit = _build_monthly_matrix_entry_bucketed(df, bucket_col="Exit Time")
  assert matrix_entry == matrix_exit
  assert matrix_entry["2026"]["3"]["trades"] == 2
  assert matrix_entry["2026"]["3"]["pnl"] == 50.0


def test_iso_oos_split_and_monthly_matrix_agree_under_entry_bucketing():
  """Integration-style: pin that an Entry-Time-based IS/OOS split and an
  Entry-Time-based monthly matrix put the same trade in the same month.
  Without this, you get the table-vs-popup divergence a user reported.
  """
  cutoff = pd.Timestamp("2026-03-31")
  df = pd.DataFrame([
    # OOS by Entry Time (entered Apr 1, after cutoff). MUST land in April.
    {"Entry Time": "2026-04-01 10:00:00", "Exit Time": "2026-04-02 14:00:00",
     "Net Profit": 100.0},
    # IS by Entry Time (entered Mar 31, on or before cutoff). The trade
    # exited Apr 5 — would have been ATTRIBUTED to April under the old
    # Exit-Time bucket, but the IS/OOS split (Entry Time) says March.
    # Entry-Time bucketing in the matrix → consistent: matrix says March,
    # IS/OOS says March. ✅
    {"Entry Time": "2026-03-31 10:30:00", "Exit Time": "2026-04-05 14:30:00",
     "Net Profit": 200.0},
  ])
  df["Entry Time"] = pd.to_datetime(df["Entry Time"])
  df["Exit Time"] = pd.to_datetime(df["Exit Time"])

  is_df = df[df["Entry Time"].dt.date <= cutoff.date()].copy()
  oos_df = df[df["Entry Time"].dt.date > cutoff.date()].copy()

  is_monthly = _build_monthly_matrix_entry_bucketed(is_df, bucket_col="Entry Time")
  oos_monthly = _build_monthly_matrix_entry_bucketed(oos_df, bucket_col="Entry Time")

  # IS contains only the Mar 31 trade (entry-time-based split agrees with
  # entry-time-based bucketing).
  assert is_monthly["2026"]["3"]["trades"] == 1
  assert is_monthly["2026"]["3"]["pnl"] == 200.0
  assert "4" not in is_monthly.get("2026", {}), \
    "IS trade entered Mar 31 must NOT land in April under Entry-Time bucketing"

  # OOS contains only the Apr 1 trade.
  assert oos_monthly["2026"]["4"]["trades"] == 1
  assert oos_monthly["2026"]["4"]["pnl"] == 100.0
  assert "3" not in oos_monthly.get("2026", {})
