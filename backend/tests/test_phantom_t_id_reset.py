"""Regression test for phantom-t_id reset in _sync_positions_from_db.

Background: incremental warmup (commit 782d5cb) saves the strategy's
public attributes (including `t_id` / `t_dir` / `t_entry` / etc.) to
disk so subsequent restarts can resume without a full historical
replay. The capture intentionally INCLUDES `t_id` because it's a
non-underscore-prefixed Python list. On restore, the strategy comes
back with t_id reflecting the strategy's view at end-of-replay —
which often includes "trades" that were technically still open on
the last historical bar but were never real DB positions.

When the engine then handles a live bar close:
  • _sync_positions_from_db clears strategy._positions and re-builds
    from DB. Pre-fix, it returned early when DB had no open positions
    WITHOUT touching t_id.
  • strategy.next() then runs with phantom t_id entries → emits
    register_exit signals for non-existent positions AND its
    max-trades-per-range gate counts the phantoms and refuses to
    generate new entries.

User symptom: "no new entries since the 29th" — the engine was alive
and the scanner was scanning, but the strategy's internal max-trades
gate was permanently saturated by phantom entries from the saved
warmup state.

Fix: when the DB has no open positions for this (portfolio, symbol),
clear the strategy's t_id / t_dir / t_entry / t_sl / t_tp / t_qty /
t_open / t_close / t_label arrays in addition to clearing _positions.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.exchange.strategy_executor import StrategyExecutor


def _make_executor() -> StrategyExecutor:
  """Minimal StrategyExecutor whose _db_factory returns a mock session
  that yields zero open Position rows."""
  fake_db = MagicMock()
  fake_query = MagicMock()
  fake_query.filter.return_value = fake_query
  fake_query.all.return_value = []  # ← zero open positions in DB
  fake_db.query.return_value = fake_query

  ex = StrategyExecutor(
    candle_aggregator=MagicMock(),
    db_factory=lambda: fake_db,
  )
  return ex


class _FakeStrategy:
  """Minimal stand-in for a BaseStrategy with the public trade-tracking
  arrays the framework exposes. Pre-populated to simulate phantom state
  carried over from incremental-warmup state restore."""
  def __init__(self):
    self._positions = {"T77966": "phantom_pos_obj", "T77967": "phantom_pos_obj"}
    self.t_id = ["T77966", "T77967", "T77983"]
    self.t_dir = [-1, -1, -1]  # all shorts
    self.t_entry = [1.234, 1.235, 1.236]
    self.t_sl = [1.300, 1.301, 1.302]
    self.t_tp = [1.100, 1.101, 1.102]
    self.t_qty = [10.0, 10.0, 10.0]
    self.t_open = [True, True, True]
    self.t_close = [None, None, None]
    self.t_label = ["short_T77966", "short_T77967", "short_T77983"]


def test_sync_clears_phantom_t_id_when_db_has_no_positions():
  """Pre-fix this test would FAIL: t_id stayed populated."""
  ex = _make_executor()
  strategy = _FakeStrategy()

  ex._sync_positions_from_db(strategy, portfolio_id=28, symbol="FLOWUSDT")

  assert strategy._positions == {}, "_positions must be cleared"
  assert strategy.t_id == [], "t_id must be cleared when DB has no positions"
  assert strategy.t_dir == [], "t_dir must be cleared"
  assert strategy.t_entry == [], "t_entry must be cleared"
  assert strategy.t_sl == [], "t_sl must be cleared"
  assert strategy.t_tp == [], "t_tp must be cleared"
  assert strategy.t_qty == [], "t_qty must be cleared"
  assert strategy.t_open == [], "t_open must be cleared"
  assert strategy.t_close == [], "t_close must be cleared"
  assert strategy.t_label == [], "t_label must be cleared"


def test_sync_does_not_break_when_strategy_lacks_t_arrays():
  """Some strategies might not have any t_X tracking arrays. The reset
  must skip missing attributes silently (they don't exist → nothing to
  clear)."""
  ex = _make_executor()

  class _MinimalStrategy:
    def __init__(self):
      self._positions = {"only_position": "x"}

  strategy = _MinimalStrategy()
  # Should not raise — just silently skip the missing attrs
  ex._sync_positions_from_db(strategy, portfolio_id=28, symbol="FLOWUSDT")
  assert strategy._positions == {}


def test_sync_with_db_positions_does_not_clear_t_arrays():
  """When the DB DOES have open positions, the existing path runs
  (which uses t_id/t_dir to map DB positions to strategy positions).
  We must NOT clear them in that case."""
  fake_pos = MagicMock()
  fake_pos.side = "Sell"
  fake_pos.entry_price = 1.234
  fake_pos.quantity = 10.0

  fake_db = MagicMock()
  fake_query = MagicMock()
  fake_query.filter.return_value = fake_query
  fake_query.all.return_value = [fake_pos]  # ONE open short position
  fake_db.query.return_value = fake_db_query = fake_query
  fake_db.query.return_value = fake_query

  ex = StrategyExecutor(
    candle_aggregator=MagicMock(),
    db_factory=lambda: fake_db,
  )
  strategy = _FakeStrategy()

  ex._sync_positions_from_db(strategy, portfolio_id=28, symbol="FLOWUSDT")

  # _positions was cleared then re-built from DB
  # t_id/t_dir/etc remain (they represent the strategy's view)
  assert strategy.t_id == ["T77966", "T77967", "T77983"], (
    "When DB has positions, t_id must NOT be wiped — the existing "
    "code path uses it to map DB positions to strategy slots"
  )
  assert strategy.t_dir == [-1, -1, -1]


def test_phantom_t_id_blocks_regression_scenario():
  """End-to-end scenario: simulate the user's actual situation —
  saved t_id has 3 phantom entries, DB is empty (no real positions),
  sync should leave the strategy in a clean state ready to fire new
  entries on the next bar close."""
  ex = _make_executor()
  strategy = _FakeStrategy()

  ex._sync_positions_from_db(strategy, portfolio_id=28, symbol="FLOWUSDT")

  # All clean
  assert len(strategy.t_id) == 0
  assert len(strategy._positions) == 0
  # The strategy can now generate new entries — its max-trades-per-range
  # gate, which counts t_id entries, sees 0 and won't refuse.
