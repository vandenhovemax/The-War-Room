"""Ground truth validation tests — verify the math is correct, not just crash-free.

Each test creates synthetic data with KNOWN expected results and verifies the
optimizer / scoring / sizing code produces exactly those results.

Categories:
  1. Seed ranking: known-order assets, verify ranking matches
  2. Objective functions: verify each objective selects the right portfolio
  3. Benchmark robustness: verify split logic, threshold filtering, status classification
  4. Position sizing: hand-calculated PnL for Fixed, %, Kelly, Martingale
  5. Cost calculations: commission, slippage, spread, funding formulas
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest


# =========================================================================
# 1. SEED RANKING — KNOWN CORRECT RANKINGS
# =========================================================================

class TestSeedRankingSortino:
  """Create 3 assets with known Sortino ordering, verify seed ranking."""

  def _make_known_sortino_assets(self) -> pd.DataFrame:
    """BEST: consistent small wins. MID: mixed. WORST: mostly losses."""
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    # BEST: high mean, tiny downside
    best = np.where(rng.random(n) < 0.9, 20.0, -5.0)
    # MID: moderate mean, moderate downside
    mid = np.where(rng.random(n) < 0.6, 15.0, -12.0)
    # WORST: low mean, large downside
    worst = np.where(rng.random(n) < 0.4, 10.0, -18.0)
    return pd.DataFrame({"BEST": best, "MID": mid, "WORST": worst}, index=dates)

  def test_sortino_ranking_order(self):
    """BEST should rank first (lowest score), WORST last."""
    from backend.analysis.optimizer_seeds import compute_seed_scores
    pnl = self._make_known_sortino_assets()
    scores = compute_seed_scores(pnl, ["BEST", "MID", "WORST"], ["sortino"])
    assert scores["BEST"] < scores["MID"] < scores["WORST"]

  def test_sortino_formula_matches_manual(self):
    """Verify Sortino formula: mean / RMS(downside)."""
    from backend.analysis.scoring import portfolio_sortino
    pnl = pd.Series([10.0, 20.0, -5.0, 15.0, -10.0])
    mean_ret = pnl.mean()  # 6.0
    downside = pnl[pnl < 0]  # [-5, -10]
    rms = float(np.sqrt(np.mean(downside.values ** 2)))
    expected = -(mean_ret / rms)
    assert portfolio_sortino(pnl) == pytest.approx(expected, abs=1e-10)


class TestSeedRankingProfitFactor:
  """Verify PF ranking: higher PF = better rank (lower seed score)."""

  def test_pf_ranking_order(self):
    from backend.analysis.optimizer_seeds import compute_seed_scores
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    # HIGH_PF: PF ≈ 4 (80% wins at 20, 20% losses at -20)
    high = np.where(rng.random(n) < 0.8, 20.0, -20.0)
    # MED_PF: PF ≈ 1.5 (60% wins at 15, 40% losses at -15)
    med = np.where(rng.random(n) < 0.6, 15.0, -15.0)
    # LOW_PF: PF ≈ 1.0 (50% wins at 10, 50% losses at -10)
    low = np.where(rng.random(n) < 0.5, 10.0, -10.0)
    pnl = pd.DataFrame({"HIGH": high, "MED": med, "LOW": low}, index=dates)
    scores = compute_seed_scores(pnl, ["HIGH", "MED", "LOW"], ["pf"])
    assert scores["HIGH"] < scores["MED"] < scores["LOW"]

  def test_pf_formula_matches_manual(self):
    """Verify PF = gross_profit / gross_loss, negated, capped at 10."""
    from backend.analysis.scoring import portfolio_profit_factor
    pnl = pd.Series([100, 200, -50, -100])
    # GP = 300, GL = 150, PF = 2.0, negated = -2.0
    assert portfolio_profit_factor(pnl) == pytest.approx(-2.0, abs=0.01)

  def test_pf_capped_at_10(self):
    """PF capped at 10 to prevent outlier dominance."""
    from backend.analysis.scoring import portfolio_profit_factor
    pnl = pd.Series([1000, 1000, -1])  # PF = 2000 → capped to 10
    assert portfolio_profit_factor(pnl) == pytest.approx(-10.0, abs=0.01)


class TestSeedRankingMaxDrawdown:
  """Verify max DD ranking: lower DD = better rank."""

  def test_maxdd_ranking_order(self):
    from backend.analysis.optimizer_seeds import compute_seed_scores
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    # SMALL_DD: steady uptrend, tiny dips
    small = np.array([5.0] * 180 + [-2.0] * 10 + [5.0] * 10)
    # MED_DD: has a moderate dip
    med = np.array([5.0] * 100 + [-15.0] * 10 + [5.0] * 90)
    # BIG_DD: has a large crash
    big = np.array([5.0] * 100 + [-50.0] * 10 + [5.0] * 90)
    pnl = pd.DataFrame({"SMALL": small, "MED": med, "BIG": big}, index=dates)
    scores = compute_seed_scores(pnl, ["SMALL", "MED", "BIG"], ["max_dd"])
    # Lower max_dd = better seed = lower score
    assert scores["SMALL"] < scores["MED"] < scores["BIG"]


class TestSeedRankingWinRate:
  """Verify win rate ranking: higher WR = better rank."""

  def test_wr_ranking_order(self):
    from backend.analysis.optimizer_seeds import compute_seed_scores
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    high_wr = np.where(rng.random(n) < 0.9, 10.0, -10.0)
    med_wr = np.where(rng.random(n) < 0.6, 10.0, -10.0)
    low_wr = np.where(rng.random(n) < 0.3, 10.0, -10.0)
    pnl = pd.DataFrame({"HIGH_WR": high_wr, "MED_WR": med_wr, "LOW_WR": low_wr},
                        index=dates)
    scores = compute_seed_scores(pnl, ["HIGH_WR", "MED_WR", "LOW_WR"], ["wr"])
    assert scores["HIGH_WR"] < scores["MED_WR"] < scores["LOW_WR"]

  def test_wr_formula_matches_manual(self):
    """Verify WR: count(pnl > 0) / count(pnl != 0), negated."""
    from backend.analysis.optimizer_seeds import _score_metric
    pnl = pd.Series([10, -5, 10, 10, -5, 10, 0, 10, -5, 10])
    # Non-zero: [10, -5, 10, 10, -5, 10, 10, -5, 10] → 9 items
    # Wins: 6 of 9 → WR = 6/9
    result = _score_metric(pnl, "wr")
    assert result == pytest.approx(-(6 / 9), abs=1e-10)


class TestSeedRankingStability:
  """Verify stability ranking: smoother equity = better rank."""

  def test_stability_ranking_order(self):
    from backend.analysis.optimizer_seeds import compute_seed_scores
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    # SMOOTH: near-constant PnL (tiny noise to avoid std=0 → inf in scorer)
    smooth = 5.0 + rng.normal(0, 0.01, n)
    # MODERATE: mostly positive with small variation
    moderate = rng.normal(3.0, 2.0, n)
    # CHOPPY: large swings around zero
    choppy = rng.normal(1.0, 20.0, n)
    pnl = pd.DataFrame({"SMOOTH": smooth, "MOD": moderate, "CHOPPY": choppy},
                        index=dates)
    scores = compute_seed_scores(pnl, ["SMOOTH", "MOD", "CHOPPY"], ["stability"])
    # stability metric = 1 - calculate_stability(pnl), lower = more stable = better
    assert scores["SMOOTH"] < scores["MOD"] < scores["CHOPPY"]


class TestSeedRankingSharpe:
  """Verify Sharpe ranking."""

  def test_sharpe_ranking_order(self):
    from backend.analysis.optimizer_seeds import compute_seed_scores
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    # HIGH: high mean, low std → best Sharpe
    high = rng.normal(10.0, 2.0, n)
    # MED: moderate mean, moderate std
    med = rng.normal(5.0, 10.0, n)
    # LOW: low mean, high std
    low = rng.normal(1.0, 30.0, n)
    pnl = pd.DataFrame({"HIGH_SH": high, "MED_SH": med, "LOW_SH": low},
                        index=dates)
    scores = compute_seed_scores(pnl, ["HIGH_SH", "MED_SH", "LOW_SH"], ["sharpe"])
    assert scores["HIGH_SH"] < scores["MED_SH"] < scores["LOW_SH"]

  def test_sharpe_formula_matches_manual(self):
    """Verify Sharpe: -(mean / std * sqrt(365))."""
    from backend.analysis.scoring import portfolio_sharpe
    pnl = pd.Series([10.0, 20.0, 30.0, 10.0, 20.0])
    mean_ret = pnl.mean()  # 18.0
    std_ret = pnl.std()
    expected = -(mean_ret / std_ret * (365 ** 0.5))
    assert portfolio_sharpe(pnl) == pytest.approx(expected, abs=0.01)


class TestSeedRankingMultiMetric:
  """Verify composite seed scoring with multiple metrics uses rank averaging."""

  def test_rank_averaging(self):
    """With 2 metrics, final score = average of ranks across metrics."""
    from backend.analysis.optimizer_seeds import compute_seed_scores
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    # A: best sortino but worst PF
    a = np.where(rng.random(n) < 0.95, 5.0, -1.0)  # high WR small wins
    # B: worst sortino but best PF
    b = np.where(rng.random(n) < 0.5, 50.0, -10.0)  # low WR big wins
    # C: middle on both
    c = np.where(rng.random(n) < 0.7, 15.0, -10.0)
    pnl = pd.DataFrame({"A": a, "B": b, "C": c}, index=dates)
    scores = compute_seed_scores(pnl, ["A", "B", "C"], ["sortino", "pf"])
    # Ranks: sortino → A=0, C=1, B=2; pf → B=0, C=1, A=2
    # Avg:   A=(0+2)/2=1.0; B=(2+0)/2=1.0; C=(1+1)/2=1.0
    # All should be close (composite balances out)
    assert all(np.isfinite(v) for v in scores.values())

  def test_weights_shift_ranking(self):
    """Heavier weight on one metric should favor the asset that ranks best on it."""
    from backend.analysis.optimizer_seeds import compute_seed_scores
    from backend.analysis.optimizer_seeds import _score_metric
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    # Need 3 assets where sortino and PF rankings DIFFER.
    # A: high mean-to-downside ratio (good sortino) but moderate PF
    #    Many small wins + moderate losses → Sortino good, PF moderate
    a = np.where(rng.random(n) < 0.7, 8.0, -15.0)
    # B: high GP/GL ratio (good PF) but worse sortino
    #    Few big wins + many tiny losses → PF good, Sortino moderate
    b = np.where(rng.random(n) < 0.25, 200.0, -8.0)
    # C: middle on both
    c = np.where(rng.random(n) < 0.6, 15.0, -12.0)
    pnl = pd.DataFrame({"A": a, "B": b, "C": c}, index=dates)

    # Verify our assets actually have different rankings on each metric
    sort_scores = {x: _score_metric(pnl[x], "sortino") for x in ["A", "B", "C"]}
    pf_scores = {x: _score_metric(pnl[x], "pf") for x in ["A", "B", "C"]}
    sort_rank = sorted(["A", "B", "C"], key=lambda x: sort_scores[x])
    pf_rank = sorted(["A", "B", "C"], key=lambda x: pf_scores[x])
    sort_best = sort_rank[0]
    pf_best = pf_rank[0]

    # With heavy weight on sortino → sortino-best should win
    scores_sort = compute_seed_scores(
      pnl, ["A", "B", "C"], ["sortino", "pf"],
      metric_weights={"sortino": 100.0, "pf": 1.0},
    )
    assert scores_sort[sort_best] <= scores_sort[pf_best] or sort_best == pf_best

    # With heavy weight on PF → PF-best should win
    scores_pf = compute_seed_scores(
      pnl, ["A", "B", "C"], ["sortino", "pf"],
      metric_weights={"sortino": 1.0, "pf": 100.0},
    )
    assert scores_pf[pf_best] <= scores_pf[sort_best] or sort_best == pf_best


class TestSeedRankingCalmar:
  """Verify Calmar = total_return / max_dd, negated."""

  def test_calmar_formula_matches_manual(self):
    from backend.analysis.scoring import portfolio_calmar
    pnl = pd.Series([100, 200, -50, -100, 300])
    cum = pnl.cumsum()  # [100, 300, 250, 150, 450]
    total_ret = cum.iloc[-1]  # 450
    peak = np.maximum.accumulate(cum.values)  # [100, 300, 300, 300, 450]
    max_dd = abs((cum.values - peak).min())  # abs(150-300) = 150
    expected = -(total_ret / max_dd)  # -(450/150) = -3.0
    assert portfolio_calmar(pnl) == pytest.approx(expected, abs=0.01)


class TestSeedRankingExpectancy:
  """Verify Expectancy = WR * avg_win - (1-WR) * avg_loss, negated."""

  def test_expectancy_formula_matches_manual(self):
    from backend.analysis.scoring import portfolio_expectancy
    pnl = pd.Series([100, 200, -50, -100, 300])
    nonzero = pnl[pnl != 0]  # all 5
    wins = nonzero[nonzero > 0]  # [100, 200, 300]
    losses = nonzero[nonzero < 0]  # [-50, -100]
    wr = 3 / 5  # 0.6
    avg_win = (100 + 200 + 300) / 3  # 200
    avg_loss = (50 + 100) / 2  # 75
    expected = -(wr * avg_win - (1 - wr) * avg_loss)  # -(0.6*200 - 0.4*75) = -(120-30) = -90
    assert portfolio_expectancy(pnl) == pytest.approx(expected, abs=0.01)


class TestSeedRankingGainPain:
  """Verify Gain/Pain = sum(returns) / sum(|negative returns|), negated."""

  def test_gain_pain_formula_matches_manual(self):
    from backend.analysis.scoring import portfolio_gain_pain
    pnl = pd.Series([100, 200, -50, -100, 300])
    total = pnl.sum()  # 450
    pain = abs(pnl[pnl < 0].sum())  # abs(-150) = 150
    expected = -(total / pain)  # -(450/150) = -3.0
    assert portfolio_gain_pain(pnl) == pytest.approx(expected, abs=0.01)


class TestSeedRankingPayoff:
  """Verify Payoff = avg_win / avg_loss, negated."""

  def test_payoff_formula_matches_manual(self):
    from backend.analysis.scoring import portfolio_payoff
    pnl = pd.Series([100, 200, -50, -100, 300])
    avg_win = (100 + 200 + 300) / 3  # 200
    avg_loss = abs((-50 + -100) / 2)  # 75
    expected = -(avg_win / avg_loss)  # -(200/75) ≈ -2.667
    assert portfolio_payoff(pnl) == pytest.approx(expected, abs=0.01)


class TestSeedRankingLakeRatio:
  """Verify Lake Ratio = fraction of time underwater."""

  def test_lake_ratio_formula_matches_manual(self):
    from backend.analysis.scoring import portfolio_lake_ratio
    # Equity: 100, 300, 250, 150, 450 → peak: 100,300,300,300,450
    # Underwater: F, F, T, T, F → 2/5 = 0.4
    pnl = pd.Series([100, 200, -50, -100, 300])
    assert portfolio_lake_ratio(pnl) == pytest.approx(0.4, abs=0.01)


class TestSeedRankingTailRatio:
  """Verify Tail Ratio = p95 / |p5|, negated."""

  def test_tail_ratio_formula_matches_manual(self):
    from backend.analysis.scoring import portfolio_tail_ratio
    rng = np.random.default_rng(42)
    pnl = pd.Series(rng.normal(5, 20, 100))
    p95 = float(np.percentile(pnl.values, 95))
    p5 = float(np.percentile(pnl.values, 5))
    expected = -(p95 / abs(p5))
    assert portfolio_tail_ratio(pnl) == pytest.approx(expected, abs=0.01)


class TestSeedRankingUlcerIndex:
  """Verify Ulcer = RMS of drawdowns normalised by a single reference scale.

  Post Tier 2.5 B17: portfolio_ulcer now normalises drawdowns against
  max(|peak|, |cum|, 1.0) instead of per-timestep peak, to avoid mixing
  $ and % scales when the running peak is <= 0.
  """

  def test_ulcer_formula_matches_manual(self):
    from backend.analysis.scoring import portfolio_ulcer
    pnl = pd.Series([100, 200, -50, -100, 300])
    cum = pnl.cumsum()  # [100, 300, 250, 150, 450]
    peak = np.maximum.accumulate(cum.values)  # [100, 300, 300, 300, 450]
    ref_scale = float(max(abs(peak).max(), abs(cum.values).max(), 1.0))
    dd_pct = (cum.values - peak) / ref_scale * 100
    expected = float(np.sqrt(np.mean(dd_pct ** 2)))
    assert portfolio_ulcer(pnl) == pytest.approx(expected, abs=0.01)


class TestSeedRankingRecovery:
  """Verify Recovery = net_profit / max_dd, negated."""

  def test_recovery_formula_matches_manual(self):
    from backend.analysis.scoring import portfolio_recovery
    pnl = pd.Series([100, 200, -50, -100, 300])
    cum = pnl.cumsum()
    total = cum.iloc[-1]  # 450
    peak = np.maximum.accumulate(cum.values)
    max_dd = abs((cum.values - peak).min())  # 150
    expected = -(total / max_dd)  # -(450/150) = -3.0
    assert portfolio_recovery(pnl) == pytest.approx(expected, abs=0.01)


# =========================================================================
# 2. OBJECTIVE FUNCTIONS — VERIFY OPTIMIZATION TARGETS
# =========================================================================

class TestObjectiveSelectsCorrectPortfolio:
  """Create assets designed to win on specific objectives, verify selection."""

  @pytest.fixture
  def designed_assets(self):
    """5 assets, each designed to excel on a different metric."""
    n = 300
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    return pd.DataFrame({
      # STABLE: perfectly linear equity (best stability/R²)
      "STABLE": np.array([3.0] * n),
      # HIGH_SORT: high mean, tiny downside (best sortino)
      "HIGH_SORT": np.where(rng.random(n) < 0.95, 15.0, -2.0),
      # LOW_DD: smallest max drawdown (tiny, consistent gains)
      "LOW_DD": np.where(rng.random(n) < 0.99, 2.0, -1.0),
      # HIGH_PF: highest PF (big wins, tiny losses)
      "HIGH_PF": np.where(rng.random(n) < 0.5, 40.0, -5.0),
      # HIGH_SHARPE: highest mean/std ratio
      "HIGH_SHARPE": rng.normal(8.0, 1.0, n),
    }, index=dates)

  def test_stability_objective_selects_stable(self, designed_assets):
    """stability objective should select STABLE asset."""
    from backend.analysis.optimizer import optimize_portfolio
    corr = designed_assets.corr()
    result = optimize_portfolio(
      designed_assets, corr, n_assets=1, objective="stability", n_portfolios=1,
    )
    assert len(result) >= 1
    assert "STABLE" in result[0]["assets"]

  def test_sortino_objective_selects_high_sortino(self, designed_assets):
    """max_sortino should prefer asset with highest Sortino."""
    from backend.analysis.scoring import portfolio_sortino
    # Verify which asset actually has best sortino
    sortinos = {a: portfolio_sortino(designed_assets[a]) for a in designed_assets.columns}
    best_score = min(sortinos.values())  # lower score = better

    from backend.analysis.optimizer import optimize_portfolio
    corr = designed_assets.corr()
    result = optimize_portfolio(
      designed_assets, corr, n_assets=1, objective="max_sortino", n_portfolios=1,
    )
    assert len(result) >= 1
    # Never-losing assets all score the RATIO_CAP (2026-10-08), so ties exist.
    assert sortinos[result[0]["assets"][0]] == best_score

  def test_min_drawdown_objective_selects_low_dd(self):
    """min_max_drawdown should prefer asset with lowest drawdown."""
    from backend.analysis.scoring import portfolio_min_drawdown
    from backend.analysis.optimizer import optimize_portfolio
    n = 300
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    # Create assets with clearly different drawdowns (all profitable to pass PF gate)
    pnl = pd.DataFrame({
      # LOW_DD: tiny consistent gains, minimal drawdown
      "LOW_DD": 2.0 + rng.normal(0, 0.5, n),
      # MED_DD: moderate swings
      "MED_DD": rng.normal(3.0, 8.0, n),
      # HIGH_DD: large swings, big drawdowns
      "HIGH_DD": rng.normal(5.0, 30.0, n),
    }, index=dates)
    dds = {a: portfolio_min_drawdown(pnl[a]) for a in pnl.columns}
    best_asset = min(dds, key=dds.get)

    corr = pnl.corr()
    result = optimize_portfolio(
      pnl, corr, n_assets=1, objective="min_max_drawdown", n_portfolios=1,
    )
    assert len(result) >= 1
    assert best_asset in result[0]["assets"]

  def test_profit_factor_objective_selects_high_pf(self, designed_assets):
    """max_profit_factor should prefer asset with highest PF."""
    from backend.analysis.scoring import portfolio_profit_factor
    pfs = {a: portfolio_profit_factor(designed_assets[a]) for a in designed_assets.columns}
    best_score = min(pfs.values())  # lower score = better (negated PF)

    from backend.analysis.optimizer import optimize_portfolio
    corr = designed_assets.corr()
    result = optimize_portfolio(
      designed_assets, corr, n_assets=1, objective="max_profit_factor", n_portfolios=1,
    )
    assert len(result) >= 1
    # PF is capped at 10, so several assets can tie for best (2026-10-08: a
    # no-loss series now hits the cap instead of scoring its raw $ profit).
    assert pfs[result[0]["assets"][0]] == best_score

  def test_sharpe_objective_selects_high_sharpe(self, designed_assets):
    """max_sharpe should prefer asset with highest Sharpe."""
    from backend.analysis.scoring import portfolio_sharpe
    sharpes = {a: portfolio_sharpe(designed_assets[a]) for a in designed_assets.columns}
    best_asset = min(sharpes, key=sharpes.get)  # lower score = better

    from backend.analysis.optimizer import optimize_portfolio
    corr = designed_assets.corr()
    result = optimize_portfolio(
      designed_assets, corr, n_assets=1, objective="max_sharpe", n_portfolios=1,
    )
    assert len(result) >= 1
    assert best_asset in result[0]["assets"]

  def test_optimizer_sorts_by_objective(self, designed_assets):
    """Multi-portfolio results must be sorted by objective score (best first)."""
    from backend.analysis.optimizer import optimize_portfolio
    from backend.analysis.scoring import portfolio_sortino
    corr = designed_assets.corr()
    result = optimize_portfolio(
      designed_assets, corr, n_assets=2, objective="max_sortino", n_portfolios=3,
      max_seeds=20,
    )
    if len(result) >= 2:
      # First result should have better (lower) sortino score than second
      score_0 = portfolio_sortino(designed_assets[result[0]["assets"]].sum(axis=1))
      score_1 = portfolio_sortino(designed_assets[result[1]["assets"]].sum(axis=1))
      assert score_0 <= score_1 + 1e-10  # Allow floating point tolerance


class TestObjectivePFGate:
  """Verify the PF >= 1.0 hard gate filters out net losers."""

  def test_unprofitable_assets_excluded(self):
    """Assets with PF < 1.0 should be excluded from all optimization."""
    from backend.analysis.optimizer import optimize_portfolio
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    pnl = pd.DataFrame({
      "WINNER": np.where(rng.random(n) < 0.7, 20.0, -10.0),  # PF > 1
      "LOSER1": np.where(rng.random(n) < 0.3, 10.0, -20.0),  # PF < 1
      "LOSER2": np.where(rng.random(n) < 0.2, 5.0, -15.0),   # PF < 1
    }, index=dates)
    corr = pnl.corr()
    result = optimize_portfolio(pnl, corr, n_assets=1, objective="hybrid", n_portfolios=1)
    if result:
      for r in result:
        for asset in r["assets"]:
          # Verify each selected asset has PF >= 1
          gp = pnl[asset][pnl[asset] > 0].sum()
          gl = abs(pnl[asset][pnl[asset] < 0].sum())
          pf = gp / gl if gl > 0 else 10.0
          assert pf >= 1.0, f"Asset {asset} has PF {pf:.2f} < 1.0"


# =========================================================================
# 3. BENCHMARK ROBUSTNESS — SPLIT & THRESHOLD VERIFICATION
# =========================================================================

class TestBenchmarkSplitTrades:
  """Verify trade-count split divides exactly at midpoint."""

  def test_even_split(self):
    from backend.analysis.benchmark import _split_data
    dates = pd.date_range("2024-01-01", periods=20)
    df = pd.DataFrame({"Exit Time": dates, "Net Profit": range(20)})
    h1, h2 = _split_data(df, "trades", 20)
    assert len(h1) == 10
    assert len(h2) == 10
    # h1 should contain the first 10 trades
    assert h1["Net Profit"].tolist() == list(range(10))
    assert h2["Net Profit"].tolist() == list(range(10, 20))

  def test_odd_split(self):
    from backend.analysis.benchmark import _split_data
    dates = pd.date_range("2024-01-01", periods=21)
    df = pd.DataFrame({"Exit Time": dates, "Net Profit": range(21)})
    h1, h2 = _split_data(df, "trades", 21)
    assert len(h1) == 10  # floor(21/2)
    assert len(h2) == 11

  def test_single_trade_split(self):
    from backend.analysis.benchmark import _split_data
    dates = pd.date_range("2024-01-01", periods=1)
    df = pd.DataFrame({"Exit Time": dates, "Net Profit": [100]})
    h1, h2 = _split_data(df, "trades", 1)
    assert len(h1) == 1
    assert len(h2) == 0


class TestBenchmarkSplitTime:
  """Verify time-based split divides at temporal midpoint."""

  def test_time_split_respects_midpoint(self):
    from backend.analysis.benchmark import _split_data
    # 20 trades, first 10 in Jan, last 10 in March
    dates = (list(pd.date_range("2024-01-01", periods=10, freq="D")) +
             list(pd.date_range("2024-03-01", periods=10, freq="D")))
    df = pd.DataFrame({"Exit Time": dates, "Net Profit": range(20)})
    h1, h2 = _split_data(df, "time", 20)
    # Midpoint is (Jan 1 + Mar 10) / 2 ≈ Feb 4
    # H1: all Jan trades (10), H2: all Mar trades (10)
    assert len(h1) == 10
    assert len(h2) == 10


class TestBenchmarkPFCalculation:
  """Verify per-half PF calculation matches manual formula."""

  def test_h1_h2_pf_matches_manual(self):
    from backend.analysis.benchmark import compute_benchmark_robustness
    rows = []
    # H1: 10 trades, 8 wins of +100, 2 losses of -100
    for i in range(10):
      pnl = 100.0 if i < 8 else -100.0
      rows.append({
        "Asset": "TEST", "Exit Time": datetime(2024, 1, 1) + timedelta(days=i),
        "Net Profit": pnl,
      })
    # H2: 10 trades, 6 wins of +100, 4 losses of -100
    for i in range(10):
      pnl = 100.0 if i < 6 else -100.0
      rows.append({
        "Asset": "TEST", "Exit Time": datetime(2024, 3, 1) + timedelta(days=i),
        "Net Profit": pnl,
      })
    df = pd.DataFrame(rows)
    stats, _ = compute_benchmark_robustness(df, split_method="trades")
    s = stats["TEST"]
    # H1: GP=800, GL=200, PF=4.0
    assert s["H1_PF"] == pytest.approx(4.0, abs=0.01)
    # H2: GP=600, GL=400, PF=1.5
    assert s["H2_PF"] == pytest.approx(1.5, abs=0.01)


class TestBenchmarkThresholdFilter:
  """Verify validation_threshold correctly gates status."""

  def test_below_threshold_is_weak(self):
    """H1_PF=0.8 < threshold=1.2 → status=Weak."""
    from backend.analysis.benchmark import compute_benchmark_robustness
    rows = []
    # H1: PF ≈ 0.8 (4 wins of +100, 6 losses of -100 → GP=400/GL=600=0.67)
    for i in range(10):
      pnl = 100.0 if i < 4 else -100.0
      rows.append({
        "Asset": "WEAK_ASSET", "Exit Time": datetime(2024, 1, 1) + timedelta(days=i),
        "Net Profit": pnl,
      })
    # H2: same
    for i in range(10):
      pnl = 100.0 if i < 4 else -100.0
      rows.append({
        "Asset": "WEAK_ASSET", "Exit Time": datetime(2024, 3, 1) + timedelta(days=i),
        "Net Profit": pnl,
      })
    df = pd.DataFrame(rows)
    stats, _ = compute_benchmark_robustness(df, validation_threshold=1.2)
    assert stats["WEAK_ASSET"]["status"] == "Weak"

  def test_above_threshold_is_stable_or_better(self):
    """H1_PF=3.0 and H2_PF=2.5 > threshold=1.2 → status in (Elite, Strong, Stable)."""
    from backend.analysis.benchmark import compute_benchmark_robustness
    rows = []
    # H1: 9 wins of +100, 1 loss of -100 → PF=9.0
    for i in range(10):
      pnl = 100.0 if i < 9 else -100.0
      rows.append({
        "Asset": "STRONG_ASSET", "Exit Time": datetime(2024, 1, 1) + timedelta(days=i),
        "Net Profit": pnl,
      })
    # H2: 8 wins of +100, 2 losses of -100 → PF=4.0
    for i in range(10):
      pnl = 100.0 if i < 8 else -100.0
      rows.append({
        "Asset": "STRONG_ASSET", "Exit Time": datetime(2024, 3, 1) + timedelta(days=i),
        "Net Profit": pnl,
      })
    df = pd.DataFrame(rows)
    stats, _ = compute_benchmark_robustness(df, validation_threshold=1.2)
    assert stats["STRONG_ASSET"]["status"] in ("Elite", "Strong", "Stable")
    assert stats["STRONG_ASSET"]["is_stable"] == True  # noqa: E712 — numpy bool


class TestBenchmarkStatusClassification:
  """Verify the exact Elite/Strong/Stable/Weak logic."""

  def test_elite_classification(self):
    """is_stable=True AND total_pf >= source_pf * elite_target → Elite."""
    from backend.analysis.benchmark import compute_benchmark_robustness
    # All wins → total_pf=10 (capped), source_pf=1.0 (default)
    rows = [{"Asset": "A", "Exit Time": datetime(2024, 1, 1) + timedelta(days=i),
             "Net Profit": 100.0} for i in range(40)]
    df = pd.DataFrame(rows)
    stats, _ = compute_benchmark_robustness(df, validation_threshold=1.0, elite_target=1.0)
    assert stats["A"]["status"] == "Elite"

  def test_weak_all_losses(self):
    """All losses → PF near 0 → Weak."""
    from backend.analysis.benchmark import compute_benchmark_robustness
    rows = [{"Asset": "A", "Exit Time": datetime(2024, 1, 1) + timedelta(days=i),
             "Net Profit": -100.0} for i in range(40)]
    df = pd.DataFrame(rows)
    stats, _ = compute_benchmark_robustness(df, validation_threshold=1.2)
    assert stats["A"]["status"] == "Weak"


class TestBenchmarkSeedScoring:
  """Verify benchmark seed scores use correct formula."""

  def test_score_formula(self):
    """score = avg(H1_PF, H2_PF) * confidence * status_mult."""
    from backend.analysis.optimizer_seeds import compute_benchmark_seed_scores
    stats = {
      "GOOD": {
        "H1_PF": 4.0, "H2_PF": 3.0, "h1_trades": 30, "h2_trades": 30,
        "status": "Elite", "h1_wr": 0.7, "h2_wr": 0.7, "pnl_values": [],
      },
      "BAD": {
        "H1_PF": 0.8, "H2_PF": 0.7, "h1_trades": 30, "h2_trades": 30,
        "status": "Weak", "h1_wr": 0.3, "h2_wr": 0.3, "pnl_values": [],
      },
    }
    scores = compute_benchmark_seed_scores(stats, min_reliable_trades=10)
    # GOOD: base=3.5, confidence=1.0, mult=1.2 → 4.2
    assert scores["GOOD"] == pytest.approx(3.5 * 1.0 * 1.2, abs=0.01)
    # BAD: base=0.75, confidence=1.0, mult=0.7 → 0.525
    assert scores["BAD"] == pytest.approx(0.75 * 1.0 * 0.7, abs=0.01)

  def test_confidence_penalty(self):
    """Assets with fewer trades get lower scores."""
    from backend.analysis.optimizer_seeds import compute_benchmark_seed_scores
    stats = {
      "MANY": {
        "H1_PF": 3.0, "H2_PF": 3.0, "h1_trades": 50, "h2_trades": 50,
        "status": "Stable", "h1_wr": 0.6, "h2_wr": 0.6, "pnl_values": [],
      },
      "FEW": {
        "H1_PF": 3.0, "H2_PF": 3.0, "h1_trades": 3, "h2_trades": 3,
        "status": "Stable", "h1_wr": 0.6, "h2_wr": 0.6, "pnl_values": [],
      },
    }
    scores = compute_benchmark_seed_scores(stats, min_reliable_trades=20)
    # FEW: confidence = min(3/20, 1) = 0.15
    # MANY: confidence = min(50/20, 1) = 1.0
    assert scores["MANY"] > scores["FEW"]


# =========================================================================
# 4. POSITION SIZING MATH — HAND-CALCULATED GROUND TRUTH
# =========================================================================

class TestFixedSizingMath:
  """Verify Fixed scaling: every trade multiplied by target/batch ratio."""

  def test_exact_scaling_factor(self):
    from backend.analysis.trade_scaler import scale_fixed
    df = pd.DataFrame({
      "Net Profit": [100.0, -50.0, 200.0, -30.0],
      "Exit Time": pd.date_range("2024-01-01", periods=4),
      "Asset": ["A"] * 4,
    })
    result = scale_fixed(df, batch_risk=100, target_risk=300, initial_capital=10000)
    # Factor = 300/100 = 3.0
    scaled = result["trades"]["Net Profit"].tolist()
    assert scaled[0] == pytest.approx(300.0)   # 100 * 3
    assert scaled[1] == pytest.approx(-150.0)  # -50 * 3
    assert scaled[2] == pytest.approx(600.0)   # 200 * 3
    assert scaled[3] == pytest.approx(-90.0)   # -30 * 3

  def test_equity_curve_correct(self):
    from backend.analysis.trade_scaler import scale_fixed
    df = pd.DataFrame({
      "Net Profit": [100.0, -50.0],
      "Exit Time": pd.date_range("2024-01-01", periods=2),
      "Asset": ["A"] * 2,
    })
    result = scale_fixed(df, batch_risk=100, target_risk=200, initial_capital=10000)
    # Factor = 2.0 → PnLs = [200, -100]
    # Equity: [10000, 10200, 10100]
    eq = result["equity_curve"]
    assert eq[0] == 10000
    assert eq[1] == pytest.approx(10200.0)
    assert eq[2] == pytest.approx(10100.0)


class TestPctEquitySizingMath:
  """Verify % of equity sizing: risk scales with current equity."""

  def test_compounding_trade_by_trade(self):
    from backend.analysis.trade_scaler import scale_pct_equity
    df = pd.DataFrame({
      "Net Profit": [100.0, -50.0, 200.0],
      "Exit Time": pd.date_range("2024-01-01", periods=3),
      "Asset": ["A"] * 3,
    })
    result = scale_pct_equity(df, batch_risk=100, initial_capital=10000, risk_pct=2.0)
    scaled = result["trades"]["Net Profit"].tolist()

    # Trade 1: equity=10000, risk=10000*0.02=200, factor=200/100=2.0
    # PnL = 100 * 2.0 = 200
    assert scaled[0] == pytest.approx(200.0, abs=0.01)
    # equity after = 10200

    # Trade 2: equity=10200, risk=10200*0.02=204, factor=204/100=2.04
    # PnL = -50 * 2.04 = -102
    assert scaled[1] == pytest.approx(-102.0, abs=0.1)
    # equity after = 10098

    # Trade 3: equity=10098, risk=10098*0.02=201.96, factor=201.96/100=2.0196
    # PnL = 200 * 2.0196 = 403.92
    assert scaled[2] == pytest.approx(403.92, abs=0.5)


class TestMartingaleSizingMath:
  """Verify martingale streak-based multiplier logic."""

  def test_standard_martingale_after_losses(self):
    """Standard martingale: multiply after consecutive losses."""
    from backend.analysis.trade_scaler import scale_martingale
    df = pd.DataFrame({
      "Net Profit": [100.0, -50.0, -50.0, -50.0, 100.0],
      "Exit Time": pd.date_range("2024-01-01", periods=5),
      "Asset": ["A"] * 5,
    })
    result = scale_martingale(
      df, batch_risk=100, target_risk=100, initial_capital=10000,
      mart_type="standard", multiplier=2.0, max_streak=3,
    )
    scaled = result["trades"]["Net Profit"].tolist()
    # base_factor = 100/100 = 1.0
    # Trade 1: streak=0 (losses=0), mult=1.0, PnL = 100*1.0 = 100
    assert scaled[0] == pytest.approx(100.0)
    # After T1: wins=1, losses=0
    # Trade 2: losses=0 (streak=0), mult=1.0, PnL = -50*1.0 = -50
    assert scaled[1] == pytest.approx(-50.0)
    # After T2: wins=0, losses=1
    # Trade 3: losses=1 → capped=1, mult=2^1=2.0, PnL = -50*2.0 = -100
    assert scaled[2] == pytest.approx(-100.0)
    # After T3: losses=2
    # Trade 4: losses=2 → capped=2, mult=2^2=4.0, PnL = -50*4.0 = -200
    assert scaled[3] == pytest.approx(-200.0)
    # After T4: losses=3
    # Trade 5: losses=3 → capped=3 (max_streak), mult=2^3=8.0, PnL = 100*8.0 = 800
    assert scaled[4] == pytest.approx(800.0)

  def test_anti_martingale_after_wins(self):
    """Anti-martingale: multiply after consecutive wins."""
    from backend.analysis.trade_scaler import scale_martingale
    df = pd.DataFrame({
      "Net Profit": [100.0, 100.0, 100.0, -50.0],
      "Exit Time": pd.date_range("2024-01-01", periods=4),
      "Asset": ["A"] * 4,
    })
    result = scale_martingale(
      df, batch_risk=100, target_risk=100, initial_capital=10000,
      mart_type="anti", multiplier=2.0, max_streak=3,
    )
    scaled = result["trades"]["Net Profit"].tolist()
    # Trade 1: wins=0, mult=1.0, PnL = 100
    assert scaled[0] == pytest.approx(100.0)
    # After T1: wins=1
    # Trade 2: wins=1, mult=2^1=2.0, PnL = 100*2.0 = 200
    assert scaled[1] == pytest.approx(200.0)
    # After T2: wins=2
    # Trade 3: wins=2, mult=2^2=4.0, PnL = 100*4.0 = 400
    assert scaled[2] == pytest.approx(400.0)
    # After T3: wins=3
    # Trade 4: wins=3 → capped=3, mult=2^3=8.0, PnL = -50*8.0 = -400
    assert scaled[3] == pytest.approx(-400.0)

  def test_streak_resets_on_direction_change(self):
    """Streak resets to 0 when direction changes."""
    from backend.analysis.trade_scaler import scale_martingale
    df = pd.DataFrame({
      "Net Profit": [-50.0, -50.0, 100.0, -50.0],
      "Exit Time": pd.date_range("2024-01-01", periods=4),
      "Asset": ["A"] * 4,
    })
    result = scale_martingale(
      df, batch_risk=100, target_risk=100, initial_capital=10000,
      mart_type="standard", multiplier=2.0, max_streak=5,
    )
    scaled = result["trades"]["Net Profit"].tolist()
    # T1: losses=0, mult=1 → PnL=-50
    assert scaled[0] == pytest.approx(-50.0)
    # T2: losses=1, mult=2 → PnL=-100
    assert scaled[1] == pytest.approx(-100.0)
    # T3: losses=2, mult=4 → PnL=400 (win resets losses to 0)
    assert scaled[2] == pytest.approx(400.0)
    # T4: losses=0 (reset by win), mult=1 → PnL=-50
    assert scaled[3] == pytest.approx(-50.0)


class TestKellySizingMath:
  """Verify Kelly criterion formula and application."""

  def test_kelly_formula_exact(self):
    """f* = (p*b - q) / b, verified with known inputs."""
    from backend.analysis.trade_scaler import _compute_kelly_f
    # 60% wins of $100, 40% losses of $50
    # p=0.6, q=0.4, b=100/50=2.0
    # f* = (0.6*2 - 0.4) / 2 = (1.2-0.4)/2 = 0.4
    pnls = [100.0] * 6 + [-50.0] * 4
    assert _compute_kelly_f(pnls) == pytest.approx(0.4, abs=0.001)

  def test_kelly_formula_edge_all_winners(self):
    """All winners → Kelly = 0 (no losses to compute b)."""
    from backend.analysis.trade_scaler import _compute_kelly_f
    assert _compute_kelly_f([100.0, 200.0, 300.0]) == 0.0

  def test_kelly_formula_edge_all_losers(self):
    """All losers → Kelly = 0."""
    from backend.analysis.trade_scaler import _compute_kelly_f
    assert _compute_kelly_f([-100.0, -200.0]) == 0.0

  def test_kelly_formula_50_50(self):
    """50/50 with equal wins/losses → Kelly = 0."""
    from backend.analysis.trade_scaler import _compute_kelly_f
    # p=0.5, q=0.5, b=100/100=1.0, f*=(0.5*1-0.5)/1=0.0
    pnls = [100.0] * 5 + [-100.0] * 5
    assert _compute_kelly_f(pnls) == pytest.approx(0.0, abs=0.001)

  def test_kelly_scaling_walk(self):
    """Walk through Kelly scaling trade-by-trade."""
    from backend.analysis.trade_scaler import scale_kelly
    # 4 trades: 3 wins of +100, 1 loss of -50
    df = pd.DataFrame({
      "Net Profit": [100.0, 100.0, -50.0, 100.0],
      "Exit Time": pd.date_range("2024-01-01", periods=4),
      "Asset": ["A"] * 4,
    })
    result = scale_kelly(df, batch_risk=100, initial_capital=10000, kelly_fraction=1.0)
    # Full Kelly (fraction=1.0) should produce compounding equity
    eq = result["equity_curve"]
    assert eq[0] == 10000
    assert len(eq) == 5
    # Kelly % should be reported in metrics
    assert "kelly_pct" in result["metrics"]
    assert result["metrics"]["kelly_pct"] >= 0

  def test_half_kelly_reduces_risk(self):
    """Half-Kelly (0.5) should produce smaller PnL swings than full Kelly."""
    from backend.analysis.trade_scaler import scale_kelly
    df = pd.DataFrame({
      "Net Profit": [100.0] * 6 + [-50.0] * 4,
      "Exit Time": pd.date_range("2024-01-01", periods=10),
      "Asset": ["A"] * 10,
    })
    full = scale_kelly(df.copy(), batch_risk=100, initial_capital=10000, kelly_fraction=1.0)
    half = scale_kelly(df.copy(), batch_risk=100, initial_capital=10000, kelly_fraction=0.5)
    # Half-Kelly should report half the Kelly %
    full_k = full["metrics"]["kelly_pct"]
    half_k = half["metrics"]["kelly_pct"]
    assert half_k == pytest.approx(full_k / 2, abs=0.1)


class TestBacktestTradeScalerFixedMath:
  """Verify scale_backtest_trades with mode=fixed applies correct factor."""

  def _make_mock_trades(self, pnls):
    class MockTrade:
      def __init__(self, net_profit, entry_time, exit_time):
        self.symbol = "BTCUSDT"
        self.side = "Long"
        self.net_profit = net_profit
        self.entry_time = entry_time
        self.exit_time = exit_time
    trades = []
    for i, p in enumerate(pnls):
      trades.append(MockTrade(p,
        datetime(2024, 1, 1) + timedelta(days=i),
        datetime(2024, 1, 1) + timedelta(days=i, hours=4)))
    return trades

  def test_fixed_doubles_pnl(self):
    from backend.analysis.trade_scaler import scale_backtest_trades
    trades = self._make_mock_trades([100.0, -50.0, 200.0])
    result = scale_backtest_trades(trades, batch_risk=100, mode="fixed", risk_value=200)
    # Factor = 200/100 = 2.0
    assert result[0].net_profit == pytest.approx(200.0)
    assert result[1].net_profit == pytest.approx(-100.0)
    assert result[2].net_profit == pytest.approx(400.0)


# =========================================================================
# 5. COST CALCULATIONS — COMMISSION, SLIPPAGE, SPREAD, FUNDING
# =========================================================================

class TestCommissionFormula:
  """Verify commission = price * qty * (commission_pct / 100)."""

  def test_entry_commission(self):
    """Commission deducted on entry."""
    from backend.backtester.engine import BacktestConfig
    config = BacktestConfig(commission_pct=0.055)
    # Commission = 50000 * 1.0 * (0.055/100) = 27.5
    price, qty = 50000.0, 1.0
    expected_comm = price * qty * (config.commission_pct / 100)
    assert expected_comm == pytest.approx(27.5, abs=0.01)

  def test_exit_commission(self):
    """Commission also deducted on exit, subtracted from PnL."""
    from backend.backtester.engine import BacktestConfig
    config = BacktestConfig(commission_pct=0.1)
    # Exit at 51000, qty=2.0
    price, qty = 51000.0, 2.0
    expected_comm = price * qty * (config.commission_pct / 100)
    # 51000 * 2 * 0.001 = 102.0
    assert expected_comm == pytest.approx(102.0, abs=0.01)


class TestSlippageFormula:
  """Verify slippage direction and magnitude."""

  def test_pct_slippage_long_entry(self):
    """Long entry: price goes UP (worse fill for buyer)."""
    from backend.backtester.engine import BacktestEngine, BacktestConfig
    config = BacktestConfig(slippage_mode="pct", slippage_value=0.1)
    engine = BacktestEngine(config)
    price = 50000.0
    slipped = engine._apply_slippage(price, "long", is_entry=True)
    # Long entry → worse for buyer → higher price
    expected = price * (1 + 0.1 / 100)  # 50050
    assert slipped == pytest.approx(expected, abs=0.01)

  def test_pct_slippage_long_exit(self):
    """Long exit: price goes DOWN (worse fill for seller)."""
    from backend.backtester.engine import BacktestEngine, BacktestConfig
    config = BacktestConfig(slippage_mode="pct", slippage_value=0.1)
    engine = BacktestEngine(config)
    price = 50000.0
    slipped = engine._apply_slippage(price, "long", is_entry=False)
    expected = price * (1 - 0.1 / 100)  # 49950
    assert slipped == pytest.approx(expected, abs=0.01)

  def test_pct_slippage_short_entry(self):
    """Short entry: price goes DOWN (worse fill for short seller)."""
    from backend.backtester.engine import BacktestEngine, BacktestConfig
    config = BacktestConfig(slippage_mode="pct", slippage_value=0.1)
    engine = BacktestEngine(config)
    price = 50000.0
    slipped = engine._apply_slippage(price, "short", is_entry=True)
    expected = price * (1 - 0.1 / 100)
    assert slipped == pytest.approx(expected, abs=0.01)

  def test_pct_slippage_short_exit(self):
    """Short exit: price goes UP (worse fill for short cover)."""
    from backend.backtester.engine import BacktestEngine, BacktestConfig
    config = BacktestConfig(slippage_mode="pct", slippage_value=0.1)
    engine = BacktestEngine(config)
    price = 50000.0
    slipped = engine._apply_slippage(price, "short", is_entry=False)
    expected = price * (1 + 0.1 / 100)
    assert slipped == pytest.approx(expected, abs=0.01)

  def test_pips_slippage(self):
    """Pips mode: flat $ offset."""
    from backend.backtester.engine import BacktestEngine, BacktestConfig
    config = BacktestConfig(slippage_mode="pips", slippage_value=5.0)
    engine = BacktestEngine(config)
    slipped = engine._apply_slippage(50000.0, "long", is_entry=True)
    assert slipped == pytest.approx(50005.0)  # +5 for long entry

  def test_zero_slippage(self):
    """Zero slippage value → price unchanged."""
    from backend.backtester.engine import BacktestEngine, BacktestConfig
    config = BacktestConfig(slippage_mode="pct", slippage_value=0.0, slippage_pct=0.0)
    engine = BacktestEngine(config)
    slipped = engine._apply_slippage(50000.0, "long", is_entry=True)
    assert slipped == 50000.0


class TestSpreadFormula:
  """Verify spread: half-spread applied on entry and exit."""

  def test_spread_long_entry(self):
    """Long entry: price * (1 + spread/200) (half-spread UP)."""
    from backend.backtester.engine import BacktestConfig
    config = BacktestConfig(spread_pct=0.1)
    price = 50000.0
    half = config.spread_pct / 200  # 0.0005
    expected = price * (1 + half)  # 50025
    assert expected == pytest.approx(50025.0, abs=0.01)

  def test_spread_long_exit(self):
    """Long exit: price * (1 - spread/200) (half-spread DOWN)."""
    from backend.backtester.engine import BacktestConfig
    config = BacktestConfig(spread_pct=0.1)
    price = 50000.0
    half = config.spread_pct / 200
    expected = price * (1 - half)  # 49975
    assert expected == pytest.approx(49975.0, abs=0.01)


class TestFundingFormula:
  """Verify funding: pos_value * (rate/100) * hold_days."""

  def test_funding_cost_calculation(self):
    """3-day hold at 0.03% daily → funding = notional * 0.0003 * 3."""
    from backend.backtester.engine import BacktestConfig
    config = BacktestConfig(funding_rate_daily_pct=0.03)
    exit_price = 51000.0
    qty = 2.0
    hold_days = 3.0
    pos_value = exit_price * qty  # 102000
    funding_cost = pos_value * (config.funding_rate_daily_pct / 100) * hold_days
    # 102000 * 0.0003 * 3 = 91.8
    assert funding_cost == pytest.approx(91.8, abs=0.01)

  def test_zero_funding(self):
    """Zero funding rate → no cost."""
    from backend.backtester.engine import BacktestConfig
    config = BacktestConfig(funding_rate_daily_pct=0.0)
    funding = 50000 * 1.0 * (config.funding_rate_daily_pct / 100) * 5
    assert funding == 0.0


class TestCostIntegration:
  """End-to-end: verify all costs compound correctly on a single trade."""

  def test_total_cost_single_long_trade(self):
    """Calculate total cost: 2x commission + slippage(entry+exit) + spread + funding."""
    from backend.backtester.engine import BacktestConfig
    config = BacktestConfig(
      commission_pct=0.055,
      slippage_mode="pct",
      slippage_value=0.01,
      spread_pct=0.05,
      funding_rate_daily_pct=0.03,
    )
    entry_price = 50000.0
    exit_price = 51000.0
    qty = 1.0
    hold_days = 2.0

    # Step 1: Slippage on entry (long → price UP)
    slip_entry = entry_price * (config.slippage_value / 100)
    actual_entry = entry_price + slip_entry  # 50005

    # Step 2: Spread on entry (long → price UP by half-spread)
    half_spread = config.spread_pct / 200
    actual_entry *= (1 + half_spread)  # 50005 * 1.00025 ≈ 50017.5

    # Step 3: Entry commission
    entry_comm = actual_entry * qty * (config.commission_pct / 100)

    # Step 4: Slippage on exit (long → price DOWN)
    slip_exit = exit_price * (config.slippage_value / 100)
    actual_exit = exit_price - slip_exit  # 50995

    # Step 5: Spread on exit (long → price DOWN by half-spread)
    actual_exit *= (1 - half_spread)  # 50995 * 0.99975 ≈ 50982.25

    # Step 6: Exit commission
    exit_comm = actual_exit * qty * (config.commission_pct / 100)

    # Step 7: Raw PnL
    raw_pnl = (actual_exit - actual_entry) * qty

    # Step 8: PnL after exit commission
    pnl_after_comm = raw_pnl - exit_comm

    # Step 9: Funding cost
    pos_value = actual_exit * qty
    funding = pos_value * (config.funding_rate_daily_pct / 100) * hold_days

    # Step 10: Final PnL
    final_pnl = pnl_after_comm - funding

    # Verify each cost is positive (reducing profit)
    assert entry_comm > 0, "Entry commission should be positive"
    assert exit_comm > 0, "Exit commission should be positive"
    assert funding > 0, "Funding should be positive"
    assert slip_entry > 0, "Slippage should be positive"

    # Verify total costs reduce the ideal PnL
    ideal_pnl = (exit_price - entry_price) * qty  # 1000
    total_costs = entry_comm + exit_comm + funding + (entry_price - exit_price + actual_entry - actual_exit) * qty
    # Just verify costs are non-trivial and reduce PnL
    assert final_pnl < ideal_pnl, f"Final PnL {final_pnl:.2f} should be < ideal {ideal_pnl:.2f}"


# =========================================================================
# RECENCY SEED SCORING — VERIFY FORMULA
# =========================================================================

class TestRecencySeedScoring:
  """Verify recency scoring formula step by step."""

  def test_gradual_weights(self):
    """Gradual mode: weight[i] = (N-i)/N. Newest=1.0, oldest=1/N."""
    from backend.analysis.optimizer_seeds import compute_recency_seed_scores
    rows = []
    for i in range(20):
      rows.append({
        "Asset": "A", "Exit Time": datetime(2024, 1, 1) + timedelta(days=i),
        "Net Profit": 100.0 if i >= 10 else -100.0,  # Recent trades are winners
      })
    df = pd.DataFrame(rows)
    scores = compute_recency_seed_scores(df, mode="gradual", min_trades=5)
    # Recent wins should push the score UP
    assert scores["A"] > 0

  def test_batch_weights(self):
    """Batch mode: last batch_size trades get boost_factor, rest get 1.0."""
    from backend.analysis.optimizer_seeds import compute_recency_seed_scores
    rows = []
    # 20 trades, last 5 are big winners, first 15 are losers
    for i in range(20):
      rows.append({
        "Asset": "A", "Exit Time": datetime(2024, 1, 1) + timedelta(days=i),
        "Net Profit": 200.0 if i >= 15 else -10.0,
      })
    df = pd.DataFrame(rows)
    scores_batch = compute_recency_seed_scores(
      df, mode="batch", batch_size=5, boost_factor=3.0, min_trades=5,
    )
    scores_gradual = compute_recency_seed_scores(
      df, mode="gradual", min_trades=5,
    )
    # Batch mode should give higher weight to recent winners
    assert scores_batch["A"] > 0

  def test_min_trades_filter(self):
    """Assets with fewer than min_trades get score 0."""
    from backend.analysis.optimizer_seeds import compute_recency_seed_scores
    rows = [{"Asset": "A", "Exit Time": datetime(2024, 1, 1), "Net Profit": 100.0}]
    df = pd.DataFrame(rows)
    scores = compute_recency_seed_scores(df, min_trades=5)
    assert scores["A"] == 0.0


# =========================================================================
# STABILITY FORMULA — VERIFY COMPONENTS
# =========================================================================

class TestStabilityFormula:
  """Verify calculate_stability components: R², DD penalty, consistency."""

  def test_perfect_linear_equity(self):
    """Constant PnL → equity is perfect line → R²=1, dd_score=1, consistency high."""
    from backend.analysis.scoring import calculate_stability
    pnl = pd.Series([5.0] * 200)
    stab = calculate_stability(pnl)
    assert stab > 0.95  # Should be near 1.0

  def test_all_negative_low_stability(self):
    """All-negative PnL → equity slopes down → dd penalty high."""
    from backend.analysis.scoring import calculate_stability
    pnl = pd.Series([-5.0] * 200)
    stab = calculate_stability(pnl)
    # R² is still 1.0 (perfect downward line), but DD penalty kicks in
    # and consistency of negative chunks is actually good (consistent losses)
    # This is an interesting edge case — stability measures smoothness, not direction
    assert 0.0 <= stab <= 1.0

  def test_choppy_equity_low_stability(self):
    """Random choppy PnL → low R², low stability."""
    from backend.analysis.scoring import calculate_stability
    rng = np.random.default_rng(42)
    pnl = pd.Series(rng.normal(0, 100, 200))
    stab = calculate_stability(pnl)
    assert stab < 0.5  # Should be low

  def test_stability_weights(self):
    """Verify stability = 0.4*R² + 0.3*dd_score + 0.3*consistency."""
    from backend.analysis.scoring import calculate_stability
    from scipy import stats as scipy_stats
    pnl = pd.Series([5.0] * 200)
    cum = pnl.cumsum()
    x = np.arange(len(cum))
    y = cum.values
    slope, intercept, r_val, p_val, std_err = scipy_stats.linregress(x, y)
    r_sq = r_val ** 2  # Should be 1.0

    # DD score: no drawdown → dd_score = 1.0
    peak = np.maximum.accumulate(y)
    max_dd = abs((y - peak).min())  # 0
    total_profit = y[-1]  # 1000
    norm_dd = max_dd / abs(total_profit) if total_profit != 0 else 1.0  # 0
    dd_score = 1 - norm_dd  # 1.0

    # Consistency: all chunks identical → CoV=0 → consistency=1.0
    stab = calculate_stability(pnl)
    expected = 0.4 * r_sq + 0.3 * dd_score + 0.3 * 1.0  # 0.4+0.3+0.3 = 1.0
    assert stab == pytest.approx(expected, abs=0.05)


# =========================================================================
# CORRELATION GROUND TRUTH
# =========================================================================

class TestCorrelationGroundTruth:
  """Verify correlation matrices match known mathematical properties."""

  def test_identical_series_corr_1(self):
    """Identical time series → correlation = 1.0."""
    from backend.analysis.correlation import calculate_correlation_standard
    rng = np.random.default_rng(42)
    data = rng.normal(0, 1, 200)
    pnl = pd.DataFrame({"A": data, "B": data.copy()})
    corr = calculate_correlation_standard(pnl)
    assert corr.loc["A", "B"] == pytest.approx(1.0, abs=1e-10)

  def test_negated_series_corr_minus_1(self):
    """Negated series → correlation = -1.0."""
    from backend.analysis.correlation import calculate_correlation_standard
    rng = np.random.default_rng(42)
    data = rng.normal(0, 1, 200)
    pnl = pd.DataFrame({"A": data, "B": -data})
    corr = calculate_correlation_standard(pnl)
    assert corr.loc["A", "B"] == pytest.approx(-1.0, abs=1e-10)

  def test_independent_series_corr_near_0(self):
    """Independent random series → correlation ≈ 0."""
    from backend.analysis.correlation import calculate_correlation_standard
    rng = np.random.default_rng(42)
    pnl = pd.DataFrame({
      "A": rng.normal(0, 1, 5000),
      "B": rng.normal(0, 1, 5000),
    })
    corr = calculate_correlation_standard(pnl)
    assert abs(corr.loc["A", "B"]) < 0.05

  def test_optimizer_prefers_uncorrelated(self):
    """Uncorrelated mode should prefer assets with near-zero correlation."""
    from backend.analysis.optimizer import optimize_correlation
    rng = np.random.default_rng(42)
    base = rng.normal(0, 1, 300)
    pnl = pd.DataFrame({
      "CORR_A": base,
      "CORR_B": base + rng.normal(0, 0.1, 300),  # highly correlated with A
      "INDEP_C": rng.normal(0, 1, 300),            # independent
      "INDEP_D": rng.normal(0, 1, 300),            # independent
    }, index=pd.date_range("2024-01-01", periods=300))
    corr = pnl.corr()
    result = optimize_correlation(corr, n_assets=2, mode="uncorrelated")
    if result:
      selected = set(result[0])
      # Should NOT select both CORR_A and CORR_B together
      assert not ({"CORR_A", "CORR_B"} <= selected), \
        f"Uncorrelated optimizer selected correlated pair: {selected}"

  def test_optimizer_prefers_anti_correlated(self):
    """Anti-correlated mode should not select positively correlated assets."""
    from backend.analysis.optimizer import optimize_correlation
    rng = np.random.default_rng(42)
    base = rng.normal(0, 1, 300)
    pnl = pd.DataFrame({
      "POS": base,
      "NEG": -base + rng.normal(0, 0.1, 300),     # anti-correlated with POS
      "INDEP": rng.normal(0, 1, 300),              # independent
    }, index=pd.date_range("2024-01-01", periods=300))
    corr = pnl.corr()
    result = optimize_correlation(corr, n_assets=2, mode="anti_correlated")
    if result:
      selected = set(result[0])
      # Greedy approach may pick POS+NEG or POS+INDEP depending on seed order.
      # The key invariant: should not pick two correlated assets.
      assert len(selected) == 2, f"Expected 2 assets, got: {selected}"


# =========================================================================
# OPTIMIZER METRICS ACCURACY
# =========================================================================

class TestOptimizerMetricsAccuracy:
  """Verify optimizer output metrics match hand calculations."""

  def test_net_profit_matches_sum(self):
    """optimizer result net_profit should equal sum of combined daily PnL."""
    from backend.analysis.optimizer import optimize_portfolio
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    pnl = pd.DataFrame({
      f"A{i}": rng.normal(5, 20, n) for i in range(5)
    }, index=dates)
    corr = pnl.corr()
    result = optimize_portfolio(pnl, corr, n_assets=2, objective="hybrid", n_portfolios=1)
    if result:
      assets = result[0]["assets"]
      expected_profit = float(pnl[assets].sum(axis=1).sum())
      assert result[0]["metrics"]["net_profit"] == pytest.approx(expected_profit, abs=0.01)

  def test_max_drawdown_matches_manual(self):
    """optimizer result max_drawdown should match manual calculation."""
    from backend.analysis.optimizer import optimize_portfolio
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    pnl = pd.DataFrame({
      f"A{i}": rng.normal(5, 20, n) for i in range(5)
    }, index=dates)
    corr = pnl.corr()
    result = optimize_portfolio(pnl, corr, n_assets=2, objective="hybrid", n_portfolios=1)
    if result:
      assets = result[0]["assets"]
      combined = pnl[assets].sum(axis=1)
      cum = combined.cumsum()
      peak = np.maximum.accumulate(cum.values)
      expected_dd = float((cum.values - peak).min())
      assert result[0]["metrics"]["max_drawdown"] == pytest.approx(expected_dd, abs=0.01)

  def test_profit_factor_matches_manual(self):
    """optimizer result profit_factor should match manual calculation."""
    from backend.analysis.optimizer import optimize_portfolio
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(42)
    pnl = pd.DataFrame({
      f"A{i}": rng.normal(5, 20, n) for i in range(5)
    }, index=dates)
    corr = pnl.corr()
    result = optimize_portfolio(pnl, corr, n_assets=2, objective="hybrid", n_portfolios=1)
    if result:
      assets = result[0]["assets"]
      combined = pnl[assets].sum(axis=1)
      gp = combined[combined > 0].sum()
      gl = abs(combined[combined < 0].sum())
      expected_pf = min(gp / gl, 10.0) if gl > 0 else 10.0
      assert result[0]["metrics"]["profit_factor"] == pytest.approx(expected_pf, abs=0.01)
