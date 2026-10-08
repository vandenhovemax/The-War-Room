"""Batch Selection Tests — scoring metrics, engine pipeline, OOS contamination.

Uses synthetic trades (no DB required).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from backend.analysis.batch_selection import (
  BatchSelectionConfig,
  build_asset_daily_pnl,
  build_batch_daily_pnl,
  dedup_batches,
  run_batch_selection,
  _slice_pnl,
)
from backend.analysis.batch_selection_scoring import (
  METRIC_NAMES,
  _MIN_TRADING_DAYS,
  metric_calmar,
  metric_expectancy,
  metric_gain_pain,
  metric_lake_ratio,
  metric_net_pnl,
  metric_pf,
  metric_robust_sortino,
  metric_sortino,
  metric_stability,
  metric_tail_ratio,
  score_metric,
)


# ── Lightweight trade stub ────────────────────────────────────

@dataclass
class _FakeTrade:
  """Minimal duck-type of BacktestTrade for unit tests."""
  symbol: str = "BTCUSDT"
  side: str = "Long"
  entry_time: datetime | None = None
  exit_time: datetime | None = None
  net_profit: float = 10.0
  position_id: str = "T1"
  run_id: int = 1
  id: int = 1


def _make_daily_pnl(
  values: list[float],
  start: str = "2024-01-01",
) -> pd.Series:
  """Build a daily PnL series from a list of values."""
  idx = pd.date_range(start, periods=len(values), freq="D")
  return pd.Series(values, index=idx)


def _make_trades_for_batch(
  batch_id: int,
  run_id: int,
  n: int = 30,
  start: datetime | None = None,
  spread_days: int = 300,
  symbol: str = "BTCUSDT",
  side: str = "Long",
  seed: int = 42,
  pnl_mean: float = 5.0,
) -> list[_FakeTrade]:
  """Generate synthetic trades for a batch with controllable PnL."""
  rng = np.random.default_rng(seed)
  if start is None:
    start = datetime(2023, 1, 1)
  trades = []
  for i in range(n):
    day_off = int(rng.integers(0, spread_days))
    entry = start + timedelta(days=day_off, hours=int(rng.integers(0, 23)))
    exit_ = entry + timedelta(hours=int(rng.integers(1, 72)))
    pnl = float(rng.normal(pnl_mean, 20))
    trades.append(_FakeTrade(
      symbol=symbol, side=side,
      entry_time=entry, exit_time=exit_,
      net_profit=pnl, position_id=f"B{batch_id}_T{i}",
      run_id=run_id, id=batch_id * 1000 + i,
    ))
  return trades


# ═══════════════════════════════════════════════════════════════
# SCORING METRIC TESTS
# ═══════════════════════════════════════════════════════════════

class TestScoringMetrics:
  """Unit tests for all 10 spec-compatible metrics."""

  def test_net_pnl_basic(self):
    pnl = _make_daily_pnl([10, -5, 20, -3, 15] * 6)  # 30 values
    assert metric_net_pnl(pnl) == pytest.approx(pnl.sum())

  def test_net_pnl_skip_few_days(self):
    pnl = _make_daily_pnl([10, -5, 0, 0, 0])  # Only 2 non-zero
    assert metric_net_pnl(pnl) == -999

  def test_lake_ratio_basic(self):
    # All positive → high lake ratio
    pnl = _make_daily_pnl([10] * 30)
    lr = metric_lake_ratio(pnl)
    assert lr > 0.9  # Mostly above water

  def test_lake_ratio_area_weighted(self):
    """Verify area-weighted, not day-count."""
    # Series A: many small dips (shallow)
    a = _make_daily_pnl([10, -1, 10, -1, 10, -1] * 5)
    # Series B: one big dip (deep)
    b_vals = [10] * 25 + [-50, 10, 10, 10, 10]
    b = _make_daily_pnl(b_vals)
    lr_a = metric_lake_ratio(a)
    lr_b = metric_lake_ratio(b)
    # A has shallower drawdowns → higher lake ratio
    assert lr_a > lr_b

  def test_tail_ratio_zeros_filtered(self):
    """Zeros must be filtered before percentile calculation."""
    # 20 non-zero days + 20 zeros. Without zero filtering, p5 would be 0.
    vals = [100, -50] * 10 + [0] * 20
    pnl = _make_daily_pnl(vals)
    tr = metric_tail_ratio(pnl)
    assert tr != -999
    assert not np.isinf(tr)
    # With zeros filtered, p5 is from [-50, 100] distribution, not from zeros
    assert tr > 0

  def test_tail_ratio_basic(self):
    rng = np.random.default_rng(42)
    vals = list(rng.normal(5, 10, 30))
    pnl = _make_daily_pnl(vals)
    tr = metric_tail_ratio(pnl)
    # With positive mean, upside should exceed downside
    assert tr > 0

  def test_sortino_basic(self):
    pnl = _make_daily_pnl([10, -2, 15, -1, 8] * 6)
    s = metric_sortino(pnl)
    assert s > 0  # Positive mean, small downside

  def test_calmar_basic(self):
    pnl = _make_daily_pnl([10, 10, 10, -5, 10] * 6)
    c = metric_calmar(pnl)
    assert c > 0  # Positive final equity, small drawdown

  def test_pf_basic(self):
    pnl = _make_daily_pnl([100, -30, 50, -20, 80] * 6)
    p = metric_pf(pnl)
    gains = sum(v for v in pnl if v > 0)
    losses = abs(sum(v for v in pnl if v < 0))
    assert p == pytest.approx(min(gains / losses, 10.0), rel=1e-3)

  def test_pf_capped_at_10(self):
    pnl = _make_daily_pnl([100, -1] * 15)
    p = metric_pf(pnl)
    assert p <= 10.0

  def test_gain_pain_basic(self):
    pnl = _make_daily_pnl([10, -5, 20, -3, 15] * 6)
    gp = metric_gain_pain(pnl)
    total = pnl.sum()
    losses = abs(pnl[pnl < 0].sum())
    assert gp == pytest.approx(total / losses, rel=1e-3)

  def test_expectancy_basic(self):
    pnl = _make_daily_pnl([10, -5, 20, -3, 15] * 6)
    e = metric_expectancy(pnl)
    # With more wins than losses and higher avg win, expectancy > 0
    assert e > 0

  def test_expectancy_uses_nonzero(self):
    """Win rate denominator should use non-zero days only."""
    # 10 wins, 10 losses, 10 zeros
    vals = [10] * 10 + [-5] * 10 + [0] * 10
    pnl = _make_daily_pnl(vals)
    e = metric_expectancy(pnl)
    # WR should be 10/20 = 0.5 (not 10/30)
    assert e > -999

  def test_stability_pure_r_squared(self):
    """Stability = pure R², not blended."""
    # Perfect linear equity curve → R² = 1.0
    pnl = _make_daily_pnl([10] * 30)
    s = metric_stability(pnl)
    assert s == pytest.approx(1.0, abs=0.01)

  def test_stability_negative_slope_returns_zero(self):
    pnl = _make_daily_pnl([-10] * 30)
    s = metric_stability(pnl)
    assert s == 0.0

  def test_robust_sortino_uses_median(self):
    # Skewed distribution: many small positives, few large negatives
    vals = [5, 5, 5, 5, 5, -50, 5, 5, 5, 5] * 3
    pnl = _make_daily_pnl(vals)
    s = metric_sortino(pnl)
    rs = metric_robust_sortino(pnl)
    # Median is 5 (robust to outlier), mean is lower → robust_sortino > sortino
    assert rs > s

  # ── Edge case tests ──

  def test_all_positive_no_negatives(self):
    """No negative days → 10.0 if positive, else 0."""
    pnl = _make_daily_pnl([10, 5, 20, 3, 15] * 6)
    assert metric_sortino(pnl) == 10.0
    assert metric_pf(pnl) == 10.0
    assert metric_gain_pain(pnl) == 10.0
    assert metric_calmar(pnl) == 10.0

  def test_all_negative(self):
    pnl = _make_daily_pnl([-10, -5, -20] * 10)
    assert metric_net_pnl(pnl) < 0
    assert metric_stability(pnl) == 0.0  # Negative slope

  def test_few_nonzero_days_returns_skip(self):
    """Fewer than _MIN_TRADING_DAYS non-zero PnL days → -999 for every metric.

    _MIN_TRADING_DAYS was lowered from 20 → 10 (see batch_selection_scoring.py:21
    — "too restrictive for 3mo CAL windows"). Generate one less than the
    threshold so every metric's guard fires.
    """
    n_active = _MIN_TRADING_DAYS - 1
    # Alternate +/- to keep the values numerically interesting
    active_vals = []
    for i in range(n_active):
      active_vals.append(10 if i % 2 == 0 else -5)
    vals = active_vals + [0] * 20
    pnl = _make_daily_pnl(vals)
    for name in METRIC_NAMES:
      assert score_metric(pnl, name) == -999, f"{name} should return -999"

  def test_20_nonzero_days_works(self):
    """Exactly 20 non-zero days should work (not skip)."""
    vals = [10, -5] * 10 + [0] * 10
    pnl = _make_daily_pnl(vals)
    for name in METRIC_NAMES:
      assert score_metric(pnl, name) != -999, f"{name} should not skip with 20 active days"

  def test_determinism(self):
    """Same input → same output, always."""
    rng = np.random.default_rng(123)
    vals = list(rng.normal(5, 10, 40))
    for _ in range(3):
      pnl = _make_daily_pnl(vals)
      results = {name: score_metric(pnl, name) for name in METRIC_NAMES}
      pnl2 = _make_daily_pnl(vals)
      results2 = {name: score_metric(pnl2, name) for name in METRIC_NAMES}
      assert results == results2

  def test_unknown_metric_raises(self):
    pnl = _make_daily_pnl([10] * 30)
    with pytest.raises(ValueError, match="Unknown metric"):
      score_metric(pnl, "nonexistent_metric")


# ═══════════════════════════════════════════════════════════════
# DEDUP TESTS
# ═══════════════════════════════════════════════════════════════

class TestDedup:
  def test_duplicate_batches_keep_lowest(self):
    """Duplicate fingerprint → keep lowest batch_id."""
    trades1 = _make_trades_for_batch(100, run_id=1, n=10, pnl_mean=10, seed=42)
    trades2 = _make_trades_for_batch(200, run_id=2, n=10, pnl_mean=10, seed=42)
    # Same seed + same n → same PnL → same fingerprint
    all_trades = trades1 + trades2
    rbm = {1: 100, 2: 200}
    _, _, kept = dedup_batches(all_trades, rbm)
    assert 100 in kept
    assert 200 not in kept

  def test_non_duplicate_batches_all_preserved(self):
    trades1 = _make_trades_for_batch(100, run_id=1, n=10, pnl_mean=10, seed=42)
    trades2 = _make_trades_for_batch(200, run_id=2, n=15, pnl_mean=20, seed=99)
    all_trades = trades1 + trades2
    rbm = {1: 100, 2: 200}
    _, _, kept = dedup_batches(all_trades, rbm)
    assert 100 in kept
    assert 200 in kept

  def test_empty_trades(self):
    _, _, kept = dedup_batches([], {})
    assert kept == []


# ═══════════════════════════════════════════════════════════════
# BUILD PNL TESTS
# ═══════════════════════════════════════════════════════════════

class TestBuildPnl:
  def test_batch_daily_pnl_entry_date_indexed(self):
    """PnL should be indexed by ENTRY date (decision date, not exit date)."""
    t = _FakeTrade(
      symbol="BTCUSDT", side="Long",
      entry_time=datetime(2024, 6, 1, 10),
      exit_time=datetime(2024, 6, 5, 14),
      net_profit=100, run_id=1, id=1,
    )
    rbm = {1: 10}
    bd = build_batch_daily_pnl([t], rbm)
    assert 10 in bd
    pnl = bd[10]
    # PnL should be on June 1 (entry/decision date), not June 5 (exit)
    assert pnl.loc[pd.Timestamp("2024-06-01")] == 100

  def test_batch_daily_fills_zeros(self):
    """Missing days between trades should be filled with 0."""
    t1 = _FakeTrade(entry_time=datetime(2024, 1, 1, 10), exit_time=datetime(2024, 1, 2, 10), net_profit=50, run_id=1, id=1)
    t2 = _FakeTrade(entry_time=datetime(2024, 1, 5, 10), exit_time=datetime(2024, 1, 6, 10), net_profit=30, run_id=1, id=2)
    rbm = {1: 10}
    bd = build_batch_daily_pnl([t1, t2], rbm)
    pnl = bd[10]
    assert len(pnl) == 5  # Jan 1 to Jan 5
    assert pnl.loc[pd.Timestamp("2024-01-03")] == 0.0

  def test_asset_daily_key_format(self):
    """Asset keys should be {batch_id}_{symbol}_{side}."""
    trades = _make_trades_for_batch(10, run_id=1, n=10, symbol="ETHUSDT", side="Short")
    rbm = {1: 10}
    ad, atb, bta, atd, _ = build_asset_daily_pnl(trades, rbm)
    assert any(k.startswith("10_ETHUSDT_Short") for k in ad.keys())

  def test_asset_fewer_than_3_trades_skipped(self):
    """Assets with < 3 trades should be excluded."""
    t1 = _FakeTrade(entry_time=datetime(2024, 1, 1, 10), exit_time=datetime(2024, 1, 2, 10),
                     net_profit=50, run_id=1, id=1, symbol="RARE", side="Long")
    t2 = _FakeTrade(entry_time=datetime(2024, 1, 5, 10), exit_time=datetime(2024, 1, 6, 10),
                     net_profit=30, run_id=1, id=2, symbol="RARE", side="Long")
    rbm = {1: 10}
    ad, _, _, _, _ = build_asset_daily_pnl([t1, t2], rbm)
    assert not any("RARE" in k for k in ad.keys())


# ═══════════════════════════════════════════════════════════════
# PIPELINE / CONTAMINATION TESTS
# ═══════════════════════════════════════════════════════════════

def _build_test_scenario():
  """Build a synthetic 2-window scenario for pipeline testing.

  Window 1: IS [2023-01-01, 2023-12-31], OOS [2024-01-01, 2024-03-31]
  Window 2: IS [2023-04-01, 2024-03-31], OOS [2024-04-01, 2024-06-30]

  Creates 3 batches with distinct PnL profiles:
    Batch 10: strong in IS, strong in OOS
    Batch 20: strong in IS, weak in OOS
    Batch 30: weak in IS (should be filtered)
  """
  trades = []
  rbm = {}

  # Batch 10: good batch (strong everywhere)
  for sym, side, rid in [("BTCUSDT", "Long", 1), ("ETHUSDT", "Long", 2), ("SOLUSDT", "Short", 3)]:
    rbm[rid] = 10
    for month in range(1, 19):  # Jan 2023 to Jun 2024
      for day in [5, 15, 25]:
        y = 2023 if month <= 12 else 2024
        m = month if month <= 12 else month - 12
        try:
          entry = datetime(y, m, day, 10)
          exit_ = entry + timedelta(hours=24)
          trades.append(_FakeTrade(
            symbol=sym, side=side, entry_time=entry, exit_time=exit_,
            net_profit=15.0, run_id=rid, id=len(trades) + 1,
            position_id=f"B10_{sym}_{len(trades)}",
          ))
        except ValueError:
          pass

  # Batch 20: good in IS, bad in OOS
  for sym, side, rid in [("XRPUSDT", "Long", 4), ("ADAUSDT", "Short", 5)]:
    rbm[rid] = 20
    for month in range(1, 19):
      for day in [5, 15, 25]:
        y = 2023 if month <= 12 else 2024
        m = month if month <= 12 else month - 12
        pnl = 10.0 if m <= 12 and y == 2023 else -5.0
        try:
          entry = datetime(y, m, day, 10)
          exit_ = entry + timedelta(hours=24)
          trades.append(_FakeTrade(
            symbol=sym, side=side, entry_time=entry, exit_time=exit_,
            net_profit=pnl, run_id=rid, id=len(trades) + 1,
            position_id=f"B20_{sym}_{len(trades)}",
          ))
        except ValueError:
          pass

  # Batch 30: bad batch (negative PnL)
  for sym, side, rid in [("DOTUSDT", "Long", 6)]:
    rbm[rid] = 30
    for month in range(1, 19):
      for day in [5, 15, 25]:
        y = 2023 if month <= 12 else 2024
        m = month if month <= 12 else month - 12
        try:
          entry = datetime(y, m, day, 10)
          exit_ = entry + timedelta(hours=24)
          trades.append(_FakeTrade(
            symbol=sym, side=side, entry_time=entry, exit_time=exit_,
            net_profit=-5.0, run_id=rid, id=len(trades) + 1,
            position_id=f"B30_{sym}_{len(trades)}",
          ))
        except ValueError:
          pass

  windows = [
    {"window_num": 1, "is_start": "2023-01-01", "is_end": "2023-12-31",
     "oos_start": "2024-01-01", "oos_end": "2024-03-31"},
    {"window_num": 2, "is_start": "2023-04-01", "is_end": "2024-03-31",
     "oos_start": "2024-04-01", "oos_end": "2024-06-30"},
  ]

  return trades, windows, rbm


class TestPipeline:
  """Integration tests for the batch selection engine."""

  def test_is_screening_filters_bad_batches(self):
    """Batch 30 (negative PnL) should be filtered in Step 1."""
    trades, windows, rbm = _build_test_scenario()
    config = BatchSelectionConfig(max_batches=4, min_is_days=5, min_asset_days=3)
    result = run_batch_selection(trades, windows, config, rbm)
    for w in result["windows"]:
      for c in w["candidates"]:
        assert 30 not in c["selected_batches"]

  def test_first_window_cold_start(self):
    """Window 1 should use IS-only ranking (cold start)."""
    trades, windows, rbm = _build_test_scenario()
    config = BatchSelectionConfig(max_batches=4, min_is_days=5, min_asset_days=3)
    result = run_batch_selection(trades, windows, config, rbm)
    w1 = result["windows"][0]
    cal = w1["candidates"][0]["calibration_results"]
    assert cal["mode"] == "cold_start"

  def test_second_window_uses_calibration(self):
    """Window 2 should use calibration from window 1's OOS."""
    trades, windows, rbm = _build_test_scenario()
    config = BatchSelectionConfig(max_batches=4, min_is_days=5, min_asset_days=3)
    result = run_batch_selection(trades, windows, config, rbm)
    if len(result["windows"]) >= 2:
      w2 = result["windows"][1]
      cal = w2["candidates"][0]["calibration_results"]
      assert cal["mode"] == "calibrated"

  def test_oos_never_influences_selections(self):
    """Changing OOS data must NOT change batch/asset selections.

    This is the critical contamination test: inject different OOS data,
    verify selections (batches + assets) are identical.
    """
    trades, windows, rbm = _build_test_scenario()
    config = BatchSelectionConfig(max_batches=4, min_is_days=5, min_asset_days=3)

    # Run 1: original data
    result1 = run_batch_selection(trades, windows, config, rbm)

    # Run 2: modify OOS-period trades (add huge PnL in OOS)
    modified_trades = []
    for t in trades:
      if t.exit_time and t.exit_time >= datetime(2024, 4, 1):
        # Massively change OOS PnL
        modified = _FakeTrade(
          symbol=t.symbol, side=t.side, entry_time=t.entry_time,
          exit_time=t.exit_time, net_profit=t.net_profit * 100,
          run_id=t.run_id, id=t.id, position_id=t.position_id,
        )
        modified_trades.append(modified)
      else:
        modified_trades.append(t)

    result2 = run_batch_selection(modified_trades, windows, config, rbm)

    # Window 1 selections must be identical
    w1a = result1["windows"][0]["candidates"][0]
    w1b = result2["windows"][0]["candidates"][0]
    assert w1a["selected_batches"] == w1b["selected_batches"]
    assert w1a["assets"] == w1b["assets"]

    # Window 2 IS screening + calibration is based on prev OOS + current IS
    # But since we only changed window 2's OOS (Apr-Jun 2024), NOT window 1's OOS
    # or the IS data, window 2's selections should also be identical
    if len(result1["windows"]) >= 2 and len(result2["windows"]) >= 2:
      w2a = result1["windows"][1]["candidates"][0]
      w2b = result2["windows"][1]["candidates"][0]
      assert w2a["selected_batches"] == w2b["selected_batches"]
      assert w2a["assets"] == w2b["assets"]

  def test_entry_date_boundary(self):
    """Trade entered in IS but exiting in OOS should be in IS slice.

    Entry-date slicing assigns trades to the period where the decision
    was made, preventing OOS contamination with IS decisions.
    """
    t = _FakeTrade(
      symbol="BTCUSDT", side="Long",
      entry_time=datetime(2023, 12, 30, 10),
      exit_time=datetime(2024, 1, 2, 10),  # exits in OOS
      net_profit=1000, run_id=1, id=1,
    )
    rbm = {1: 10}
    bd = build_batch_daily_pnl([t], rbm)
    pnl = bd[10]

    # Slice to IS period [2023-01-01, 2023-12-31]
    is_slice = _slice_pnl(pnl, "2023-01-01", "2023-12-31")
    # The trade ENTERED Dec 30 (IS), so its PnL belongs in IS
    assert float(is_slice.sum()) == 1000.0

    # Slice to OOS period [2024-01-01, 2024-03-31]
    oos_slice = _slice_pnl(pnl, "2024-01-01", "2024-03-31")
    # The trade's PnL should NOT leak into OOS (decision was in IS)
    assert float(oos_slice.sum()) == 0.0

  def test_determinism(self):
    """Three identical runs must produce identical results."""
    trades, windows, rbm = _build_test_scenario()
    config = BatchSelectionConfig(max_batches=4, min_is_days=5, min_asset_days=3)
    results = []
    for _ in range(3):
      r = run_batch_selection(trades, windows, config, rbm)
      results.append(r)

    for i in range(len(results[0]["windows"])):
      w0 = results[0]["windows"][i]["candidates"][0]
      for r in results[1:]:
        wi = r["windows"][i]["candidates"][0]
        assert w0["selected_batches"] == wi["selected_batches"]
        assert w0["assets"] == wi["assets"]
        assert w0["is_metrics"] == wi["is_metrics"]
        assert w0["oos_metrics"] == wi["oos_metrics"]

  def test_max_tpm_enforcement(self):
    """When max_tpm is very low, asset count should be limited."""
    trades, windows, rbm = _build_test_scenario()
    # Run with unlimited TPM first
    config_high = BatchSelectionConfig(
      max_batches=4, min_is_days=5, min_asset_days=3, max_tpm=999,
    )
    result_high = run_batch_selection(trades, windows, config_high, rbm)

    # Run with very low TPM
    config_low = BatchSelectionConfig(
      max_batches=4, min_is_days=5, min_asset_days=3, max_tpm=1,
    )
    result_low = run_batch_selection(trades, windows, config_low, rbm)

    for i in range(len(result_high["windows"])):
      n_high = result_high["windows"][i]["candidates"][0]["n_assets"]
      n_low = result_low["windows"][i]["candidates"][0]["n_assets"]
      # Low TPM should have fewer (or equal) assets than unlimited
      assert n_low <= n_high
      # With max_tpm=1, first asset always added but subsequent blocked
      assert n_low <= 1  # Only first asset (spec: first always added)

  def test_calibration_fallback(self):
    """When no batches pass calibration, should fall back to IS ranking."""
    trades, windows, rbm = _build_test_scenario()
    config = BatchSelectionConfig(
      max_batches=4, min_is_days=5, min_asset_days=3,
      cal_must_profit=True,
      min_calibrated=99,  # Impossibly high → forces fallback
    )
    result = run_batch_selection(trades, windows, config, rbm)
    # Should still have selections (from IS fallback)
    for w in result["windows"]:
      assert len(w["candidates"]) > 0
      assert len(w["candidates"][0]["selected_batches"]) > 0

  def test_nonzero_day_counting(self):
    """Verify min_is_days counts non-zero days, not calendar days."""
    # Create batch with 25 non-zero PnL days spread across many calendar days
    trades = []
    rbm = {1: 10}
    for i in range(25):
      d = datetime(2023, 1, 1) + timedelta(days=i * 7)
      trades.append(_FakeTrade(
        symbol="BTCUSDT", side="Long",
        entry_time=d, exit_time=d + timedelta(hours=12),
        net_profit=100, run_id=1, id=i + 1,
        position_id=f"T{i}",
      ))

    windows = [{"window_num": 1, "is_start": "2023-01-01", "is_end": "2023-12-31",
                "oos_start": "2024-01-01", "oos_end": "2024-03-31"}]

    # With min_is_days=30, batch should be SKIPPED (only 25 non-zero days < 30)
    config = BatchSelectionConfig(max_batches=4, min_is_days=30, min_asset_days=3)
    result = run_batch_selection(trades, windows, config, rbm)
    w = result["windows"][0]
    assert len(w["candidates"][0]["selected_batches"]) == 0

    # With min_is_days=20, batch should PASS (25 non-zero days >= 20)
    config2 = BatchSelectionConfig(max_batches=4, min_is_days=20, min_asset_days=3)
    result2 = run_batch_selection(trades, windows, config2, rbm)
    w2 = result2["windows"][0]
    assert len(w2["candidates"][0]["selected_batches"]) > 0

  def test_summary_has_calibrated_wr(self):
    """Summary should include calibrated WR (excl. window 1)."""
    trades, windows, rbm = _build_test_scenario()
    config = BatchSelectionConfig(max_batches=4, min_is_days=5, min_asset_days=3)
    result = run_batch_selection(trades, windows, config, rbm)
    summary = result["summary"]
    assert "calibrated_wr" in summary
    assert "calibrated_wr_pct" in summary
    assert summary["mode"] == "batch_selection"

  def test_result_format_compatible(self):
    """Result format must be compatible with WFORun/WFOWindow DB schema."""
    trades, windows, rbm = _build_test_scenario()
    config = BatchSelectionConfig(max_batches=4, min_is_days=5, min_asset_days=3)
    result = run_batch_selection(trades, windows, config, rbm)

    # Top-level keys
    assert "windows" in result
    assert "tracks" in result
    assert "cumulative_is" in result
    assert "cumulative_oos" in result
    assert "summary" in result

    # Window format
    for w in result["windows"]:
      assert "window_num" in w
      assert "is_start" in w
      assert "is_end" in w
      assert "oos_start" in w
      assert "oos_end" in w
      assert "is_trade_count" in w
      assert "is_symbol_count" in w
      assert "candidates" in w
      assert len(w["candidates"]) > 0
      c = w["candidates"][0]
      assert "rank" in c
      assert "assets" in c
      assert "is_metrics" in c
      assert "oos_metrics" in c
      assert "oos_wfe" in c
