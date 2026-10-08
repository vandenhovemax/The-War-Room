"""Unit tests for the live-parity harness comparison logic.

The full harness needs the real DB + candles (it runs on the server against
a workspace copy); these tests pin down the pure decision logic it relies
on: fill→signal-bar mapping, the exec-filter mirror, level tolerance, the
entry diff, no-look-ahead serving, and fill mirroring semantics.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from backend.services.live_parity_harness import (
  HarnessAggregator,
  diff_entries,
  exec_filter_blocks,
  levels_match,
  signal_bar_for_fill,
)


def _index(n=10, start="2026-08-01 00:00", freq="30min"):
  return pd.date_range(start=start, periods=n, freq=freq)


# ── signal_bar_for_fill ───────────────────────────────────────────────

def test_signal_bar_is_bar_before_fill():
  idx = _index()
  assert signal_bar_for_fill(idx, idx[5]) == idx[4]


def test_signal_bar_first_bar_has_no_predecessor():
  idx = _index()
  assert signal_bar_for_fill(idx, idx[0]) is None


def test_signal_bar_unknown_timestamp_returns_none():
  idx = _index()
  assert signal_bar_for_fill(idx, idx[3] + pd.Timedelta(minutes=7)) is None


# ── exec_filter_blocks (mirror of the live entry gate) ────────────────

def test_exec_filter_blocks_excluded_hour():
  bar = datetime(2026, 8, 3, 14, 30)  # Monday 14:30 NYT
  assert exec_filter_blocks(bar, {14}, set()) is True
  assert exec_filter_blocks(bar, {15}, set()) is False


def test_exec_filter_blocks_excluded_weekday():
  bar = datetime(2026, 8, 3, 14, 30)  # Monday (weekday 0)
  assert exec_filter_blocks(bar, set(), {0}) is True
  assert exec_filter_blocks(bar, set(), {6}) is False


def test_exec_filter_empty_never_blocks():
  assert exec_filter_blocks(datetime(2026, 8, 3, 14, 30), set(), set()) is False


# ── levels_match ──────────────────────────────────────────────────────

def test_levels_match_exact_and_within_tolerance():
  assert levels_match(100.0, 100.0)
  assert levels_match(100.0, 100.0 + 100.0 * 1e-7)


def test_levels_match_rejects_real_differences():
  assert not levels_match(100.0, 100.1)
  assert not levels_match(100.0, 0.0)


def test_levels_match_both_absent_ok():
  assert levels_match(0.0, 0.0)
  assert levels_match(None, 0.0)


# ── diff_entries ──────────────────────────────────────────────────────

def _e(bar, direction="long", sl=95.0):
  return {"signal_bar": bar, "direction": direction, "sl": sl}


def test_diff_identical_sets_pass():
  exp = [_e("2026-08-01T00:00:00"), _e("2026-08-01T01:00:00", "short", 105.0)]
  obs = [_e("2026-08-01T00:00:00"), _e("2026-08-01T01:00:00", "short", 105.0)]
  d = diff_entries(exp, obs)
  assert d["pass"] is True
  assert d["n_matched"] == 2 and not d["missing_in_live"] and not d["extra_in_live"]


def test_diff_missing_live_signal_fails():
  d = diff_entries([_e("2026-08-01T00:00:00")], [])
  assert d["pass"] is False
  assert d["missing_in_live"] == [["2026-08-01T00:00:00", "long"]]


def test_diff_extra_live_signal_fails():
  d = diff_entries([], [_e("2026-08-01T00:00:00")])
  assert d["pass"] is False
  assert d["extra_in_live"] == [["2026-08-01T00:00:00", "long"]]


def test_diff_sl_mismatch_fails_but_matches_identity():
  d = diff_entries([_e("2026-08-01T00:00:00", sl=95.0)],
                   [_e("2026-08-01T00:00:00", sl=94.0)])
  assert d["n_matched"] == 1
  assert d["pass"] is False
  assert d["level_mismatches"][0]["field"] == "sl"


def test_diff_backtest_sl_unknown_is_not_compared():
  d = diff_entries([_e("2026-08-01T00:00:00", sl=None)],
                   [_e("2026-08-01T00:00:00", sl=94.0)])
  assert d["pass"] is True
  assert d["n_sl_compared"] == 0


# ── HarnessAggregator: strictly no look-ahead ─────────────────────────

def test_aggregator_serves_nothing_past_cursor():
  agg = HarnessAggregator()
  bars = [
    {"timestamp": datetime(2026, 8, 1, h, 0, tzinfo=None), "open": 1.0,
     "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}
    for h in range(6)
  ]
  agg.seed_history("BTCUSDT", "240", bars)
  agg.cursor_utc = datetime(2026, 8, 1, 3, 0)
  df = agg.get_history_df("BTCUSDT", "240")
  assert len(df) == 4  # hours 0..3 only
  assert df.index.max() == pd.Timestamp("2026-08-01 03:00:00")


def test_aggregator_add_bar_respects_cursor():
  agg = HarnessAggregator()
  agg.add_bar("BTCUSDT", "240", {
    "timestamp": datetime(2026, 8, 1, 5, 0), "open": 1, "high": 1,
    "low": 1, "close": 1, "volume": 1,
  })
  agg.cursor_utc = datetime(2026, 8, 1, 4, 0)
  assert agg.get_history_df("BTCUSDT", "240").empty


# ── normalize_trades: OrderFill stream → closed trades ────────────────

class _Fill:
  def __init__(self, action, timestamp, side="short", entry_time=None,
               entry_price=100.0, qty=1.0):
    self.action = action
    self.timestamp = timestamp
    self.side = side
    self.entry_time = entry_time
    self.entry_price = entry_price
    self.qty = qty


def test_normalize_skips_entry_fills_and_maps_exit_fills():
  from backend.services.live_parity_harness import normalize_trades
  e = pd.Timestamp("2026-08-01 02:00")
  x = pd.Timestamp("2026-08-01 06:00")
  fills = [
    _Fill("entry", e, entry_time=None),
    _Fill("exit", x, entry_time=e),
  ]
  out = normalize_trades(fills)
  assert len(out) == 1
  assert out[0].entry_time == e and out[0].exit_time == x
  assert out[0].side == "short"


def test_normalize_partial_exits_keep_position_alive_to_last_leg():
  from backend.services.live_parity_harness import mirror_fills, normalize_trades
  e = pd.Timestamp("2026-08-01 02:00")
  fills = [
    _Fill("exit", pd.Timestamp("2026-08-01 04:00"), entry_time=e),  # TP1 leg
    _Fill("exit", pd.Timestamp("2026-08-01 08:00"), entry_time=e),  # runner
  ]
  out = normalize_trades(fills)
  assert len(out) == 2
  assert max(t.exit_time for t in out) == pd.Timestamp("2026-08-01 08:00")


# ── mirror_fills open-set semantics (engine fills at bar open) ────────

class _T:
  def __init__(self, entry, exit_, side="long", entry_price=100.0):
    self.entry_time = entry
    self.exit_time = exit_
    self.side = side
    self.entry_price = entry_price
    self.qty = 1.0


def test_mirror_open_set_boundaries():
  """A trade is OPEN on its entry bar (filled at open, before next()) and
  already CLOSED at signal time on the bar BEFORE its exit-fill bar — the
  exchange stop fills intra-bar, so the engine is flat when the next
  signal fires. This is the signal-time view mirror_fills implements."""
  from backend.services.live_parity_harness import mirror_fills

  class _FakeQuery:
    def __init__(self, store):
      self.store = store

    def filter(self, *a, **kw):
      return self

    def delete(self, *a, **kw):
      self.store.clear()
      return 0

  class _FakeDb:
    def __init__(self):
      self.rows = []

    def query(self, *a):
      return _FakeQuery(self.rows)

    def add(self, row):
      self.rows.append(row)

    def commit(self):
      pass

  t = _T(pd.Timestamp("2026-08-01 02:00"), pd.Timestamp("2026-08-01 04:00"))
  db = _FakeDb()
  kw = {"base_minutes": 30}
  assert mirror_fills(db, 1, "BTCUSDT", [t], pd.Timestamp("2026-08-01 01:30"), **kw) == 0
  assert mirror_fills(db, 1, "BTCUSDT", [t], pd.Timestamp("2026-08-01 02:00"), **kw) == 1
  assert mirror_fills(db, 1, "BTCUSDT", [t], pd.Timestamp("2026-08-01 03:00"), **kw) == 1
  # 03:30: exit fills at 04:00 = next bar open -> flat at signal time
  assert mirror_fills(db, 1, "BTCUSDT", [t], pd.Timestamp("2026-08-01 03:30"), **kw) == 0
  assert mirror_fills(db, 1, "BTCUSDT", [t], pd.Timestamp("2026-08-01 04:00"), **kw) == 0
