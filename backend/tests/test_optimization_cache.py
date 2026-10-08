"""Tests for the L0 optimization cache module."""
import threading

import pandas as pd
import pytest

from backend.analysis.optimization_cache import (
  CacheEntry,
  _make_corr_key,
  _make_date_key,
  _make_quality_key,
  _make_stress_key,
  clear_cache,
  cache_stats,
  get_cached_corr,
  get_cached_l0,
  get_cached_l0_incremental,
  get_cached_quality,
  get_cached_stress,
  put_cached_corr,
  put_cached_l0,
  put_cached_quality,
  put_cached_stress,
  update_cached_l0,
  MAX_CACHE_ENTRIES,
)


def _make_pnl(columns: list[str], rows: int = 10) -> pd.DataFrame:
  """Create a synthetic daily PnL DataFrame."""
  import numpy as np
  dates = pd.date_range("2025-01-01", periods=rows, freq="D")
  data = np.random.randn(rows, len(columns))
  return pd.DataFrame(data, index=dates, columns=columns)


def _make_trades(n: int = 5) -> list[dict]:
  """Create synthetic trade-like dicts."""
  return [{"symbol": f"SYM{i}", "net_profit": i * 10.0, "run_id": i} for i in range(n)]


@pytest.fixture(autouse=True)
def _clear():
  """Clear cache before and after each test."""
  clear_cache()
  yield
  clear_cache()


class TestL0CacheBasic:
  def test_cache_miss_then_hit(self):
    """First call should miss, second should hit after put."""
    batch_ids = [1, 2]
    portfolio_ids = [10]
    pnl = _make_pnl(["A~B1_Long", "B~B2_Short"])
    trades = _make_trades()
    rbm = {1: 1, 2: 2}

    # Miss
    result = get_cached_l0(batch_ids, portfolio_ids, "2025-01-01", "2025-06-01", False)
    assert result is None

    # Put
    put_cached_l0(batch_ids, portfolio_ids, "2025-01-01", "2025-06-01", False,
                  pnl, trades, rbm)

    # Hit
    result = get_cached_l0(batch_ids, portfolio_ids, "2025-01-01", "2025-06-01", False)
    assert result is not None
    assert result.batch_ids == [1, 2]
    assert result.portfolio_ids == [10]
    assert result.daily_pnl.shape == pnl.shape
    assert len(result.trades) == len(trades)

  def test_cache_invalidation_on_date_change(self):
    """Changing date_to should miss the cache."""
    pnl = _make_pnl(["A"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl, [], {})

    # Same dates → hit
    assert get_cached_l0([1], [], "2025-01-01", "2025-06-01", False) is not None

    # Different date_to → miss
    assert get_cached_l0([1], [], "2025-01-01", "2025-07-01", False) is None

    # Different date_from → miss
    assert get_cached_l0([1], [], "2025-02-01", "2025-06-01", False) is None

    # Different clip_is → miss
    assert get_cached_l0([1], [], "2025-01-01", "2025-06-01", True) is None

  def test_incremental_batch_addition(self):
    """Cache [1,2], then request [1,2,3] → only batch 3 needs loading."""
    pnl = _make_pnl(["A~B1_Long", "B~B2_Short"])
    put_cached_l0([1, 2], [], "2025-01-01", "2025-06-01", False, pnl, [], {1: 1, 2: 2})

    entry, new_batches, new_portfolios = get_cached_l0_incremental(
      [1, 2, 3], [], "2025-01-01", "2025-06-01", False,
    )
    assert entry is not None
    assert new_batches == [3]
    assert new_portfolios == []

  def test_subset_returns_full_cache(self):
    """Cache [1,2,3], request [1,2] → returns cache (superset)."""
    pnl = _make_pnl(["A~B1_Long", "B~B2_Short", "C~B3_Long"])
    put_cached_l0([1, 2, 3], [], "2025-01-01", "2025-06-01", False, pnl, [], {})

    result = get_cached_l0([1, 2], [], "2025-01-01", "2025-06-01", False)
    assert result is not None
    assert result.batch_ids == [1, 2, 3]  # Returns full cache


class TestDerivativeCaches:
  def test_stress_cache_separate_from_l0(self):
    """Different stress params get different cached results."""
    pnl = _make_pnl(["A"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl, [], {})
    date_key = _make_date_key("2025-01-01", "2025-06-01", False)

    stress_pnl_1 = _make_pnl(["A"])
    stress_key_1 = _make_stress_key(0.01, 0.02, 0.0, 0.0)
    put_cached_stress(date_key, stress_key_1, stress_pnl_1)

    # Same stress params → hit
    result = get_cached_stress(date_key, stress_key_1)
    assert result is not None
    assert result.shape == stress_pnl_1.shape

    # Different stress params → miss
    stress_key_2 = _make_stress_key(0.05, 0.02, 0.0, 0.0)
    assert get_cached_stress(date_key, stress_key_2) is None

  def test_quality_cache_separate_from_stress(self):
    """Quality filter changes don't invalidate stress cache."""
    pnl = _make_pnl(["A"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl, [], {})
    date_key = _make_date_key("2025-01-01", "2025-06-01", False)

    # Put stress
    stress_key = _make_stress_key(0.01, 0.02, 0.0, 0.0)
    stress_pnl = _make_pnl(["A"])
    put_cached_stress(date_key, stress_key, stress_pnl)

    # Put quality
    quality_key = _make_quality_key(10, 1.2, None, [])
    quality_pnl = _make_pnl(["A"])
    put_cached_quality(date_key, quality_key, quality_pnl)

    # Stress still cached
    assert get_cached_stress(date_key, stress_key) is not None

    # Quality cached with same key
    assert get_cached_quality(date_key, quality_key) is not None

    # Quality with different key → miss
    quality_key_2 = _make_quality_key(20, 1.5, None, [])
    assert get_cached_quality(date_key, quality_key_2) is None

  def test_corr_cache_by_method_and_columns(self):
    """Different corr_method → different cache entry."""
    pnl = _make_pnl(["A", "B"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl, [], {})
    date_key = _make_date_key("2025-01-01", "2025-06-01", False)

    corr_pearson = _make_pnl(["A", "B"])  # Synthetic corr matrix
    corr_key_pearson = _make_corr_key("pearson", ["A", "B"])
    put_cached_corr(date_key, corr_key_pearson, corr_pearson)

    # Same method + columns → hit
    assert get_cached_corr(date_key, corr_key_pearson) is not None

    # Different method → miss
    corr_key_spearman = _make_corr_key("spearman", ["A", "B"])
    assert get_cached_corr(date_key, corr_key_spearman) is None

    # Different columns → miss
    corr_key_diff_cols = _make_corr_key("pearson", ["A", "C"])
    assert get_cached_corr(date_key, corr_key_diff_cols) is None


class TestCacheManagement:
  def test_eviction_at_max_entries(self):
    """When MAX_CACHE_ENTRIES reached, oldest evicted."""
    import time
    for i in range(MAX_CACHE_ENTRIES):
      pnl = _make_pnl([f"asset_{i}"])
      put_cached_l0([i], [], f"2025-0{i + 1}-01", "2025-06-01", False, pnl, [], {})
      time.sleep(0.01)  # Ensure different created_at timestamps

    stats = cache_stats()
    assert stats["entries"] == MAX_CACHE_ENTRIES

    # Add one more — should evict the oldest (batch 0)
    pnl = _make_pnl(["new_asset"])
    put_cached_l0([99], [], "2025-12-01", "2025-12-31", False, pnl, [], {})

    stats = cache_stats()
    assert stats["entries"] == MAX_CACHE_ENTRIES
    # First entry (batch 0) should have been evicted
    first_key = _make_date_key("2025-01-01", "2025-06-01", False)
    assert first_key not in stats["keys"]
    # New entry should exist
    new_key = _make_date_key("2025-12-01", "2025-12-31", False)
    assert new_key in stats["keys"]

  def test_clear_cache(self):
    """clear_cache() empties everything."""
    pnl = _make_pnl(["A"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl, [], {})
    assert cache_stats()["entries"] == 1

    clear_cache()
    assert cache_stats()["entries"] == 0
    assert get_cached_l0([1], [], "2025-01-01", "2025-06-01", False) is None


class TestIncrementalUpdate:
  def test_update_merges_data(self):
    """update_cached_l0 merges new batch data into existing entry."""
    pnl1 = _make_pnl(["A~B1_Long"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl1, [{"id": 1}], {1: 1})

    date_key = _make_date_key("2025-01-01", "2025-06-01", False)
    entry = get_cached_l0([1], [], "2025-01-01", "2025-06-01", False)
    assert entry is not None

    pnl2 = _make_pnl(["B~B2_Short"])
    update_cached_l0(date_key, entry, [2], [], pnl2, [{"id": 2}], {2: 2})

    # Now cache should have both batches
    updated = get_cached_l0([1, 2], [], "2025-01-01", "2025-06-01", False)
    assert updated is not None
    assert updated.batch_ids == [1, 2]
    assert updated.daily_pnl.shape[1] == 2
    assert len(updated.trades) == 2

  def test_update_invalidates_derivatives(self):
    """Updating L0 data should invalidate stress/quality/corr caches."""
    pnl = _make_pnl(["A~B1_Long"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl, [], {})

    date_key = _make_date_key("2025-01-01", "2025-06-01", False)
    stress_key = _make_stress_key(0.01, 0.0, 0.0, 0.0)
    put_cached_stress(date_key, stress_key, _make_pnl(["A~B1_Long"]))
    assert get_cached_stress(date_key, stress_key) is not None

    # Update with new batch — derivatives should be gone
    entry = get_cached_l0([1], [], "2025-01-01", "2025-06-01", False)
    update_cached_l0(date_key, entry, [2], [], _make_pnl(["B~B2_Short"]), [], {2: 2})

    # Stress cache should be invalidated (new CacheEntry created)
    assert get_cached_stress(date_key, stress_key) is None


class TestMutationSafety:
  def test_l0_callers_must_copy(self):
    """L0 getter returns the CacheEntry reference — callers must .copy() daily_pnl."""
    pnl = _make_pnl(["A~B1_Long", "B~B2_Short"])
    put_cached_l0([1, 2], [], "2025-01-01", "2025-06-01", False, pnl, [], {})

    entry = get_cached_l0([1, 2], [], "2025-01-01", "2025-06-01", False)
    assert entry is not None
    # Simulate what cross_strategy.py does: .copy() before mutating
    safe_pnl = entry.daily_pnl.copy()
    safe_pnl.iloc[0, 0] = 999999.0

    # Original cache entry must be unaffected
    entry2 = get_cached_l0([1, 2], [], "2025-01-01", "2025-06-01", False)
    assert entry2 is not None
    assert entry2.daily_pnl.iloc[0, 0] != 999999.0, "Cache was corrupted!"

  def test_corr_cache_copy_prevents_corruption(self):
    """Mutating returned corr matrix must NOT affect the cache."""
    pnl = _make_pnl(["A", "B"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl, [], {})
    date_key = _make_date_key("2025-01-01", "2025-06-01", False)

    corr = _make_pnl(["A", "B"])
    corr_key = _make_corr_key("pearson", ["A", "B"])
    put_cached_corr(date_key, corr_key, corr)

    # Read and mutate
    result1 = get_cached_corr(date_key, corr_key)
    assert result1 is not None
    original_val = result1.iloc[0, 0]
    result1.iloc[0, 0] = 999999.0

    # Read again — should be unaffected
    result2 = get_cached_corr(date_key, corr_key)
    assert result2 is not None
    assert result2.iloc[0, 0] != 999999.0, "Cache was corrupted by mutation!"
    assert result2.iloc[0, 0] == original_val

  def test_stress_cache_copy_prevents_corruption(self):
    """Mutating returned stress PnL must NOT affect the cache."""
    pnl = _make_pnl(["A"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl, [], {})
    date_key = _make_date_key("2025-01-01", "2025-06-01", False)

    stress_pnl = _make_pnl(["A"])
    stress_key = _make_stress_key(0.01, 0.02, 0.0, 0.0)
    put_cached_stress(date_key, stress_key, stress_pnl)

    result1 = get_cached_stress(date_key, stress_key)
    assert result1 is not None
    original_val = result1.iloc[0, 0]
    result1.iloc[0, 0] = 999999.0

    result2 = get_cached_stress(date_key, stress_key)
    assert result2 is not None
    assert result2.iloc[0, 0] != 999999.0, "Cache was corrupted by mutation!"
    assert result2.iloc[0, 0] == original_val

  def test_quality_cache_copy_prevents_corruption(self):
    """Mutating returned quality PnL must NOT affect the cache."""
    pnl = _make_pnl(["A"])
    put_cached_l0([1], [], "2025-01-01", "2025-06-01", False, pnl, [], {})
    date_key = _make_date_key("2025-01-01", "2025-06-01", False)

    quality_pnl = _make_pnl(["A"])
    quality_key = _make_quality_key(10, 1.2, None, [])
    put_cached_quality(date_key, quality_key, quality_pnl)

    result1 = get_cached_quality(date_key, quality_key)
    assert result1 is not None
    original_val = result1.iloc[0, 0]
    result1.iloc[0, 0] = 999999.0

    result2 = get_cached_quality(date_key, quality_key)
    assert result2 is not None
    assert result2.iloc[0, 0] != 999999.0, "Cache was corrupted by mutation!"
    assert result2.iloc[0, 0] == original_val


class TestThreadSafety:
  def test_concurrent_reads_writes(self):
    """Concurrent reads/writes should not crash."""
    errors: list[Exception] = []
    barrier = threading.Barrier(10)

    def writer(i: int):
      try:
        barrier.wait(timeout=5)
        pnl = _make_pnl([f"asset_{i}"])
        put_cached_l0([i], [], "2025-01-01", "2025-06-01", False, pnl, [], {i: i})
      except Exception as e:
        errors.append(e)

    def reader(i: int):
      try:
        barrier.wait(timeout=5)
        get_cached_l0([i], [], "2025-01-01", "2025-06-01", False)
        cache_stats()
      except Exception as e:
        errors.append(e)

    threads = []
    for i in range(5):
      threads.append(threading.Thread(target=writer, args=(i,)))
      threads.append(threading.Thread(target=reader, args=(i,)))

    for t in threads:
      t.start()
    for t in threads:
      t.join(timeout=10)

    assert len(errors) == 0, f"Thread errors: {errors}"


class TestPortfolioMetadata:
  """Verify portfolio metadata maps are stored and restored from cache."""

  def test_portfolio_metadata_round_trip(self):
    """Portfolio maps survive cache put → get cycle."""
    clear_cache()
    pnl = _make_pnl(["BTCUSDT~B1_Long", "ETHUSDT~P5_Long"])
    p_ab = {"ETHUSDT~P5_Long": 5}
    p_sm = {5: "My Portfolio"}
    p_as = {"ETHUSDT~P5_Long": "My Portfolio"}

    put_cached_l0([1], [5], "2025-01-01", "2025-06-01", False,
                  pnl, [], {1: 1}, p_ab, p_sm, p_as)
    entry = get_cached_l0([1], [5], "2025-01-01", "2025-06-01", False)
    assert entry is not None
    assert entry.p_asset_batch == {"ETHUSDT~P5_Long": 5}
    assert entry.p_strat_map == {5: "My Portfolio"}
    assert entry.p_asset_strat == {"ETHUSDT~P5_Long": "My Portfolio"}

  def test_portfolio_metadata_incremental_merge(self):
    """Incremental update merges portfolio metadata from both entries."""
    clear_cache()
    pnl1 = _make_pnl(["BTCUSDT~B1_Long"])
    put_cached_l0([1], [5], "2025-01-01", "2025-06-01", False,
                  pnl1, [], {1: 1},
                  {"BTC~P5_Long": 5}, {5: "Port A"}, {"BTC~P5_Long": "Port A"})

    entry = get_cached_l0([1], [5], "2025-01-01", "2025-06-01", False)
    date_key = _make_date_key("2025-01-01", "2025-06-01", False)
    pnl2 = _make_pnl(["ETH~P6_Long"])
    update_cached_l0(date_key, entry, [], [6], pnl2, [], {},
                     {"ETH~P6_Long": 6}, {6: "Port B"}, {"ETH~P6_Long": "Port B"})

    merged = get_cached_l0([1], [5, 6], "2025-01-01", "2025-06-01", False)
    assert merged is not None
    assert merged.p_asset_batch == {"BTC~P5_Long": 5, "ETH~P6_Long": 6}
    assert merged.p_strat_map == {5: "Port A", 6: "Port B"}
    assert merged.p_asset_strat == {"BTC~P5_Long": "Port A", "ETH~P6_Long": "Port B"}

  def test_portfolio_metadata_copy_prevents_corruption(self):
    """Callers who .copy() metadata must not corrupt cache."""
    clear_cache()
    pnl = _make_pnl(["A~B1_Long"])
    put_cached_l0([1], [5], "2025-01-01", "2025-06-01", False,
                  pnl, [], {}, {"A~P5_Long": 5}, {5: "P"}, {"A~P5_Long": "P"})

    entry = get_cached_l0([1], [5], "2025-01-01", "2025-06-01", False)
    # Simulate what cross_strategy.py does: .copy() then mutate
    local_pab = entry.p_asset_batch.copy()
    local_pab["HACKED"] = 999

    fresh = get_cached_l0([1], [5], "2025-01-01", "2025-06-01", False)
    assert "HACKED" not in fresh.p_asset_batch
