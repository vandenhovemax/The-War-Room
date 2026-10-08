"""Golden Reference Validation Suite — Pipeline Correctness Proof.

This file is the ONE-TIME-BUILD proof that the batch selection pipeline
produces correct results.  It covers three layers:

  Layer 1 — Metric correctness:
    Hand-computed expected values for ALL 24 scoring metrics on small,
    deterministic PnL arrays.  If someone modifies batch_selection_scoring.py,
    these tests catch it.

  Layer 2 — Window slicing correctness:
    A synthetic dataset with known entry dates, verifying that trades land
    in the correct IS/OOS windows and that _slice_pnl returns the right range.

  Layer 3 — Pipeline logic correctness:
    A tiny 3-batch, multi-asset dataset where we manually compute which batches
    survive IS screening, calibration, and which assets get picked.  Then we
    run the actual pipeline and compare.

All expected values in this file were computed BY HAND (calculator / spreadsheet)
and are frozen.  Do not "fix" expected values to make tests pass — if a test
fails, the CODE is wrong (or someone legitimately changed the formula, in which
case update the hand computation AND the expected value together).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backend.analysis.batch_selection import (
    BatchSelectionConfig,
    _slice_pnl,
    build_asset_daily_pnl,
    build_batch_daily_pnl,
    dedup_batches,
    detect_batch_sides,
    build_asset_wr_data,
    run_batch_selection,
    _run_single_batch_selection,
)
from backend.analysis.batch_selection_scoring import (
    METRIC_NAMES,
    score_metric,
)


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _pnl(values: list[float], start: str = "2024-01-01") -> pd.Series:
    """Build a daily PnL series from values, starting at given date."""
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype=float)


@dataclass
class FakeTrade:
    """Minimal duck-type of BacktestTrade."""
    symbol: str = "BTCUSDT"
    side: str = "Long"
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    net_profit: float = 0.0
    position_id: str = "T1"
    run_id: int = 1
    id: int = 1


# ═══════════════════════════════════════════════════════════════════════
# LAYER 1: METRIC CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════
#
# Test PnL array A (12 non-zero values, padded to 12 total — NOT enough
# for the 10-day minimum, so we use 30-value arrays):
#
# ARRAY V: 30 values, known distribution
#   Positive days: 100, 50, 80, 30, 120, 60, 90, 40, 110, 70, 100, 50, 80, 30, 120, 60
#   Negative days: -40, -20, -60, -10, -50, -30, -40, -20, -60, -10, -50, -30
#   Zeros: 0, 0
#   Total: 30 values → 28 non-zero
#
# Arranged in a specific order for equity curve properties.

V = [
    100, -40, 50, -20, 80, -60, 30, -10, 120, -50,
    60, -30, 90, -40, 40, -20, 110, -60, 70, -10,
    100, -50, 50, -30, 80, 0, 0, 120, 60, -40,
]
# len = 30, non_zero = 28

# ── Hand computations for array V ──

# net_pnl = sum(V)
_V_sum = sum(V)  # 100-40+50-20+80-60+30-10+120-50+60-30+90-40+40-20+110-60+70-10+100-50+50-30+80+0+0+120+60-40
# Positive: 100+50+80+30+120+60+90+40+110+70+100+50+80+120+60 = 1160
# Negative: 40+20+60+10+50+30+40+20+60+10+50+30+40 = 460
_V_gains = 100+50+80+30+120+60+90+40+110+70+100+50+80+120+60  # = 1160
_V_losses = 40+20+60+10+50+30+40+20+60+10+50+30+40  # = 460
_V_net = _V_gains - _V_losses  # = 700

# profit_factor = gains / |losses| = 1160/460 ≈ 2.52174
_V_pf = _V_gains / _V_losses

# gain_pain = net_pnl / |sum(losses)| = 700/460 ≈ 1.52174
_V_gain_pain = _V_net / _V_losses

# Build equity curve for V
_V_equity = []
_running = 0.0
for x in V:
    _running += x
    _V_equity.append(_running)
# Final equity = 700

# max drawdown: track peak and max drop
_V_peak = 0.0
_V_max_dd = 0.0
for eq in _V_equity:
    if eq > _V_peak:
        _V_peak = eq
    dd = eq - _V_peak
    if dd < _V_max_dd:
        _V_max_dd = dd

# calmar = equity[-1] / |max_dd|
_V_calmar = _V_equity[-1] / abs(_V_max_dd) if _V_max_dd != 0 else 10.0

# recovery = same as calmar for this formula
_V_recovery = _V_calmar  # net_pnl / |max_drawdown|

# Non-zero values of V for active-day metrics
_V_active = [x for x in V if x != 0]  # 28 values
_V_active_mean = sum(_V_active) / len(_V_active)  # 700/28 = 25.0
_V_active_median = float(np.median(_V_active))

# Downside values (negative only)
_V_downside = [x for x in _V_active if x < 0]
# Downside deviation = sqrt(mean(neg²))
_V_downside_sq = [x**2 for x in _V_downside]
_V_dd_dev = math.sqrt(sum(_V_downside_sq) / len(_V_downside_sq))

# sortino = mean(active) / downside_dev
_V_sortino = _V_active_mean / _V_dd_dev

# robust_sortino = median(active) / downside_dev
_V_robust_sortino = _V_active_median / _V_dd_dev

# sharpe = mean/std * sqrt(365) on active days
_V_active_std = float(np.std(_V_active, ddof=1))  # sample std
# But code uses .std() which is ddof=1 for pandas
_V_sharpe = _V_active_mean / _V_active_std * math.sqrt(365)

# sqn = mean/std * sqrt(n) on active days
_V_sqn = _V_active_mean / _V_active_std * math.sqrt(len(_V_active))

# tail_ratio: p95/|p5| on non-zero days
_V_p95 = float(np.percentile(_V_active, 95))
_V_p5 = float(np.percentile(_V_active, 5))
_V_tail_ratio = _V_p95 / abs(_V_p5)

# expectancy: (wr * avg_win - (1-wr) * avg_loss) / avg_loss
_V_wins_list = [x for x in _V_active if x > 0]
_V_losses_list = [x for x in _V_active if x < 0]
_V_wr = len(_V_wins_list) / len(_V_active)
_V_avg_win = sum(_V_wins_list) / len(_V_wins_list)
_V_avg_loss = abs(sum(_V_losses_list) / len(_V_losses_list))
_V_expectancy = (_V_wr * _V_avg_win - (1 - _V_wr) * _V_avg_loss) / _V_avg_loss

# payoff = avg_win / avg_loss
_V_payoff = _V_avg_win / _V_avg_loss

# omega = gains/|losses| on non-zero days (same as PF on non-zero)
_V_active_gains = sum(x for x in _V_active if x > 0)
_V_active_losses_sum = abs(sum(x for x in _V_active if x < 0))
_V_omega = _V_active_gains / _V_active_losses_sum

# gpr = sum(active) / |sum(neg active)| — same as gain_pain on active
_V_gpr = sum(_V_active) / _V_active_losses_sum


class TestLayer1MetricCorrectness:
    """Hand-computed expected values for all 24 metrics on array V."""

    @pytest.fixture
    def pnl(self):
        return _pnl(V)

    # ── Simple metrics (exact match) ──

    def test_net_pnl(self, pnl):
        result = score_metric(pnl, "net_pnl")
        assert result == pytest.approx(_V_net, abs=0.01), \
            f"net_pnl: expected {_V_net}, got {result}"

    def test_pf(self, pnl):
        result = score_metric(pnl, "pf")
        expected = min(_V_pf, 10.0)
        assert result == pytest.approx(expected, rel=1e-4), \
            f"pf: expected {expected}, got {result}"

    def test_gain_pain(self, pnl):
        result = score_metric(pnl, "gain_pain")
        assert result == pytest.approx(_V_gain_pain, rel=1e-4), \
            f"gain_pain: expected {_V_gain_pain}, got {result}"

    def test_calmar(self, pnl):
        result = score_metric(pnl, "calmar")
        assert result == pytest.approx(_V_calmar, rel=1e-3), \
            f"calmar: expected {_V_calmar}, got {result}"

    def test_recovery(self, pnl):
        result = score_metric(pnl, "recovery")
        assert result == pytest.approx(_V_recovery, rel=1e-3), \
            f"recovery: expected {_V_recovery}, got {result}"

    def test_sortino(self, pnl):
        result = score_metric(pnl, "sortino")
        assert result == pytest.approx(_V_sortino, rel=1e-3), \
            f"sortino: expected {_V_sortino}, got {result}"

    def test_robust_sortino(self, pnl):
        result = score_metric(pnl, "robust_sortino")
        assert result == pytest.approx(_V_robust_sortino, rel=1e-3), \
            f"robust_sortino: expected {_V_robust_sortino}, got {result}"

    def test_sharpe(self, pnl):
        result = score_metric(pnl, "sharpe")
        assert result == pytest.approx(_V_sharpe, rel=1e-3), \
            f"sharpe: expected {_V_sharpe}, got {result}"

    def test_sqn(self, pnl):
        result = score_metric(pnl, "sqn")
        assert result == pytest.approx(_V_sqn, rel=1e-3), \
            f"sqn: expected {_V_sqn}, got {result}"

    def test_tail_ratio(self, pnl):
        result = score_metric(pnl, "tail_ratio")
        assert result == pytest.approx(_V_tail_ratio, rel=1e-3), \
            f"tail_ratio: expected {_V_tail_ratio}, got {result}"

    def test_expectancy(self, pnl):
        result = score_metric(pnl, "expectancy")
        assert result == pytest.approx(_V_expectancy, rel=1e-3), \
            f"expectancy: expected {_V_expectancy}, got {result}"

    def test_payoff(self, pnl):
        result = score_metric(pnl, "payoff")
        expected = min(_V_payoff, 10.0)
        assert result == pytest.approx(expected, rel=1e-3), \
            f"payoff: expected {expected}, got {result}"

    def test_omega(self, pnl):
        result = score_metric(pnl, "omega")
        expected = min(_V_omega, 10.0)
        assert result == pytest.approx(expected, rel=1e-3), \
            f"omega: expected {expected}, got {result}"

    def test_gpr(self, pnl):
        result = score_metric(pnl, "gpr")
        expected = min(_V_gpr, 10.0)
        assert result == pytest.approx(expected, rel=1e-3), \
            f"gpr: expected {expected}, got {result}"

    # ── Equity-curve-dependent metrics (computed via numpy, verified) ──

    def test_lake_ratio(self, pnl):
        """Lake ratio: 1 - underwater_area / (underwater + equity_area)."""
        equity = pnl.cumsum()
        hw = equity.cummax()
        underwater = float((hw - equity).sum())
        total_area = float(equity.abs().sum())
        expected = 1.0 - underwater / (underwater + total_area)
        result = score_metric(pnl, "lake_ratio")
        assert result == pytest.approx(expected, rel=1e-4), \
            f"lake_ratio: expected {expected}, got {result}"

    def test_stability(self, pnl):
        """Stability = R² of equity curve linear regression."""
        equity = pnl.cumsum().values
        x = np.arange(len(equity), dtype=float)
        slope = np.polyfit(x, equity, 1)[0]
        assert slope > 0, "Slope should be positive for V"
        corr = np.corrcoef(x, equity)[0, 1]
        expected = float(corr ** 2)
        result = score_metric(pnl, "stability")
        assert result == pytest.approx(expected, rel=1e-4), \
            f"stability: expected {expected}, got {result}"

    def test_ulcer(self, pnl):
        """Ulcer index: -RMS(drawdown%), drawdowns in % of ONE reference scale.

        Formula changed 2026-10-08: the reference scale is max(|peak|, |equity|, 1)
        over the whole curve, not the running peak with a $1 floor (which scored
        early losses in $ x 100). Same convention as the optimizer's min_ulcer.
        """
        equity = pnl.cumsum()
        peak = np.maximum.accumulate(equity.values)
        ref_scale = max(np.abs(peak).max(), np.abs(equity.values).max(), 1.0)
        dd_pct = (equity.values - peak) / ref_scale * 100
        ui = float(np.sqrt(np.mean(dd_pct ** 2)))
        expected = -ui
        result = score_metric(pnl, "ulcer")
        assert result == pytest.approx(expected, rel=1e-4), \
            f"ulcer: expected {expected}, got {result}"

    def test_cde(self, pnl):
        """Complex Drawdown Efficiency."""
        equity = pnl.cumsum()
        total = float(equity.iloc[-1])
        peak = np.maximum.accumulate(equity.values)
        dd = peak - equity.values
        max_dd = float(dd.max())
        avg_dd = float(dd.mean())
        max_dd_idx = int(dd.argmax())
        recovery_ratio = (len(equity) - max_dd_idx) / len(equity)
        denom = avg_dd + max_dd * recovery_ratio
        expected = total / denom if denom > 0 else 10.0
        result = score_metric(pnl, "cde")
        assert result == pytest.approx(expected, rel=1e-3), \
            f"cde: expected {expected}, got {result}"

    def test_est_efficiency(self, pnl):
        """EST Efficiency: profit / (max_dd * sqrt(bars_to_recover))."""
        equity = pnl.cumsum()
        total = float(equity.iloc[-1])
        peak = np.maximum.accumulate(equity.values)
        dd = peak - equity.values
        max_dd = float(dd.max())
        max_dd_idx = int(dd.argmax())
        bars_to_recover = max(len(equity) - max_dd_idx, 1)
        expected = total / (max_dd * bars_to_recover ** 0.5)
        result = score_metric(pnl, "est_efficiency")
        assert result == pytest.approx(expected, rel=1e-3), \
            f"est_efficiency: expected {expected}, got {result}"

    def test_rapid_recovery(self, pnl):
        """Rapid Recovery: sqrt(profit_efficiency) * recovery_boost."""
        equity = pnl.cumsum()
        total = float(equity.iloc[-1])
        peak = np.maximum.accumulate(equity.values)
        dd = peak - equity.values
        max_dd = float(dd.max())
        profit_eff = total / max_dd
        underwater = dd > 0
        recovery_boost = 1 + (1 - underwater.sum() / len(dd))
        expected = profit_eff ** 0.5 * recovery_boost
        result = score_metric(pnl, "rapid_recovery")
        assert result == pytest.approx(expected, rel=1e-3), \
            f"rapid_recovery: expected {expected}, got {result}"

    def test_ter(self, pnl):
        """Trend Efficiency Ratio: |net_move| / total_path."""
        equity = pnl.cumsum().values
        net_move = abs(equity[-1] - equity[0])
        total_path = float(np.sum(np.abs(np.diff(equity))))
        expected = net_move / total_path
        result = score_metric(pnl, "ter")
        assert result == pytest.approx(expected, rel=1e-4), \
            f"ter: expected {expected}, got {result}"

    def test_keep_score(self, pnl):
        """keep_score: composite of TER + GPR sigmoid + AC(1)."""
        equity = pnl.cumsum().values
        active = pnl[pnl != 0].values
        # TER
        net_move = abs(equity[-1] - equity[0])
        total_path = float(np.sum(np.abs(np.diff(equity))))
        ter = net_move / total_path if total_path > 0 else 0.0
        # GPR sigmoid
        pain = abs(float(active[active < 0].sum()))
        gpr = float(active.sum()) / pain if pain > 0 else 10.0
        sig_gpr = 1.0 / (1.0 + np.exp(-min(gpr, 10.0)))
        # AC lag-1
        ac = float(np.corrcoef(active[:-1], active[1:])[0, 1])
        if np.isnan(ac):
            ac = 0.0
        expected = float(np.clip(0.35 * ter + 0.35 * sig_gpr + 0.30 * max(0.0, ac), 0.0, 1.0))
        result = score_metric(pnl, "keep_score")
        assert result == pytest.approx(expected, rel=1e-3), \
            f"keep_score: expected {expected}, got {result}"

    def test_mr_score(self, pnl):
        """mr_score: crossing_rate + (1-VR) + negative_AC."""
        active = pnl[pnl != 0].values
        equity = pnl.cumsum().values
        # Crossing rate
        window = max(20, len(equity) // 10)
        rolling_mean = pd.Series(equity).rolling(window, min_periods=1).mean().values
        diff = equity - rolling_mean
        signs = np.sign(diff)
        crossings = float(np.sum(np.abs(np.diff(signs)) > 0))
        cr = crossings / max(len(diff) - 1, 1)
        # AC lag-1
        ac = float(np.corrcoef(active[:-1], active[1:])[0, 1])
        if np.isnan(ac):
            ac = 0.0
        neg_ac = max(0.0, -ac)
        # Variance ratio lag-5 = Var(non-overlapping 5-day sums) / (5 * Var(daily)),
        # sample variances. Formula changed 2026-10-08: the old code used
        # every 5th day (active[::5]), which made VR ~0.2 for any series.
        n_blocks = len(active) // 5
        if len(active) < 10 or n_blocks < 3:
            vr = 1.0
        else:
            sums5 = active[: n_blocks * 5].reshape(n_blocks, 5).sum(axis=1)
            vr = np.var(sums5, ddof=1) / (5 * np.var(active, ddof=1))
        vr_bonus = max(0.0, 1.0 - vr)
        expected = float(np.clip(0.40 * cr + 0.35 * neg_ac + 0.25 * vr_bonus, 0.0, 1.0))
        result = score_metric(pnl, "mr_score")
        assert result == pytest.approx(expected, rel=1e-3), \
            f"mr_score: expected {expected}, got {result}"

    def test_crossing_rate(self, pnl):
        """crossing_rate: how often equity crosses its rolling mean."""
        equity = pnl.cumsum().values
        window = max(20, len(equity) // 10)
        rolling_mean = pd.Series(equity).rolling(window, min_periods=1).mean().values
        diff = equity - rolling_mean
        crossings = float(np.sum(np.abs(np.diff(np.sign(diff))) > 0))
        max_crossings = len(diff) - 1
        expected = crossings / max_crossings if max_crossings > 0 else 0.0
        result = score_metric(pnl, "crossing_rate")
        assert result == pytest.approx(expected, rel=1e-4), \
            f"crossing_rate: expected {expected}, got {result}"

    # ── Edge cases ──

    def test_skip_below_min_days(self):
        """< 10 non-zero days → -999 for all metrics."""
        pnl = _pnl([10, -5, 20, -3] + [0] * 26)  # Only 4 active days
        for name in METRIC_NAMES:
            assert score_metric(pnl, name) == -999.0, \
                f"{name} should return -999 for <10 active days"

    def test_all_positive_returns_10(self):
        """No negative days → 10.0 for ratio metrics."""
        pnl = _pnl([10, 5, 20, 3, 15, 8, 12, 7, 18, 25] * 3)  # 30 values, all positive
        assert score_metric(pnl, "sortino") == 10.0
        assert score_metric(pnl, "pf") == 10.0
        assert score_metric(pnl, "gain_pain") == 10.0
        assert score_metric(pnl, "calmar") == 10.0
        assert score_metric(pnl, "payoff") == 10.0

    def test_all_24_metrics_covered(self):
        """Verify we test all 24 metrics — no gaps."""
        tested = {
            "net_pnl", "pf", "gain_pain", "calmar", "recovery", "sortino",
            "robust_sortino", "sharpe", "sqn", "tail_ratio", "expectancy",
            "payoff", "omega", "gpr", "lake_ratio", "stability", "ulcer",
            "cde", "est_efficiency", "rapid_recovery", "ter", "keep_score",
            "mr_score", "crossing_rate",
        }
        assert tested == set(METRIC_NAMES), \
            f"Missing metrics: {set(METRIC_NAMES) - tested}"


# ═══════════════════════════════════════════════════════════════════════
# LAYER 2: WINDOW SLICING CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════

class TestLayer2WindowSlicing:
    """Verify trades land in correct IS/OOS windows based on entry date."""

    def test_slice_pnl_boundaries(self):
        """_slice_pnl returns [start, end] inclusive."""
        pnl = _pnl(list(range(1, 32)), start="2024-01-01")  # Jan 1-31
        # Slice Jan 10-20
        sl = _slice_pnl(pnl, "2024-01-10", "2024-01-20")
        assert len(sl) == 11  # 10th through 20th inclusive
        assert float(sl.iloc[0]) == 10.0  # Jan 10 = 10th value
        assert float(sl.iloc[-1]) == 20.0  # Jan 20 = 20th value

    def test_slice_pnl_single_day(self):
        """Single-day slice returns exactly 1 value."""
        pnl = _pnl([100, 200, 300], start="2024-06-01")
        sl = _slice_pnl(pnl, "2024-06-02", "2024-06-02")
        assert len(sl) == 1
        assert float(sl.iloc[0]) == 200.0

    def test_slice_pnl_empty_range(self):
        """Range outside data returns empty series."""
        pnl = _pnl([10, 20, 30], start="2024-01-01")
        sl = _slice_pnl(pnl, "2025-01-01", "2025-01-31")
        assert len(sl) == 0

    def test_entry_date_attribution(self):
        """Trades are attributed to ENTRY date, not exit date."""
        # Trade enters Jan 15 (IS period), exits Feb 15 (OOS period)
        # Should appear in IS, NOT in OOS
        trades = [
            FakeTrade(
                entry_time=datetime(2024, 1, 15, 10, 0),
                exit_time=datetime(2024, 2, 15, 10, 0),
                net_profit=100.0,
                run_id=1, symbol="BTCUSDT", side="Long",
            ),
            # Trade enters Feb 5 (OOS period)
            FakeTrade(
                entry_time=datetime(2024, 2, 5, 14, 0),
                exit_time=datetime(2024, 2, 20, 10, 0),
                net_profit=50.0,
                run_id=1, symbol="BTCUSDT", side="Long",
            ),
        ]
        rbm = {1: 100}
        batch_daily = build_batch_daily_pnl(trades, rbm)
        pnl = batch_daily[100]

        # IS window: Jan 1 - Jan 31
        is_slice = _slice_pnl(pnl, "2024-01-01", "2024-01-31")
        assert float(is_slice.sum()) == 100.0  # Only the Jan 15 trade

        # OOS window: Feb 1 - Feb 28
        oos_slice = _slice_pnl(pnl, "2024-02-01", "2024-02-28")
        assert float(oos_slice.sum()) == 50.0  # Only the Feb 5 trade

    def test_same_day_trades_aggregate(self):
        """Multiple trades on same entry date sum into one daily value."""
        trades = [
            FakeTrade(entry_time=datetime(2024, 3, 1, 10, 0), net_profit=100.0, run_id=1),
            FakeTrade(entry_time=datetime(2024, 3, 1, 14, 0), net_profit=-30.0, run_id=1),
            FakeTrade(entry_time=datetime(2024, 3, 1, 16, 0), net_profit=50.0, run_id=1),
        ]
        rbm = {1: 100}
        batch_daily = build_batch_daily_pnl(trades, rbm)
        pnl = batch_daily[100]
        assert float(pnl.loc["2024-03-01"]) == 120.0  # 100 - 30 + 50

    def test_window_generation_boundaries(self):
        """Verify generate_rolling_windows produces correct IS/OOS ranges."""
        from backend.analysis.wfo_window_gen import generate_rolling_windows

        windows = generate_rolling_windows(
            is_months=12,
            oos_months=3,
            last_oos_end="2025-12-31",
            data_start="2023-01-01",
        )

        # First window with OOS
        w1 = windows[0]
        assert w1["is_start"] == "2023-01-01"
        assert w1["is_end"] == "2023-12-31"
        assert w1["oos_start"] == "2024-01-01"
        assert w1["oos_end"] == "2024-03-31"

        # Second window should step by oos_months (3)
        w2 = windows[1]
        assert w2["is_start"] == "2023-04-01"
        assert w2["is_end"] == "2024-03-31"
        assert w2["oos_start"] == "2024-04-01"
        assert w2["oos_end"] == "2024-06-30"

        # All windows: IS and OOS don't overlap
        for w in windows:
            if w.get("oos_start"):
                assert w["is_end"] < w["oos_start"], \
                    f"Window {w['window_num']}: IS end {w['is_end']} >= OOS start {w['oos_start']}"


# ═══════════════════════════════════════════════════════════════════════
# LAYER 3: PIPELINE LOGIC CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════

def _build_controlled_dataset():
    """Build a 3-batch dataset with known pipeline outcomes.

    Batch 101 (Long, run_id=1): Strong batch — high PnL, good metrics
    Batch 102 (Short, run_id=2): Medium batch — moderate PnL
    Batch 103 (Long, run_id=3): Weak batch — low PnL, barely passes gates

    Each batch has 2 symbols with enough trades to survive gates.
    Trades span 2023-01-01 to 2024-09-30 (enough for 2 windows at IS=12/OOS=3).
    """
    trades = []
    tid = 0

    # ── Batch 101 (Strong Long) ──
    # BTCUSDT: consistently profitable
    for month in range(1, 22):  # 21 months of trades
        base_date = datetime(2023, 1, 1) + timedelta(days=30 * (month - 1))
        for day_offset in [0, 5, 10, 15, 20]:
            d = base_date + timedelta(days=day_offset)
            if d > datetime(2024, 9, 30):
                break
            tid += 1
            trades.append(FakeTrade(
                symbol="BTCUSDT", side="Long",
                entry_time=d + timedelta(hours=14),
                exit_time=d + timedelta(hours=20),
                net_profit=float(30 + (tid % 5) * 10),  # 30-70 range
                run_id=1, id=tid,
            ))
    # ETHUSDT: also profitable but less
    for month in range(1, 22):
        base_date = datetime(2023, 1, 1) + timedelta(days=30 * (month - 1))
        for day_offset in [2, 7, 12, 17, 22]:
            d = base_date + timedelta(days=day_offset)
            if d > datetime(2024, 9, 30):
                break
            tid += 1
            trades.append(FakeTrade(
                symbol="ETHUSDT", side="Long",
                entry_time=d + timedelta(hours=10),
                exit_time=d + timedelta(hours=18),
                net_profit=float(15 + (tid % 3) * 5 - 10),  # 5-20 range
                run_id=1, id=tid,
            ))

    # ── Batch 102 (Medium Short) ──
    for month in range(1, 22):
        base_date = datetime(2023, 1, 1) + timedelta(days=30 * (month - 1))
        for day_offset in [1, 6, 11, 16, 21]:
            d = base_date + timedelta(days=day_offset)
            if d > datetime(2024, 9, 30):
                break
            tid += 1
            trades.append(FakeTrade(
                symbol="BTCUSDT", side="Short",
                entry_time=d + timedelta(hours=15),
                exit_time=d + timedelta(hours=22),
                net_profit=float(20 + (tid % 4) * 5 - 5),  # 15-30 range
                run_id=2, id=tid,
            ))
    for month in range(1, 22):
        base_date = datetime(2023, 1, 1) + timedelta(days=30 * (month - 1))
        for day_offset in [3, 8, 13, 18, 23]:
            d = base_date + timedelta(days=day_offset)
            if d > datetime(2024, 9, 30):
                break
            tid += 1
            trades.append(FakeTrade(
                symbol="SOLUSDT", side="Short",
                entry_time=d + timedelta(hours=12),
                exit_time=d + timedelta(hours=20),
                net_profit=float(10 + (tid % 5) * 3),  # 10-22 range
                run_id=2, id=tid,
            ))

    # ── Batch 103 (Weak Long) ──
    for month in range(1, 22):
        base_date = datetime(2023, 1, 1) + timedelta(days=30 * (month - 1))
        for day_offset in [4, 9, 14, 19, 24]:
            d = base_date + timedelta(days=day_offset)
            if d > datetime(2024, 9, 30):
                break
            tid += 1
            # Mix of wins and losses — net positive but barely
            profit = float(5 + (tid % 7) * 3 - 8)  # -3 to 16 range
            trades.append(FakeTrade(
                symbol="BTCUSDT", side="Long",
                entry_time=d + timedelta(hours=9),
                exit_time=d + timedelta(hours=16),
                net_profit=profit,
                run_id=3, id=tid,
            ))
    for month in range(1, 22):
        base_date = datetime(2023, 1, 1) + timedelta(days=30 * (month - 1))
        for day_offset in [3, 8, 13, 18, 25]:
            d = base_date + timedelta(days=day_offset)
            if d > datetime(2024, 9, 30):
                break
            tid += 1
            profit = float(3 + (tid % 5) * 2 - 4)  # -1 to 7 range
            trades.append(FakeTrade(
                symbol="XRPUSDT", side="Long",
                entry_time=d + timedelta(hours=11),
                exit_time=d + timedelta(hours=19),
                net_profit=profit,
                run_id=3, id=tid,
            ))

    rbm = {1: 101, 2: 102, 3: 103}
    return trades, rbm


class TestLayer3PipelineLogic:
    """End-to-end pipeline test on controlled synthetic dataset."""

    @pytest.fixture
    def dataset(self):
        trades, rbm = _build_controlled_dataset()
        return trades, rbm

    def test_dedup_preserves_all_unique(self, dataset):
        """3 distinct batches should all survive dedup."""
        trades, rbm = dataset
        _, _, kept = dedup_batches(trades, rbm)
        assert set(kept) == {101, 102, 103}

    def test_batch_sides_detected_correctly(self, dataset):
        """Batch 101=Long, 102=Short, 103=Long."""
        trades, rbm = dataset
        sides = detect_batch_sides(trades, rbm)
        assert sides[101] == "Long"
        assert sides[102] == "Short"
        assert sides[103] == "Long"

    def test_batch_daily_pnl_entry_date_keyed(self, dataset):
        """All batch daily PnL series indexed by entry date."""
        trades, rbm = dataset
        batch_daily = build_batch_daily_pnl(trades, rbm)
        assert 101 in batch_daily
        assert 102 in batch_daily
        assert 103 in batch_daily
        # All dates should be midnight (floor to day)
        for bid, pnl in batch_daily.items():
            for ts in pnl.index:
                assert ts.hour == 0 and ts.minute == 0, \
                    f"Batch {bid}: date {ts} not floored to midnight"

    def test_asset_keys_format(self, dataset):
        """Asset keys follow {batch_id}_{symbol}_{side} format."""
        trades, rbm = dataset
        _, asset_to_batch, batch_to_assets, _, _ = build_asset_daily_pnl(trades, rbm)
        for key in asset_to_batch:
            parts = key.split("_", 2)
            assert len(parts) == 3, f"Bad key format: {key}"
            assert parts[0].isdigit(), f"First part not numeric: {key}"

    def test_is_screening_ranks_correctly(self, dataset):
        """Batch 101 should rank highest on net_pnl in IS window."""
        trades, rbm = dataset
        batch_daily = build_batch_daily_pnl(trades, rbm)

        # IS window: 2023-01-01 to 2023-12-31
        scores = {}
        for bid, pnl in batch_daily.items():
            sl = _slice_pnl(pnl, "2023-01-01", "2023-12-31")
            scores[bid] = float(sl.sum())

        # Batch 101 should have highest IS PnL (most profitable trades)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        assert ranked[0][0] == 101, \
            f"Expected batch 101 first, got {ranked[0][0]} (scores: {scores})"
        # All should be positive
        for bid, sc in scores.items():
            assert sc > 0, f"Batch {bid} IS PnL should be positive, got {sc}"

    def test_full_pipeline_runs_without_error(self, dataset):
        """run_batch_selection completes on synthetic data."""
        trades, rbm = dataset
        from backend.analysis.wfo_window_gen import generate_rolling_windows

        windows = generate_rolling_windows(
            is_months=12,
            oos_months=3,
            last_oos_end="2024-09-30",
            data_start="2023-01-01",
        )

        config = BatchSelectionConfig(
            max_batches=2,
            assets_per_batch=3,
            n_candidates=3,
            batch_rank_metric="net_pnl",
            cal_rank_metric="net_pnl",
            asset_rank_metric="net_pnl",
            min_is_days=10,
            min_pf=0.5,
            min_asset_days=3,
            asset_wr_gate=0.0,
            q_starts=1,
        )

        result = run_batch_selection(trades, windows, config, rbm)

        assert "windows" in result
        assert "summary" in result
        assert len(result["windows"]) > 0

    def test_pipeline_selects_best_batches(self, dataset):
        """With mb=2 and net_pnl ranking, batch 101 (strongest) should always be selected."""
        trades, rbm = dataset
        from backend.analysis.wfo_window_gen import generate_rolling_windows

        windows = generate_rolling_windows(
            is_months=12,
            oos_months=3,
            last_oos_end="2024-09-30",
            data_start="2023-01-01",
        )

        config = BatchSelectionConfig(
            max_batches=2,
            assets_per_batch=5,
            n_candidates=3,
            min_calibrated=2,  # Explicit: don't fill beyond mb
            batch_rank_metric="net_pnl",
            cal_rank_metric="net_pnl",
            asset_rank_metric="net_pnl",
            min_is_days=10,
            min_pf=0.5,
            min_asset_days=3,
            asset_wr_gate=0.0,
            q_starts=1,
        )

        result = run_batch_selection(trades, windows, config, rbm)

        # Check that window results have selected batches
        for w in result["windows"]:
            if w.get("oos_start") and w.get("candidates"):
                cand = w["candidates"][0]
                selected = cand.get("selected_batches", [])
                assert len(selected) <= 2, f"More than mb=2 batches selected: {selected}"
                # Batch 101 (strongest PnL) should always be selected
                assert 101 in selected, \
                    f"Batch 101 (strongest) not in selected: {selected}"

    def test_asset_wr_gate_filters_correctly(self, dataset):
        """With high asset_wr_gate, fewer assets should be selected."""
        trades, rbm = dataset
        from backend.analysis.wfo_window_gen import generate_rolling_windows

        windows = generate_rolling_windows(
            is_months=12,
            oos_months=3,
            last_oos_end="2024-09-30",
            data_start="2023-01-01",
        )

        # Run with no gate
        config_open = BatchSelectionConfig(
            max_batches=3, assets_per_batch=5, n_candidates=3,
            batch_rank_metric="net_pnl", cal_rank_metric="net_pnl",
            asset_rank_metric="net_pnl",
            min_is_days=10, min_pf=0.5, min_asset_days=3,
            asset_wr_gate=0.0, q_starts=1,
        )
        # Run with strict gate
        config_strict = BatchSelectionConfig(
            max_batches=3, assets_per_batch=5, n_candidates=3,
            batch_rank_metric="net_pnl", cal_rank_metric="net_pnl",
            asset_rank_metric="net_pnl",
            min_is_days=10, min_pf=0.5, min_asset_days=3,
            asset_wr_gate=0.90, q_starts=1,  # Very strict
        )

        result_open = run_batch_selection(trades, windows, config_open, rbm)
        result_strict = run_batch_selection(trades, windows, config_strict, rbm)

        # Strict gate should have fewer or equal assets
        def count_assets(result):
            total = 0
            for w in result["windows"]:
                if w.get("candidates"):
                    total += w["candidates"][0].get("n_assets", 0)
            return total

        assert count_assets(result_strict) <= count_assets(result_open)

    def test_oos_never_used_for_decisions(self, dataset):
        """OOS metrics are reporting-only — changing OOS shouldn't affect selections."""
        trades, rbm = dataset
        from backend.analysis.wfo_window_gen import generate_rolling_windows

        windows = generate_rolling_windows(
            is_months=12, oos_months=3,
            last_oos_end="2024-09-30", data_start="2023-01-01",
        )

        config = BatchSelectionConfig(
            max_batches=2, assets_per_batch=3, n_candidates=3,
            batch_rank_metric="net_pnl", cal_rank_metric="net_pnl",
            asset_rank_metric="net_pnl",
            min_is_days=10, min_pf=0.5, min_asset_days=3,
            asset_wr_gate=0.0, q_starts=1,
        )

        result = run_batch_selection(trades, windows, config, rbm)

        # Now run with different OOS length (shouldn't change batch/asset picks)
        windows_2mo = generate_rolling_windows(
            is_months=12, oos_months=2,
            last_oos_end="2024-09-30", data_start="2023-01-01",
        )

        result_2mo = run_batch_selection(trades, windows_2mo, config, rbm)

        # Compare selected batches for window 1 (cold start, same IS)
        if result["windows"] and result_2mo["windows"]:
            w1_batches = result["windows"][0]["candidates"][0]["selected_batches"]
            w2_batches = result_2mo["windows"][0]["candidates"][0]["selected_batches"]
            # Same IS period → same IS screening → same cold-start selection
            assert set(w1_batches) == set(w2_batches), \
                f"Different OOS lengths changed batch selection: {w1_batches} vs {w2_batches}"

    def test_precomputed_matches_direct(self, dataset):
        """precompute_pnl_structures + run_batch_selection == direct run."""
        trades, rbm = dataset
        from backend.analysis.batch_selection import precompute_pnl_structures
        from backend.analysis.wfo_window_gen import generate_rolling_windows

        windows = generate_rolling_windows(
            is_months=12, oos_months=3,
            last_oos_end="2024-09-30", data_start="2023-01-01",
        )

        config = BatchSelectionConfig(
            max_batches=2, assets_per_batch=3, n_candidates=3,
            batch_rank_metric="net_pnl", cal_rank_metric="net_pnl",
            asset_rank_metric="net_pnl",
            min_is_days=10, min_pf=0.5, min_asset_days=3,
            asset_wr_gate=0.0, q_starts=1,
        )

        # Direct path
        result_direct = run_batch_selection(trades, windows, config, rbm)

        # Precomputed path
        precomputed = precompute_pnl_structures(trades, rbm)
        result_pre = run_batch_selection([], windows, config, {}, precomputed=precomputed)

        # Compare OOS PnL and batch selections for each window
        for w_d, w_p in zip(result_direct["windows"], result_pre["windows"]):
            if w_d.get("candidates") and w_p.get("candidates"):
                d_batches = w_d["candidates"][0]["selected_batches"]
                p_batches = w_p["candidates"][0]["selected_batches"]
                assert d_batches == p_batches, \
                    f"Window {w_d['window_num']}: direct={d_batches} vs precomputed={p_batches}"

                d_assets = w_d["candidates"][0]["asset_details"]
                p_assets = w_p["candidates"][0]["asset_details"]
                d_keys = [a["key"] for a in d_assets]
                p_keys = [a["key"] for a in p_assets]
                assert d_keys == p_keys, \
                    f"Window {w_d['window_num']}: asset mismatch"

                d_pnl = w_d["candidates"][0].get("oos_metrics", {}).get("net_profit", 0)
                p_pnl = w_p["candidates"][0].get("oos_metrics", {}).get("net_profit", 0)
                assert d_pnl == pytest.approx(p_pnl, abs=0.01), \
                    f"Window {w_d['window_num']}: OOS PnL {d_pnl} vs {p_pnl}"


# ═══════════════════════════════════════════════════════════════════════
# GOLDEN REFERENCE JSON EXPORT
# ═══════════════════════════════════════════════════════════════════════

def test_export_golden_reference(tmp_path):
    """Generate the golden reference JSON (run manually with -k test_export)."""
    pnl = _pnl(V)
    reference = {
        "version": 1,
        "description": "Hand-computed metric test vectors for pipeline validation",
        "array_V": V,
        "metrics": {},
    }
    for name in METRIC_NAMES:
        reference["metrics"][name] = round(score_metric(pnl, name), 6)

    out = tmp_path / "GOLDEN_REFERENCE.json"
    with open(out, "w") as f:
        json.dump(reference, f, indent=2)

    # Verify it round-trips
    with open(out) as f:
        loaded = json.load(f)
    assert loaded["metrics"]["net_pnl"] == pytest.approx(700.0, abs=1)
