"""Walk Forward Optimization — OOS evaluation with H1/H2 support.

Evaluates selected assets on OOS data AFTER optimization is complete.
Supports standard (single OOS) and overlapping (H1/H2 dual-phase).
"""
from __future__ import annotations

import pandas as pd

from backend.analysis.trade_concentration import concentration_from_trades
from backend.analysis.wfo_asset_selection import (
  _compute_metrics,
  _compute_oos_benchmark,
  _filter_trades_by_dates,
)
from backend.db.models import BacktestTrade


def _trade_to_key(
  t: BacktestTrade,
  needs_side: bool,
  run_batch_map: dict[int, int] | None = None,
) -> str:
  """Build asset key for a trade, optionally batch-tagged."""
  if needs_side:
    base = f"{t.symbol}_{(t.side or 'Long').title()}"
  else:
    base = t.symbol
  if run_batch_map and t.run_id in run_batch_map:
    from backend.api.routes.cross_strategy import _add_batch_tag
    return _add_batch_tag(base, run_batch_map[t.run_id])
  return base


def _filter_oos_trades(
  all_trades: list[BacktestTrade],
  oos_start: str,
  oos_end: str,
  assets: list[str],
  needs_side: bool,
  run_batch_map: dict[int, int] | None = None,
) -> list[BacktestTrade]:
  """Filter trades for OOS evaluation: within date range + matching assets.

  When run_batch_map is provided, uses batch-tagged keys (SYMBOL~B{id}_Side)
  to match trades — same as Cross-Strategy.
  """
  asset_set = set(assets)
  oos_all = _filter_trades_by_dates(all_trades, oos_start, oos_end)
  return [t for t in oos_all if _trade_to_key(t, needs_side, run_batch_map) in asset_set]


def compute_wfe(
  is_metrics: dict,
  oos_metrics: dict,
  is_start: str,
  is_end: str,
  oos_start: str,
  oos_end: str,
) -> float | None:
  """WFE = (OOS PnL/day) / (IS PnL/day)."""
  is_pnl = is_metrics.get("net_profit", 0)
  oos_pnl = oos_metrics.get("net_profit", 0)
  is_days = max((pd.Timestamp(is_end) - pd.Timestamp(is_start)).days, 1)
  oos_days = max((pd.Timestamp(oos_end) - pd.Timestamp(oos_start)).days, 1)
  if is_pnl == 0:
    return None
  return round((oos_pnl / oos_days) / (is_pnl / is_days), 4)


def compute_degradation(
  h1_metrics: dict,
  h2_metrics: dict,
  h1_start: str,
  h1_end: str,
  h2_start: str,
  h2_end: str,
) -> float | None:
  """Degradation Ratio = (H2 PnL/day) / (H1 PnL/day).

  < 1.0 means performance decayed in the second half.
  """
  h1_pnl = h1_metrics.get("net_profit", 0)
  h2_pnl = h2_metrics.get("net_profit", 0)
  h1_days = max((pd.Timestamp(h1_end) - pd.Timestamp(h1_start)).days, 1)
  h2_days = max((pd.Timestamp(h2_end) - pd.Timestamp(h2_start)).days, 1)
  h1_rate = h1_pnl / h1_days
  if h1_rate == 0:
    return None
  return round((h2_pnl / h2_days) / h1_rate, 4)


def evaluate_oos_standard(
  all_trades: list[BacktestTrade],
  window: dict,
  assets: list[str],
  is_metrics: dict,
  needs_side: bool,
  run_batch_map: dict[int, int] | None = None,
  session_mask_data: dict | None = None,
) -> dict:
  """Standard OOS evaluation (rolling/static modes).

  Returns: oos_metrics, oos_wfe, oos_benchmark
  """
  if not window.get("oos_start") or not window.get("oos_end"):
    return {"oos_metrics": None, "oos_wfe": None, "oos_benchmark": None}

  oos_trades = _filter_oos_trades(
    all_trades, window["oos_start"], window["oos_end"], assets, needs_side,
    run_batch_map,
  )
  # Apply session mask to OOS trades (same mask learned from IS)
  if session_mask_data and oos_trades:
    from backend.analysis.session_mask_filter import apply_session_mask
    oos_trades = apply_session_mask(oos_trades, session_mask_data)
  oos_metrics = _compute_metrics(oos_trades)
  wfe = compute_wfe(
    is_metrics, oos_metrics,
    window["is_start"], window["is_end"],
    window["oos_start"], window["oos_end"],
  )

  oos_benchmark = None
  if oos_trades and len(oos_trades) >= 4:
    oos_benchmark = _compute_oos_benchmark(oos_trades)

  oos_concentration = concentration_from_trades(oos_trades) if oos_trades else None

  return {
    "oos_metrics": oos_metrics,
    "oos_wfe": wfe,
    "oos_benchmark": oos_benchmark,
    "oos_concentration": oos_concentration,
  }


def evaluate_oos_overlapping(
  all_trades: list[BacktestTrade],
  window: dict,
  assets: list[str],
  is_metrics: dict,
  needs_side: bool,
  run_batch_map: dict[int, int] | None = None,
  session_mask_data: dict | None = None,
) -> dict:
  """Overlapping OOS evaluation (dual-phase H1/H2).

  Evaluates OOS-H1, OOS-H2, and OOS-Total separately.
  Returns: oos_h1_metrics, oos_h2_metrics, oos_metrics (total),
           wfe_h1, oos_wfe (total), degradation_ratio, oos_benchmark
  """
  _sm = session_mask_data  # shorthand
  h1_start = window.get("oos_h1_start")
  h1_end = window.get("oos_h1_end")
  h2_start = window.get("oos_h2_start")
  h2_end = window.get("oos_h2_end")

  if not h1_start or not h2_end:
    return {
      "oos_h1_metrics": None, "oos_h2_metrics": None,
      "oos_metrics": None, "wfe_h1": None,
      "oos_wfe": None, "degradation_ratio": None,
      "oos_benchmark": None,
    }

  # Lazy import for session mask (only if needed)
  _apply_mask = None
  if _sm:
    from backend.analysis.session_mask_filter import apply_session_mask
    _apply_mask = apply_session_mask

  # H1 evaluation
  h1_trades = _filter_oos_trades(all_trades, h1_start, h1_end, assets, needs_side, run_batch_map)
  if _apply_mask and h1_trades:
    h1_trades = _apply_mask(h1_trades, _sm)
  h1_metrics = _compute_metrics(h1_trades)

  # H2 evaluation
  h2_trades = _filter_oos_trades(all_trades, h2_start, h2_end, assets, needs_side, run_batch_map)
  if _apply_mask and h2_trades:
    h2_trades = _apply_mask(h2_trades, _sm)
  h2_metrics = _compute_metrics(h2_trades)

  # Total OOS (H1 + H2 combined)
  total_trades = _filter_oos_trades(
    all_trades, h1_start, h2_end, assets, needs_side, run_batch_map,
  )
  if _apply_mask and total_trades:
    total_trades = _apply_mask(total_trades, _sm)
  total_metrics = _compute_metrics(total_trades)

  # WFE on H1 only (immediate reactivity)
  wfe_h1 = compute_wfe(is_metrics, h1_metrics,
                        window["is_start"], window["is_end"], h1_start, h1_end)

  # WFE on total OOS
  wfe_total = compute_wfe(is_metrics, total_metrics,
                           window["is_start"], window["is_end"], h1_start, h2_end)

  # Degradation ratio: H2 vs H1
  degradation = compute_degradation(h1_metrics, h2_metrics,
                                     h1_start, h1_end, h2_start, h2_end)

  # Benchmark on total OOS
  oos_benchmark = None
  if total_trades and len(total_trades) >= 4:
    oos_benchmark = _compute_oos_benchmark(total_trades)

  # Concentration diagnostics — per-half and total. H1/H2 concentration helps
  # spot configs whose edge in one half is just one outlier.
  oos_concentration = concentration_from_trades(total_trades) if total_trades else None
  oos_h1_concentration = concentration_from_trades(h1_trades) if h1_trades else None
  oos_h2_concentration = concentration_from_trades(h2_trades) if h2_trades else None

  return {
    "oos_h1_metrics": h1_metrics,
    "oos_h2_metrics": h2_metrics,
    "oos_metrics": total_metrics,
    "wfe_h1": wfe_h1,
    "oos_wfe": wfe_total,
    "degradation_ratio": degradation,
    "oos_benchmark": oos_benchmark,
    "oos_concentration": oos_concentration,
    "oos_h1_concentration": oos_h1_concentration,
    "oos_h2_concentration": oos_h2_concentration,
  }
