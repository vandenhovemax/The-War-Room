"""Statistical test implementations for OOS integrity checks."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sp_stats


def ks_test(is_series: pd.Series, oos_series: pd.Series) -> tuple[float, float]:
  """Two-sample Kolmogorov-Smirnov test.

  Returns (statistic, p_value). Low p = different distributions.
  """
  is_clean = is_series.dropna().values
  oos_clean = oos_series.dropna().values
  if len(is_clean) < 5 or len(oos_clean) < 5:
    return (0.0, 1.0)
  stat, p = sp_stats.ks_2samp(is_clean, oos_clean)
  return (float(stat), float(p))


def compute_psi(
  is_series: pd.Series, oos_series: pd.Series, n_bins: int = 10,
) -> float:
  """Population Stability Index between IS and OOS distributions.

  PSI < 0.10: no shift. 0.10-0.25: moderate. > 0.25: significant.
  """
  is_clean = is_series.dropna().values
  oos_clean = oos_series.dropna().values
  if len(is_clean) < n_bins or len(oos_clean) < n_bins:
    return 0.0

  # Use IS quantile-based bins
  bins = np.percentile(is_clean, np.linspace(0, 100, n_bins + 1))
  bins[0] = -np.inf
  bins[-1] = np.inf
  # Remove duplicate bin edges
  bins = np.unique(bins)
  if len(bins) < 3:
    return 0.0

  is_hist = np.histogram(is_clean, bins=bins)[0].astype(float)
  oos_hist = np.histogram(oos_clean, bins=bins)[0].astype(float)

  # Normalize to proportions, avoid zeros
  is_pct = np.maximum(is_hist / is_hist.sum(), 1e-6)
  oos_pct = np.maximum(oos_hist / oos_hist.sum(), 1e-6)

  psi = float(np.sum((oos_pct - is_pct) * np.log(oos_pct / is_pct)))
  return psi


def adversarial_validation(
  is_df: pd.DataFrame, oos_df: pd.DataFrame,
) -> float:
  """Train classifier to distinguish IS from OOS. Returns AUC.

  AUC ≈ 0.5 = indistinguishable. AUC > 0.55 = different distributions.
  """
  try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score
  except ImportError:
    return 0.5  # sklearn not available

  # Align columns
  common = sorted(set(is_df.columns) & set(oos_df.columns))
  if not common:
    return 0.5

  is_subset = is_df[common].fillna(0).values
  oos_subset = oos_df[common].fillna(0).values

  X = np.vstack([is_subset, oos_subset])
  y = np.array([0] * len(is_subset) + [1] * len(oos_subset))

  if len(X) < 20:
    return 0.5

  clf = LogisticRegression(max_iter=200, solver="lbfgs", C=0.1)
  n_folds = min(5, min(len(is_subset), len(oos_subset)))
  if n_folds < 2:
    return 0.5

  scores = cross_val_score(clf, X, y, cv=n_folds, scoring="roc_auc")
  return float(np.mean(scores))


def autocorrelation_decay(
  series: pd.Series, max_lags: int = 30,
) -> list[float]:
  """Compute autocorrelation for lags 1..max_lags."""
  n = len(series)
  if n < max_lags + 10:
    max_lags = max(1, n - 10)
  return [float(series.autocorr(lag=i)) for i in range(1, max_lags + 1)]


def permutation_test(
  is_values: np.ndarray, oos_values: np.ndarray,
  n_perms: int = 200,
) -> float:
  """Permutation test for difference in means. Returns p-value."""
  if len(is_values) < 5 or len(oos_values) < 5:
    return 1.0

  observed_diff = abs(float(np.mean(is_values) - np.mean(oos_values)))
  combined = np.concatenate([is_values, oos_values])
  n_is = len(is_values)

  rng = np.random.default_rng(42)
  count_extreme = 0
  for _ in range(n_perms):
    perm = rng.permutation(combined)
    perm_diff = abs(float(np.mean(perm[:n_is]) - np.mean(perm[n_is:])))
    if perm_diff >= observed_diff:
      count_extreme += 1

  return (count_extreme + 1) / (n_perms + 1)
