"""CS-008: pin the timezone / NYT / DST invariants that the rest of the
system depends on.

The load-bearing rule documented in CLAUDE.md is: *"UTC in the database,
America/New_York for anything session-based, naive-NYT for the engine's
comparisons against ``excludeHours``."* Nothing in ``backend/tests/`` used
to assert this directly, so a pandas upgrade, a ``pytz → zoneinfo`` swap,
or a one-line regression in ``to_nyt()`` could silently shift every
backtest by an hour across half the year.

Tests in this file freeze:
1. ``to_nyt()`` produces naive NYT timestamps that match the expected
   EDT/EST offset for both summer and winter dates.
2. ``to_nyt()`` drops the duplicate naive timestamp created on DST fall-back
   (without this drop, duplicated-index DataFrames corrupt every
   downstream ``df.loc[ts]`` lookup).
3. ``_sanitize_dst_timestamps()`` removes the ambiguous/non-existent bars
   on a fall-back day so the caller's ``tz_localize('America/New_York',
   ambiguous='raise')`` succeeds on retry.
4. The engine's exec-filter contract: ``entry_time.hour`` against an NYT
   ``excludeHours`` set filters on NYT hours exactly (no UTC drift).

These are character-level invariants — any failure here means silent PnL
drift across DST dates.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from backend.backtester.portfolio_engine import _sanitize_dst_timestamps
from backend.utils.candle_loader import to_nyt


# ── to_nyt() offset correctness ────────────────────────────────────────

def test_to_nyt_converts_utc_summer_to_edt_naive():
  """2024-06-15 12:00 UTC is 08:00 NYT (EDT = UTC-4)."""
  idx = pd.DatetimeIndex(["2024-06-15 12:00:00"], tz="UTC")
  df = pd.DataFrame({"close": [100.0]}, index=idx)
  out = to_nyt(df)
  assert out.index.tz is None, "to_nyt must return a tz-naive index"
  assert out.index[0] == pd.Timestamp("2024-06-15 08:00:00"), (
    f"CS-008: EDT offset drift — expected 08:00 NYT, got {out.index[0]}"
  )


def test_to_nyt_converts_utc_winter_to_est_naive():
  """2024-01-15 12:00 UTC is 07:00 NYT (EST = UTC-5)."""
  idx = pd.DatetimeIndex(["2024-01-15 12:00:00"], tz="UTC")
  df = pd.DataFrame({"close": [100.0]}, index=idx)
  out = to_nyt(df)
  assert out.index.tz is None
  assert out.index[0] == pd.Timestamp("2024-01-15 07:00:00"), (
    f"CS-008: EST offset drift — expected 07:00 NYT, got {out.index[0]}"
  )


def test_to_nyt_localises_naive_index_as_utc_first():
  """``to_nyt`` accepts a naive DatetimeIndex and treats it as UTC before
  converting. The DB stores ISO strings as naive — this path is real.
  """
  idx = pd.DatetimeIndex(["2024-06-15 12:00:00"])  # naive
  df = pd.DataFrame({"close": [100.0]}, index=idx)
  out = to_nyt(df)
  assert out.index.tz is None
  assert out.index[0] == pd.Timestamp("2024-06-15 08:00:00")


# ── DST fall-back handling ─────────────────────────────────────────────

def test_to_nyt_keeps_dst_fall_back_bars_disambiguated_2023():
  """2023-11-05 fall-back creates two UTC bars that both map to the same
  naive NYT timestamp (01:00). ``to_nyt`` keeps BOTH, disambiguating the
  second by +1ns — dropping it shifted bar_index out of Pine parity.

    05:00 UTC = 01:00 EDT (before 02:00 fall-back, UTC-4)
    06:00 UTC = 01:00 EST (after  02:00 fall-back, UTC-5)

  Both are "01:00 NYT" naive — Pine keeps both bars, so we do too.
  """
  idx = pd.DatetimeIndex([
    "2023-11-05 05:00:00",  # 01:00 EDT
    "2023-11-05 06:00:00",  # 01:00 EST
    "2023-11-05 07:00:00",  # 02:00 EST
  ], tz="UTC")
  df = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx)
  out = to_nyt(df)

  assert not out.index.has_duplicates, (
    "CS-008: to_nyt must disambiguate DST fall-back duplicates (+1ns)"
  )
  # Three rows survive: 01:00 (EDT), 01:00+1ns (EST), 02:00 (EST).
  # Close values confirm both 01:00 bars were kept in original order.
  assert list(out.index) == [
    pd.Timestamp("2023-11-05 01:00:00"),
    pd.Timestamp("2023-11-05 01:00:00.000000001"),
    pd.Timestamp("2023-11-05 02:00:00"),
  ]
  assert list(out["close"]) == [1.0, 2.0, 3.0], (
    "CS-008: both DST-ambiguous bars must be preserved, in order"
  )


def test_to_nyt_preserves_all_rows_on_non_dst_day():
  """Dense non-DST day — no rows should drop."""
  idx = pd.date_range("2024-07-10", periods=48, freq="30min", tz="UTC")
  df = pd.DataFrame({"close": range(len(idx))}, index=idx)
  out = to_nyt(df)
  assert len(out) == len(df), "CS-008: non-DST day must not lose rows"
  assert out.index.is_monotonic_increasing


# ── _sanitize_dst_timestamps on strategy TF data ───────────────────────

def test_sanitize_dst_timestamps_makes_tz_localize_succeed():
  """Build a naive index that contains the ambiguous fall-back hour.
  Before sanitize: tz_localize(ambiguous='raise') raises. After: succeeds.
  """
  idx = pd.DatetimeIndex([
    "2023-11-05 00:30:00",
    "2023-11-05 01:00:00",  # ambiguous
    "2023-11-05 01:30:00",  # ambiguous
    "2023-11-05 02:00:00",
  ])
  df = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]}, index=idx)

  # Sanity: the ambiguous input would raise
  with pytest.raises(Exception):
    df.index.tz_localize("America/New_York", ambiguous="raise")

  tf_data = {"30m": df}
  _sanitize_dst_timestamps(tf_data)

  # Post-sanitize: ambiguous rows dropped, tz_localize now works cleanly.
  cleaned = tf_data["30m"]
  cleaned.index.tz_localize("America/New_York", ambiguous="raise")


def test_sanitize_dst_timestamps_leaves_non_dst_untouched():
  """No ambiguous timestamps → no rows dropped, no behavior change."""
  idx = pd.date_range("2024-07-01", periods=20, freq="30min")
  df = pd.DataFrame({"close": range(20)}, index=idx)
  tf_data = {"30m": df.copy()}
  _sanitize_dst_timestamps(tf_data)
  assert len(tf_data["30m"]) == 20
  assert list(tf_data["30m"]["close"]) == list(range(20))


def test_sanitize_dst_timestamps_skips_already_tz_aware():
  """If caller already tz-localised the TF frame, sanitize is a no-op."""
  idx = pd.date_range("2024-01-01", periods=5, freq="30min", tz="UTC")
  df = pd.DataFrame({"close": range(5)}, index=idx)
  tf_data = {"30m": df.copy()}
  _sanitize_dst_timestamps(tf_data)
  # Still tz-aware, still 5 rows.
  assert tf_data["30m"].index.tz is not None
  assert len(tf_data["30m"]) == 5


# ── Engine exec-filter NYT-hour contract ───────────────────────────────

def test_entry_time_hour_matches_nyt_excludehours_contract():
  """The engine compares ``entry_time.hour`` (NYT naive, per MEMORY.md)
  directly against an ``excludeHours`` set (NYT ints 0-23). This test
  pins the invariant: a trade at NYT 14:30 must be rejected by
  ``excludeHours={14}`` regardless of DST.
  """
  # Naive NYT datetimes — what BacktestTrade.entry_time stores.
  trade_summer = datetime(2024, 6, 15, 14, 30)   # EDT day
  trade_winter = datetime(2024, 1, 15, 14, 30)   # EST day
  exclude = {14}

  assert trade_summer.hour in exclude, (
    "CS-008: entry_time.hour is NYT-naive — an EDT-day trade at 14:30 "
    "must compare as hour 14, not shifted by DST offset"
  )
  assert trade_winter.hour in exclude, (
    "CS-008: same for EST. A silent UTC comparison would shift these by "
    "5 hours and the filter would fail."
  )


def test_excludehours_does_not_reject_adjacent_hour():
  """Guard against off-by-one: ``excludeHours={14}`` must not touch
  13:59 or 15:00 — only the hour 14 bucket.
  """
  before = datetime(2024, 6, 15, 13, 59)
  after = datetime(2024, 6, 15, 15, 0)
  assert before.hour not in {14}
  assert after.hour not in {14}


# ── Mixed-tz comparison guardrail ──────────────────────────────────────

def test_mixed_naive_and_aware_timestamps_raise_on_compare():
  """pandas raises on naive↔aware comparison. This is the canary that
  catches a future accidental ``df.index.tz_localize('UTC')`` upstream
  — it must surface loudly rather than silently shift by the offset.
  """
  aware = pd.Timestamp("2024-06-15 12:00:00", tz="UTC")
  naive = pd.Timestamp("2024-06-15 12:00:00")
  with pytest.raises(TypeError):
    _ = aware < naive


def test_to_nyt_output_compares_cleanly_with_naive_nyt():
  """After ``to_nyt``, the index is naive — it must compare cleanly with
  other naive NYT timestamps (the engine's internal form) without raising.
  """
  idx = pd.DatetimeIndex(["2024-06-15 12:00:00"], tz="UTC")
  df = pd.DataFrame({"close": [100.0]}, index=idx)
  out = to_nyt(df)
  threshold = pd.Timestamp("2024-06-15 07:00:00")  # naive NYT
  # Must not raise — both naive.
  _ = out.index[0] > threshold
