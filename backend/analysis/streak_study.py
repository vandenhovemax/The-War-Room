"""Streak study — analyse win/loss streak patterns per strategy.

Uses IS (In-Sample) trades only to determine whether a strategy exhibits
streak dependence (autocorrelation in outcomes), which would justify
Martingale or Anti-Martingale position sizing.

Never uses OOS data — that would contaminate out-of-sample validation.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict

logger = logging.getLogger(__name__)

BE_ZONE = 0.01  # ±$0.01 breakeven threshold


def analyze(trades: list, initial_capital: float = 10_000.0) -> dict:
    """Run full streak analysis on a list of trade objects.

    Args:
        trades: list of BacktestTrade ORM objects (IS trades only).
        initial_capital: starting equity for the simulated comparison.

    Returns:
        dict with transition_matrix, streak_distributions, autocorrelation,
        chi_squared_p, recommendation, comparison, and trade_count.
    """
    # Extract chronological PnL series
    sorted_trades = sorted(trades, key=lambda t: t.exit_time or t.entry_time)
    pnls = [t.net_profit or 0.0 for t in sorted_trades]

    # Convert to win/loss series (skip breakeven)
    outcomes: list[int] = []  # +1 = win, -1 = loss
    for pnl in pnls:
        if pnl > BE_ZONE:
            outcomes.append(1)
        elif pnl < -BE_ZONE:
            outcomes.append(-1)
        # breakeven trades are skipped

    if len(outcomes) < 10:
        return _insufficient_data(len(outcomes))

    # ── Transition matrix ──
    transition = _compute_transition_matrix(outcomes)

    # ── Streak distributions ──
    streaks = _compute_streak_distributions(outcomes)

    # ── Autocorrelation (lag-1) ──
    autocorr = _compute_autocorrelation(outcomes)

    # ── Chi-squared test for independence ──
    chi2_p = _chi_squared_test(outcomes)

    # ── Recommendation ──
    recommendation = _make_recommendation(transition, autocorr, chi2_p)

    # ── Simulated comparison: Fixed vs Martingale vs Anti-Martingale ──
    comparison = _simulate_comparison(pnls, initial_capital)

    return {
        "trade_count": len(outcomes),
        "total_trades": len(pnls),
        "transition_matrix": transition,
        "streak_distributions": streaks,
        "autocorrelation": round(autocorr, 4),
        "chi_squared_p": round(chi2_p, 4),
        "recommendation": recommendation,
        "comparison": comparison,
    }


def _insufficient_data(n: int) -> dict:
    """Return a stub result when there aren't enough trades."""
    return {
        "trade_count": n,
        "total_trades": n,
        "transition_matrix": {
            "p_win_after_win": None, "p_loss_after_win": None,
            "p_win_after_loss": None, "p_loss_after_loss": None,
        },
        "streak_distributions": {"wins": {}, "losses": {}},
        "autocorrelation": None,
        "chi_squared_p": None,
        "recommendation": {
            "best_method": "fixed",
            "confidence": "low",
            "reason": f"Only {n} trades — need at least 10 for streak analysis.",
        },
        "comparison": {},
    }


def _compute_transition_matrix(outcomes: list[int]) -> dict:
    """Compute P(W|W), P(L|W), P(W|L), P(L|L) from outcome sequence."""
    counts = {"WW": 0, "WL": 0, "LW": 0, "LL": 0}
    for i in range(1, len(outcomes)):
        prev = "W" if outcomes[i - 1] == 1 else "L"
        curr = "W" if outcomes[i] == 1 else "L"
        counts[prev + curr] += 1

    after_win = counts["WW"] + counts["WL"]
    after_loss = counts["LW"] + counts["LL"]

    return {
        "p_win_after_win": round(counts["WW"] / after_win, 4) if after_win > 0 else None,
        "p_loss_after_win": round(counts["WL"] / after_win, 4) if after_win > 0 else None,
        "p_win_after_loss": round(counts["LW"] / after_loss, 4) if after_loss > 0 else None,
        "p_loss_after_loss": round(counts["LL"] / after_loss, 4) if after_loss > 0 else None,
        "counts": counts,
        "after_win_total": after_win,
        "after_loss_total": after_loss,
    }


def _compute_streak_distributions(outcomes: list[int]) -> dict:
    """Count how often each streak length occurs for wins and losses."""
    win_streaks: dict[int, int] = defaultdict(int)
    loss_streaks: dict[int, int] = defaultdict(int)

    current_streak = 0
    current_type = 0  # +1 or -1

    for o in outcomes:
        if o == current_type:
            current_streak += 1
        else:
            # Record the completed streak
            if current_streak > 0:
                if current_type == 1:
                    win_streaks[current_streak] += 1
                elif current_type == -1:
                    loss_streaks[current_streak] += 1
            current_type = o
            current_streak = 1

    # Record final streak
    if current_streak > 0:
        if current_type == 1:
            win_streaks[current_streak] += 1
        elif current_type == -1:
            loss_streaks[current_streak] += 1

    # Convert to sorted dicts with string keys for JSON
    return {
        "wins": {str(k): v for k, v in sorted(win_streaks.items())},
        "losses": {str(k): v for k, v in sorted(loss_streaks.items())},
        "max_win_streak": max(win_streaks.keys()) if win_streaks else 0,
        "max_loss_streak": max(loss_streaks.keys()) if loss_streaks else 0,
    }


def _compute_autocorrelation(outcomes: list[int]) -> float:
    """Compute lag-1 autocorrelation of the outcome series.

    Positive = streaky (wins follow wins, losses follow losses)
    Negative = mean-reverting (wins follow losses, losses follow wins)
    ~0 = independent (no pattern)
    """
    n = len(outcomes)
    if n < 3:
        return 0.0

    mean = sum(outcomes) / n
    var = sum((o - mean) ** 2 for o in outcomes) / n
    if var < 1e-10:
        return 0.0

    cov = sum((outcomes[i] - mean) * (outcomes[i + 1] - mean)
              for i in range(n - 1)) / (n - 1)
    return cov / var


def _chi_squared_test(outcomes: list[int]) -> float:
    """Test whether transitions are independent of previous outcome.

    Returns p-value. Low p-value (<0.05) = significant streak dependence.
    Uses 2x2 contingency table: rows = previous outcome, cols = current outcome.
    """
    counts = {"WW": 0, "WL": 0, "LW": 0, "LL": 0}
    for i in range(1, len(outcomes)):
        prev = "W" if outcomes[i - 1] == 1 else "L"
        curr = "W" if outcomes[i] == 1 else "L"
        counts[prev + curr] += 1

    # 2x2 table: [[WW, WL], [LW, LL]]
    a, b, c, d = counts["WW"], counts["WL"], counts["LW"], counts["LL"]
    n = a + b + c + d
    if n == 0:
        return 1.0

    # Chi-squared with Yates' correction for 2x2
    numerator = n * (abs(a * d - b * c) - n / 2) ** 2
    denom = (a + b) * (c + d) * (a + c) * (b + d)
    if denom == 0:
        return 1.0

    chi2 = numerator / denom

    # Approximate p-value from chi-squared distribution (df=1)
    # Using the complementary error function approximation
    return _chi2_p_value(chi2, df=1)


def _chi2_p_value(chi2: float, df: int = 1) -> float:
    """Approximate p-value for chi-squared statistic with given df.

    Uses the regularized incomplete gamma function approximation.
    For df=1, this simplifies to P = erfc(sqrt(chi2/2)).
    """
    if chi2 <= 0:
        return 1.0
    # For df=1: p = erfc(sqrt(chi2/2))
    return math.erfc(math.sqrt(chi2 / 2))


def _make_recommendation(
    transition: dict,
    autocorr: float,
    chi2_p: float,
) -> dict:
    """Generate a plain-English recommendation based on streak analysis."""
    p_ww = transition.get("p_win_after_win")
    p_wl = transition.get("p_win_after_loss")
    significant = chi2_p < 0.05

    if p_ww is None or p_wl is None:
        return {
            "best_method": "fixed",
            "confidence": "low",
            "reason": "Insufficient data to compute transition probabilities.",
        }

    # Determine streak type
    if autocorr > 0.05 and p_ww > p_wl:
        streak_type = "persistent"  # wins follow wins → anti-martingale benefits
    elif autocorr < -0.05 and p_wl > p_ww:
        streak_type = "mean_reverting"  # wins follow losses → martingale benefits
    else:
        streak_type = "independent"

    # Confidence based on statistical significance and sample size
    if significant and abs(autocorr) > 0.1:
        confidence = "high"
    elif significant or abs(autocorr) > 0.05:
        confidence = "medium"
    else:
        confidence = "low"

    # Build recommendation
    if streak_type == "persistent":
        return {
            "best_method": "anti_martingale",
            "confidence": confidence,
            "reason": (
                f"Wins tend to follow wins (P(W|W)={p_ww:.1%} vs P(W|L)={p_wl:.1%}). "
                f"Autocorrelation={autocorr:+.3f}. "
                f"{'Statistically significant (p={:.3f})'.format(chi2_p) if significant else 'Not statistically significant (p={:.3f})'.format(chi2_p)}. "
                "Anti-Martingale can exploit winning streaks by sizing up."
            ),
        }
    elif streak_type == "mean_reverting":
        return {
            "best_method": "martingale",
            "confidence": confidence,
            "reason": (
                f"Wins tend to follow losses (P(W|L)={p_wl:.1%} vs P(W|W)={p_ww:.1%}). "
                f"Autocorrelation={autocorr:+.3f}. "
                f"{'Statistically significant (p={:.3f})'.format(chi2_p) if significant else 'Not statistically significant (p={:.3f})'.format(chi2_p)}. "
                "Martingale can exploit mean-reversion by sizing up after losses."
            ),
        }
    else:
        return {
            "best_method": "fixed",
            "confidence": confidence,
            "reason": (
                f"No significant streak pattern detected (P(W|W)={p_ww:.1%}, P(W|L)={p_wl:.1%}). "
                f"Autocorrelation={autocorr:+.3f}, p={chi2_p:.3f}. "
                "Fixed sizing is the safest choice — no evidence that streaks are exploitable."
            ),
        }


def _simulate_comparison(
    pnls: list[float],
    initial_capital: float,
    multiplier: float = 2.0,
    max_streak: int = 3,
) -> dict:
    """Simulate Fixed, Martingale, and Anti-Martingale on the same trade sequence.

    Returns net profit, max drawdown, and final equity for each method.
    """
    results = {}
    for label, mode in [("fixed", "fixed"), ("martingale", "martingale"), ("anti_martingale", "anti_martingale")]:
        eq = initial_capital
        peak = eq
        max_dd = 0.0
        cons_w = 0
        cons_l = 0

        for pnl in pnls:
            # Determine multiplier
            if mode == "martingale":
                streak = cons_l
                capped = min(streak, max_streak)
                mult = multiplier ** capped if capped > 0 else 1.0
            elif mode == "anti_martingale":
                streak = cons_w
                capped = min(streak, max_streak)
                mult = multiplier ** capped if capped > 0 else 1.0
            else:
                mult = 1.0

            eq += pnl * mult
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

            # Update streaks
            if pnl > BE_ZONE:
                cons_w += 1
                cons_l = 0
            elif pnl < -BE_ZONE:
                cons_l += 1
                cons_w = 0

        results[label] = {
            "net_profit": round(eq - initial_capital, 2),
            "final_equity": round(eq, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
        }

    return results
