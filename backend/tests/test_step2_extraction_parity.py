"""Parity test for the Step-2 service extraction (F1).

Verifies that `backend.analysis.step2_service.run_step2` produces output
that matches the contract of the old `_run_hybrid_step2` closure in
`backend/api/routes/wfo.py`.

Strategy:
  1. Build a tiny synthetic step1_result (2 OOS windows, one batch) plus
     a handful of trades.
  2. Call run_step2 twice with the same inputs — result must be identical
     on every deterministic field (the function is pure given its args).
  3. Assert the full public return-shape: every key the closure returned
     must be present with the expected type.
  4. Assert invariants the old closure was known to enforce:
       - summary.total_trades == len(selected_pnls)
       - sum(selected_pnls) ≈ summary.net_profit (within rounding)
       - asset_frequency.pct is present for every asset
       - config_used echoes the inputs we passed
  5. hybrid_mode=False short-circuits to None.

We deliberately do NOT compare against a live DB run — the synthetic
fixture is the single source of truth for the parity contract.
"""
from __future__ import annotations

from collections import namedtuple
from datetime import datetime

from backend.analysis.step2_service import run_step2

LightTrade = namedtuple("LightTrade", [
  "run_id", "symbol", "side", "net_profit", "entry_time", "exit_time", "timeframe",
])


def _build_fixture():
  """Minimal deterministic step1 result + trades covering 2 OOS windows."""
  # One batch id = 42, one run_id = 100 mapping to it.
  run_batch_map = {100: 42}

  # Trades span 2024-01 through 2024-04. Two OOS windows: Jan-Feb, Mar-Apr.
  trades = [
    LightTrade(100, "BTCUSDT", "long",  120.0, datetime(2024, 1, 5, 10), datetime(2024, 1, 5, 14), "30"),
    LightTrade(100, "BTCUSDT", "long",  -50.0, datetime(2024, 1, 12, 10), datetime(2024, 1, 12, 14), "30"),
    LightTrade(100, "BTCUSDT", "long",   80.0, datetime(2024, 1, 20, 10), datetime(2024, 1, 20, 14), "30"),
    LightTrade(100, "BTCUSDT", "long",  -30.0, datetime(2024, 2, 3, 10),  datetime(2024, 2, 3, 14),  "30"),
    LightTrade(100, "BTCUSDT", "long",  200.0, datetime(2024, 2, 14, 10), datetime(2024, 2, 14, 14), "30"),
    LightTrade(100, "BTCUSDT", "long",  -80.0, datetime(2024, 2, 25, 10), datetime(2024, 2, 25, 14), "30"),
    LightTrade(100, "BTCUSDT", "long",  100.0, datetime(2024, 3, 6, 10),  datetime(2024, 3, 6, 14),  "30"),
    LightTrade(100, "BTCUSDT", "long",  -40.0, datetime(2024, 3, 15, 10), datetime(2024, 3, 15, 14), "30"),
    LightTrade(100, "BTCUSDT", "long",  150.0, datetime(2024, 3, 22, 10), datetime(2024, 3, 22, 14), "30"),
    LightTrade(100, "BTCUSDT", "long",  -20.0, datetime(2024, 4, 4, 10),  datetime(2024, 4, 4, 14),  "30"),
    LightTrade(100, "BTCUSDT", "long",   60.0, datetime(2024, 4, 12, 10), datetime(2024, 4, 12, 14), "30"),
    LightTrade(100, "BTCUSDT", "long",   90.0, datetime(2024, 4, 20, 10), datetime(2024, 4, 20, 14), "30"),
  ]

  step1 = {
    "windows": [
      {
        "window_num": 1,
        "is_start":  "2023-11-01",
        "oos_start": "2024-01-01",
        "oos_end":   "2024-02-28",
        "candidates": [{"selected_batches": [42]}],
      },
      {
        "window_num": 2,
        "is_start":  "2024-01-01",
        "oos_start": "2024-03-01",
        "oos_end":   "2024-04-30",
        "candidates": [{"selected_batches": [42]}],
      },
    ],
  }

  config = {
    "hybrid_mode": True,
    "hybrid_n_trades": 3,
    "hybrid_n_assets": 1,
    "hybrid_min_pf": 0.5,
    "hybrid_min_wr": 0.0,
    "hybrid_staleness_days": 365,
    "hybrid_rebalance_every": 1,
    "hybrid_rebalance_mode": "fixed",
    "hybrid_warmup_trades": 0,
    "hybrid_min_hold": 0,
    "hybrid_scoring_method": "composite",
    "hybrid_objective": "net_profit",
    "hybrid_composite_weights": {},
    "hybrid_exec_day_profit": False,
    "hybrid_sample_count_floor": 0,
    "hybrid_rescue_enabled": False,
    "hybrid_rescue_quota_pct": 0.0,
    "hybrid_rescue_min_trades": 30,
    "hybrid_rescue_min_score": 0.0,
    "hybrid_rescue_confidence_gap": 0.0,
    "session_filter_enabled": False,
    "session_filter_mode": "exclude",
    "session_filter_exclude_hours": [],
    "session_filter_exclude_days": [],
    "category_filter_enabled": False,
    "category_influence": 0.0,
    "category_lookback_trades": 20,
    "category_max_per_cat": 0,
    "category_score_metric": "wr",
    "category_mode": "soft_weight",
    "category_streak_required": 3,
    "category_fire_multiplier": 1.0,
    "category_fire_duration_trades": 10,
    "oos_months": 2,
    "preset_id": None,
  }

  return step1, trades, run_batch_map, config


def test_hybrid_mode_off_returns_none():
  step1, trades, rbm, config = _build_fixture()
  config["hybrid_mode"] = False
  assert run_step2(step1, trades, rbm, config, db=None) is None


def test_no_oos_windows_returns_none():
  step1, trades, rbm, config = _build_fixture()
  step1["windows"] = [{"window_num": 1, "is_start": "2023-11-01"}]  # no oos_start
  assert run_step2(step1, trades, rbm, config, db=None) is None


def test_return_shape_contract():
  """Every key the old closure returned must still be present."""
  step1, trades, rbm, config = _build_fixture()
  out = run_step2(step1, trades, rbm, config, db=None)
  assert out is not None

  required_keys = {
    "mode", "walk_forward",
    "windows_processed", "windows_total",
    "equity_curve", "equity_dates",
    "all_trade_count", "selected_trade_count",
    "warmup_trades", "rebalance_count",
    "asset_frequency", "selection_changes",
    "summary", "per_window",
    "winning_batches", "step2_config",
    "aggregate_equity_curve", "aggregate_monthly_matrix",
    "aggregate_breakdown", "cumulative_metrics",
    "selected_records", "config_used",
  }
  missing = required_keys - set(out.keys())
  assert not missing, f"run_step2 dropped keys: {missing}"

  assert out["mode"] == "event"
  assert out["walk_forward"] is True
  assert out["windows_total"] == 2
  assert 42 in out["winning_batches"]


def test_determinism():
  """Two back-to-back calls with identical inputs must match bit-for-bit."""
  step1, trades, rbm, config = _build_fixture()
  a = run_step2(step1, trades, rbm, config, db=None)
  b = run_step2(step1, trades, rbm, config, db=None)
  assert a == b, "run_step2 is not deterministic for identical inputs"


def test_summary_invariants():
  """
  The closure enforced:
    - summary.total_trades == len(selected_pnls)
    - summary.net_profit ≈ sum(selected_pnls)
    - asset_frequency has a pct entry for every asset
    - config_used echoes inputs we passed
  """
  step1, trades, rbm, config = _build_fixture()
  out = run_step2(step1, trades, rbm, config, db=None)
  assert out is not None

  pnls = out["selected_pnls"] if "selected_pnls" in out else None
  # The closure doesn't expose `selected_pnls` directly in the return;
  # selected_records carries the per-trade pnls.
  records = out["selected_records"]
  net_from_records = sum(float(r.get("pnl", 0) or 0) for r in records)

  assert out["summary"]["total_trades"] == len(records), (
    "total_trades must equal len(selected_records) after dedup"
  )
  assert abs(out["summary"]["net_profit"] - net_from_records) < 1e-6, (
    f"summary.net_profit ({out['summary']['net_profit']}) != "
    f"sum(selected_records.pnl) ({net_from_records})"
  )

  for asset, freq in out["asset_frequency"].items():
    assert "pct" in freq, f"asset_frequency[{asset}] missing pct"
    assert "selected" in freq and "eligible" in freq

  cfg_used = out["config_used"]
  assert cfg_used["n_trades"] == config["hybrid_n_trades"]
  assert cfg_used["n_assets"] == config["hybrid_n_assets"]
  assert cfg_used["rebalance_mode"] == config["hybrid_rebalance_mode"]


def test_build_config_from_request_roundtrip():
  """Helper must project all known keys off a request-like object."""
  from backend.analysis.step2_service import build_config_from_request, _CONFIG_KEYS

  class _Req:
    pass

  r = _Req()
  for k in _CONFIG_KEYS:
    setattr(r, k, f"val_{k}")

  cfg = build_config_from_request(r)
  for k in _CONFIG_KEYS:
    assert cfg[k] == f"val_{k}", f"build_config_from_request lost key {k}"
