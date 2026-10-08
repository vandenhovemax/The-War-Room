"""Unified Trade Log — read-only merge of BacktestTrade + Trade.

Purpose:
  Step-2 / event_wfo needs a single trade stream per batch. Historical trades
  come from `backtest_trades` (populated by the offline engine and by the
  nightly batch-store refresh in L1.1). Live fills come from `trades`
  (populated by the live executor). Both must appear to Step-2 as one
  time-ordered stream keyed by (batch_id, symbol, side).

Design:
  * READ ONLY. Additive. No new tables. No writes.
  * Output shape is the existing `LightTrade` namedtuple so downstream
    consumers (`precompute_pnl_structures`, `run_event_wfo`, ...) do not
    need to change — they already duck-type on .symbol / .net_profit /
    .entry_time / .exit_time / .run_id / .side / .timeframe.
  * Live `Trade` rows are mapped to LightTrade via:
        entry_time = opened_at
        exit_time  = closed_at
        net_profit = realized_pnl
        run_id     = -batch_id  (sentinel — live rows have no BacktestRun;
                                 the negative marker keeps the run→batch
                                 map consistent and avoids collisions with
                                 real run_ids.)

TIMEZONE (2026-06-10 fix):
  `Trade.opened_at` / `closed_at` are written by the live engine in UTC
  (models.py: datetime.now(timezone.utc)). `BacktestTrade.entry_time` is
  NYT-naive by repo convention. Merging the two streams unconverted skewed
  every live fill 4-5 hours late relative to its backtest twin —
  corrupting event ordering inside the Step-2 walk AND the session
  exec-filter hour check (UTC hour tested against NYT exclude list).
  Every live timestamp is now converted UTC → NYT-naive before entering
  the merged stream.

Usage:
    from backend.analysis.trade_log_unified import load_unified_trades

    trades, rbm, batch_info = load_unified_trades(
      db,
      batch_ids=[12, 17, 41],
      cutoff=portfolio.optimization_end_date,  # D
    )
    # Pass `trades` directly into run_event_wfo / precompute_pnl_structures.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend.analysis.batch_selection import LightTrade, load_trades_lightweight
from backend.db.models import Trade

log = logging.getLogger(__name__)

_NYT = ZoneInfo("America/New_York")


def _utc_to_nyt_naive(dt: datetime | None) -> datetime | None:
  """UTC (naive or aware) → NYT tz-naive, matching BacktestTrade times."""
  if dt is None:
    return None
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(_NYT).replace(tzinfo=None)


def load_unified_trades(
  db,
  batch_ids: list[int] | None = None,
  *,
  group_names: list[str] | None = None,
  cutoff: datetime | None = None,
  portfolio_id: int | None = None,
  scale_pnl: bool = True,
) -> tuple[list[LightTrade], dict[int, int], dict[int, dict]]:
  """Load historical + live trades as a single LightTrade list.

  Args:
    db:            SQLAlchemy session.
    batch_ids:     Specific batch IDs (None = all active, same as loader).
    group_names:   Load by batch group (overrides batch_ids), same as loader.
    cutoff:        Only include live `Trade` rows with opened_at > cutoff.
                   Pass `portfolio.optimization_end_date` so historical +
                   live do not double-count the same time window.
                   None = include all live rows (be careful — only use when
                   you know the backtest_trades table ends cleanly).
    portfolio_id:  If given, restrict live `Trade` rows to this portfolio.
                   Recommended for live-loop callers.
    scale_pnl:     Passed through to load_trades_lightweight. Live `Trade`
                   rows are already at production sizing and are NOT scaled.

  Returns:
    Same 3-tuple shape as load_trades_lightweight:
      (trades, run_batch_map, batch_info)
    Live rows appear with synthetic run_id = -batch_id. They are added to
    run_batch_map so downstream code can still look up batch_info[bid]
    via run_batch_map[run_id].
  """
  # 1. Historical trades via the existing loader (unchanged behavior).
  hist_trades, rbm, batch_info = load_trades_lightweight(
    db, batch_ids=batch_ids, group_names=group_names, scale_pnl=scale_pnl,
  )

  if not batch_info:
    return hist_trades, rbm, batch_info

  valid_bids = set(batch_info.keys())

  # 2. Live trades joined on batch_id. We deliberately do NOT infer batch_id
  # from symbol/portfolio — the executor already writes the batch_id on every
  # fill (see strategy_executor.py / order_manager.py). Rows with NULL
  # batch_id are skipped (they can't be attributed to a Step-2 basket).
  q = db.query(Trade).filter(Trade.batch_id.in_(valid_bids))
  if cutoff is not None:
    q = q.filter(Trade.opened_at > cutoff)
  if portfolio_id is not None:
    q = q.filter(Trade.portfolio_id == portfolio_id)
  q = q.filter(Trade.is_test == False)  # noqa: E712 — soft-hidden test fills

  live_rows = q.all()
  if not live_rows:
    log.info(
      "load_unified_trades: %d historical trades, 0 live (cutoff=%s)",
      len(hist_trades), cutoff,
    )
    return hist_trades, rbm, batch_info

  # 3. Map live rows → LightTrade. Use negative run_id as a sentinel so it
  # cannot collide with any real BacktestRun.id (which is a positive int).
  live_lights: list[LightTrade] = []
  for t in live_rows:
    bid = t.batch_id
    if bid is None or bid not in valid_bids:
      continue
    synthetic_run_id = -int(bid)
    rbm.setdefault(synthetic_run_id, bid)
    tf = batch_info[bid].get("timeframe", "")
    # Live fills are at production sizing — never re-scale.
    pnl = float(t.realized_pnl or 0.0)
    live_lights.append(LightTrade(
      run_id=synthetic_run_id,
      symbol=t.symbol,
      side=t.side,
      net_profit=pnl,
      # UTC → NYT-naive so live fills sort/filter consistently with
      # BacktestTrade rows (see TIMEZONE note in module docstring).
      entry_time=_utc_to_nyt_naive(t.opened_at),
      exit_time=_utc_to_nyt_naive(t.closed_at),
      timeframe=tf,
    ))

  merged = hist_trades + live_lights
  log.info(
    "load_unified_trades: %d historical + %d live = %d total "
    "(cutoff=%s, portfolio_id=%s)",
    len(hist_trades), len(live_lights), len(merged), cutoff, portfolio_id,
  )
  return merged, rbm, batch_info
