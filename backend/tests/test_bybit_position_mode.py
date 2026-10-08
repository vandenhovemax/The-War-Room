"""Bybit position-mode guard.

The engine assumes one-way mode end-to-end:
  - sizing adds to existing (portfolio, symbol, side) positions
  - reconciliation relies on at most one row per (symbol, side)
  - martingale's pyramiding behavior only works when a new same-side
    entry ADDS to the existing position (one-way) rather than creating
    a separate long/short pair (hedge)

`BybitTradingClient.get_position_mode()` detects the current account
mode by probing a symbol's position rows:
  - one entry with positionIdx=0       → one_way
  - two entries with positionIdx=1,2   → hedge
  - nothing / API error                → unknown

`EngineCoordinator.start()` refuses to start the live engine if the
mode is hedge. These tests pin both layers.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.exchange.bybit_trading import BybitTradingClient


# ---------------------------------------------------------------------------
# BybitTradingClient.get_position_mode — three detection paths
# ---------------------------------------------------------------------------

def _make_client_with_session(session) -> BybitTradingClient:
  """Build a BybitTradingClient with its pybit session swapped for a fake.

  The real __init__ would try to hit the Bybit API. Bypass it with
  __new__ and inject the bits get_position_mode actually touches.
  """
  client = BybitTradingClient.__new__(BybitTradingClient)
  client._session = session
  return client


class _FakeSession:
  def __init__(self, list_entries, raise_exc: Exception | None = None):
    self._list = list_entries
    self._raise = raise_exc
    self.calls: list[dict] = []

  def get_positions(self, **kwargs) -> dict:
    self.calls.append(kwargs)
    if self._raise is not None:
      raise self._raise
    return {
      "retCode": 0,
      "retMsg": "OK",
      "result": {"list": self._list},
    }


def test_position_mode_returns_one_way_for_single_idx_zero():
  """Bybit returns one row with positionIdx=0 → one-way confirmed."""
  session = _FakeSession([
    {"symbol": "BTCUSDT", "side": "None", "size": "0", "positionIdx": 0},
  ])
  client = _make_client_with_session(session)
  assert client.get_position_mode() == "one_way"
  # Probed BTCUSDT on linear category
  assert session.calls == [{"category": "linear", "symbol": "BTCUSDT"}]


def test_position_mode_returns_one_way_even_with_open_long():
  """A real open long in one-way mode still shows positionIdx=0."""
  session = _FakeSession([
    {"symbol": "BTCUSDT", "side": "Buy", "size": "0.5", "positionIdx": 0},
  ])
  client = _make_client_with_session(session)
  assert client.get_position_mode() == "one_way"


def test_position_mode_returns_hedge_for_two_rows_idx_1_and_2():
  """Hedge mode returns two rows per symbol (idx 1 long, idx 2 short)."""
  session = _FakeSession([
    {"symbol": "BTCUSDT", "side": "Buy", "size": "0", "positionIdx": 1},
    {"symbol": "BTCUSDT", "side": "Sell", "size": "0", "positionIdx": 2},
  ])
  client = _make_client_with_session(session)
  assert client.get_position_mode() == "hedge"


def test_position_mode_returns_hedge_if_any_row_is_non_zero_idx():
  """A single row with idx=1 (long-only hedge snapshot) still indicates hedge."""
  session = _FakeSession([
    {"symbol": "BTCUSDT", "side": "Buy", "size": "0.5", "positionIdx": 1},
  ])
  client = _make_client_with_session(session)
  assert client.get_position_mode() == "hedge"


def test_position_mode_returns_unknown_when_api_fails():
  """Network/API failure → 'unknown' (coordinator's call how to handle)."""
  session = _FakeSession([], raise_exc=RuntimeError("connection reset"))
  client = _make_client_with_session(session)
  assert client.get_position_mode() == "unknown"


def test_position_mode_returns_unknown_when_empty_list():
  """Empty list from Bybit → cannot classify → 'unknown'."""
  session = _FakeSession([])
  client = _make_client_with_session(session)
  assert client.get_position_mode() == "unknown"


def test_position_mode_accepts_custom_probe_symbol():
  """Caller can override the probe symbol (defaults to BTCUSDT)."""
  session = _FakeSession([
    {"symbol": "ETHUSDT", "side": "None", "size": "0", "positionIdx": 0},
  ])
  client = _make_client_with_session(session)
  assert client.get_position_mode(symbol="ETHUSDT") == "one_way"
  assert session.calls == [{"category": "linear", "symbol": "ETHUSDT"}]


# ---------------------------------------------------------------------------
# EngineCoordinator.start — refuses to start on hedge mode
# ---------------------------------------------------------------------------

def test_engine_refuses_to_start_in_hedge_mode(monkeypatch):
  """A hedge-mode detection at startup must short-circuit start() to False
  before any trading infrastructure is wired up."""
  import asyncio

  from backend.exchange.engine_coordinator import EngineCoordinator

  # Fake client that claims hedge mode
  class _HedgeClient:
    def test_connection(self) -> bool:
      return True

    def get_position_mode(self, symbol: str = "BTCUSDT") -> str:
      return "hedge"

  # Build coordinator with a minimal settings namespace — paper_trading=False
  # so the live-mode branch runs. live_engine_enabled=True to pass the gate.
  settings = SimpleNamespace(
    paper_trading=False,
    live_engine_enabled=True,
    bybit_api_key="k", bybit_api_secret="s",
    bybit_testnet=True, testnet_use_mainnet_prices=False,
  )

  coord = EngineCoordinator.__new__(EngineCoordinator)
  coord._settings = settings
  coord._running = False
  coord._trading_client = None
  coord._position_manager = None
  coord._order_manager = None
  coord._ws_manager = None
  coord._candle_aggregator = None
  coord._strategy_executor = None
  coord._safety_monitor = None
  coord._tsl_tracker = None
  coord._paper_simulator = None
  coord._reconciliation_task = None
  coord._fill_poll_task = None
  coord._wallet_refresh_task = None
  coord._kelly_recompute_task = None
  coord._open_position_keys = set()
  coord._in_flight_entries = {}
  coord._loop = None

  # Intercept the BybitTradingClient construction inside start()
  def _fake_ctor(*args, **kwargs):
    return _HedgeClient()

  monkeypatch.setattr(
    "backend.exchange.engine_coordinator.BybitTradingClient",
    _fake_ctor,
  )

  ok = asyncio.run(coord.start())
  assert ok is False, "Engine must refuse to start when Bybit is in hedge mode"
  assert coord._running is False


def test_engine_continues_on_unknown_mode(monkeypatch, caplog):
  """'unknown' mode (probe failed / empty list) should NOT block startup —
  only a confirmed 'hedge' should. Warning-only for unknown."""
  import asyncio
  import logging

  from backend.exchange.engine_coordinator import EngineCoordinator

  class _UnknownClient:
    def test_connection(self) -> bool:
      return True

    def get_position_mode(self, symbol: str = "BTCUSDT") -> str:
      return "unknown"

  # Stop start() right after the mode check by forcing an exception on
  # the next step (PositionManager construction). We only care that the
  # mode check itself didn't short-circuit to False.
  settings = SimpleNamespace(
    paper_trading=False,
    live_engine_enabled=True,
    bybit_api_key="k", bybit_api_secret="s",
    bybit_testnet=True, testnet_use_mainnet_prices=False,
  )

  coord = EngineCoordinator.__new__(EngineCoordinator)
  coord._settings = settings
  coord._running = False
  coord._trading_client = None
  coord._position_manager = None
  coord._order_manager = None
  coord._ws_manager = None
  coord._candle_aggregator = None
  coord._strategy_executor = None
  coord._safety_monitor = None
  coord._tsl_tracker = None
  coord._paper_simulator = None
  coord._reconciliation_task = None
  coord._fill_poll_task = None
  coord._wallet_refresh_task = None
  coord._kelly_recompute_task = None
  coord._open_position_keys = set()
  coord._in_flight_entries = {}
  coord._loop = None

  monkeypatch.setattr(
    "backend.exchange.engine_coordinator.BybitTradingClient",
    lambda *a, **kw: _UnknownClient(),
  )

  # Sentinel exception — if raised, we know start() passed the mode gate
  sentinel = RuntimeError("sentinel-passed-mode-gate")

  def _boom(*a, **kw):
    raise sentinel

  monkeypatch.setattr(
    "backend.exchange.engine_coordinator.PositionManager",
    _boom,
  )

  # When mode is 'unknown' the mode gate must take the warning branch
  # (not the hedge branch that returns False immediately). Proof: the
  # warning log fires AND PositionManager is reached (raises sentinel).
  caplog.set_level(logging.WARNING, logger="backend.exchange.engine_coordinator")
  ok = asyncio.run(coord.start())
  assert ok is False  # sentinel aborted later; mode gate did NOT abort
  assert any(
    "Could not verify Bybit position mode" in rec.message
    for rec in caplog.records
  ), "Unknown-mode warning should have been logged"
  assert any(
    "sentinel-passed-mode-gate" in rec.message
    for rec in caplog.records
  ), "PositionManager sentinel should have been reached (proves mode gate did not short-circuit)"
