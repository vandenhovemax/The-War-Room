"""Timezone parity and DST handling tests.

Covers the contracts that backtest, optimization, and live share:
  - ``to_nyt()`` converts a UTC-indexed DataFrame to naive NYT and drops
    the duplicate row produced by DST fall-back (2023-11-05 01:00 NYT).
  - ``_sanitize_dst_timestamps`` removes DST-ambiguous and non-existent
    rows from a tf_data dict keyed by timeframe.
  - ``_filter_trades_exec`` compares ``excludeHours`` directly against
    ``entry_time.hour`` — entry_time is NYT-naive, so the same exclude
    set applies identically to trades on both sides of a DST switch.
  - Mixing tz-aware and tz-naive timestamps raises (catches regressions
    where UTC leaks into the NYT-naive entry_time contract).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import pytest

from backend.analysis.batch_selection import _filter_trades_exec
from backend.backtester.portfolio_engine import _sanitize_dst_timestamps
from backend.utils.candle_loader import to_nyt


# --- to_nyt -------------------------------------------------------------

def test_to_nyt_converts_utc_to_naive_nyt():
  """UTC index is converted to naive NYT with correct offset."""
  # 2024-06-15 14:00 UTC = 10:00 EDT (UTC-4 in summer)
  idx = pd.DatetimeIndex(["2024-06-15 14:00:00"], tz="UTC")
  df = pd.DataFrame({"close": [100.0]}, index=idx)

  out = to_nyt(df)

  assert out.index.tz is None, "Result must be naive"
  assert out.index[0] == pd.Timestamp("2024-06-15 10:00:00")


def test_to_nyt_localizes_naive_utc_input():
  """Naive input is assumed UTC, then converted to NYT."""
  idx = pd.DatetimeIndex(["2024-01-15 14:00:00"])  # naive, treated as UTC
  df = pd.DataFrame({"close": [100.0]}, index=idx)

  out = to_nyt(df)

  # 2024-01-15 14:00 UTC = 09:00 EST (UTC-5 in winter)
  assert out.index[0] == pd.Timestamp("2024-01-15 09:00:00")
  assert out.index.tz is None


def test_to_nyt_preserves_dst_fallback_bars_with_ns_disambiguation():
  """DST fall-back creates two 01:00 NYT rows; to_nyt keeps BOTH bars.

  On 2023-11-05, NYT clocks go 01:00 EDT → 02:00 → 01:00 EST. Two distinct
  UTC timestamps (05:00 and 06:00) both map to 01:00 naive NYT.

  CS-008 fix (2026-05-05): dropping the duplicate shifted bar_index out of
  parity with PineScript, which keeps both bars. Instead each successive
  duplicate is disambiguated by +1ns — invisible to hour/minute strategy
  filters, but preserves row count and ordering.
  """
  idx = pd.DatetimeIndex([
    "2023-11-05 04:00:00",  # 00:00 EDT
    "2023-11-05 05:00:00",  # 01:00 EDT — will collide
    "2023-11-05 06:00:00",  # 01:00 EST — duplicate naive NYT
    "2023-11-05 07:00:00",  # 02:00 EST
  ], tz="UTC")
  df = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]}, index=idx)

  out = to_nyt(df)

  # All four bars survive; the duplicate is offset by 1ns, not dropped
  assert len(out) == 4
  assert not out.index.duplicated().any()
  # Exact 01:00 NYT lookup hits only the first bar (EDT, value 2.0)
  row_01 = out[out.index == pd.Timestamp("2023-11-05 01:00:00")]
  assert len(row_01) == 1
  assert row_01["close"].iloc[0] == 2.0
  # The displaced bar sits at 01:00:00.000000001 with the EST value
  row_01b = out[out.index == pd.Timestamp("2023-11-05 01:00:00.000000001")]
  assert len(row_01b) == 1
  assert row_01b["close"].iloc[0] == 3.0


# --- _sanitize_dst_timestamps -------------------------------------------

def test_sanitize_drops_ambiguous_fallback_bar():
  """Ambiguous NYT-naive timestamps (DST fall-back) get dropped."""
  # Two naive rows both interpretable as 01:00 NYT on 2023-11-05.
  # Pandas cannot localize this ambiguously, so the row must be dropped.
  idx = pd.DatetimeIndex([
    "2023-11-05 00:00:00",
    "2023-11-05 01:00:00",  # ambiguous (EDT or EST?)
    "2023-11-05 02:00:00",
    "2023-11-05 03:00:00",
  ])
  df = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]}, index=idx)
  tf_data = {"60": df}

  _sanitize_dst_timestamps(tf_data)

  out = tf_data["60"]
  # The ambiguous 01:00 row is dropped (NaT-masked)
  assert pd.Timestamp("2023-11-05 01:00:00") not in out.index
  # Non-ambiguous bars survive
  assert pd.Timestamp("2023-11-05 00:00:00") in out.index
  assert pd.Timestamp("2023-11-05 03:00:00") in out.index


def test_sanitize_drops_spring_forward_nonexistent_bar():
  """Spring-forward 02:00-03:00 NYT does not exist — rows get dropped."""
  # 2024-03-10 02:30 NYT does not exist (clock jumps 02:00 → 03:00)
  idx = pd.DatetimeIndex([
    "2024-03-10 01:30:00",
    "2024-03-10 02:30:00",  # non-existent in NYT
    "2024-03-10 03:30:00",
  ])
  df = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx)
  tf_data = {"30": df}

  _sanitize_dst_timestamps(tf_data)

  out = tf_data["30"]
  assert pd.Timestamp("2024-03-10 02:30:00") not in out.index
  assert pd.Timestamp("2024-03-10 01:30:00") in out.index
  assert pd.Timestamp("2024-03-10 03:30:00") in out.index


def test_sanitize_noop_when_no_ambiguity():
  """Clean tz-naive data passes through unchanged."""
  idx = pd.DatetimeIndex([
    "2024-06-15 10:00:00",
    "2024-06-15 10:30:00",
    "2024-06-15 11:00:00",
  ])
  df = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=idx)
  tf_data = {"30": df.copy()}

  _sanitize_dst_timestamps(tf_data)

  assert len(tf_data["30"]) == 3
  assert list(tf_data["30"].index) == list(idx)


def test_sanitize_skips_already_tz_aware():
  """tz-aware frames are left alone (no double-localize)."""
  idx = pd.DatetimeIndex(
    ["2024-06-15 10:00:00"], tz="America/New_York",
  )
  df = pd.DataFrame({"close": [1.0]}, index=idx)
  tf_data = {"60": df}

  _sanitize_dst_timestamps(tf_data)

  assert tf_data["60"].index.tz is not None


# --- Exec filter parity across DST --------------------------------------

@dataclass
class _FakeTrade:
  """Duck-typed stand-in for ORM BacktestTrade / LightTrade.

  _filter_trades_exec only reads `.entry_time.hour` and `.entry_time.weekday()`,
  so a minimal dataclass is sufficient and keeps the test free of DB fixtures.
  """
  entry_time: datetime | None


def _trade(entry_time: datetime) -> _FakeTrade:
  return _FakeTrade(entry_time=entry_time)


def test_exec_filter_same_nyt_hour_across_dst():
  """A trade at 09:00 NYT is treated identically in winter and summer.

  This is the invariant Max relies on: `excludeHours` in the saved
  config are NYT hours, and `entry_time` is NYT-naive. The set
  comparison `et.hour in exclude_hours` must produce the same result
  on 2024-01-15 09:00 (EST) and 2024-07-15 09:00 (EDT).
  """
  winter = _trade(datetime(2024, 1, 15, 9, 0))  # 09:00 NYT, EST
  summer = _trade(datetime(2024, 7, 15, 9, 0))  # 09:00 NYT, EDT

  # Excluding hour 9 blocks both
  blocked = _filter_trades_exec([winter, summer], exclude_hours={9}, exclude_days=None)
  assert blocked == []

  # Excluding hour 10 blocks neither
  kept = _filter_trades_exec([winter, summer], exclude_hours={10}, exclude_days=None)
  assert kept == [winter, summer]


def test_exec_filter_uses_nyt_weekday():
  """Weekday exclusion runs on entry_time.weekday() (NYT-naive)."""
  # 2024-03-11 is a Monday NYT — exclude Monday (weekday=0)
  monday = _trade(datetime(2024, 3, 11, 10, 0))
  tuesday = _trade(datetime(2024, 3, 12, 10, 0))

  out = _filter_trades_exec([monday, tuesday], exclude_hours=None, exclude_days={0})

  assert out == [tuesday]


def test_exec_filter_empty_sets_is_noop():
  """Empty exclude sets return trades unchanged (short-circuit)."""
  t = _trade(datetime(2024, 6, 15, 9, 0))
  assert _filter_trades_exec([t], exclude_hours=None, exclude_days=None) == [t]
  assert _filter_trades_exec([t], exclude_hours=set(), exclude_days=set()) == [t]


def test_exec_filter_drops_trades_with_null_entry_time():
  """Trades missing entry_time are dropped when any filter is active."""
  good = _trade(datetime(2024, 6, 15, 10, 0))
  bad = _FakeTrade(entry_time=None)

  out = _filter_trades_exec([good, bad], exclude_hours={0}, exclude_days=None)

  assert out == [good]


# --- tz-aware vs naive comparison guard ---------------------------------

def test_tz_aware_and_naive_comparison_raises():
  """Mixing tz-aware and tz-naive timestamps must raise.

  Regression guard for the NYT-naive entry_time contract. If code ever
  leaks a UTC-aware timestamp into a comparison with entry_time, pandas
  / datetime raises TypeError — this test documents that behavior so
  silent coercion cannot sneak in.
  """
  naive = pd.Timestamp("2024-06-15 10:00:00")
  aware = pd.Timestamp("2024-06-15 10:00:00", tz="UTC")

  with pytest.raises(TypeError):
    naive < aware  # noqa: B015 — intentional comparison to trigger TypeError
