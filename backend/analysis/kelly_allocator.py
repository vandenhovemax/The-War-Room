"""Multi-variate Kelly Criterion portfolio allocation.

Computes optimal capital weights across strategies/assets using IS data.
Core: F* = C^-1 M (covariance-inverse times mean returns).
Half-Kelly baseline is non-negotiable for live safety.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class KellyAllocConfig:
  mode: str = "per_strategy"           # "per_asset" or "per_strategy"
  half_kelly_multiplier: float = 0.5   # non-negotiable baseline
  sr_threshold: float = 0.5            # OOS Sharpe below this -> zero weight
  max_cap_pct: float = 0.40            # max single-strategy weight cap
  corr_warning_threshold: float = 0.7  # flag correlated pairs above this
  regime_dampening_factor: float = 0.5 # blend toward equal-weight in high vol
  min_trades_per_strategy: int = 100   # minimum IS sample length
  is_lookback_days: int = 0            # 0 = all IS data
  schedule_type: str = "monthly"       # weekly|biweekly|monthly|2month|6month|by_trades
  schedule_trade_count: int = 100      # for "by_trades" mode


@dataclass
class KellyAllocResult:
  weights: dict[str, float] = field(default_factory=dict)
  raw_kelly_weights: dict[str, float] = field(default_factory=dict)
  sr_scaling_factors: dict[str, float] = field(default_factory=dict)
  concentration_flags: list[str] = field(default_factory=list)
  herfindahl_index: float = 0.0
  regime_state: str = "normal"
  regime_dampening_applied: float = 0.0
  warnings: list[str] = field(default_factory=list)
  computed_at: str = ""

  def to_dict(self) -> dict:
    return asdict(self)


def compute_kelly_allocation(
  daily_pnl: pd.DataFrame,
  oos_sharpe: dict[str, float] | None = None,
  config: KellyAllocConfig | None = None,
) -> KellyAllocResult:
  """Compute multivariate Kelly allocation weights from IS daily PnL.

  Args:
    daily_pnl: IS daily PnL matrix (index=date, columns=strategy/asset names).
    oos_sharpe: Optional per-strategy OOS Sharpe ratios for Add-on 1.
    config: Allocation config. Defaults to KellyAllocConfig().

  Returns:
    KellyAllocResult with weights, diagnostics, and flags.
  """
  if config is None:
    config = KellyAllocConfig()

  result = KellyAllocResult(computed_at=datetime.now(timezone.utc).isoformat())
  names = list(daily_pnl.columns)
  n = len(names)

  if n == 0:
    result.warnings.append("No strategies/assets in daily PnL matrix")
    return result

  # Trim to lookback window if configured
  if config.is_lookback_days > 0 and len(daily_pnl) > config.is_lookback_days:
    daily_pnl = daily_pnl.iloc[-config.is_lookback_days:]

  # Check minimum trade counts per strategy
  trade_counts = (daily_pnl != 0).sum()
  low_count = [n for n, c in trade_counts.items() if c < config.min_trades_per_strategy]
  if low_count:
    result.warnings.append(
      f"Low IS sample (<{config.min_trades_per_strategy} active days): "
      + ", ".join(str(x) for x in low_count[:5])
    )

  # If only 1 strategy, weight is 1.0
  if n == 1:
    result.weights = {names[0]: 1.0}
    result.raw_kelly_weights = {names[0]: 1.0}
    result.herfindahl_index = 1.0
    return result

  # Core: M = mean return vector, C = covariance matrix
  M = daily_pnl.mean().values  # (n,)
  C = daily_pnl.cov().values   # (n, n)

  # Attempt F* = C^-1 M via np.linalg.solve (no scipy needed)
  try:
    F_star = np.linalg.solve(C, M)  # (n,)
  except np.linalg.LinAlgError:
    logger.warning("Kelly allocation: singular covariance matrix — falling back to equal-weight")
    result.warnings.append("CRITICAL: Singular covariance matrix — using equal-weight fallback")
    eq = 1.0 / n
    result.weights = {name: eq for name in names}
    result.raw_kelly_weights = dict(result.weights)
    result.herfindahl_index = 1.0 / n
    return result

  # Clip negative weights to 0 (non-negative constraint)
  F_star = np.maximum(F_star, 0.0)

  # Normalize to sum = 1
  total = F_star.sum()
  if total <= 0:
    # All strategies have negative Kelly — fall back to equal-weight
    result.warnings.append("All raw Kelly weights non-positive — using equal-weight fallback")
    eq = 1.0 / n
    result.weights = {name: eq for name in names}
    result.raw_kelly_weights = {name: 0.0 for name in names}
    result.herfindahl_index = 1.0 / n
    return result

  weights = F_star / total
  result.raw_kelly_weights = {names[i]: round(float(weights[i]), 6) for i in range(n)}

  # Apply Half-Kelly (non-negotiable)
  weights *= config.half_kelly_multiplier
  # Re-normalize after half-kelly (weights should still sum to 1 for allocation)
  w_sum = weights.sum()
  if w_sum > 0:
    weights /= w_sum

  # Add-on 1: OOS Sharpe scaling
  sr_factors = {}
  if oos_sharpe:
    weights, sr_factors = _apply_oos_sharpe_scaling(
      weights, names, oos_sharpe, config.sr_threshold,
    )
  result.sr_scaling_factors = sr_factors

  # Add-on 2: Max cap per strategy
  weights = _apply_max_cap(weights, config.max_cap_pct)

  # Add-on 3: Concentration check (diagnostic only)
  flags, hhi = _concentration_check(daily_pnl, weights, names, config.corr_warning_threshold)
  result.concentration_flags = flags
  result.herfindahl_index = round(hhi, 4)

  # Add-on 4: Regime dampening
  weights, regime_state, dampening = _regime_dampening(daily_pnl, weights, config.regime_dampening_factor)
  result.regime_state = regime_state
  result.regime_dampening_applied = round(dampening, 4)

  # Final weights
  result.weights = {names[i]: round(float(weights[i]), 6) for i in range(n)}

  return result


def _apply_oos_sharpe_scaling(
  weights: np.ndarray,
  names: list[str],
  oos_sharpe: dict[str, float],
  sr_threshold: float,
) -> tuple[np.ndarray, dict[str, float]]:
  """Scale weights by OOS Sharpe confidence. SR < threshold -> zeroed."""
  sr_factors: dict[str, float] = {}
  for i, name in enumerate(names):
    sr = oos_sharpe.get(name, 0.0)
    if sr < sr_threshold:
      # Below threshold: zero out
      weights[i] = 0.0
      sr_factors[name] = 0.0
    else:
      # Map SR to [0.5, 1.0] multiplier: f(sr) = 0.5 + 0.5 * min(sr / 2.0, 1.0)
      factor = 0.5 + 0.5 * min(sr / 2.0, 1.0)
      weights[i] *= factor
      sr_factors[name] = round(factor, 4)

  # Re-normalize
  w_sum = weights.sum()
  if w_sum > 0:
    weights /= w_sum
  else:
    # All zeroed out — equal-weight fallback
    weights[:] = 1.0 / len(names)
    for name in names:
      sr_factors[name] = sr_factors.get(name, 0.0)

  return weights, sr_factors


def _apply_max_cap(weights: np.ndarray, max_cap_pct: float) -> np.ndarray:
  """Cap any weight > max_cap_pct, redistribute excess proportionally."""
  if max_cap_pct <= 0 or max_cap_pct >= 1.0:
    return weights

  # Iteratively cap and redistribute (converges in a few passes)
  for _ in range(10):
    over = weights > max_cap_pct
    if not over.any():
      break
    excess = (weights[over] - max_cap_pct).sum()
    weights[over] = max_cap_pct
    under = ~over & (weights > 0)
    under_sum = weights[under].sum()
    if under_sum > 0:
      weights[under] += excess * (weights[under] / under_sum)

  # Final normalize
  w_sum = weights.sum()
  if w_sum > 0:
    weights /= w_sum
  return weights


def _concentration_check(
  daily_pnl: pd.DataFrame,
  weights: np.ndarray,
  names: list[str],
  corr_threshold: float,
) -> tuple[list[str], float]:
  """Diagnostic: flag correlated pairs, compute Herfindahl index."""
  flags: list[str] = []

  # Pairwise correlation
  corr = daily_pnl.corr()
  n = len(names)
  for i in range(n):
    for j in range(i + 1, n):
      c = corr.iloc[i, j]
      if abs(c) > corr_threshold and weights[i] > 0.01 and weights[j] > 0.01:
        flags.append(
          f"{names[i]} & {names[j]} corr={c:.2f} (>{corr_threshold})"
        )

  # Herfindahl-Hirschman Index (sum of squared weights)
  hhi = float((weights ** 2).sum())

  return flags, hhi


def _regime_dampening(
  daily_pnl: pd.DataFrame,
  weights: np.ndarray,
  dampening_factor: float,
) -> tuple[np.ndarray, str, float]:
  """Blend toward equal-weight in high-volatility regimes."""
  n = len(weights)
  if n == 0 or dampening_factor <= 0:
    return weights, "normal", 0.0

  # Compute portfolio realized volatility (using current weights)
  portfolio_returns = (daily_pnl.values * weights).sum(axis=1)
  current_vol = np.std(portfolio_returns) if len(portfolio_returns) > 1 else 0.0

  # Compute historical volatility percentile (rolling 30-day vol)
  if len(daily_pnl) < 60:
    return weights, "insufficient_data", 0.0

  equal_weights = np.ones(n) / n
  hist_returns = (daily_pnl.values * equal_weights).sum(axis=1)
  rolling_vol = pd.Series(hist_returns).rolling(30).std().dropna()

  if rolling_vol.empty:
    return weights, "normal", 0.0

  vol_percentile = float((rolling_vol < current_vol).mean())

  # Classify regime
  if vol_percentile > 0.8:
    regime = "high_vol"
    blend = dampening_factor  # blend toward equal-weight
  elif vol_percentile < 0.2:
    regime = "low_vol"
    blend = 0.0  # full Kelly weights
  else:
    regime = "normal"
    blend = 0.0

  if blend > 0:
    eq = np.ones(n) / n
    weights = (1.0 - blend) * weights + blend * eq
    # Re-normalize
    w_sum = weights.sum()
    if w_sum > 0:
      weights /= w_sum

  return weights, regime, blend
