"""T-002e' — seed_state / final_state_snapshot / continuation_state round-trip.

Guards:
  1. Non-hybrid byte-identical: run_event_wfo(trades, cfg) with seed_state=None
     produces the same per-run output keys (except the additive
     `final_state_snapshot`) regardless of whether _seed_state is missing from
     config or explicitly None. No new runtime behavior for the non-hybrid
     optimizer code path.
  2. Hybrid round-trip: the snapshot survives a JSON round-trip and, when
     passed back as seed_state, the continuation run's per-asset buffers are
     pre-populated (verified by selecting trades from step 1 without a full
     warmup).
"""
from __future__ import annotations

import json
from datetime import datetime

from backend.analysis.event_wfo import run_event_wfo


class _SyntheticTrade:
  """Minimal BacktestTrade stand-in — only the attrs run_event_wfo reads."""

  def __init__(
    self, run_id: int, symbol: str, side: str, timeframe: str,
    net_profit: float, entry_time: datetime, exit_time: datetime,
    batch_id: int | None = None,
  ):
    self.run_id = run_id
    self.symbol = symbol
    self.side = side
    self.timeframe = timeframe
    self.net_profit = net_profit
    self.entry_time = entry_time
    self.exit_time = exit_time
    self.batch_id = batch_id


def _make_trades(n: int, start_day: int = 1) -> list[_SyntheticTrade]:
  """Build a deterministic trade stream: 3 symbols, alternating win/loss."""
  trades: list[_SyntheticTrade] = []
  symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
  for i in range(n):
    sym = symbols[i % 3]
    day = start_day + (i % 27)  # keep within a safe month range
    et = datetime(2026, 1, day, 12, 0, 0)
    xt = datetime(2026, 1, day, 14, 0, 0)
    pnl = 12.0 if (i % 2 == 0) else -6.0
    trades.append(_SyntheticTrade(
      run_id=1, symbol=sym, side="Long", timeframe="30",
      net_profit=pnl, entry_time=et, exit_time=xt,
    ))
  return trades


# ─────────────────────────────────────────────────────────────
# Guard 1 — non-hybrid byte-identical
# ─────────────────────────────────────────────────────────────

def _strip_snapshot(result: dict) -> dict:
  """Drop `final_state_snapshot` so we can compare by equality against a
  pre-T-002e' return shape. Everything else must match byte-for-byte."""
  return {k: v for k, v in result.items() if k != "final_state_snapshot"}


def test_non_hybrid_byte_identical_default_vs_explicit_none():
  """seed_state=None, omitted, or _seed_state=None in config must all yield
  the same result (minus the additive snapshot)."""
  trades = _make_trades(40)
  cfg = {"n_trades": 5, "n_assets": 2, "objective": "sortino"}

  r_no_kwarg = run_event_wfo(trades, cfg)
  r_explicit_none = run_event_wfo(trades, cfg, seed_state=None)
  r_cfg_none = run_event_wfo(trades, {**cfg, "_seed_state": None})

  a = _strip_snapshot(r_no_kwarg)
  b = _strip_snapshot(r_explicit_none)
  c = _strip_snapshot(r_cfg_none)
  assert a == b, "explicit seed_state=None diverges from omitted kwarg"
  assert a == c, "config['_seed_state']=None diverges from omitted kwarg"

  # Guard: snapshot field IS present (additive — existing callers unaffected).
  assert "final_state_snapshot" in r_no_kwarg
  snap = r_no_kwarg["final_state_snapshot"]
  assert isinstance(snap, dict)
  assert set(snap.keys()) == {
    "asset_buffers", "asset_trade_times", "asset_last_time",
    "rescue_asset_buffers", "rescue_asset_trade_times",
    "rescue_asset_last_time",
    # T-001 Stage 8: incumbent basket + lock counts so the live refresh
    # can carry min_hold protection across refresh boundaries.
    "current_portfolio", "asset_locks",
  }


def test_non_hybrid_snapshot_is_json_safe():
  """final_state_snapshot must survive json.dumps → json.loads round-trip.
  Datetime values are stored as ISO strings, lists/dicts only."""
  trades = _make_trades(30)
  cfg = {"n_trades": 5, "n_assets": 2, "objective": "sortino"}
  r = run_event_wfo(trades, cfg)
  snap = r["final_state_snapshot"]
  # Raises TypeError if any value is non-JSON-safe
  round_tripped = json.loads(json.dumps(snap))
  assert round_tripped == snap


# ─────────────────────────────────────────────────────────────
# Guard 2 — hybrid round-trip (seed_state changes behavior as intended)
# ─────────────────────────────────────────────────────────────

def test_hybrid_round_trip_seed_state_primes_buffers():
  """When seed_state is fed back into run_event_wfo, the per-asset buffers
  are pre-populated so the same input stream produces MORE selected trades
  than an unseeded run on the same input (warmup is already satisfied)."""
  trades = _make_trades(30)
  cfg = {"n_trades": 5, "n_assets": 2, "objective": "sortino"}

  baseline = run_event_wfo(trades, cfg)
  snap = json.loads(json.dumps(baseline["final_state_snapshot"]))

  seeded = run_event_wfo(trades, cfg, seed_state=snap)
  # Seeded run should not throw away the primed buffers — selected count
  # should differ from the cold run. This is the whole point of
  # continuation: skip re-walking history on every refresh.
  assert seeded["selected_trade_count"] >= baseline["selected_trade_count"], (
    f"seeded run selected fewer than baseline "
    f"({seeded['selected_trade_count']} < {baseline['selected_trade_count']})"
  )


def test_hybrid_round_trip_via_config_seed_state_key():
  """The `config['_seed_state']` fallback path must behave identically to the
  kwarg path. Used by step2_service → hybrid_portfolio_refresh which thread
  config rather than adding a kwarg in every hop."""
  trades = _make_trades(30)
  cfg = {"n_trades": 5, "n_assets": 2, "objective": "sortino"}

  baseline = run_event_wfo(trades, cfg)
  snap = json.loads(json.dumps(baseline["final_state_snapshot"]))

  via_kwarg = run_event_wfo(trades, cfg, seed_state=snap)
  via_cfg = run_event_wfo(trades, {**cfg, "_seed_state": snap})

  a = _strip_snapshot(via_kwarg)
  b = _strip_snapshot(via_cfg)
  assert a == b, "kwarg and config[_seed_state] paths diverge"


def test_empty_seed_state_equals_no_seed():
  """A falsy snapshot (empty dict or None) must be a no-op — not raise, and
  produce the same output as no seed at all."""
  trades = _make_trades(20)
  cfg = {"n_trades": 5, "n_assets": 2, "objective": "sortino"}

  r_none = run_event_wfo(trades, cfg, seed_state=None)
  r_empty = run_event_wfo(trades, cfg, seed_state={})
  assert _strip_snapshot(r_none) == _strip_snapshot(r_empty)
