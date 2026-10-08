"""CS-020: ``BybitPrivateClient.get_positions`` used an exact ``size == 0``
float comparison to skip closed positions. Bybit V5 returns ``size`` as a
string; after ``_safe_float`` parsing, any subnormal residue (e.g. ``1e-12``)
would NOT equal zero and would be surfaced as an "open" position. A
reduce-only order sized to that residue then fails with a lot-size error
on the next tick.

Fixed by comparing ``abs(size) < 1e-9`` — well below any Bybit min_qty
(which is ~1e-5 for USDT perps), so real dust-free positions still flow
through.

These tests run ``get_positions`` against a stubbed HTTP layer (no real
network) and exercise three cases:
  1. Non-zero size   → surfaced
  2. Exact "0"       → dropped (old behavior preserved)
  3. Subnormal 1e-12 → dropped (new behavior; old code would surface)
"""
from __future__ import annotations

import asyncio

import pytest

from backend.exchange.bybit_private import BybitPrivateClient


class _StubClient(BybitPrivateClient):
  """Subclass that bypasses HTTP — hands a canned response to ``_get``."""

  def __init__(self, canned: dict):
    super().__init__(api_key="k", api_secret="s", testnet=True)
    self._canned = canned

  async def _get(self, path, params):  # type: ignore[override]
    return self._canned


def _run(coro):
  return asyncio.get_event_loop().run_until_complete(coro) \
    if asyncio.get_event_loop().is_running() is False \
    else asyncio.new_event_loop().run_until_complete(coro)


def test_get_positions_surfaces_real_open_position():
  client = _StubClient({
    "list": [{
      "symbol": "BTCUSDT", "side": "Buy", "size": "0.001",
      "avgPrice": "50000", "markPrice": "50100",
      "unrealisedPnl": "0.1", "leverage": "10", "liqPrice": "45000",
    }],
  })
  out = asyncio.run(client.get_positions())
  assert len(out) == 1
  assert out[0]["symbol"] == "BTCUSDT"
  assert out[0]["size"] == pytest.approx(0.001)


def test_get_positions_drops_exact_zero_size():
  """Old behavior must be preserved for the exact "0" case."""
  client = _StubClient({
    "list": [{
      "symbol": "BTCUSDT", "side": "Buy", "size": "0",
      "avgPrice": "0", "markPrice": "50000",
      "unrealisedPnl": "0", "leverage": "10", "liqPrice": "0",
    }],
  })
  out = asyncio.run(client.get_positions())
  assert out == []


def test_get_positions_drops_subnormal_dust():
  """CS-020 fix: a 1e-12 residue must NOT be surfaced.
  Old ``size == 0`` would pass this through as open; new epsilon drops it.
  """
  client = _StubClient({
    "list": [{
      "symbol": "BTCUSDT", "side": "Buy", "size": "1e-12",
      "avgPrice": "50000", "markPrice": "50000",
      "unrealisedPnl": "0", "leverage": "10", "liqPrice": "0",
    }],
  })
  out = asyncio.run(client.get_positions())
  assert out == []


def test_get_positions_surfaces_just_above_epsilon():
  """A size of 1e-8 (still tiny but 10x the epsilon) must surface — so
  the epsilon never eats a real (if tiny) lot on a high-precision pair.
  """
  client = _StubClient({
    "list": [{
      "symbol": "BTCUSDT", "side": "Buy", "size": "1e-8",
      "avgPrice": "50000", "markPrice": "50000",
      "unrealisedPnl": "0", "leverage": "10", "liqPrice": "0",
    }],
  })
  out = asyncio.run(client.get_positions())
  assert len(out) == 1
  assert out[0]["size"] == pytest.approx(1e-8)


def test_get_positions_drops_empty_list():
  client = _StubClient({"list": []})
  out = asyncio.run(client.get_positions())
  assert out == []


def test_get_positions_drops_missing_size_field():
  """Missing ``size`` → ``_safe_float(None) == 0.0`` → dropped."""
  client = _StubClient({
    "list": [{"symbol": "BTCUSDT", "side": "Buy"}],
  })
  out = asyncio.run(client.get_positions())
  assert out == []
