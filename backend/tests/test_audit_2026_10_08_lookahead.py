"""O1 (2026-10-08): Batch Selection must not score trades still open at the decision.

Daily PnL is keyed by ENTRY date. A trade entered on the last in-sample day
and closed days later used to bring its full result — only known after the
cutoff — into the selection. Selection now reads a decision-time view.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backend.analysis.batch_selection import (
  BatchSelectionConfig,
  _known_at_decision,
  build_asset_daily_pnl,
  build_asset_wr_data,
  build_batch_daily_pnl,
  build_straddle_table,
  precompute_pnl_structures,
  run_batch_selection,
)


@dataclass
class FakeTrade:
  symbol: str = "BTCUSDT"
  side: str = "Long"
  entry_time: datetime | None = None
  exit_time: datetime | None = None
  net_profit: float = 0.0
  position_id: str = "T1"
  run_id: int = 1
  id: int = 1


IS_END = datetime(2023, 12, 31)
WINDOW = {
  "window_num": 1, "is_start": "2023-01-01", "is_end": "2023-12-31",
  "oos_start": "2024-01-01", "oos_end": "2024-03-31",
}


def _daily_trades(run_id: int, symbol: str, pnl: float, tid0: int) -> list[FakeTrade]:
  """One same-day trade every 3 days through IS and OOS."""
  out = []
  d = datetime(2023, 1, 2)
  tid = tid0
  while d <= datetime(2024, 3, 30):
    tid += 1
    out.append(FakeTrade(symbol=symbol, entry_time=d + timedelta(hours=9),
                         exit_time=d + timedelta(hours=15), net_profit=pnl,
                         run_id=run_id, id=tid, position_id=f"P{tid}"))
    d += timedelta(days=3)
  return out


def _dataset():
  # Batch 201: steady +12 per trade. Batch 202: +10 per trade, plus ONE trade
  # entered on the last IS day that only closes three days into OOS for +5000.
  trades = _daily_trades(1, "BTCUSDT", 12.0, 0) + _daily_trades(2, "ETHUSDT", 10.0, 10_000)
  trades.append(FakeTrade(symbol="ETHUSDT", entry_time=IS_END + timedelta(hours=20),
                          exit_time=IS_END + timedelta(days=3, hours=4),
                          net_profit=5000.0, run_id=2, id=99_999, position_id="BIG"))
  return trades, {1: 201, 2: 202}


def _config() -> BatchSelectionConfig:
  return BatchSelectionConfig(
    max_batches=1, assets_per_batch=1, n_candidates=2, min_calibrated=1,
    batch_rank_metric="net_pnl", cal_rank_metric="net_pnl", asset_rank_metric="net_pnl",
    min_is_days=10, min_pf=0.5, min_asset_days=3, asset_wr_gate=0.0, q_starts=1,
  )


class TestKnownAtDecision:
  def test_open_trade_removed_and_inputs_untouched(self):
    trades, rbm = _dataset()
    batch_daily = build_batch_daily_pnl(trades, rbm)
    asset_daily, *_ = build_asset_daily_pnl(trades, rbm)
    wr = build_asset_wr_data(trades, rbm)
    day = pd.Timestamp("2023-12-31")
    before = float(batch_daily[202].at[day])
    assert before == 5010.0  # a regular +10 trade and the +5000 open one

    kb, ka, kw = _known_at_decision(batch_daily, asset_daily, wr,
                                    build_straddle_table(trades, rbm), day)

    assert kb[202].at[day] == 10.0             # the open +5000 is gone, the +10 stays
    assert ka["202_ETHUSDT_Long"].at[day] == 10.0
    assert kw["202_ETHUSDT_Long"].at[day, "total"] == 1
    assert batch_daily[202].at[day] == before  # originals not mutated
    assert kb[201] is batch_daily[201]         # unaffected series not copied

  def test_trade_closed_before_cutoff_is_kept(self):
    trades, rbm = _dataset()
    batch_daily = build_batch_daily_pnl(trades, rbm)
    asset_daily, *_ = build_asset_daily_pnl(trades, rbm)
    # Decision three days later: the big trade has closed, so it is known.
    later = pd.Timestamp("2024-01-03")
    kb, _, _ = _known_at_decision(batch_daily, asset_daily, None,
                                  build_straddle_table(trades, rbm), later)
    assert kb[202].at[pd.Timestamp("2023-12-31")] == 5010.0

  def test_same_day_trades_never_in_table(self):
    trades, rbm = _dataset()
    table = build_straddle_table(trades, rbm)
    assert len(table["pnl"]) == 1 and table["pnl"][0] == 5000.0
    assert table["entry_day"][0] == np.datetime64("2023-12-31")


class TestPipelineIgnoresUnclosedTrade:
  def test_steady_batch_wins_selection(self):
    trades, rbm = _dataset()
    result = run_batch_selection(trades, [WINDOW], _config(), rbm)
    picked = result["windows"][0]["candidates"][0]["selected_batches"]
    # Old behaviour picked 202: its IS net PnL included the +5000 that only
    # materialised in OOS. Known at the decision, 201 earns more.
    assert picked == [201]

  def test_precomputed_path_matches(self, tmp_path):
    trades, rbm = _dataset()
    pre = precompute_pnl_structures(trades, rbm, cache_dir=tmp_path)
    assert "straddle_table" in pre
    # Reload from the on-disk cache (which stores no raw trades).
    cached = precompute_pnl_structures(trades, rbm, cache_dir=tmp_path)
    assert cached["trades"] == []
    result = run_batch_selection([], [WINDOW], _config(), rbm, precomputed=cached)
    picked = result["windows"][0]["candidates"][0]["selected_batches"]
    assert picked == [201]
