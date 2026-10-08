"""Portfolio scoring functions for optimization objectives.

Each function takes a PnL Series (daily or trade-level) and returns a score.
Convention: lower = better (for minimization-based greedy search).

Undefined ratios (no losing day, no drawdown, zero variance) score
-RATIO_CAP when the series made money: the same cap as profit factor and as
the Batch Selection metrics. They used to return the raw dollar numerator,
which put dollars on a ratio scale — a never-losing $500 series beat every
real ratio while a never-losing $0.50 one lost to most (2026-10-08 audit).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

RATIO_CAP = 10.0


def portfolio_sortino(pnl: pd.Series) -> float:
  """Sortino Ratio (negated). Higher Sortino = lower score."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  mean_ret = pnl.mean()
  downside = pnl[pnl < 0]
  if downside.empty:
    return -RATIO_CAP if mean_ret > 0 else float("inf")
  downside_dev = float(np.sqrt(np.mean(downside.values ** 2)))
  if downside_dev == 0:
    return -RATIO_CAP if mean_ret > 0 else float("inf")
  return -(mean_ret / downside_dev)


def portfolio_calmar(pnl: pd.Series) -> float:
  """Calmar Ratio (negated): total return / max drawdown."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  cum = pnl.cumsum()
  total_ret = cum.iloc[-1]
  peak = np.maximum.accumulate(cum.values)
  max_dd = abs((cum.values - peak).min())
  if max_dd == 0:
    return -RATIO_CAP if total_ret > 0 else float("inf")
  return -(total_ret / max_dd)


def portfolio_recovery(pnl: pd.Series) -> float:
  """Recovery Factor (negated): net profit / max drawdown."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  cum = pnl.cumsum()
  total = cum.iloc[-1]
  peak = np.maximum.accumulate(cum.values)
  max_dd = abs((cum.values - peak).min())
  if max_dd == 0:
    return -RATIO_CAP if total > 0 else float("inf")
  return -(total / max_dd)


def ulcer_index(equity: np.ndarray, peak: np.ndarray) -> float:
  """Ulcer Index of a P&L equity curve: RMS drawdown, in %.

  Drawdowns are normalised against a single reference scale for the whole
  series (max of |peak| and |equity|, floor 1.0) rather than per-timestep peak, so
  early-period losses (when running peak is 0 or negative) don't mix a raw
  $ drawdown into a % series (Tier 2.5 B17). The result is bounded by 200.

  Every Ulcer Index in the codebase goes through here so the optimizer,
  Batch Selection, event WFO and the KPI page measure the same thing.
  """
  if len(equity) == 0:
    return 0.0
  ref_scale = float(max(np.abs(peak).max(), np.abs(equity).max(), 1.0))
  dd_pct = (equity - peak) / ref_scale * 100
  return float(np.sqrt(np.mean(dd_pct ** 2)))


def portfolio_ulcer(pnl: pd.Series) -> float:
  """Ulcer Index: RMS of drawdowns (lower = less pain). See ulcer_index()."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  cum = pnl.cumsum().values
  return ulcer_index(cum, np.maximum.accumulate(cum))


def portfolio_profit_factor(pnl: pd.Series) -> float:
  """Profit Factor (negated): gross profit / gross loss, capped at 10.

  No losing day hits the same cap as any other PF >= 10 — returning the raw
  gross profit here put dollars on a ratio scale and let a short, never-losing
  history outrank everything (the vectorised path already used the cap).
  """
  if pnl.empty:
    return float("inf")
  gross_profit = pnl[pnl > 0].sum()
  gross_loss = abs(pnl[pnl < 0].sum())
  if gross_loss == 0:
    return -RATIO_CAP if gross_profit > 0 else float("inf")
  return -min(gross_profit / gross_loss, RATIO_CAP)


def portfolio_min_drawdown(pnl: pd.Series) -> float:
  """Max drawdown magnitude (lower = better, returns abs value)."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  cum = pnl.cumsum()
  peak = np.maximum.accumulate(cum.values)
  return abs(float((cum.values - peak).min()))


def portfolio_sharpe(pnl: pd.Series) -> float:
  """Sharpe Ratio (negated). Higher Sharpe = lower score."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  mean_ret = pnl.mean()
  std_ret = pnl.std()
  # A constant series gives std ~1e-18, not exactly 0, through float rounding
  # (exact `== 0` scored a constant $0.03/day series at Sharpe -8e16).
  if std_ret <= 1e-12 * max(abs(mean_ret), 1.0):
    return -RATIO_CAP if mean_ret > 0 else float("inf")
  return -(mean_ret / std_ret * (365 ** 0.5))


def portfolio_expectancy(pnl: pd.Series) -> float:
  """Expectancy (negated): wr * avg_win - (1-wr) * avg_loss."""
  if pnl.empty:
    return float("inf")
  nonzero = pnl[pnl != 0]
  if nonzero.empty:
    return float("inf")
  wins = nonzero[nonzero > 0]
  losses = nonzero[nonzero < 0]
  if wins.empty:
    return float("inf")
  wr = len(wins) / len(nonzero)
  avg_win = float(wins.mean())
  avg_loss = abs(float(losses.mean())) if not losses.empty else 0
  return -(wr * avg_win - (1 - wr) * avg_loss)


def portfolio_lake_ratio(pnl: pd.Series) -> float:
  """Lake ratio: fraction of equity curve underwater (lower = better)."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  cum = pnl.cumsum()
  peak = np.maximum.accumulate(cum.values)
  underwater = (peak - cum.values) > 0
  return float(underwater.sum() / len(underwater))


def portfolio_tail_ratio(pnl: pd.Series) -> float:
  """Tail ratio (negated): p95 gain / |p5 loss|. Higher = better upside skew."""
  if pnl.empty or len(pnl) < 10:
    return float("inf")
  p95 = float(np.percentile(pnl.values, 95))
  p5 = float(np.percentile(pnl.values, 5))
  if p5 >= 0:
    return -RATIO_CAP if p95 > 0 else float("inf")
  return -(p95 / abs(p5))


def portfolio_gain_pain(pnl: pd.Series) -> float:
  """Gain/Pain ratio (negated): sum(returns) / sum(|negative returns|)."""
  if pnl.empty:
    return float("inf")
  total = float(pnl.sum())
  pain = abs(float(pnl[pnl < 0].sum()))
  if pain == 0:
    return -RATIO_CAP if total > 0 else float("inf")
  return -(total / pain)


def portfolio_payoff(pnl: pd.Series) -> float:
  """Payoff ratio (negated): avg_win / avg_loss."""
  if pnl.empty:
    return float("inf")
  wins = pnl[pnl > 0]
  losses = pnl[pnl < 0]
  if wins.empty:
    return float("inf")
  if losses.empty:
    return -RATIO_CAP
  return -(float(wins.mean()) / abs(float(losses.mean())))


def portfolio_cde(pnl: pd.Series) -> float:
  """CDE: profit / (avg_dd + max_dd * recovery_time_ratio). Negated."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  cum = pnl.cumsum()
  total = float(cum.iloc[-1])
  if total <= 0:
    return float("inf")
  peak = np.maximum.accumulate(cum.values)
  dd = peak - cum.values
  max_dd = float(dd.max())
  avg_dd = float(dd.mean())
  if max_dd == 0:
    return -RATIO_CAP
  max_dd_idx = int(dd.argmax())
  recovery_ratio = (len(cum) - max_dd_idx) / len(cum)
  denom = avg_dd + max_dd * recovery_ratio
  return -(total / denom) if denom > 0 else -RATIO_CAP


def portfolio_robust_sortino(pnl: pd.Series) -> float:
  """Robust Sortino: sortino / (1 + |max_dd|). Negated."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  raw_sortino = -portfolio_sortino(pnl)  # Un-negate to get real value
  if not np.isfinite(raw_sortino) or raw_sortino <= 0:
    return float("inf")
  cum = pnl.cumsum()
  peak = np.maximum.accumulate(cum.values)
  max_dd = abs(float((cum.values - peak).min()))
  return -(raw_sortino / (1 + max_dd))


def portfolio_est_efficiency(pnl: pd.Series) -> float:
  """EST Efficiency: profit / (max_dd * sqrt(bars_to_recover)). Negated."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  cum = pnl.cumsum()
  total = float(cum.iloc[-1])
  if total <= 0:
    return float("inf")
  peak = np.maximum.accumulate(cum.values)
  dd = peak - cum.values
  max_dd = float(dd.max())
  if max_dd == 0:
    return -RATIO_CAP
  max_dd_idx = int(dd.argmax())
  bars_to_recover = max(len(cum) - max_dd_idx, 1)
  return -(total / (max_dd * bars_to_recover ** 0.5))


def portfolio_rapid_recovery(pnl: pd.Series) -> float:
  """Rapid Recovery: sqrt(profit_efficiency) * recovery_boost. Negated."""
  if pnl.empty or len(pnl) < 2:
    return float("inf")
  cum = pnl.cumsum()
  total = float(cum.iloc[-1])
  if total <= 0:
    return float("inf")
  peak = np.maximum.accumulate(cum.values)
  dd = peak - cum.values
  max_dd = float(dd.max())
  if max_dd == 0:
    return -RATIO_CAP
  profit_eff = total / max_dd
  underwater = dd > 0
  recovery_boost = 1 + (1 - underwater.sum() / len(dd))
  return -(profit_eff ** 0.5 * recovery_boost)


def portfolio_stability_score(pnl: pd.Series) -> float:
  """Stability objective (lower = more stable = better)."""
  return 1 - calculate_stability(pnl)


def calculate_stability(pnl: pd.Series) -> float:
  """Blended stability score (0–1). Combines R², normalized DD, consistency."""
  if pnl.empty or len(pnl) < 2:
    return 0.0
  cum = pnl.cumsum()
  x = np.arange(len(cum))
  y = cum.values

  # R² (linearity of equity curve) — only reward upward-sloping curves
  slope, intercept, r_val, p_val, std_err = stats.linregress(x, y)
  r_sq = float(r_val ** 2) if np.isfinite(r_val) and slope > 0 else 0.0

  # Normalized max drawdown penalty
  peak = np.maximum.accumulate(y)
  max_dd = abs((y - peak).min()) if (y - peak).min() < 0 else 0
  total_profit = y[-1] if y[-1] > 0 else 1
  norm_dd = min(max_dd / abs(total_profit), 1.0) if total_profit != 0 else 1.0
  dd_score = 1 - norm_dd

  # Consistency (CoV of chunk returns)
  n_chunks = min(30, max(4, len(pnl) // 20))
  chunk_size = len(pnl) // n_chunks
  if chunk_size > 0 and n_chunks >= 2:
    chunks = [pnl.iloc[i * chunk_size:(i + 1) * chunk_size].sum()
              for i in range(n_chunks)]
    mean_r = np.mean(chunks)
    std_r = np.std(chunks)
    cov = min(std_r / abs(mean_r), 3.0) if abs(mean_r) > 0 else 3.0
    consistency = max(0, 1 - cov / 3.0)
  else:
    consistency = 0.5

  return 0.4 * r_sq + 0.3 * dd_score + 0.3 * consistency


def _fast_stability(daily_pnl_array: np.ndarray) -> float:
  """Same formula as calculate_stability(), pure numpy (no scipy/pandas).

  Computes: 0.4 * R² + 0.3 * dd_score + 0.3 * consistency
  Identical output to calculate_stability(pd.Series(daily_pnl_array)).
  Avoids scipy.stats.linregress dispatch and pd.Series construction overhead.
  """
  n = len(daily_pnl_array)
  if n < 2:
    return 0.0
  cum = np.cumsum(daily_pnl_array)
  x = np.arange(n, dtype=np.float64)
  y = cum.astype(np.float64)

  # R² (identical to scipy.stats.linregress r_val**2)
  x_mean = x.mean()
  y_mean = y.mean()
  x_dev = x - x_mean
  y_dev = y - y_mean
  ss_xy = (x_dev * y_dev).sum()
  ss_xx = (x_dev * x_dev).sum()
  ss_yy = (y_dev * y_dev).sum()
  if ss_xx == 0 or ss_yy == 0:
    r_sq = 0.0
  else:
    slope = ss_xy / ss_xx
    r_val = ss_xy / np.sqrt(ss_xx * ss_yy)
    # Only reward upward-sloping curves (declining equity = 0 stability)
    r_sq = float(r_val ** 2) if np.isfinite(r_val) and slope > 0 else 0.0

  # Normalized max drawdown penalty (identical to lines 245-249)
  peak = np.maximum.accumulate(y)
  dd_vals = y - peak
  max_dd = abs(dd_vals.min()) if dd_vals.min() < 0 else 0
  total_profit = y[-1] if y[-1] > 0 else 1
  norm_dd = min(max_dd / abs(total_profit), 1.0) if total_profit != 0 else 1.0
  dd_score = 1 - norm_dd

  # Consistency (CoV of chunk returns, identical to lines 252-262)
  n_chunks = min(30, max(4, n // 20))
  chunk_size = n // n_chunks
  if chunk_size > 0 and n_chunks >= 2:
    chunks = [daily_pnl_array[i * chunk_size:(i + 1) * chunk_size].sum()
              for i in range(n_chunks)]
    mean_r = np.mean(chunks)
    std_r = np.std(chunks)
    cov = min(std_r / abs(mean_r), 3.0) if abs(mean_r) > 0 else 3.0
    consistency = max(0, 1 - cov / 3.0)
  else:
    consistency = 0.5

  return 0.4 * r_sq + 0.3 * dd_score + 0.3 * consistency


def _batch_fast_stability(combined_vals: np.ndarray, candidate_cols: np.ndarray) -> np.ndarray:
  """Vectorized stability for K candidates simultaneously.

  Produces identical results to calling _fast_stability(combined_vals + candidate_cols[:, k])
  for each k, but in a single numpy operation.

  Args:
      combined_vals: shape (T,) — current portfolio daily PnL
      candidate_cols: shape (T, K) — each column is one candidate's daily PnL

  Returns:
      shape (K,) — stability scores, one per candidate
  """
  # Build all test portfolios at once: (T, K)
  test = combined_vals[:, None] + candidate_cols

  T, K = test.shape
  if T < 2:
    return np.zeros(K)

  cum = np.cumsum(test, axis=0)  # (T, K)
  x = np.arange(T, dtype=np.float64)

  # === R² (vectorized across K columns) ===
  x_mean = x.mean()
  y_mean = cum.mean(axis=0)  # (K,)
  x_dev = x - x_mean  # (T,)
  y_dev = cum - y_mean[None, :]  # (T, K)

  ss_xy = (x_dev[:, None] * y_dev).sum(axis=0)  # (K,)
  ss_xx = (x_dev ** 2).sum()  # scalar
  ss_yy = (y_dev ** 2).sum(axis=0)  # (K,)

  denom = ss_xx * ss_yy
  # Only reward upward-sloping curves (ss_xy > 0 ↔ slope > 0), matching _fast_stability
  r_sq = np.where((denom > 0) & (ss_xy > 0), (ss_xy ** 2) / denom, 0.0)
  r_sq = np.clip(r_sq, 0.0, 1.0)

  # === Drawdown score (vectorized) ===
  peak = np.maximum.accumulate(cum, axis=0)  # (T, K)
  dd_vals = cum - peak  # (T, K), all <= 0
  max_dd_raw = dd_vals.min(axis=0)  # (K,), most negative values
  max_dd = np.where(max_dd_raw < 0, np.abs(max_dd_raw), 0.0)  # (K,)

  total_profit = cum[-1, :]  # (K,)
  # A final P&L <= 0 divides by 1.0 exactly like calculate_stability(); an
  # extra "== 0 → 1.0" override here used to score a flat (never-traded)
  # candidate 0.3 lower than the reference path did.
  safe_total = np.where(total_profit > 0, total_profit, 1.0)
  norm_dd = np.clip(max_dd / np.abs(safe_total), 0.0, 1.0)
  dd_score = 1.0 - norm_dd

  # === Consistency (CoV of chunk returns, vectorized) ===
  n_chunks = min(30, max(4, T // 20))
  chunk_size = T // n_chunks
  consistency = np.full(K, 0.5)  # default

  if chunk_size > 0 and n_chunks >= 2:
    usable = n_chunks * chunk_size
    chunk_sums = test[:usable].reshape(n_chunks, chunk_size, K).sum(axis=1)  # (n_chunks, K)
    mean_r = chunk_sums.mean(axis=0)  # (K,)
    std_r = chunk_sums.std(axis=0)  # (K,)

    abs_mean = np.abs(mean_r)
    cov = np.where(abs_mean > 0, np.clip(std_r / abs_mean, 0, 3.0), 3.0)
    consistency = np.clip(1.0 - cov / 3.0, 0.0, 1.0)

  return 0.4 * r_sq + 0.3 * dd_score + 0.3 * consistency


def _batch_score_candidates(
  combined: np.ndarray,
  candidate_cols: np.ndarray,
  score_fn_name: str,
) -> np.ndarray:
  """Score K candidates at once for greedy search.

  Args:
      combined: shape (T,) — current portfolio daily PnL (sum of selected assets)
      candidate_cols: shape (T, K) — each column is one candidate's daily PnL
      score_fn_name: key from SCORING_FUNCTIONS

  Returns:
      shape (K,) — score per candidate (lower = better)
  """
  test = combined[:, None] + candidate_cols  # (T, K)
  T, K = test.shape

  if T < 2:
    return np.full(K, float("inf"))

  # Suppress numpy warnings — np.where evaluates both branches before selecting,
  # so division-by-zero in the "false" branch is expected and harmless.
  prev_err = np.seterr(divide="ignore", invalid="ignore")
  try:
    return _batch_score_inner(test, combined, candidate_cols, score_fn_name)
  finally:
    np.seterr(**prev_err)


def _batch_score_inner(
  test: np.ndarray,
  combined: np.ndarray,
  candidate_cols: np.ndarray,
  score_fn_name: str,
) -> np.ndarray:
  """Inner batch scoring logic (called by _batch_score_candidates with error state managed)."""
  T, K = test.shape

  # === Precompute shared building blocks ===
  cum = np.cumsum(test, axis=0)  # (T, K)
  total_ret = cum[-1, :]  # (K,)
  peak = np.maximum.accumulate(cum, axis=0)  # (T, K)
  dd = peak - cum  # (T, K), all >= 0
  max_dd = dd.max(axis=0)  # (K,)
  mean_ret = test.mean(axis=0)  # (K,)

  # Downside deviation (for sortino/robust_sortino)
  # Must match individual: sqrt(mean(negative²)) over ONLY negative values
  neg_mask_dd = test < 0
  neg_sq = np.where(neg_mask_dd, test ** 2, 0)
  neg_sq_sum = neg_sq.sum(axis=0)  # (K,)
  n_neg_dd = neg_mask_dd.sum(axis=0)  # (K,)
  downside_dev = np.where(n_neg_dd > 0, np.sqrt(neg_sq_sum / n_neg_dd), 0.0)  # (K,)

  # Std (for sharpe) — ddof=1 to match pd.Series.std()
  std_ret = test.std(axis=0, ddof=1)  # (K,)

  # Gross profit / gross loss (for profit_factor, gain_pain)
  pos_mask = test > 0
  neg_mask = test < 0
  gross_profit = np.where(pos_mask, test, 0).sum(axis=0)  # (K,)
  gross_loss = np.abs(np.where(neg_mask, test, 0).sum(axis=0))  # (K,)

  # Win/loss stats (for expectancy, payoff)
  nonzero_mask = test != 0
  n_nonzero = nonzero_mask.sum(axis=0)  # (K,)
  n_wins = pos_mask.sum(axis=0)  # (K,)

  win_sum = np.where(pos_mask, test, 0).sum(axis=0)
  loss_sum = np.abs(np.where(neg_mask, test, 0).sum(axis=0))
  n_losses = neg_mask.sum(axis=0)

  # Route to objective-specific scoring
  if score_fn_name == "max_sortino":
    scores = np.where(
      downside_dev > 0,
      -(mean_ret / downside_dev),
      np.where(mean_ret > 0, -RATIO_CAP, float("inf")),
    )

  elif score_fn_name == "max_calmar":
    scores = np.where(
      max_dd > 0,
      -(total_ret / max_dd),
      np.where(total_ret > 0, -RATIO_CAP, float("inf")),
    )

  elif score_fn_name == "max_recovery":
    scores = np.where(
      max_dd > 0,
      -(total_ret / max_dd),
      np.where(total_ret > 0, -RATIO_CAP, float("inf")),
    )

  elif score_fn_name == "min_ulcer":
    # Vectorised ulcer_index(): one ref_scale per candidate (Tier 2.5 B17),
    # avoiding per-timestep peak mixing $ and % when running peak <= 0.
    ref_scale = np.maximum(
      np.maximum(np.abs(peak).max(axis=0), np.abs(cum).max(axis=0)),
      1.0,
    )  # (K,)
    dd_pct = (cum - peak) / ref_scale[None, :] * 100
    scores = np.sqrt((dd_pct ** 2).mean(axis=0))

  elif score_fn_name == "max_profit_factor":
    pf = np.where(gross_loss > 0, np.clip(gross_profit / gross_loss, 0, RATIO_CAP), RATIO_CAP)
    # Never traded (no profit, no loss) is the WORST score, like the reference
    # path. Negating an inf PF used to make it -inf: the best possible score.
    scores = np.where((gross_loss > 0) | (gross_profit > 0), -pf, float("inf"))

  elif score_fn_name == "min_max_drawdown":
    scores = max_dd

  elif score_fn_name == "max_sharpe":
    scores = np.where(
      std_ret > 1e-12 * np.maximum(np.abs(mean_ret), 1.0),  # see portfolio_sharpe
      -(mean_ret / std_ret * (365 ** 0.5)),
      np.where(mean_ret > 0, -RATIO_CAP, float("inf")),
    )

  elif score_fn_name == "max_expectancy":
    wr = np.where(n_nonzero > 0, n_wins / n_nonzero, 0.0)
    avg_win = np.where(n_wins > 0, win_sum / n_wins, 0.0)
    avg_loss = np.where(n_losses > 0, loss_sum / n_losses, 0.0)
    exp = wr * avg_win - (1 - wr) * avg_loss
    scores = np.where(n_wins > 0, -exp, float("inf"))

  elif score_fn_name == "min_lake_ratio":
    underwater = (dd > 0).sum(axis=0)
    scores = underwater / T

  elif score_fn_name == "max_tail_ratio":
    if T < 10:
      # portfolio_tail_ratio needs >= 10 observations for a 5th percentile
      scores = np.full(K, float("inf"))
    else:
      p95 = np.percentile(test, 95, axis=0)
      p5 = np.percentile(test, 5, axis=0)
      scores = np.where(
        p5 < 0,
        -(p95 / np.abs(p5)),
        np.where(p95 > 0, -RATIO_CAP, float("inf")),
      )

  elif score_fn_name == "max_gain_pain":
    scores = np.where(
      gross_loss > 0,
      -(total_ret / gross_loss),
      np.where(total_ret > 0, -RATIO_CAP, float("inf")),
    )

  elif score_fn_name == "max_payoff":
    avg_win = np.where(n_wins > 0, win_sum / n_wins, 0.0)
    avg_loss_val = np.where(n_losses > 0, loss_sum / n_losses, 0.0)
    scores = np.where(
      (n_wins > 0) & (avg_loss_val > 0),
      -(avg_win / avg_loss_val),
      np.where(n_wins > 0, -RATIO_CAP, float("inf")),
    )

  elif score_fn_name == "max_cde":
    avg_dd = dd.mean(axis=0)
    max_dd_idx = dd.argmax(axis=0)  # (K,)
    recovery_ratio = (T - max_dd_idx) / T
    denom_cde = avg_dd + max_dd * recovery_ratio
    scores = np.where(
      (total_ret > 0) & (denom_cde > 0),
      -(total_ret / denom_cde),
      np.where(total_ret > 0, -RATIO_CAP, float("inf")),
    )

  elif score_fn_name == "max_robust_sortino":
    raw_sortino = np.where(
      downside_dev > 0,
      mean_ret / downside_dev,
      np.where(mean_ret > 0, RATIO_CAP, 0.0),
    )
    scores = np.where(
      raw_sortino > 0,
      -(raw_sortino / (1 + max_dd)),
      float("inf"),
    )

  elif score_fn_name == "max_est_efficiency":
    max_dd_idx = dd.argmax(axis=0)
    bars_to_recover = np.maximum(T - max_dd_idx, 1)
    scores = np.where(
      (total_ret > 0) & (max_dd > 0),
      -(total_ret / (max_dd * np.sqrt(bars_to_recover))),
      np.where(total_ret > 0, -RATIO_CAP, float("inf")),
    )

  elif score_fn_name == "max_rapid_recovery":
    profit_eff = np.where(max_dd > 0, total_ret / max_dd, np.abs(total_ret))
    underwater_frac = (dd > 0).sum(axis=0) / T
    recovery_boost = 1 + (1 - underwater_frac)
    # No drawdown → -RATIO_CAP, exactly like portfolio_rapid_recovery
    scores = np.where(
      total_ret > 0,
      np.where(
        max_dd > 0,
        -(np.sqrt(np.abs(profit_eff)) * recovery_boost),
        -RATIO_CAP,
      ),
      float("inf"),
    )

  elif score_fn_name == "stability":
    scores = 1 - _batch_fast_stability(combined, candidate_cols)

  elif score_fn_name in ("max_gpr", "max_ter", "max_keep_score", "max_mr_score", "max_crossing_rate"):
    # Structural objectives — fall back to per-candidate loop
    scores = np.empty(K)
    fn = SCORING_FUNCTIONS.get(score_fn_name)
    if fn is None:
      from backend.analysis.seed_ranker import (
        portfolio_gpr, portfolio_ter, portfolio_keep_score,
        portfolio_meanrev_score, portfolio_crossing_rate,
      )
      _structural_map = {
        "max_gpr": portfolio_gpr, "max_ter": portfolio_ter,
        "max_keep_score": portfolio_keep_score,
        "max_mr_score": portfolio_meanrev_score,
        "max_crossing_rate": portfolio_crossing_rate,
      }
      fn = _structural_map[score_fn_name]
    for k in range(K):
      scores[k] = fn(pd.Series(test[:, k]))

  else:
    # Unknown objective — fall back to per-candidate loop
    fn = SCORING_FUNCTIONS.get(score_fn_name)
    if fn is None:
      return np.full(K, float("inf"))
    scores = np.empty(K)
    for k in range(K):
      scores[k] = fn(pd.Series(test[:, k]))

  return scores


# Map objective codes → scoring functions
SCORING_FUNCTIONS = {
  "max_sortino": portfolio_sortino,
  "max_calmar": portfolio_calmar,
  "max_recovery": portfolio_recovery,
  "min_ulcer": portfolio_ulcer,
  "max_profit_factor": portfolio_profit_factor,
  "min_max_drawdown": portfolio_min_drawdown,
  "stability": portfolio_stability_score,
  "max_sharpe": portfolio_sharpe,
  "max_expectancy": portfolio_expectancy,
  "min_lake_ratio": portfolio_lake_ratio,
  "max_tail_ratio": portfolio_tail_ratio,
  "max_gain_pain": portfolio_gain_pain,
  "max_payoff": portfolio_payoff,
  "max_cde": portfolio_cde,
  "max_robust_sortino": portfolio_robust_sortino,
  "max_est_efficiency": portfolio_est_efficiency,
  "max_rapid_recovery": portfolio_rapid_recovery,
  # Structural quality objectives (trend + mean-reversion)
  "max_gpr": None,  # lazy-loaded below
  "max_ter": None,
  "max_keep_score": None,
  "max_mr_score": None,
  "max_crossing_rate": None,
}

# Lazy-load structural objectives to avoid circular imports
def _init_structural_objectives():
    from backend.analysis.seed_ranker import (
        portfolio_gpr, portfolio_ter, portfolio_keep_score,
        portfolio_meanrev_score, portfolio_crossing_rate,
    )
    SCORING_FUNCTIONS["max_gpr"] = portfolio_gpr
    SCORING_FUNCTIONS["max_ter"] = portfolio_ter
    SCORING_FUNCTIONS["max_keep_score"] = portfolio_keep_score
    SCORING_FUNCTIONS["max_mr_score"] = portfolio_meanrev_score
    SCORING_FUNCTIONS["max_crossing_rate"] = portfolio_crossing_rate

_init_structural_objectives()
