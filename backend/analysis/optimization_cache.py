"""Cross-strategy optimization cache.

Caches expensive I/O-bound results (trade loading, daily PnL construction)
that depend only on batch_ids + date range + a few filter params.

Cache is invalidated when:
- OOS cutoff date (date_to) changes
- batch_ids change (but supports incremental addition)
- Quality filter params change
"""
from __future__ import annotations

import hashlib
import json
import time
import threading
from dataclasses import dataclass, field
import pandas as pd
from sqlalchemy.orm.session import make_transient

from backend.utils.logger import get_logger

logger = get_logger(__name__)

_cache_lock = threading.Lock()


@dataclass
class CacheEntry:
  """A single cached optimization precomputation."""
  daily_pnl: pd.DataFrame
  trades: list  # list[BacktestTrade]
  run_batch_map: dict[int, int]
  # Portfolio metadata maps (strategy attribution)
  p_asset_batch: dict[str, int] = field(default_factory=dict)
  p_strat_map: dict[int, str] = field(default_factory=dict)
  p_asset_strat: dict[str, str] = field(default_factory=dict)
  # Optional cached derivatives
  stress_daily_pnl: pd.DataFrame | None = None
  stress_key: str = ""
  quality_daily_pnl: pd.DataFrame | None = None
  quality_key: str = ""
  corr_matrix: pd.DataFrame | None = None
  corr_key: str = ""
  # Metadata
  created_at: float = field(default_factory=time.time)
  batch_ids: list[int] = field(default_factory=list)
  portfolio_ids: list[int] = field(default_factory=list)


# Global cache — keyed by (date_from, date_to, clip_is)
# Each entry stores data for a SET of batch_ids, supporting incremental growth
_l0_cache: dict[str, CacheEntry] = {}

# Maximum cache entries (prevents unbounded growth)
MAX_CACHE_ENTRIES = 5


def _make_date_key(date_from: str | None, date_to: str | None, clip_is: bool) -> str:
  """Primary cache key based on date range."""
  return f"{date_from or 'none'}|{date_to or 'none'}|{clip_is}"


def _make_stress_key(slippage: float, commission: float, funding: float, spread: float) -> str:
  return f"stress:{slippage}:{commission}:{funding}:{spread}"


def _make_quality_key(min_trades: int, min_pf: float, per_batch: dict | None, category_filter: list) -> str:
  pb_hash = hashlib.md5(json.dumps(per_batch or {}, sort_keys=True).encode()).hexdigest()[:8]
  cf_hash = hashlib.md5(json.dumps(sorted(category_filter)).encode()).hexdigest()[:8] if category_filter else "none"
  return f"quality:{min_trades}:{min_pf}:{pb_hash}:{cf_hash}"


def _make_corr_key(corr_method: str, columns: list[str]) -> str:
  col_hash = hashlib.md5("|".join(sorted(columns)).encode()).hexdigest()[:12]
  return f"corr:{corr_method}:{col_hash}"


def get_cached_l0(
  batch_ids: list[int],
  portfolio_ids: list[int],
  date_from: str | None,
  date_to: str | None,
  clip_is: bool,
) -> CacheEntry | None:
  """Return cached L0 data if available for these exact batch_ids + date range.

  Supports superset: if cache has batches [1,2,3] and request is [1,2],
  returns the full cache (caller can filter). If request is a superset
  of what's cached, returns None (cache miss).
  """
  date_key = _make_date_key(date_from, date_to, clip_is)
  with _cache_lock:
    entry = _l0_cache.get(date_key)
  if entry is None:
    return None

  requested_batches = set(batch_ids)
  requested_portfolios = set(portfolio_ids)
  cached_batches = set(entry.batch_ids)
  cached_portfolios = set(entry.portfolio_ids)

  # Exact match or subset → cache hit
  if requested_batches <= cached_batches and requested_portfolios <= cached_portfolios:
    logger.info("L0 cache HIT: date_key=%s, requested=%s, cached=%s",
                date_key, sorted(requested_batches), sorted(cached_batches))
    return entry

  # Superset → cache miss (need to load the new batches)
  return None


def get_cached_l0_incremental(
  batch_ids: list[int],
  portfolio_ids: list[int],
  date_from: str | None,
  date_to: str | None,
  clip_is: bool,
) -> tuple[CacheEntry | None, list[int], list[int]]:
  """Check if we can do an incremental load.

  Returns:
      (cached_entry, new_batch_ids, new_portfolio_ids)
      If cached_entry is not None and new lists are empty, everything is cached.
      If cached_entry is not None with non-empty new lists, only those need loading.
      If cached_entry is None, everything needs loading.
  """
  date_key = _make_date_key(date_from, date_to, clip_is)
  with _cache_lock:
    entry = _l0_cache.get(date_key)
  if entry is None:
    return None, batch_ids, portfolio_ids

  cached_batches = set(entry.batch_ids)
  cached_portfolios = set(entry.portfolio_ids)
  new_batches = [b for b in batch_ids if b not in cached_batches]
  new_portfolios = [p for p in portfolio_ids if p not in cached_portfolios]

  if not new_batches and not new_portfolios:
    # Everything cached
    return entry, [], []

  # Partial cache — return what we have + list what's new
  logger.info("L0 cache PARTIAL: cached=%s, new_batches=%s, new_portfolios=%s",
              sorted(cached_batches), new_batches, new_portfolios)
  return entry, new_batches, new_portfolios


def put_cached_l0(
  batch_ids: list[int],
  portfolio_ids: list[int],
  date_from: str | None,
  date_to: str | None,
  clip_is: bool,
  daily_pnl: pd.DataFrame,
  trades: list,
  run_batch_map: dict[int, int],
  p_asset_batch: dict[str, int] | None = None,
  p_strat_map: dict[int, str] | None = None,
  p_asset_strat: dict[str, str] | None = None,
):
  """Store L0 data in cache.

  Detaches ORM trade objects from their session so they remain
  accessible after the original DB session closes.
  """
  # Detach ORM objects — prevents lazy-load crashes on closed sessions
  for t in trades:
    try:
      make_transient(t)
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)  # Already detached or not an ORM object
  date_key = _make_date_key(date_from, date_to, clip_is)
  entry = CacheEntry(
    daily_pnl=daily_pnl,
    trades=trades,
    run_batch_map=run_batch_map,
    p_asset_batch=p_asset_batch or {},
    p_strat_map=p_strat_map or {},
    p_asset_strat=p_asset_strat or {},
    batch_ids=sorted(batch_ids),
    portfolio_ids=sorted(portfolio_ids),
  )
  with _cache_lock:
    # Evict oldest if at capacity
    if len(_l0_cache) >= MAX_CACHE_ENTRIES and date_key not in _l0_cache:
      oldest_key = min(_l0_cache, key=lambda k: _l0_cache[k].created_at)
      del _l0_cache[oldest_key]
      logger.info("L0 cache EVICT: %s", oldest_key)
    _l0_cache[date_key] = entry
  logger.info("L0 cache PUT: date_key=%s, batches=%s, assets=%d",
              date_key, sorted(batch_ids), daily_pnl.shape[1])


def update_cached_l0(
  date_key: str,
  entry: CacheEntry,
  new_batch_ids: list[int],
  new_portfolio_ids: list[int],
  new_daily_pnl: pd.DataFrame,
  new_trades: list,
  new_run_batch_map: dict[int, int],
  new_p_asset_batch: dict[str, int] | None = None,
  new_p_strat_map: dict[int, str] | None = None,
  new_p_asset_strat: dict[str, str] | None = None,
):
  """Merge new batch data into existing cache entry (incremental update)."""
  # Detach new ORM trade objects from their session
  for t in new_trades:
    try:
      make_transient(t)
    except Exception as _exc:
      logger.debug("swallowed: %s", _exc)
  # Merge daily PnL (concat columns, align on date index)
  merged_pnl = pd.concat([entry.daily_pnl, new_daily_pnl], axis=1)
  merged_pnl = merged_pnl.fillna(0)  # New batches may not overlap fully

  # Merge trades
  merged_trades = entry.trades + new_trades

  # Merge run_batch_map
  merged_rbm = {**entry.run_batch_map, **new_run_batch_map}

  # Merge portfolio metadata maps
  merged_pab = {**entry.p_asset_batch, **(new_p_asset_batch or {})}
  merged_psm = {**entry.p_strat_map, **(new_p_strat_map or {})}
  merged_pas = {**entry.p_asset_strat, **(new_p_asset_strat or {})}

  # Merge batch/portfolio ID lists
  merged_batch_ids = sorted(set(entry.batch_ids + new_batch_ids))
  merged_portfolio_ids = sorted(set(entry.portfolio_ids + new_portfolio_ids))

  # Invalidate derived caches (stress, quality, corr) since columns changed
  with _cache_lock:
    _l0_cache[date_key] = CacheEntry(
      daily_pnl=merged_pnl,
      trades=merged_trades,
      run_batch_map=merged_rbm,
      p_asset_batch=merged_pab,
      p_strat_map=merged_psm,
      p_asset_strat=merged_pas,
      batch_ids=merged_batch_ids,
      portfolio_ids=merged_portfolio_ids,
    )
  logger.info("L0 cache UPDATE: date_key=%s, now %d batches, %d assets",
              date_key, len(merged_batch_ids), merged_pnl.shape[1])


def put_cached_stress(date_key: str, stress_key: str, daily_pnl: pd.DataFrame):
  """Cache stress-adjusted daily PnL."""
  with _cache_lock:
    entry = _l0_cache.get(date_key)
    if entry:
      entry.stress_daily_pnl = daily_pnl
      entry.stress_key = stress_key


def get_cached_stress(date_key: str, stress_key: str) -> pd.DataFrame | None:
  """Get cached stress-adjusted daily PnL if stress params match."""
  with _cache_lock:
    entry = _l0_cache.get(date_key)
    if entry and entry.stress_key == stress_key and entry.stress_daily_pnl is not None:
      return entry.stress_daily_pnl.copy()
  return None


def put_cached_quality(date_key: str, quality_key: str, daily_pnl: pd.DataFrame):
  """Cache quality-filtered daily PnL."""
  with _cache_lock:
    entry = _l0_cache.get(date_key)
    if entry:
      entry.quality_daily_pnl = daily_pnl
      entry.quality_key = quality_key


def get_cached_quality(date_key: str, quality_key: str) -> pd.DataFrame | None:
  """Get cached quality-filtered daily PnL if filter params match."""
  with _cache_lock:
    entry = _l0_cache.get(date_key)
    if entry and entry.quality_key == quality_key and entry.quality_daily_pnl is not None:
      return entry.quality_daily_pnl.copy()
  return None


def put_cached_corr(date_key: str, corr_key: str, corr_matrix: pd.DataFrame):
  """Cache correlation matrix."""
  with _cache_lock:
    entry = _l0_cache.get(date_key)
    if entry:
      entry.corr_matrix = corr_matrix
      entry.corr_key = corr_key


def get_cached_corr(date_key: str, corr_key: str) -> pd.DataFrame | None:
  """Get cached correlation matrix if method + columns match."""
  with _cache_lock:
    entry = _l0_cache.get(date_key)
    if entry and entry.corr_key == corr_key and entry.corr_matrix is not None:
      return entry.corr_matrix.copy()
  return None


def clear_cache():
  """Clear all cached data."""
  with _cache_lock:
    _l0_cache.clear()
  logger.info("L0 cache CLEARED")


class _TradeSnapshot:
  """Lightweight mutable snapshot of BacktestTrade for cache returns.

  CS-018: ``copy.deepcopy`` on ORM objects is brutal — SQLAlchemy has to
  traverse ``_sa_instance_state``, relationships, and identity maps.
  Profiling showed deepcopy dominated cache-hit cost at 30-40ms per call
  on ~50k-trade caches.

  A ``__slots__`` wrapper with direct ``getattr`` reads is roughly 10-20x
  faster because it skips all the ORM machinery and allocates a flat C
  struct instead of a full dict. We carry every field that downstream
  consumers (``trade_scaler``, ``daily_pnl``, correlation, etc.) read or
  mutate, so snapshots are drop-in replacements for BacktestTrade.

  The cache still stores the original ORM objects (pristine) — every cache
  hit returns fresh snapshots, so in-place mutation of ``net_profit`` /
  ``quantity`` / ``commission`` by ``scale_backtest_trades`` can't leak
  back into the cache.
  """

  __slots__ = (
    "id", "run_id", "trade_num", "position_id", "symbol", "side",
    "entry_time", "exit_time", "entry_price", "exit_price", "quantity",
    "net_profit", "net_profit_pct", "commission", "mae", "mfe",
    "sl_price", "tp_price", "exit_reason", "sl_past_be_time",
  )

  def __init__(self, src):
    for attr in _TradeSnapshot.__slots__:
      object.__setattr__(self, attr, getattr(src, attr, None))


def clone_cached_trades(trades: list) -> list:
  """Return fresh per-trade snapshots so callers can mutate them safely.

  CS-018: replaces ``copy.deepcopy`` with a flat ``__slots__`` wrapper. The
  cached ORM objects stay pristine; each call hands back a fresh mutable
  list that downstream scaling/attribution can modify in place without
  contaminating the cache.
  """
  return [_TradeSnapshot(t) for t in trades]


def cache_stats() -> dict:
  """Return cache statistics for debugging."""
  with _cache_lock:
    return {
      "entries": len(_l0_cache),
      "keys": list(_l0_cache.keys()),
      "details": {
        k: {
          "batches": v.batch_ids,
          "assets": v.daily_pnl.shape[1],
          "has_stress": v.stress_daily_pnl is not None,
          "has_quality": v.quality_daily_pnl is not None,
          "has_corr": v.corr_matrix is not None,
          "age_s": round(time.time() - v.created_at, 1),
        }
        for k, v in _l0_cache.items()
      },
    }
