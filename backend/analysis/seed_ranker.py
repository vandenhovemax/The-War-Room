"""
seed_ranker.py — Trend + Mean-Reversion Quality Metrics
==========================================================

Unified module that grades every asset as:
  - TREND-suitable (high TER, Hurst > 0.5, positive AC)
  - MEAN-REVERSION-suitable (low TER, Hurst < 0.5, negative AC, high MRI)
  - NEUTRAL (no clear edge either way)

Each asset gets TWO grades:
  - trend_grade:    how good it is for trend-following strategies
  - meanrev_grade:  how good it is for mean-reversion strategies
  - regime:         "TREND" / "MEANREV" / "NEUTRAL" classification

This lets you:
  1. Filter assets BY STRATEGY TYPE at batch backtest level
  2. Use the right metrics as seed ranking for each strategy type
  3. Avoid putting a mean-reverting asset into a trend optimizer (or vice versa)

All functions: pure numpy/pandas, no scipy.
"""

import numpy as np
import pandas as pd
from typing import Optional


# ==========================================================================
# 0. SPARSE PnL HELPER
# ==========================================================================

def _active_returns(returns: np.ndarray) -> np.ndarray:
    """
    Strip zero-PnL days from daily returns.

    When an asset only has 5-20 positions per year, most daily PnL values
    are 0 (no position open). Metrics like Hurst, AC, VR, Half-Life need
    continuous non-zero returns to be meaningful — zero-heavy series produce
    garbage (e.g. AC falsely positive because 0 autocorrelates with 0).

    This extracts only the non-zero days = the days a position was active.
    """
    mask = returns != 0.0
    active = returns[mask]
    return active if len(active) >= 10 else returns  # fallback if too few


# ==========================================================================
# 1. SHARED CORE METRICS (used by both trend and mean-reversion)
# ==========================================================================

def trend_efficiency_ratio(equity_curve: np.ndarray) -> float:
    """
    TER = |equity[end] - equity[start]| / sum(|step[i] - step[i-1]|)
    Range [0, 1]. High = clean trend. Low = choppy (good for mean-rev).
    """
    if len(equity_curve) < 2:
        return 0.0
    net_move = abs(equity_curve[-1] - equity_curve[0])
    total_path = np.sum(np.abs(np.diff(equity_curve)))
    if total_path == 0:
        return 0.0
    return float(net_move / total_path)


def hurst_exponent(returns: np.ndarray, min_window: int = 20,
                   max_window: Optional[int] = None) -> float:
    """
    Hurst Exponent via Rescaled Range (R/S).
    H > 0.5 = trending. H < 0.5 = mean-reverting. H = 0.5 = random walk.
    Uses only active (non-zero) returns to avoid zero-PnL contamination.
    """
    returns = _active_returns(returns)
    n = len(returns)
    if n < min_window * 2:
        return 0.5

    if max_window is None:
        max_window = n // 2

    window_sizes = []
    w = min_window
    while w <= max_window:
        window_sizes.append(w)
        w = int(w * 1.5)
        if w == window_sizes[-1]:
            w += 1

    if len(window_sizes) < 3:
        return 0.5

    log_rs = []
    log_n = []

    for w in window_sizes:
        n_chunks = n // w
        if n_chunks < 1:
            continue
        rs_values = []
        for i in range(n_chunks):
            chunk = returns[i * w:(i + 1) * w]
            mean_chunk = np.mean(chunk)
            deviations = np.cumsum(chunk - mean_chunk)
            R = np.max(deviations) - np.min(deviations)
            S = np.std(chunk, ddof=1)
            if S > 0:
                rs_values.append(R / S)
        if len(rs_values) > 0:
            log_rs.append(np.log(np.mean(rs_values)))
            log_n.append(np.log(w))

    if len(log_rs) < 3:
        return 0.5

    log_n_arr = np.array(log_n)
    log_rs_arr = np.array(log_rs)
    n_pts = len(log_n_arr)
    sum_x = np.sum(log_n_arr)
    sum_y = np.sum(log_rs_arr)
    sum_xy = np.sum(log_n_arr * log_rs_arr)
    sum_xx = np.sum(log_n_arr ** 2)
    denom = n_pts * sum_xx - sum_x ** 2
    if denom == 0:
        return 0.5
    H = float((n_pts * sum_xy - sum_x * sum_y) / denom)
    return max(0.0, min(1.0, H))


def gain_to_pain_ratio(returns: np.ndarray) -> float:
    """
    GPR = sum(all returns) / abs(sum(negative returns))
    Works for both trend and mean-rev — just measures reward vs pain.
    """
    if len(returns) == 0:
        return 0.0
    total = np.sum(returns)
    pain = abs(np.sum(returns[returns < 0]))
    if pain == 0:
        return 10.0 if total > 0 else 0.0
    return float(total / pain)


def autocorrelation(returns: np.ndarray, lag: int = 1) -> float:
    """
    AC(lag) = corr(returns[t], returns[t-lag])
    Positive = trending. Negative = mean-reverting.
    Uses only active (non-zero) returns to avoid zero-PnL contamination.
    """
    returns = _active_returns(returns)
    n = len(returns)
    if n <= lag + 1:
        return 0.0
    x = returns[lag:]
    y = returns[:-lag]
    mx, my = np.mean(x), np.mean(y)
    sx, sy = np.std(x), np.std(y)
    if sx == 0 or sy == 0:
        return 0.0
    corr = np.mean((x - mx) * (y - my)) / (sx * sy)
    return float(np.clip(corr, -1.0, 1.0))


def deflated_sharpe_has_confidence(
    returns: np.ndarray, n_trials: int = 20,
) -> bool:
    """Companion to deflated_sharpe — True if the returns sample is large
    enough for the DSR to carry real statistical meaning (≥30 active
    returns, ≥2 trials). When False, deflated_sharpe() returns the 0.50
    neutral default, which is indistinguishable from a real DSR of 0.50 —
    downstream callers that gate on "DSR ≥ 0.50" should also check this
    flag to avoid treating insufficient data as a pass.
    """
    active = _active_returns(returns)
    return len(active) >= 30 and n_trials >= 2


def deflated_sharpe(returns: np.ndarray, n_trials: int = 20,
                    periods: int = 365) -> float:
    """
    DSR — probability that observed Sharpe exceeds expected max from N noise trials.
    Works for both strategy types — just measures statistical significance.

    Uses only active (non-zero) returns. If fewer than 30 active returns,
    returns neutral 0.50 instead of penalizing — low trade count per asset
    is expected when significance comes from the ensemble (353+ assets).

    Callers that gate on the numeric DSR value (e.g. "DSR ≥ 0.50 = pass")
    should also call :func:`deflated_sharpe_has_confidence` to distinguish
    the defaulted-neutral case from a real sample that scored exactly 0.50.
    """
    returns = _active_returns(returns)
    T = len(returns)
    if T < 10 or n_trials < 2:
        return 0.5  # neutral, not 0.0 — don't penalize lack of data

    # If very few active returns, signal is too noisy to judge → neutral
    if T < 30:
        return 0.5

    mu = np.mean(returns)
    std = np.std(returns, ddof=1)
    if std == 0:
        return 0.5  # neutral, not 0.0

    sr = (mu / std) * np.sqrt(periods)
    z = (returns - mu) / std
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4))

    se_inner = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2) / (T - 1)
    if se_inner <= 0:
        return 0.0
    se_sr = np.sqrt(se_inner)

    var_sr = 1.0 / (T - 1)
    gamma_em = 0.5772156649
    sr_star = np.sqrt(var_sr) * (
        (1.0 - gamma_em) * _norm_ppf(1.0 - 1.0 / n_trials)
        + gamma_em * _norm_ppf(1.0 - 1.0 / (n_trials * np.e))
    )

    z_score = (sr - sr_star) / se_sr
    return float(np.clip(_norm_cdf(z_score), 0.0, 1.0))


# ==========================================================================
# 2. MEAN-REVERSION SPECIFIC METRICS
# ==========================================================================

def mean_reversion_intensity(returns: np.ndarray) -> float:
    """
    Mean Reversion Intensity (MRI).

    MRI = -AC(1) * (1 - TER) * crossing_rate

    Combines three signals:
      - Negative AC -> returns reverse (the core mean-rev signal)
      - Low TER -> choppy path (more opportunities for mean-rev entries)
      - High crossing rate -> price crosses its mean often (actionable)

    Range: roughly [0, 1]. Higher = stronger mean-reversion behavior.
    Returns 0 if the asset doesn't mean-revert.
    """
    if len(returns) < 20:
        return 0.0

    ac = autocorrelation(returns, lag=1)
    equity = np.cumsum(returns)
    ter = trend_efficiency_ratio(equity)
    cr = _crossing_rate(returns)

    # Only score positively if AC is negative (actual mean-reversion)
    if ac >= 0:
        return 0.0

    mri = (-ac) * (1.0 - ter) * cr
    return float(np.clip(mri, 0.0, 1.0))


def variance_ratio(returns: np.ndarray, lag: int = 5) -> float:
    """
    Variance Ratio test (Lo & MacKinlay, 1988).

    VR(q) = Var(r_q) / (q * Var(r_1))

    Where r_q = q-period returns, r_1 = 1-period returns.

    VR = 1.0 -> random walk
    VR < 1.0 -> mean-reverting (returns cancel out over longer periods)
    VR > 1.0 -> trending (returns compound over longer periods)

    Uses only active (non-zero) returns to avoid zero-PnL contamination.
    """
    returns = _active_returns(returns)
    n = len(returns)
    if n < lag * 2:
        return 1.0  # assume random walk

    var_1 = np.var(returns, ddof=1)
    if var_1 == 0:
        return 1.0

    # q-period returns
    q_returns = np.array([
        np.sum(returns[i:i + lag])
        for i in range(0, n - lag + 1, lag)
    ])

    if len(q_returns) < 3:
        return 1.0

    var_q = np.var(q_returns, ddof=1)
    vr = var_q / (lag * var_1)

    return float(vr)


def half_life(returns: np.ndarray) -> float:
    """
    Half-life of mean reversion (Ornstein-Uhlenbeck model).

    Fits: P(t) - P(t-1) = theta * (P(t-1) - mean) + noise
    Half-life = -ln(2) / ln(1 + theta)

    Lower half-life = faster snap-back = better for mean-reversion.
    Returns the half-life in periods (active days, not calendar days).
    Returns np.inf if not mean-reverting.
    Uses only active (non-zero) returns for the OU fit.
    """
    returns = _active_returns(returns)
    if len(returns) < 20:
        return float('inf')

    prices = np.cumsum(returns)  # synthetic price from PnL

    y = np.diff(prices)  # P(t) - P(t-1)
    x = prices[:-1] - np.mean(prices[:-1])  # P(t-1) - mean

    # Simple OLS: y = theta * x + epsilon
    sum_xy = np.sum(x * y)
    sum_xx = np.sum(x * x)

    if sum_xx == 0:
        return float('inf')

    theta = sum_xy / sum_xx

    # theta must be negative for mean-reversion
    if theta >= 0:
        return float('inf')

    # Half-life = -ln(2) / ln(1 + theta)
    inner = 1.0 + theta
    if inner <= 0:
        return 1.0  # instant reversion, cap at 1

    hl = -np.log(2) / np.log(inner)
    return float(max(1.0, min(hl, 500.0)))  # clamp to [1, 500]


def _crossing_rate(returns: np.ndarray, use_raw: bool = False) -> float:
    """
    How often the cumulative PnL crosses its rolling mean.
    Range [0, 1]. High = frequently crossing = good for mean-rev.

    NOTE: This uses the FULL equity curve (including flat days) because
    crossings of the equity curve are real events — a flat stretch followed
    by a move that crosses the mean IS a crossing. Stripping zeros here
    would remove information about how long the equity stayed flat.
    """
    if len(returns) < 20:
        return 0.0

    equity = np.cumsum(returns)
    window = max(20, len(equity) // 10)
    rolling_mean = pd.Series(equity).rolling(window, min_periods=1).mean().values

    diff = equity - rolling_mean
    crossings = np.sum(np.abs(np.diff(np.sign(diff))) > 0)

    # Normalize by number of possible crossings
    max_crossings = len(diff) - 1
    if max_crossings == 0:
        return 0.0

    return float(crossings / max_crossings)


# ==========================================================================
# 3. TREND-FOLLOWING SCORE (same as v1)
# ==========================================================================

def trend_score(returns: np.ndarray, n_pool: int = 20) -> float:
    """
    Combined trend-quality score. Higher = better for trend-following.

    TrendScore = 0.30 * TER
               + 0.25 * max(0, Hurst - 0.5) * 2    # scaled to [0, 1]
               + 0.25 * sigmoid(GPR)
               + 0.20 * max(0, AC(1))

    Range: roughly [0, 1].
    """
    equity = np.cumsum(returns)
    ter = trend_efficiency_ratio(equity)
    h = hurst_exponent(returns)
    gpr = gain_to_pain_ratio(returns)
    ac = autocorrelation(returns, lag=1)

    sig_gpr = 1.0 / (1.0 + np.exp(-gpr))

    score = (
        0.30 * ter
        + 0.25 * max(0.0, (h - 0.5)) * 2.0
        + 0.25 * sig_gpr
        + 0.20 * max(0.0, ac)
    )
    return float(np.clip(score, 0.0, 1.0))


# ==========================================================================
# 4. MEAN-REVERSION SCORE
# ==========================================================================

def meanrev_score(returns: np.ndarray) -> float:
    """
    Combined mean-reversion quality score. Higher = better for mean-rev strategies.

    MeanRevScore = 0.25 * MRI
                 + 0.25 * max(0, 0.5 - Hurst) * 2     # how far below 0.5
                 + 0.20 * max(0, 1 - VR(5))            # VR < 1 = mean-reverting
                 + 0.15 * norm_half_life                # faster snap-back = better
                 + 0.15 * crossing_rate                 # more crossings = more opportunities

    Range: roughly [0, 1].
    """
    mri = mean_reversion_intensity(returns)
    h = hurst_exponent(returns)
    vr = variance_ratio(returns, lag=5)
    hl = half_life(returns)
    cr = _crossing_rate(returns)

    # Normalize half-life: 1 day = perfect (1.0), 100+ days = useless (0.0)
    # Using inverse: norm_hl = 1 / (1 + hl/10)
    norm_hl = 1.0 / (1.0 + hl / 10.0) if hl < float('inf') else 0.0

    score = (
        0.25 * mri
        + 0.25 * max(0.0, (0.5 - h)) * 2.0
        + 0.20 * max(0.0, 1.0 - vr)
        + 0.15 * norm_hl
        + 0.15 * cr
    )
    return float(np.clip(score, 0.0, 1.0))


# ==========================================================================
# 5. UNIFIED GRADING + CLASSIFICATION
# ==========================================================================

def grade_letter(score: float) -> str:
    """Convert a 0-1 score to a letter grade."""
    if score >= 0.45:
        return "A+"
    if score >= 0.35:
        return "A"
    if score >= 0.28:
        return "B+"
    if score >= 0.22:
        return "B"
    if score >= 0.16:
        return "C+"
    if score >= 0.10:
        return "C"
    if score >= 0.05:
        return "D"
    return "F"


# Grade ordering for comparisons (higher = better)
GRADE_ORDER = {"A+": 7, "A": 6, "B+": 5, "B": 4, "C+": 3, "C": 2, "D": 1, "F": 0}


def grade_letter_zoomed(score: float, all_scores: list[float]) -> str:
    """Auto-zoom grading: normalize score within batch range, then grade.

    Maps the batch's actual min-max range to 0-1 before applying
    standard grade_letter() thresholds. Spreads grades A+ to F
    even when raw scores cluster in a narrow band (e.g. 0.10-0.25).
    """
    if len(all_scores) < 2:
        return grade_letter(score)
    min_s = min(all_scores)
    max_s = max(all_scores)
    if max_s <= min_s:
        return "C"
    normalized = (score - min_s) / (max_s - min_s)
    return grade_letter(normalized)


def classify_regime(trend_sc: float, meanrev_sc: float,
                    threshold: float = 0.05) -> str:
    """
    Classify asset regime based on trend vs mean-rev scores.

    Returns "TREND", "MEANREV", or "NEUTRAL".
    threshold = minimum gap between scores to classify (avoids noise).
    """
    diff = trend_sc - meanrev_sc
    if diff > threshold:
        return "TREND"
    elif diff < -threshold:
        return "MEANREV"
    else:
        return "NEUTRAL"


def grade_all_assets(
    daily_pnl: pd.DataFrame,
    n_pool: Optional[int] = None,
) -> pd.DataFrame:
    """
    Grade every asset for BOTH trend-following and mean-reversion suitability.

    This is the main function to call at batch backtest level.
    Each asset gets:
      - trend_score, trend_grade
      - meanrev_score, meanrev_grade
      - regime classification (TREND / MEANREV / NEUTRAL)
      - all individual metrics for transparency

    LOW TRADE COUNT HANDLING:
    Assets with 5-20 positions are handled correctly. Metrics that need
    continuous returns (Hurst, AC, VR, Half-Life, DSR) automatically strip
    zero-PnL days and compute on active returns only. DSR returns neutral
    (0.50) for assets with < 30 active return days. TER and GPR work on
    the full series (zeros don't affect them). No asset is penalized for
    having few trades — significance comes from the ensemble.

    Parameters
    ----------
    daily_pnl : pd.DataFrame
        Daily PnL, one column per asset.
    n_pool : int, optional
        Pool size for DSR. Defaults to number of columns.

    Returns
    -------
    pd.DataFrame sorted by trend_score descending, with all metrics + grades.
    """
    if n_pool is None:
        n_pool = len(daily_pnl.columns)

    rows = []
    for asset in daily_pnl.columns:
        pnl = daily_pnl[asset].values
        equity = np.cumsum(pnl)

        # Shared metrics
        ter = trend_efficiency_ratio(equity)
        h = hurst_exponent(pnl)
        gpr = gain_to_pain_ratio(pnl)
        ac1 = autocorrelation(pnl, lag=1)
        dsr = deflated_sharpe(pnl, n_trials=n_pool)

        # Mean-reversion specific
        mri = mean_reversion_intensity(pnl)
        vr = variance_ratio(pnl, lag=5)
        hl = half_life(pnl)
        cr = _crossing_rate(pnl)

        # Composite scores
        t_score = trend_score(pnl, n_pool=n_pool)
        m_score = meanrev_score(pnl)

        # Classification
        regime = classify_regime(t_score, m_score)

        rows.append({
            "asset":          asset,
            # --- Regime ---
            "regime":         regime,
            # --- Trend ---
            "trend_score":    round(t_score, 4),
            "trend_grade":    grade_letter(t_score),
            # --- Mean-Rev ---
            "meanrev_score":  round(m_score, 4),
            "meanrev_grade":  grade_letter(m_score),
            # --- Shared metrics ---
            "TER":            round(ter, 4),
            "Hurst":          round(h, 4),
            "GPR":            round(gpr, 4),
            "AC(1)":          round(ac1, 4),
            "DSR":            round(dsr, 4),
            # --- Mean-rev specific ---
            "MRI":            round(mri, 4),
            "VR(5)":          round(vr, 4),
            "HalfLife":       round(hl, 1) if hl < float('inf') else "inf",
            "CrossRate":      round(cr, 4),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("trend_score", ascending=False).reset_index(drop=True)
    return df


# ==========================================================================
# 6. PRE-FILTERS (strategy-type aware)
# ==========================================================================

def prefilter_for_trend(
    daily_pnl: pd.DataFrame,
    hurst_min: float = 0.50,
    ter_percentile: int = 25,
) -> list[str]:
    """Remove assets unsuitable for trend-following."""
    assets = list(daily_pnl.columns)
    metrics = {}
    for asset in assets:
        pnl = daily_pnl[asset].values
        equity = np.cumsum(pnl)
        metrics[asset] = {
            "hurst": hurst_exponent(pnl),
            "ter": trend_efficiency_ratio(equity),
        }

    survivors = [a for a in assets if metrics[a]["hurst"] >= hurst_min]
    if len(survivors) == 0:
        return assets

    ter_values = [metrics[a]["ter"] for a in survivors]
    ter_cutoff = np.percentile(ter_values, ter_percentile)
    survivors = [a for a in survivors if metrics[a]["ter"] >= ter_cutoff]
    return survivors if len(survivors) > 0 else assets


def prefilter_for_meanrev(
    daily_pnl: pd.DataFrame,
    hurst_max: float = 0.50,
    vr_max: float = 1.0,
    min_crossing_rate: float = 0.05,
) -> list[str]:
    """
    Remove assets unsuitable for mean-reversion.

    Kills:
      - Hurst >= hurst_max (trending, not mean-reverting)
      - VR(5) >= vr_max (returns compound, not cancel)
      - Crossing rate below minimum (not enough opportunities)
    """
    assets = list(daily_pnl.columns)
    metrics = {}
    for asset in assets:
        pnl = daily_pnl[asset].values
        metrics[asset] = {
            "hurst": hurst_exponent(pnl),
            "vr": variance_ratio(pnl, lag=5),
            "cr": _crossing_rate(pnl),
        }

    survivors = [
        a for a in assets
        if metrics[a]["hurst"] < hurst_max
        and metrics[a]["vr"] < vr_max
        and metrics[a]["cr"] >= min_crossing_rate
    ]

    return survivors if len(survivors) > 0 else assets


# ==========================================================================
# 7. POST-FILTER (works for both strategy types)
# ==========================================================================

def postfilter_ac(
    daily_pnl: pd.DataFrame,
    assets: list[str],
    strategy_type: str = "trend",
    lag: int = 1,
) -> dict[str, dict]:
    """
    Post-optimization AC check. Flags mismatched assets.

    For trend strategies: flags negative AC (mean-reverting at signal freq)
    For meanrev strategies: flags positive AC (trending at signal freq)
    """
    results = {}
    for asset in assets:
        if asset not in daily_pnl.columns:
            continue
        pnl = daily_pnl[asset].values
        ac = autocorrelation(pnl, lag=lag)

        if strategy_type == "trend":
            flagged = ac < 0
        elif strategy_type == "meanrev":
            flagged = ac > 0
        else:
            flagged = False

        results[asset] = {"ac": round(ac, 4), "flagged": flagged}
    return results


# ==========================================================================
# 8. SEED METRIC SCORING (for _score_metric integration)
# ==========================================================================

def score_metric(metric: str, pnl: np.ndarray, n_pool: int = 20) -> float:
    """
    Score a single asset on a given metric.
    Returns NEGATED values (lower = better) for existing _score_metric() convention.

    Metrics available:
      Shared:    "gpr", "ter", "hurst", "dsr"
      Trend:     "ac_trend" (higher AC = better for trend)
      Mean-rev:  "ac_meanrev" (lower/more negative AC = better for mean-rev)
                 "mri", "vr", "half_life", "crossing_rate"
      Legacy:    "ac" (same as ac_trend, kept for backward compat)
      Composite: "trend_score", "meanrev_score"
    """
    if metric == "gpr":
        return -min(gain_to_pain_ratio(pnl), 10.0)
    elif metric == "ter":
        equity = np.cumsum(pnl)
        return -trend_efficiency_ratio(equity)
    elif metric == "hurst":
        return -hurst_exponent(pnl)
    elif metric == "dsr":
        return -deflated_sharpe(pnl, n_trials=n_pool)
    elif metric in ("ac", "ac_trend"):
        # Higher AC = more trending = better for trend-following
        return -autocorrelation(pnl, lag=1)
    elif metric == "ac_meanrev":
        # More negative AC = more mean-reverting = better for mean-rev
        # So we return +AC (positive AC = bad for mean-rev = higher score)
        return autocorrelation(pnl, lag=1)
    elif metric == "mri":
        return -mean_reversion_intensity(pnl)
    elif metric == "vr":
        # Lower VR = better for mean-rev
        # VR < 1.0 → mean-reverting → negative score (good)
        # VR >= 1.0 → trending → positive score (bad, penalized)
        vr = variance_ratio(pnl, lag=5)
        if vr < 1.0:
            return -(1.0 - vr)  # e.g. VR=0.7 → -0.3 (good)
        else:
            return (vr - 1.0)   # e.g. VR=1.5 → +0.5 (penalized)
    elif metric == "half_life":
        # Lower half-life = better for mean-rev
        # Normalized to [-1, 0] range like all other metrics
        # Uses same normalization as meanrev_score: 1/(1+hl/10)
        hl = half_life(pnl)
        if hl == float('inf'):
            return 0.0  # not mean-reverting → worst score
        return -1.0 / (1.0 + hl / 10.0)  # hl=1 → -0.91, hl=100 → -0.09
    elif metric == "crossing_rate":
        return -_crossing_rate(pnl)
    elif metric == "trend_score":
        return -trend_score(pnl, n_pool=n_pool)
    elif metric == "meanrev_score":
        return -meanrev_score(pnl)
    else:
        raise ValueError(f"Unknown metric: {metric}")


# ==========================================================================
# 9. PORTFOLIO-LEVEL OBJECTIVES (for scoring.py / L1/L2/L3)
# ==========================================================================

def portfolio_gpr(pnl: pd.Series) -> float:
    """Gain-to-Pain of combined portfolio. Negated (lower=better)."""
    values = pnl.values
    total = np.sum(values)
    pain = abs(np.sum(values[values < 0]))
    if pain == 0:
        # No losses: best score only if it actually made money — a flat,
        # never-traded series used to tie for the best possible GPR.
        return -10.0 if total > 0 else float("inf")
    return -float(min(total / pain, 10.0))


def portfolio_ter(pnl: pd.Series) -> float:
    """Trend Efficiency of portfolio equity. Negated (lower=better)."""
    equity = np.cumsum(pnl.values)
    return -trend_efficiency_ratio(equity)


def portfolio_keep_score(pnl: pd.Series) -> float:
    """Combined trend-quality of portfolio. Negated (lower=better)."""
    values = pnl.values
    return -trend_score(values)


def portfolio_meanrev_score(pnl: pd.Series) -> float:
    """Combined mean-reversion quality of portfolio. Negated (lower=better)."""
    values = pnl.values
    return -meanrev_score(values)


def portfolio_crossing_rate(pnl: pd.Series) -> float:
    """Crossing rate of portfolio equity. Negated (lower=better).
    Higher crossing = more mean-rev opportunities in the combined equity."""
    return -_crossing_rate(pnl.values)


# ==========================================================================
# 10. DSR BENCHMARK GATE
# ==========================================================================

def dsr_benchmark_multiplier(pnl: np.ndarray, n_pool: int = 20) -> float:
    """
    DSR < 0.50 -> 0.5x penalty (likely noise)
    DSR 0.50-0.80 -> 1.0x neutral
    DSR > 0.80 -> 1.1x boost (statistically significant)

    Note: deflated_sharpe() already returns 0.50 (neutral) for assets with
    fewer than 30 active returns, so low-trade-count assets get 1.0x by
    default — they are neither penalized nor boosted. This is correct
    because per-asset significance is not expected; the edge is validated
    at the ensemble level.
    """
    dsr = deflated_sharpe(pnl, n_trials=n_pool)
    if dsr < 0.50:
        return 0.5
    elif dsr > 0.80:
        return 1.1
    else:
        return 1.0


# ==========================================================================
# INTERNAL HELPERS
# ==========================================================================

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + _erf(x / np.sqrt(2.0)))

def _norm_ppf(p: float) -> float:
    if p <= 0:
        return -6.0
    if p >= 1:
        return 6.0
    if p < 0.5:
        return -_norm_ppf(1.0 - p)
    t = np.sqrt(-2.0 * np.log(1.0 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t ** 2) / (1.0 + d1 * t + d2 * t ** 2 + d3 * t ** 3)

def _erf(x: float) -> float:
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-x * x)
    return sign * y
