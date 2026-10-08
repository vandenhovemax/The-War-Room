"""
Trade storage — stores exit fills as BacktestTrade records.
Each exit fill (TP1, TSL, SL, signal close) becomes one row.
Partial closes from the same position share a position_id for grouping.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from backend.backtester.models import OrderFill
from backend.db.models import BacktestTrade

log = logging.getLogger(__name__)


def store_trades(
  db: Session,
  run_id: int,
  symbol: str,
  trades: list[OrderFill],
  initial_capital: float,
) -> None:
  """Store OrderFills as BacktestTrade records using bulk insert.

  Exit fills now carry entry_price, entry_time, sl_price, tp_price
  directly from the engine, so no fragile dict lookup is needed.

  Entry commission is proportionally allocated to each exit fill
  so that stored net_profit reflects the full round-trip cost.

  Uses bulk_insert_mappings for 2-5x faster insertion vs row-by-row db.add().
  """
  # Build entry commission map: position_id → entry commission
  entry_comm: dict[str, float] = {}
  for fill in trades:
    if fill.action == "entry":
      entry_comm[fill.position_id] = fill.commission

  # Build total exit qty per position (for proportional allocation)
  exit_qty_total: dict[str, float] = defaultdict(float)
  for fill in trades:
    if fill.action == "exit":
      exit_qty_total[fill.position_id] += fill.qty

  trade_num = 0
  trade_dicts: list[dict] = []

  for fill in trades:
    if fill.action != "exit":
      continue

    trade_num += 1

    # Proportionally allocate entry commission to this exit
    pos_entry_comm = entry_comm.get(fill.position_id, 0.0)
    total_eq = exit_qty_total.get(fill.position_id, fill.qty)
    share = (fill.qty / total_eq) if total_eq > 0 else 1.0
    entry_comm_share = pos_entry_comm * share
    adjusted_pnl = fill.pnl - entry_comm_share

    trade_dicts.append({
      "run_id": run_id,
      "trade_num": trade_num,
      "position_id": fill.position_id,
      "symbol": symbol,
      "side": fill.side,
      "entry_time": fill.entry_time or fill.timestamp,
      "exit_time": fill.timestamp,
      "entry_price": fill.entry_price,
      "exit_price": fill.price,
      "quantity": fill.qty,
      "net_profit": adjusted_pnl,
      "net_profit_pct": (adjusted_pnl / initial_capital * 100) if initial_capital > 0 else 0,
      "sl_price": fill.sl_price or None,
      "tp_price": fill.tp_price or None,
      "exit_reason": fill.label or "signal",
      "sl_past_be_time": fill.sl_past_be_time,
      "mae": fill.mae,
      "mfe": fill.mfe,
      "commission": fill.commission + entry_comm_share,
    })

  if not trade_dicts:
    return

  # Phantom-dupe guard: if this run already has stored trades (i.e., this is
  # an APPEND from replay_run_forward / batch_rebuild), drop any incoming
  # rows whose (entry_time, side, entry_price) collide with an already-stored
  # position. Different position_id + same entry coordinates == phantom dupe.
  # Same position_id is allowed through (legitimate split-exit second row).
  #
  # On a fresh full backtest this query returns nothing and is effectively
  # free — a single indexed lookup on run_id.
  existing_keys: dict[tuple, str] = {}
  for row in db.query(
    BacktestTrade.entry_time, BacktestTrade.side, BacktestTrade.entry_price,
    BacktestTrade.position_id,
  ).filter(BacktestTrade.run_id == run_id).all():
    existing_keys[(row[0], row[1], row[2])] = row[3]

  if existing_keys:
    kept: list[dict] = []
    skipped = 0
    for td in trade_dicts:
      k = (td["entry_time"], td["side"], td["entry_price"])
      existing_pid = existing_keys.get(k)
      if existing_pid is not None and existing_pid != td["position_id"]:
        skipped += 1
        continue
      kept.append(td)
    if skipped:
      log.warning(
        "store_trades: blocked %d phantom-dupe rows on run_id=%d symbol=%s "
        "(same entry coordinates, different position_id — likely a refresh "
        "re-emission)", skipped, run_id, symbol,
      )
    trade_dicts = kept

  if trade_dicts:
    db.bulk_insert_mappings(BacktestTrade, trade_dicts)
