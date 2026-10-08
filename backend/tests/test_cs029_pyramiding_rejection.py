"""CS-029: Reject portfolios with max_pyramiding > 1 at live activation.

The live engine is 1-position-per-symbol-per-side (see
``_has_open_position``). A portfolio optimized with ``max_pyramiding > 1``
cannot reproduce its backtest numbers live, so activating it silently
produces results the user cannot compare back to the optimizer.

This test pins the rejection path: when the saved ``source_strategies``
blob OR any batch's ``parameters`` blob declares ``max_pyramiding > 1``,
``strategy_executor.activate_portfolio`` must return False with a clear
error log.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.exchange.strategy_executor import StrategyExecutor


def _build_executor() -> StrategyExecutor:
  exec_ = StrategyExecutor.__new__(StrategyExecutor)
  exec_._active = {}
  exec_._db_factory = MagicMock()
  return exec_


def _make_portfolio(pid, source_strategies: dict):
  return SimpleNamespace(
    id=pid,
    source_strategies=json.dumps(source_strategies),
    is_active=True,
  )


def _make_asset(symbol="BTCUSDT", side="Long", batch_id=10):
  return SimpleNamespace(
    symbol=symbol, trade_side=side, batch_id=batch_id, is_active=True,
  )


def _setup_db_mock(portfolio, assets, batches: dict[int, dict] | None = None):
  """Build a db mock that returns a portfolio + active assets + optional batches."""
  from backend.db.models import Portfolio, PortfolioAsset, BatchBacktestRun

  db = MagicMock()

  def get_side_effect(model, pk):
    if model is Portfolio:
      return portfolio
    if model is BatchBacktestRun and batches is not None:
      if pk in batches:
        return SimpleNamespace(
          strategy_id=batches[pk].get("strategy_id", 1),
          parameters=json.dumps(batches[pk].get("parameters", {})),
        )
      return None
    return None

  db.get.side_effect = get_side_effect

  def query_side_effect(model):
    q = MagicMock()
    mname = getattr(model, "__name__", str(model))
    if mname == "PortfolioAsset":
      q.filter.return_value.all.return_value = assets
    else:
      q.filter.return_value.first.return_value = None
    return q

  db.query.side_effect = query_side_effect
  return db


def test_rejects_top_level_max_pyramiding_gt_1(caplog):
  portfolio = _make_portfolio(1, {"max_pyramiding": 3, "batch_ids": []})
  assets = [_make_asset(batch_id=None)]
  db = _setup_db_mock(portfolio, assets)

  exec_ = _build_executor()
  exec_._db_factory = lambda: db

  with caplog.at_level(logging.ERROR, logger="backend.exchange.strategy_executor"):
    result = exec_.activate_portfolio(1)

  assert result is False
  assert any(
    "max_pyramiding" in rec.message and "cannot activate" in rec.message
    for rec in caplog.records
  ), "Expected explicit CS-029 rejection log"


def test_rejects_batch_max_pyramiding_gt_1(caplog):
  portfolio = _make_portfolio(
    2, {"is_cross_strat": True, "batch_ids": [42], "strategy_name": "x"},
  )
  assets = [_make_asset(batch_id=42)]
  batches = {42: {"parameters": {"max_pyramiding": 2, "strategy_inputs": {}}}}
  db = _setup_db_mock(portfolio, assets, batches=batches)

  exec_ = _build_executor()
  exec_._db_factory = lambda: db

  with caplog.at_level(logging.ERROR, logger="backend.exchange.strategy_executor"):
    result = exec_.activate_portfolio(2)

  assert result is False
  # Must name the offending batch id
  logged = " ".join(rec.message for rec in caplog.records)
  assert "42" in logged and "max_pyramiding" in logged


def test_accepts_max_pyramiding_1_and_missing(caplog):
  """max_pyramiding=1 or absent must NOT trigger the CS-029 reject.

  (Activation then hits the strategy-resolution path which returns False for
  any other reason; we assert only that the CS-029 log did not fire.)
  """
  portfolio = _make_portfolio(3, {"max_pyramiding": 1, "batch_ids": []})
  assets = [_make_asset(batch_id=None)]
  db = _setup_db_mock(portfolio, assets)

  exec_ = _build_executor()
  exec_._db_factory = lambda: db

  with caplog.at_level(logging.ERROR, logger="backend.exchange.strategy_executor"):
    exec_.activate_portfolio(3)

  pyramiding_errors = [
    rec for rec in caplog.records if "cannot activate" in rec.message
  ]
  assert not pyramiding_errors, (
    f"max_pyramiding=1 must not trigger CS-029 reject, got: {pyramiding_errors}"
  )


def test_string_max_pyramiding_coerced_to_int():
  """Saved JSON may store the value as a string; the check must still trip."""
  portfolio = _make_portfolio(4, {"max_pyramiding": "4", "batch_ids": []})
  assets = [_make_asset(batch_id=None)]
  db = _setup_db_mock(portfolio, assets)

  exec_ = _build_executor()
  exec_._db_factory = lambda: db

  assert exec_.activate_portfolio(4) is False


def test_offending_message_mentions_the_value():
  portfolio = _make_portfolio(5, {"max_pyramiding": 5, "batch_ids": []})
  assets = [_make_asset(batch_id=None)]
  db = _setup_db_mock(portfolio, assets)

  exec_ = _build_executor()
  exec_._db_factory = lambda: db

  import logging as _lg

  handler_records: list[_lg.LogRecord] = []

  class _Cap(_lg.Handler):
    def emit(self, record):
      handler_records.append(record)

  logger = _lg.getLogger("backend.exchange.strategy_executor")
  h = _Cap(level=_lg.ERROR)
  logger.addHandler(h)
  try:
    exec_.activate_portfolio(5)
  finally:
    logger.removeHandler(h)

  msgs = [r.getMessage() for r in handler_records]
  assert any("max_pyramiding=5" in m for m in msgs), msgs
