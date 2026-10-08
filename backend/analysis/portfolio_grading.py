"""Portfolio grading — single letter grade from OOS metrics.

Computes a weighted score from key robustness metrics, then maps
to a letter grade (A+ through F).  Used by both portfolio OOS and
cross-strategy OOS endpoints.

Strategy type is auto-detected from win rate and payoff ratio,
and the grade weights adapt accordingly:

  Default weights:
    Stability R²   25%   raw 0-1 value
    WFE            25%   capped at 1.0
    Return/MaxDD   20%   min(value / 5.0, 1.0)
    Sortino        15%   min(value / 3.0, 1.0)
    Profit Factor  15%   min((value - 1.0) / 2.0, 1.0), floor 0

  Trend-following adjustments:
    Stability R²   10%   (trend equity curves are naturally lumpy)
    Sortino        30%   (asymmetric upside is the signal)

  Mean-reversion adjustments:
    Stability R²   30%   (smoothness IS the signal)
    Sortino        10%   (less relevant for high-frequency symmetric returns)
"""
from __future__ import annotations


def compute_return_to_dd_ratio(net_profit: float, max_drawdown: float) -> float:
  """Net profit / abs(max drawdown).  Returns 0.0 on div-by-zero."""
  if max_drawdown == 0 or max_drawdown is None:
    return 0.0
  return round(net_profit / abs(max_drawdown), 2)


def detect_strategy_type(
  win_rate: float = 0.0,
  profit_factor: float = 0.0,
  avg_win: float = 0.0,
  avg_loss: float = 0.0,
) -> str:
  """Auto-detect strategy type from OOS trade metrics.

  Returns one of: "trend_following", "mean_reversion", "mixed".

  Detection logic:
    - win_rate < 45% AND payoff > 2.0 → trend_following
    - win_rate > 55% AND payoff < 1.5 → mean_reversion
    - everything else → mixed

  Payoff ratio = avg_win / abs(avg_loss).  Falls back to profit_factor
  heuristic if avg_win/avg_loss not available.
  """
  # Compute payoff ratio
  if avg_win > 0 and avg_loss != 0:
    payoff = avg_win / abs(avg_loss)
  elif profit_factor > 0 and win_rate > 0:
    # Approximate: PF = (WR * avg_win) / ((1-WR) * avg_loss)
    # => payoff ≈ PF * (1-WR/100) / (WR/100)
    wr_frac = win_rate / 100.0
    if wr_frac > 0 and wr_frac < 1:
      payoff = profit_factor * (1 - wr_frac) / wr_frac
    else:
      payoff = 1.0
  else:
    payoff = 1.0

  if win_rate < 45 and payoff > 2.0:
    return "trend_following"
  if win_rate > 55 and payoff < 1.5:
    return "mean_reversion"
  return "mixed"


# Weight presets per strategy type
_WEIGHTS = {
  "mixed": {
    "stability": 0.25,
    "wfe": 0.25,
    "return_dd": 0.20,
    "sortino": 0.15,
    "pf": 0.15,
  },
  "trend_following": {
    "stability": 0.10,
    "wfe": 0.25,
    "return_dd": 0.20,
    "sortino": 0.30,
    "pf": 0.15,
  },
  "mean_reversion": {
    "stability": 0.30,
    "wfe": 0.25,
    "return_dd": 0.20,
    "sortino": 0.10,
    "pf": 0.15,
  },
}


def compute_portfolio_grade(
  stability_r2: float = 0.0,
  wfe: float = 0.0,
  return_to_dd: float = 0.0,
  sortino: float = 0.0,
  profit_factor: float = 0.0,
  strategy_type: str = "mixed",
) -> tuple[str, float]:
  """Return (letter_grade, numeric_score) from robustness metrics.

  Weights adapt based on strategy_type.

  Score is 0-1 weighted sum.  Letter thresholds:
    >= 0.90 = A+, >= 0.80 = A, >= 0.70 = B+, >= 0.60 = B,
    >= 0.50 = C+, >= 0.40 = C, >= 0.30 = D, >= 0.20 = D-, < 0.20 = F

  Profitability floor: a profitable portfolio (PF > 1.0) cannot receive F.
    PF >= 1.0  → minimum grade D-
    PF >= 1.3  → minimum grade D
  This prevents regime-punished but profitable strategies from being graded
  the same as strategies that are actually losing money.
  """
  w = _WEIGHTS.get(strategy_type, _WEIGHTS["mixed"])

  # Normalize each component to 0-1
  s_stability = max(0.0, min(stability_r2, 1.0))
  s_wfe = max(0.0, min(wfe, 1.0))
  s_return_dd = max(0.0, min(return_to_dd / 5.0, 1.0))
  s_sortino = max(0.0, min(sortino / 3.0, 1.0))
  s_pf = max(0.0, min((profit_factor - 1.0) / 2.0, 1.0))

  score = (
    w["stability"] * s_stability
    + w["wfe"] * s_wfe
    + w["return_dd"] * s_return_dd
    + w["sortino"] * s_sortino
    + w["pf"] * s_pf
  )
  score = round(score, 4)

  if score >= 0.90:
    grade = "A+"
  elif score >= 0.80:
    grade = "A"
  elif score >= 0.70:
    grade = "B+"
  elif score >= 0.60:
    grade = "B"
  elif score >= 0.50:
    grade = "C+"
  elif score >= 0.40:
    grade = "C"
  elif score >= 0.30:
    grade = "D"
  elif score >= 0.20:
    grade = "D-"
  else:
    grade = "F"

  # Profitability floor — a profitable strategy should never get F.
  # F is reserved for strategies that are genuinely losing money.
  if profit_factor >= 1.3 and grade == "F":
    grade = "D"
  elif profit_factor >= 1.0 and grade == "F":
    grade = "D-"

  return grade, score


def get_grade_weights(strategy_type: str = "mixed") -> dict[str, float]:
  """Return the weight dict for a given strategy type (for tooltip display)."""
  return dict(_WEIGHTS.get(strategy_type, _WEIGHTS["mixed"]))


# Human-readable labels for strategy types
STRATEGY_TYPE_LABELS = {
  "trend_following": "Trend Following",
  "mean_reversion": "Mean Reversion",
  "mixed": "Mixed / Hybrid",
}
