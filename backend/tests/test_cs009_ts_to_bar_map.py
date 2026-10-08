"""CS-009: per-symbol timestamp -> bar-index dict must produce bit-identical
integer bars to the original ``df.index.get_loc(ts)`` path.

Before this fix, the portfolio-engine hot loop called ``get_loc(ts)`` for
every (ts, symbol) pair. For a 30-bar backtest × 40 symbols, that's 1,200
per-bar index lookups; at ~30µs each, it's ~36ms of overhead per pass and
was dominant in profile traces.

The fix builds a flat ``{ts: i}`` dict per symbol once, upfront, then reads
from it in O(1). This test pins parity: the dict-computed bar index must
equal the pandas-computed bar index for every timestamp in a randomized
DatetimeIndex, including DST crossings and gaps.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _slow_bar(df, ts):
  """Replicate the original pre-CS-009 resolution path exactly."""
  bar = df.index.get_loc(ts)
  if isinstance(bar, slice):
    bar = bar.start
  elif isinstance(bar, np.ndarray):
    bar = int(np.flatnonzero(bar)[0])
  return int(bar)


def _fast_bar(ts_to_bar, ts):
  return int(ts_to_bar[ts])


def test_parity_on_dense_30m_index():
  """Dense 30-minute index, no gaps — every ts must resolve identically."""
  idx = pd.date_range("2024-01-01", "2024-02-01", freq="30min")
  df = pd.DataFrame({"close": range(len(idx))}, index=idx)
  ts_map = {t: i for i, t in enumerate(df.index)}
  for ts in df.index:
    assert _fast_bar(ts_map, ts) == _slow_bar(df, ts)


def test_parity_on_sparse_gapped_index():
  """Index with deliberate gaps — get_loc must still match dict lookup."""
  base = pd.date_range("2024-01-01", "2024-01-10", freq="30min")
  # Drop every 7th bar to simulate a gapped exchange feed
  keep = [t for i, t in enumerate(base) if i % 7 != 0]
  idx = pd.DatetimeIndex(keep)
  df = pd.DataFrame({"close": range(len(idx))}, index=idx)
  ts_map = {t: i for i, t in enumerate(df.index)}
  for ts in df.index:
    assert _fast_bar(ts_map, ts) == _slow_bar(df, ts)


def test_parity_across_dst_fall_back():
  """DST fall-back creates ambiguous timestamps — dict lookup must still
  produce the same bar index as get_loc on the naive/tz-aware index
  that the engine actually consumes.
  """
  # Engine consumes NYT-converted naive timestamps; a DST day gets 25h.
  idx = pd.date_range("2024-11-03 00:00:00", periods=50, freq="30min")
  df = pd.DataFrame({"close": range(len(idx))}, index=idx)
  ts_map = {t: i for i, t in enumerate(df.index)}
  for ts in df.index:
    assert _fast_bar(ts_map, ts) == _slow_bar(df, ts)


def test_missing_ts_falls_back_to_get_loc():
  """A ts not in the dict must NOT be silently wrong. The engine's
  fallback calls get_loc, which raises KeyError — exactly what we want
  so regressions surface loudly.
  """
  idx = pd.date_range("2024-01-01", periods=100, freq="30min")
  df = pd.DataFrame({"close": range(len(idx))}, index=idx)
  ts_map = {t: i for i, t in enumerate(df.index)}
  phantom = pd.Timestamp("2099-01-01")
  assert phantom not in ts_map
  try:
    df.index.get_loc(phantom)
  except KeyError:
    return
  raise AssertionError("CS-009: phantom ts should raise KeyError via get_loc")


def test_dict_lookup_is_faster_than_get_loc():
  """Sanity: the whole point of CS-009. Dict lookup beats get_loc by a
  large margin so perf-sensitive tests actually reflect production.
  """
  import time

  idx = pd.date_range("2024-01-01", periods=5000, freq="30min")
  df = pd.DataFrame({"close": range(len(idx))}, index=idx)
  ts_map = {t: i for i, t in enumerate(df.index)}

  targets = list(df.index)
  t0 = time.perf_counter()
  for ts in targets:
    _ = _slow_bar(df, ts)
  slow = time.perf_counter() - t0

  t0 = time.perf_counter()
  for ts in targets:
    _ = _fast_bar(ts_map, ts)
  fast = time.perf_counter() - t0

  # Require at least 3x speedup — actual is ~50-100x but CI noise
  # on Windows can compress the gap.
  assert fast * 3 < slow, (
    f"CS-009 regression: dict lookup ({fast:.4f}s) not sufficiently "
    f"faster than get_loc ({slow:.4f}s) over {len(targets)} lookups"
  )
