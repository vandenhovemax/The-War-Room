"""CS-018: ``clone_cached_trades`` must return mutable, independent copies
without paying the ``copy.deepcopy`` price on ORM objects.

Before this fix, every cache hit ran ``copy.deepcopy(trades)`` over a list
of SQLAlchemy ORM objects. On a ~50k-trade cache that single call burned
30-40ms — the dominant cost of any cache hit path.

The fix replaces deepcopy with a flat ``__slots__`` snapshot wrapper that
carries every BacktestTrade field as a plain attribute. Downstream consumers
(``trade_scaler``, ``daily_pnl``, etc.) still mutate ``net_profit`` /
``quantity`` / ``commission`` in place — snapshots are drop-in replacements.

Contracts pinned:
1. Snapshots are independent — mutating a returned trade does not affect
   the cached original (nor any other clone).
2. Every field consumed downstream is carried (symbol, side, entry_time,
   exit_time, entry_price, exit_price, quantity, net_profit, commission,
   run_id, etc.).
3. The snapshot class is not a SQLAlchemy ORM object (no ``_sa_instance_state``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from backend.analysis.optimization_cache import (
  _TradeSnapshot,
  clone_cached_trades,
)


def _fake_trade(**overrides):
  """Build a minimal trade-like object mirroring BacktestTrade's attribute
  surface. Using a plain object so the test doesn't require a live DB.
  """
  defaults = dict(
    id=1,
    run_id=10,
    trade_num=1,
    position_id="p1",
    symbol="BTCUSDT",
    side="Long",
    entry_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
    exit_time=datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc),
    entry_price=42000.0,
    exit_price=43000.0,
    quantity=1.0,
    net_profit=100.0,
    net_profit_pct=2.4,
    commission=0.5,
    mae=-50.0,
    mfe=150.0,
    sl_price=41500.0,
    tp_price=44000.0,
    exit_reason="tp",
    sl_past_be_time=None,
  )
  defaults.update(overrides)
  return SimpleNamespace(**defaults)


def test_clones_carry_every_consumed_field():
  src = _fake_trade()
  clone = clone_cached_trades([src])[0]
  for attr in _TradeSnapshot.__slots__:
    assert getattr(clone, attr) == getattr(src, attr), (
      f"CS-018: snapshot missing/wrong value for {attr!r}"
    )


def test_clone_is_independent_of_source():
  src = _fake_trade(net_profit=100.0, quantity=1.0, commission=0.5)
  clones = clone_cached_trades([src])
  c = clones[0]
  c.net_profit = 999.0
  c.quantity = 7.0
  c.commission = 3.0
  # Source is untouched — cache stays pristine.
  assert src.net_profit == 100.0
  assert src.quantity == 1.0
  assert src.commission == 0.5


def test_two_clones_are_independent_of_each_other():
  src = _fake_trade(net_profit=100.0)
  a, b = clone_cached_trades([src, src])  # same source twice
  a.net_profit = 1.0
  b.net_profit = 2.0
  assert a.net_profit == 1.0
  assert b.net_profit == 2.0
  assert src.net_profit == 100.0


def test_snapshot_is_not_orm_object():
  """Regression: make sure we don't accidentally carry SQLAlchemy state
  that would re-introduce deepcopy's cost.
  """
  c = clone_cached_trades([_fake_trade()])[0]
  assert not hasattr(c, "_sa_instance_state"), (
    "CS-018: snapshot should not carry SQLAlchemy instance state"
  )
  # __slots__ → no __dict__
  assert not hasattr(c, "__dict__"), (
    "CS-018: snapshot should use __slots__ for flat memory layout"
  )


def test_clone_list_independence():
  trades = [_fake_trade(trade_num=i) for i in range(5)]
  out = clone_cached_trades(trades)
  assert len(out) == 5
  # Different list identity
  assert out is not trades
  # Each element is a fresh snapshot
  for i, (o, t) in enumerate(zip(out, trades)):
    assert o is not t
    assert o.trade_num == i


def test_cache_source_survives_multiple_clone_rounds():
  """The whole point of the clone: downstream mutates, cache survives."""
  src = _fake_trade(net_profit=100.0, quantity=1.0)
  # Round 1: scaling mutates the clone
  c1 = clone_cached_trades([src])[0]
  c1.net_profit *= 2.0
  c1.quantity *= 2.0
  # Round 2: fresh clone — should see ORIGINAL values, not Round-1 mutations
  c2 = clone_cached_trades([src])[0]
  assert c2.net_profit == 100.0, (
    "CS-018 regression: cache got contaminated — second clone sees the "
    "first clone's mutations. This is exactly the double-scaling bug."
  )
  assert c2.quantity == 1.0
