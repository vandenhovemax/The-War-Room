"""Batch Selection — Spec-compatible scoring metrics.

All metrics follow the spec's conventions:
  - Higher = better (opposite of scoring.py's lower = better)
  - Non-zero PnL days < 20 → -999 (skip)
  - No negative days → 10.0 if positive, else 0
  - Division by zero → 10.0 if numerator > 0, else 0

These are INTENTIONALLY separate from scoring.py because:
  - scoring.py uses lower=better (negated for minimization)
  - scoring.py's lake_ratio counts DAYS underwater; spec weights by DEPTH
  - scoring.py's tail_ratio includes zero-PnL days; spec filters zeros
  - scoring.py's stability is blended; spec is pure R²
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_SKIP = -999.0
_MIN_TRADING_DAYS = 10  # Match research scripts (was 20, too restrictive for 3mo CAL windows)


def _active_days(pnl: pd.Series) -> int:
  """Count non-zero PnL days.

  Defensive note: every public metric in this module calls this first and
  returns ``_SKIP`` when the count is below ``_MIN_TRADING_DAYS`` (10).
  This is the single guard that protects the downstream ``equity.iloc[-1]``
  reads — an empty series has ``_active_days == 0 < 10``, so iloc[-1] is
  never reached on an empty input.
  """
  if pnl.empty:
    return 0
  return int((pnl != 0).sum())


def _safe_div(numerator: float, denominator: float) -> float:
  """Division with spec edge-case handling."""
  if denominator == 0 or np.isnan(denominator):
    return 10.0 if numerator > 0 else 0.0
  result = numerator / denominator
  if np.isinf(result) or np.isnan(result):
    return 10.0 if numerator > 0 else 0.0
  return result


def metric_net_pnl(pnl: pd.Series) -> float:
  """Sum of daily PnL."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  return float(pnl.sum())


def metric_lake_ratio(pnl: pd.Series) -> float:
  """Area-weighted lake ratio: 1 - underwater_area / (underwater + equity_area).

  Measures fraction of equity-curve area that is above water.
  """
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum()
  hw = equity.cummax()
  underwater = (hw - equity).sum()
  total_area = equity.abs().sum()
  denom = underwater + total_area
  if denom == 0:
    return 10.0 if equity.iloc[-1] > 0 else 0.0
  return float(1.0 - underwater / denom)


def metric_tail_ratio(pnl: pd.Series) -> float:
  """p95/|p5| on non-zero days only (zeros filtered first)."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0]
  if len(active) == 0:
    return 0.0
  p95 = float(np.percentile(active, 95))
  p5 = float(np.percentile(active, 5))
  neg = active[active < 0]
  if len(neg) == 0:
    return 10.0 if p95 > 0 else 0.0
  return _safe_div(p95, abs(p5))


def metric_sortino(pnl: pd.Series) -> float:
  """mean(active) / downside_deviation on non-zero days only."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0]
  mean_ret = float(active.mean())
  downside = active[active < 0]
  if len(downside) == 0:
    return 10.0 if mean_ret > 0 else 0.0
  dd = float(np.sqrt(np.mean(downside.values ** 2)))
  return _safe_div(mean_ret, dd)


def metric_calmar(pnl: pd.Series) -> float:
  """equity[-1] / |max_drawdown|."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum()
  final = float(equity.iloc[-1])
  drawdown = equity - equity.cummax()
  max_dd = float(drawdown.min())
  if max_dd == 0:
    return 10.0 if final > 0 else 0.0
  return _safe_div(final, abs(max_dd))


def metric_pf(pnl: pd.Series) -> float:
  """Profit factor: min(gross_profit / gross_loss, 10.0)."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  gains = float(pnl[pnl > 0].sum())
  losses = float(pnl[pnl < 0].sum())
  if losses == 0:
    return 10.0 if gains > 0 else 0.0
  return min(_safe_div(gains, abs(losses)), 10.0)


def metric_gain_pain(pnl: pd.Series) -> float:
  """sum(pnl) / |sum(losses)|."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  total = float(pnl.sum())
  losses = float(pnl[pnl < 0].sum())
  if losses == 0:
    return 10.0 if total > 0 else 0.0
  return _safe_div(total, abs(losses))


def metric_expectancy(pnl: pd.Series) -> float:
  """(wr * avg_win - (1-wr) * avg_loss) / avg_loss.

  Win rate computed on non-zero days only.
  """
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0]
  if len(active) == 0:
    return 0.0
  wins = active[active > 0]
  losses_s = active[active < 0]
  if len(losses_s) == 0:
    return 10.0 if len(wins) > 0 else 0.0
  wr = len(wins) / len(active)
  avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
  avg_loss = float(abs(losses_s.mean()))
  numerator = wr * avg_win - (1 - wr) * avg_loss
  return _safe_div(numerator, avg_loss)


def metric_stability(pnl: pd.Series) -> float:
  """Pure R² of equity curve regression. Returns 0 if slope <= 0."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum().values
  if len(equity) < 2:
    return 0.0
  x = np.arange(len(equity), dtype=float)
  # Linear regression slope check
  slope = np.polyfit(x, equity, 1)[0]
  if slope <= 0:
    return 0.0
  # Pure R² = pearson_correlation(x, equity)²
  corr = np.corrcoef(x, equity)[0, 1]
  if np.isnan(corr):
    return 0.0
  return float(corr ** 2)


def metric_robust_sortino(pnl: pd.Series) -> float:
  """Median-based Sortino on non-zero days: median(active) / downside_dev."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0]
  med = float(np.median(active.values))
  downside = active[active < 0]
  if len(downside) == 0:
    return 10.0 if med > 0 else 0.0
  dd = float(np.sqrt(np.mean(downside.values ** 2)))
  return _safe_div(med, dd)


# ── Additional metrics (ported from scoring.py, higher=better) ──


def metric_sharpe(pnl: pd.Series) -> float:
  """Annualised Sharpe on non-zero days: mean/std * sqrt(365)."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0]
  mean_ret = float(active.mean())
  std_ret = float(active.std())
  if std_ret <= 1e-12 * max(abs(mean_ret), 1.0):  # float-constant series → std ~1e-18
    return 10.0 if mean_ret > 0 else 0.0
  return mean_ret / std_ret * (365 ** 0.5)


def metric_ulcer(pnl: pd.Series) -> float:
  """Inverted Ulcer Index: -RMS(drawdown%).

  Lower ulcer = better, so we negate for higher=better convention.
  Uses the shared scoring.ulcer_index() reference scale: dividing by the
  running peak (with a $1 floor) used to score a window whose first trade
  lost in "$ x 100" instead of %, ~100x worse than the same $ drawdown
  after a win.
  """
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  from backend.analysis.scoring import ulcer_index
  equity = pnl.cumsum().values
  return -ulcer_index(equity, np.maximum.accumulate(equity))


def metric_recovery(pnl: pd.Series) -> float:
  """Recovery Factor: net_pnl / |max_drawdown|."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum()
  total = float(equity.iloc[-1])
  dd = equity - equity.cummax()
  max_dd = float(dd.min())
  if max_dd == 0:
    return 10.0 if total > 0 else 0.0
  return _safe_div(total, abs(max_dd))


def metric_payoff(pnl: pd.Series) -> float:
  """Payoff Ratio: avg_win / avg_loss."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0]
  wins = active[active > 0]
  losses = active[active < 0]
  if len(wins) == 0:
    return 0.0
  if len(losses) == 0:
    return 10.0
  return min(_safe_div(float(wins.mean()), abs(float(losses.mean()))), 10.0)


def metric_cde(pnl: pd.Series) -> float:
  """Complex Drawdown Efficiency: profit / (avg_dd + max_dd * recovery_ratio)."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum()
  total = float(equity.iloc[-1])
  if total <= 0:
    return 0.0
  peak = np.maximum.accumulate(equity.values)
  dd = peak - equity.values
  max_dd = float(dd.max())
  avg_dd = float(dd.mean())
  if max_dd == 0:
    return 10.0
  max_dd_idx = int(dd.argmax())
  recovery_ratio = (len(equity) - max_dd_idx) / len(equity)
  denom = avg_dd + max_dd * recovery_ratio
  return _safe_div(total, denom) if denom > 0 else 10.0


def metric_sqn(pnl: pd.Series) -> float:
  """System Quality Number (Van Tharp): mean/std * sqrt(n) on non-zero days."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0]
  n = len(active)
  mean_ret = float(active.mean())
  std_ret = float(active.std())
  if std_ret <= 1e-12 * max(abs(mean_ret), 1.0):  # float-constant series → std ~1e-18
    return 10.0 if mean_ret > 0 else 0.0
  return mean_ret / std_ret * (n ** 0.5)


def metric_omega(pnl: pd.Series) -> float:
  """Omega Ratio at threshold=0: sum(gains) / sum(|losses|) on non-zero days.

  Equivalent to PF on non-zero days — but using the full distribution
  rather than just sign-based splitting. With threshold=0 these are
  identical, but omega is often cited separately in literature.
  """
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0]
  gains = float(active[active > 0].sum())
  losses = float(active[active < 0].sum())
  if losses == 0:
    return 10.0 if gains > 0 else 0.0
  return min(_safe_div(gains, abs(losses)), 10.0)


def metric_est_efficiency(pnl: pd.Series) -> float:
  """EST Efficiency: profit / (max_dd * sqrt(bars_to_recover))."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum()
  total = float(equity.iloc[-1])
  if total <= 0:
    return 0.0
  peak = np.maximum.accumulate(equity.values)
  dd = peak - equity.values
  max_dd = float(dd.max())
  if max_dd == 0:
    return 10.0
  max_dd_idx = int(dd.argmax())
  bars_to_recover = max(len(equity) - max_dd_idx, 1)
  return _safe_div(total, max_dd * bars_to_recover ** 0.5)


def metric_rapid_recovery(pnl: pd.Series) -> float:
  """Rapid Recovery: sqrt(profit_efficiency) * recovery_boost."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum()
  total = float(equity.iloc[-1])
  if total <= 0:
    return 0.0
  peak = np.maximum.accumulate(equity.values)
  dd = peak - equity.values
  max_dd = float(dd.max())
  if max_dd == 0:
    return 10.0
  profit_eff = total / max_dd
  underwater = dd > 0
  recovery_boost = 1 + (1 - underwater.sum() / len(dd))
  return profit_eff ** 0.5 * recovery_boost


# ── Structural quality metrics (from seed_ranker) ──────────


def metric_gpr(pnl: pd.Series) -> float:
  """Gain-to-Pain Ratio on non-zero days: sum(gains) / sum(|losses|)."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0]
  total = float(active.sum())
  pain = float(abs(active[active < 0].sum()))
  if pain == 0:
    return 10.0 if total > 0 else 0.0
  return min(_safe_div(total, pain), 10.0)


def metric_ter(pnl: pd.Series) -> float:
  """Trend Efficiency Ratio: |net_move| / total_path on equity curve.

  Range [0,1]. Higher = cleaner trend.
  """
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum().values
  if len(equity) < 2:
    return 0.0
  net_move = abs(equity[-1] - equity[0])
  total_path = float(np.sum(np.abs(np.diff(equity))))
  if total_path == 0:
    return 0.0
  return float(net_move / total_path)


def metric_keep_score(pnl: pd.Series) -> float:
  """Combined trend quality: 0.35*TER + 0.35*sigmoid(GPR) + 0.30*max(0, AC(1)).

  Simplified from seed_ranker.trend_score: Hurst is expensive, so positive
  lag-1 autocorrelation stands in for persistence.
  """
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum().values
  active = pnl[pnl != 0].values
  if len(equity) < 2 or len(active) < 20:
    return 0.0
  # TER
  net_move = abs(equity[-1] - equity[0])
  total_path = float(np.sum(np.abs(np.diff(equity))))
  ter = net_move / total_path if total_path > 0 else 0.0
  # GPR sigmoid
  pain = abs(float(active[active < 0].sum()))
  gpr = float(active.sum()) / pain if pain > 0 else 10.0
  sig_gpr = 1.0 / (1.0 + np.exp(-min(gpr, 10.0)))
  # Autocorrelation lag-1
  if len(active) > 1:
    ac = float(np.corrcoef(active[:-1], active[1:])[0, 1])
    if np.isnan(ac):
      ac = 0.0
  else:
    ac = 0.0
  score = 0.35 * ter + 0.35 * sig_gpr + 0.30 * max(0.0, ac)
  return float(np.clip(score, 0.0, 1.0))


def metric_mr_score(pnl: pd.Series) -> float:
  """Mean-reversion quality: crossing_rate + (1-VR) + negative_AC.

  Simplified: uses crossing_rate and autocorrelation as main signals.
  """
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  active = pnl[pnl != 0].values
  equity = pnl.cumsum().values
  if len(active) < 20:
    return 0.0
  # Crossing rate
  window = max(20, len(equity) // 10)
  rolling_mean = pd.Series(equity).rolling(window, min_periods=1).mean().values
  diff = equity - rolling_mean
  signs = np.sign(diff)
  crossings = float(np.sum(np.abs(np.diff(signs)) > 0))
  cr = crossings / max(len(diff) - 1, 1)
  # Autocorrelation lag-1 (negative = mean-reverting)
  ac = float(np.corrcoef(active[:-1], active[1:])[0, 1]) if len(active) > 1 else 0.0
  if np.isnan(ac):
    ac = 0.0
  neg_ac = max(0.0, -ac)  # Only count negative AC
  # Variance ratio lag-5 (Lo & MacKinlay): variance of 5-day SUMS over 5x the
  # daily variance. The old code took every 5th day (active[::5]), whose
  # variance equals the daily one, so VR was ~0.2 for every series and this
  # term was a constant +0.2 bonus. seed_ranker.variance_ratio is canonical.
  from backend.analysis.seed_ranker import variance_ratio
  vr_bonus = max(0.0, 1.0 - variance_ratio(active, lag=5))
  score = 0.40 * cr + 0.35 * neg_ac + 0.25 * vr_bonus
  return float(np.clip(score, 0.0, 1.0))


def metric_crossing_rate(pnl: pd.Series) -> float:
  """Crossing rate: how often equity crosses its rolling mean. Range [0,1]."""
  if _active_days(pnl) < _MIN_TRADING_DAYS:
    return _SKIP
  equity = pnl.cumsum().values
  if len(equity) < 20:
    return 0.0
  window = max(20, len(equity) // 10)
  rolling_mean = pd.Series(equity).rolling(window, min_periods=1).mean().values
  diff = equity - rolling_mean
  crossings = float(np.sum(np.abs(np.diff(np.sign(diff))) > 0))
  max_crossings = len(diff) - 1
  return float(crossings / max_crossings) if max_crossings > 0 else 0.0


# ── Dispatcher ──────────────────────────────────────────────

_METRICS = {
  "net_pnl": metric_net_pnl,
  "lake_ratio": metric_lake_ratio,
  "tail_ratio": metric_tail_ratio,
  "sortino": metric_sortino,
  "calmar": metric_calmar,
  "pf": metric_pf,
  "gain_pain": metric_gain_pain,
  "expectancy": metric_expectancy,
  "stability": metric_stability,
  "robust_sortino": metric_robust_sortino,
  "sharpe": metric_sharpe,
  "ulcer": metric_ulcer,
  "recovery": metric_recovery,
  "payoff": metric_payoff,
  "cde": metric_cde,
  "sqn": metric_sqn,
  "omega": metric_omega,
  "est_efficiency": metric_est_efficiency,
  "rapid_recovery": metric_rapid_recovery,
  "gpr": metric_gpr,
  "ter": metric_ter,
  "keep_score": metric_keep_score,
  "mr_score": metric_mr_score,
  "crossing_rate": metric_crossing_rate,
}

METRIC_NAMES = list(_METRICS.keys())


def score_metric(daily_pnl: pd.Series, metric_name: str) -> float:
  """Score a daily PnL series using the named metric.

  Returns float (higher = better). Returns -999 for invalid/insufficient data.
  """
  fn = _METRICS.get(metric_name)
  if fn is None:
    raise ValueError(f"Unknown metric: {metric_name!r}. Valid: {METRIC_NAMES}")
  return fn(daily_pnl)
