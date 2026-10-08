"""Trade-concentration diagnostics — detect configs whose headline OOS PnL
is driven by a handful of outlier winners rather than a distributed edge.

Motivating example (Feb 2025 live WFAS run):
  - A monthly view showed 3 "winning" trades totalling $11,216
  - 2 of them were the EXACT same ALGO short at Feb 1 11:00 AM ($5,028 each)
    selected by two different batches (B43 and B44)
  - Those 2 near-identical fills = $10,057 = 90% of the month
  - Config headline looks great but collapses if that one bar goes the other way

Rule agreed with user (2026-04-21):
  No single trade may contribute more than 25% of the cumulative OOS PnL.

This module is the single source of truth for concentration metrics. It is
invoked from:
  - backend/analysis/wfo_oos_eval.py — surfaces concentration on the OOS eval
    returned to /api/analysis/wfo and the Cross-Strategy UI
  - research harnesses that want to gate/reject over-concentrated configs

Design goals:
  - Zero coupling to ORM. Works on plain iterables of (pnl, ts, symbol, side).
    Callers that have BacktestTrade objects use concentration_from_trades().
  - Returns full diagnostic dict, NOT just a pass/fail scalar — caller decides
    whether to hard-gate or just surface in UI.
  - Period-agnostic: OOS-total, per-quarter, per-month all at once.
  - Null-safe on empty input or zero-cum-pnl configs.
  - Absolute-value ranked: a catastrophic loser is as concentrated as an
    outlier winner. A single trade worth -40% of cum is still a red flag.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

# Default thresholds. Callers can override per-call.
DEFAULT_MAX_TRADE_PCT_OF_OOS = 0.25       # user's primary rule
DEFAULT_MAX_TRADE_PCT_OF_QUARTER = 0.50   # more lenient — quarters have fewer trades
DEFAULT_MAX_TRADE_PCT_OF_MONTH = 0.80     # most lenient — 1-3 trades/month is normal
DEFAULT_MAX_TOP3_PCT_OF_OOS = 0.50        # top-3 combined shouldn't exceed half of cum


@dataclass(frozen=True)
class TradeRecord:
  """Minimal trade shape used by concentration diagnostics.

  entry_time is NYT naive (per CLAUDE.md convention — matches BacktestTrade).
  """
  pnl: float
  entry_time: datetime
  symbol: str = ""
  side: str = ""


def _period_key(ts: datetime, granularity: str) -> str:
  """Return a canonical period key for grouping."""
  if granularity == "month":
    return ts.strftime("%Y-%m")
  if granularity == "quarter":
    q = (ts.month - 1) // 3 + 1
    return f"{ts.year}-Q{q}"
  if granularity == "year":
    return str(ts.year)
  raise ValueError(f"unknown granularity: {granularity!r}")


def compute_concentration(
  trades: Sequence[TradeRecord | tuple],
  *,
  max_trade_pct_of_oos: float = DEFAULT_MAX_TRADE_PCT_OF_OOS,
  max_trade_pct_of_quarter: float = DEFAULT_MAX_TRADE_PCT_OF_QUARTER,
  max_trade_pct_of_month: float = DEFAULT_MAX_TRADE_PCT_OF_MONTH,
  max_top3_pct_of_oos: float = DEFAULT_MAX_TOP3_PCT_OF_OOS,
) -> dict:
  """Compute concentration diagnostics and pass/fail gates.

  `trades` accepts either TradeRecord instances OR tuples of shape
  (pnl, entry_time) or (pnl, entry_time, symbol, side). Tuples give
  zero-friction wiring into pipelines that don't have a shared dataclass.

  Returns:
    dict with:
      max_trade_pnl: float  — largest |pnl|
      cum_oos_pnl: float    — signed sum of all trade pnls
      max_trade_pct_of_oos: float   — user's primary rule metric (0-∞)
      top3_trade_pct_of_oos: float
      herfindahl_gross_profit: float  — HHI on winner |pnl| shares, [0, 1]
      per_quarter_max_pct: dict[str, float]
      per_month_max_pct: dict[str, float]
      passes_concentration_gate: bool  — True iff all thresholds satisfied
      violations: list[str]  — human-readable reasons a gate failed
      n_trades, n_quarters, n_months: int
  """
  records: list[TradeRecord] = []
  for t in trades:
    if isinstance(t, TradeRecord):
      records.append(t)
    elif isinstance(t, tuple):
      if len(t) == 2:
        pnl, ts = t
        records.append(TradeRecord(float(pnl), ts))
      elif len(t) == 4:
        pnl, ts, sym, side = t
        records.append(TradeRecord(float(pnl), ts, str(sym), str(side)))
      else:
        raise ValueError(f"trade tuple must be len 2 or 4, got {len(t)}")
    else:
      raise TypeError(f"unsupported trade type: {type(t)}")

  if not records:
    return {
      "max_trade_pnl": 0.0,
      "cum_oos_pnl": 0.0,
      "max_trade_pct_of_oos": 0.0,
      "top3_trade_pct_of_oos": 0.0,
      "herfindahl_gross_profit": 0.0,
      "per_quarter_max_pct": {},
      "per_month_max_pct": {},
      "passes_concentration_gate": True,
      "violations": [],
      "n_trades": 0,
      "n_quarters": 0,
      "n_months": 0,
    }

  pnls = [r.pnl for r in records]
  cum_oos = sum(pnls)

  # Absolute-value ranking: catastrophic losers count as concentration too.
  abs_pnls = sorted((abs(p) for p in pnls), reverse=True)
  max_abs = abs_pnls[0]
  top3_abs = sum(abs_pnls[:3])

  cum_oos_denom = abs(cum_oos) if cum_oos != 0 else 1e-9
  max_trade_pct_oos = max_abs / cum_oos_denom
  top3_pct_oos = top3_abs / cum_oos_denom

  # Herfindahl on gross profit (winners only)
  wins = [p for p in pnls if p > 0]
  gross_profit = sum(wins) if wins else 0.0
  if gross_profit > 0:
    shares = [p / gross_profit for p in wins]
    herfindahl = sum(s * s for s in shares)  # [1/n, 1.0]
  else:
    herfindahl = 0.0

  # Per-period concentration
  per_quarter_pnl: dict[str, list[float]] = defaultdict(list)
  per_month_pnl: dict[str, list[float]] = defaultdict(list)
  for r in records:
    per_quarter_pnl[_period_key(r.entry_time, "quarter")].append(r.pnl)
    per_month_pnl[_period_key(r.entry_time, "month")].append(r.pnl)

  per_quarter_max_pct = {
    k: (max(abs(p) for p in pnl_list) / (abs(sum(pnl_list)) or 1e-9))
    for k, pnl_list in per_quarter_pnl.items()
  }
  per_month_max_pct = {
    k: (max(abs(p) for p in pnl_list) / (abs(sum(pnl_list)) or 1e-9))
    for k, pnl_list in per_month_pnl.items()
  }

  # Gates
  violations: list[str] = []
  if max_trade_pct_oos > max_trade_pct_of_oos:
    violations.append(
      f"max_trade={max_abs:.2f} is {max_trade_pct_oos:.1%} of OOS cum "
      f"{cum_oos:.2f} (> {max_trade_pct_of_oos:.0%} threshold)"
    )
  if top3_pct_oos > max_top3_pct_of_oos:
    violations.append(
      f"top-3 trades = {top3_abs:.2f} is {top3_pct_oos:.1%} of OOS cum "
      f"(> {max_top3_pct_of_oos:.0%} threshold)"
    )
  worst_quarter = max(per_quarter_max_pct.items(), key=lambda kv: kv[1], default=(None, 0))
  if worst_quarter[0] and worst_quarter[1] > max_trade_pct_of_quarter:
    violations.append(
      f"quarter {worst_quarter[0]}: max_trade is {worst_quarter[1]:.1%} of quarter PnL "
      f"(> {max_trade_pct_of_quarter:.0%})"
    )
  worst_month = max(per_month_max_pct.items(), key=lambda kv: kv[1], default=(None, 0))
  if worst_month[0] and worst_month[1] > max_trade_pct_of_month:
    violations.append(
      f"month {worst_month[0]}: max_trade is {worst_month[1]:.1%} of month PnL "
      f"(> {max_trade_pct_of_month:.0%})"
    )

  return {
    "max_trade_pnl": round(max_abs, 2),
    "cum_oos_pnl": round(cum_oos, 2),
    "max_trade_pct_of_oos": round(max_trade_pct_oos, 4),
    "top3_trade_pct_of_oos": round(top3_pct_oos, 4),
    "herfindahl_gross_profit": round(herfindahl, 4),
    "per_quarter_max_pct": {k: round(v, 4) for k, v in per_quarter_max_pct.items()},
    "per_month_max_pct": {k: round(v, 4) for k, v in per_month_max_pct.items()},
    "passes_concentration_gate": len(violations) == 0,
    "violations": violations,
    "n_trades": len(records),
    "n_quarters": len(per_quarter_pnl),
    "n_months": len(per_month_pnl),
  }


def concentration_from_trades(trades, **kwargs) -> dict:
  """Shortcut for callers holding BacktestTrade-like objects.

  Works on any object that exposes `.net_profit`, `.entry_time`,
  `.symbol` and `.side`. Returns the same dict as compute_concentration().
  """
  records = [
    TradeRecord(
      pnl=float(getattr(t, "net_profit", 0) or 0),
      entry_time=getattr(t, "entry_time"),
      symbol=str(getattr(t, "symbol", "") or ""),
      side=str(getattr(t, "side", "") or ""),
    )
    for t in trades
    if getattr(t, "entry_time", None) is not None
  ]
  return compute_concentration(records, **kwargs)


def dedupe_same_bar_cross_batch(
  trades: Iterable[TradeRecord | tuple],
  *,
  bar_resolution_minutes: int = 30,
) -> list[TradeRecord]:
  """Collapse (symbol, side, bar) triples to a single representative trade.

  Motivation: when two batches both select the same asset, they generate
  two separate trades on the same bar with near-identical fills. These are
  not two independent wins — they're the same market event double-counted.

  Dedup rule: within a (symbol, side, bar_bucket) key, keep ONE trade whose
  pnl is the MEAN of the bucket. This is equivalent to equal-weighting the
  batches that selected this asset.

  bar_resolution_minutes: time granularity for the bucket (default 30m —
    matches the common 30m trading bar). 11:00 and 11:04 on same symbol/side
    merge; 11:00 and 11:35 do not.
  """
  buckets: dict[tuple, list[TradeRecord]] = defaultdict(list)
  for t in trades:
    if isinstance(t, tuple):
      if len(t) == 2:
        pnl, ts = t
        t = TradeRecord(float(pnl), ts)
      else:
        pnl, ts, sym, side = t
        t = TradeRecord(float(pnl), ts, str(sym), str(side))
    mins_since_epoch = int(t.entry_time.timestamp() // 60)
    bar_idx = mins_since_epoch // bar_resolution_minutes
    key = (t.symbol, t.side, bar_idx)
    buckets[key].append(t)

  out: list[TradeRecord] = []
  for key, bucket in buckets.items():
    if len(bucket) == 1:
      out.append(bucket[0])
    else:
      mean_pnl = sum(r.pnl for r in bucket) / len(bucket)
      out.append(TradeRecord(
        pnl=mean_pnl,
        entry_time=bucket[0].entry_time,
        symbol=bucket[0].symbol,
        side=bucket[0].side,
      ))
  out.sort(key=lambda r: r.entry_time)
  return out
