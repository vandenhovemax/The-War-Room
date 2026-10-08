"""Portfolio analysis module — correlation, optimization, Monte Carlo."""
from backend.analysis.correlation import get_correlation_matrix
from backend.analysis.daily_pnl import build_daily_pnl_matrix
from backend.analysis.fat_tail_bootstrap import fat_tail_bootstrap
from backend.analysis.monte_carlo import monte_carlo_simulation
from backend.analysis.optimizer import optimize_portfolio
from backend.analysis.optimizer_split import optimize_split_portfolio
from backend.analysis.random_exit import random_exit_stress_test
from backend.analysis.robustness_scorecard import compute_grades
from backend.analysis.scoring import SCORING_FUNCTIONS, calculate_stability

__all__ = [
  "build_daily_pnl_matrix",
  "get_correlation_matrix",
  "SCORING_FUNCTIONS",
  "calculate_stability",
  "optimize_portfolio",
  "optimize_split_portfolio",
  "monte_carlo_simulation",
  "fat_tail_bootstrap",
  "random_exit_stress_test",
  "compute_grades",
]
