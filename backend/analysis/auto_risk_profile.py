"""Auto Risk Profile — analyze IS trade streaks per asset per batch to recommend a risk mode.

Logic:
  1. Compute streak statistics from IS trades (consecutive wins/losses).
  2. Build transition patterns: after a win streak ends, how many losses follow?
     After a loss streak ends, how many wins follow? This is the core data.
  3. Classify the asset:
     - High P(2+ consecutive wins) → Anti-Martingale (ride win streaks)
     - High P(2+ consecutive losses) → Martingale (mean-revert after losses)
     - Neither (random / low streak probability) → Fixed (regular risk)
  4. Derive wait-time parameters using 3 methods (best available):
     a) PATTERN-MIRROR: replay past transition patterns to pick the optimal
        start_after / reset_after that would have captured the most streak value.
     b) STATISTICAL: use the run-length distribution of post-transition streaks
        to pick percentile-based thresholds.
     c) FALLBACK: when not enough transition data, use safe/medium/aggressive tiers.
  5. If confidence is below the user's threshold → force Fixed.

Key: recommendations are PER SYMBOL PER BATCH — the same symbol in
different batches (different strategies) will get independent analysis.

The caller provides:
  - base_risk_fixed ($): user-chosen fixed risk amount
  - base_risk_mart ($): base risk for mart/anti-mart modes
  - multiplier: user-chosen multiplier (e.g. 2.0)
  - aggression: "conservative" or "aggressive"
  - min_confidence: below this → force fixed (default 0.4 = 40%)
  - strategy_hint: "trend" | "meanrev" | "auto" — biases thresholds
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from collections import defaultdict

from backend.constants import BE_ZONE

logger = logging.getLogger(__name__)


# ── Default Thresholds ──────────────────────────────────────────────────
ANTI_MART_P2_THRESHOLD = 0.35   # ≥35% of wins come in 2+ streaks → anti-mart
MART_P2_THRESHOLD = 0.35        # ≥35% of losses come in 2+ streaks → mart
MIN_TRADES_FOR_AUTO = 15        # need at least 15 IS trades for reliable stats
MIN_TRANSITIONS_FOR_PATTERN = 5 # need ≥5 transitions to trust pattern-mirror
MIN_TRANSITIONS_FOR_STATS = 3   # need ≥3 transitions for basic statistical method

# Strategy hint adjustments — lower threshold = easier to trigger that mode
TREND_ANTI_MART_THRESHOLD = 0.28
TREND_MART_THRESHOLD = 0.42
MEANREV_ANTI_MART_THRESHOLD = 0.42
MEANREV_MART_THRESHOLD = 0.28

# Fallback tiers (when too few transitions to derive from data)
FALLBACK_TIERS = {
    # (start_after for martingale, reset_after for anti-martingale, max_streak)
    "conservative": (2, 2, 3),
    "moderate":     (1, 3, 3),
    "aggressive":   (0, 4, 4),
}


# ═══════════════════════════════════════════════════════════════════════
# Transition Pattern Analysis
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class TransitionPattern:
    """Captures the run-length pattern when the streak flips.

    For martingale analysis we care about: after a win streak of length W,
    the loss streak that follows has length L. We store all (W → L) pairs.

    For anti-martingale we care about: after a loss streak of length L,
    the win streak that follows has length W.
    """
    # After a win streak ends, how many losses follow? list of loss-run lengths
    win_to_loss_runs: list[int] = field(default_factory=list)
    # After a loss streak ends, how many wins follow? list of win-run lengths
    loss_to_win_runs: list[int] = field(default_factory=list)
    # Full alternating run sequence: [(is_win, run_length), ...]
    run_sequence: list[tuple[bool, int]] = field(default_factory=list)


def build_transition_patterns(pnls: list[float]) -> TransitionPattern:
    """Scan the PnL sequence and build transition patterns.

    Walk through trades, identify alternating win/loss runs, then record
    what happens at each flip: after N wins, how many losses come next?
    After N losses, how many wins come next?
    """
    tp = TransitionPattern()
    if not pnls:
        return tp

    # Build the run sequence
    runs: list[tuple[bool, int]] = []  # (is_win, length)
    current_is_win: bool | None = None
    current_len = 0

    for pnl in pnls:
        if abs(pnl) <= BE_ZONE:
            continue
        is_win = pnl > BE_ZONE
        if current_is_win is None:
            current_is_win = is_win
            current_len = 1
        elif is_win == current_is_win:
            current_len += 1
        else:
            runs.append((current_is_win, current_len))
            current_is_win = is_win
            current_len = 1

    if current_is_win is not None and current_len > 0:
        runs.append((current_is_win, current_len))

    tp.run_sequence = runs

    # Build transitions: look at consecutive pairs
    for i in range(len(runs) - 1):
        prev_is_win, prev_len = runs[i]
        next_is_win, next_len = runs[i + 1]

        if prev_is_win and not next_is_win:
            # Win streak ended → loss streak followed
            tp.win_to_loss_runs.append(next_len)
        elif not prev_is_win and next_is_win:
            # Loss streak ended → win streak followed
            tp.loss_to_win_runs.append(next_len)

    return tp


# ═══════════════════════════════════════════════════════════════════════
# Derivation Method 1: PATTERN-MIRROR
# ═══════════════════════════════════════════════════════════════════════

def _derive_pattern_mirror(
    transition_runs: list[int],
    aggression: str,
) -> tuple[int, int, str] | None:
    """Replay past transitions to find the optimal wait time.

    For MARTINGALE start_after: transition_runs = loss-run lengths after wins.
    We want to know: "after how many losses should I start scaling?"
    Look at the distribution of loss-run lengths. The idea is:
      - If losses typically come in runs of [2, 4, 3, 2, 5, 3], the mode is ~2-3.
      - start_after should be set so we START scaling when we're "in" the pattern.
      - Conservative: start at the point where we've seen enough losses to confirm
        we're in a real streak (higher percentile of run lengths).
      - Aggressive: start earlier.

    For ANTI-MARTINGALE reset_after: transition_runs = win-run lengths after losses.
    We want: "after how many wins should I stop riding and reset?"
    The pattern says typical win streaks are length [3, 5, 2, 4, 3, 6].
      - Conservative: reset at median (stop riding at the typical end).
      - Aggressive: reset at 75th percentile (ride longer).

    Returns (value, max_streak, detail_str) or None if not enough data.
    """
    if len(transition_runs) < MIN_TRANSITIONS_FOR_PATTERN:
        return None

    sorted_runs = sorted(transition_runs)
    n = len(sorted_runs)

    # Compute percentiles
    p25 = sorted_runs[max(0, n // 4)]
    p50 = sorted_runs[n // 2]
    p75 = sorted_runs[max(0, min(n - 1, int(n * 0.75)))]
    mode_val = max(set(sorted_runs), key=sorted_runs.count)
    avg_val = sum(sorted_runs) / n

    detail = (
        f"Pattern-mirror: {n} transitions, "
        f"lengths={sorted_runs[:8]}{'…' if n > 8 else ''}, "
        f"mode={mode_val} avg={avg_val:.1f} "
        f"P25={p25} P50={p50} P75={p75}"
    )

    if aggression == "aggressive":
        # Enter/ride early: use P25 for start_after, P75 for reset_after
        value = max(0, p25 - 1)
        max_streak = max(2, min(p75, 6))
    elif aggression == "conservative":
        # Cautious: wait longer (P50) for start_after, reset at mode for reset_after
        value = max(1, min(p50 - 1, 3))
        max_streak = max(2, min(p50 + 1, 4))
    else:  # moderate
        value = max(0, min(mode_val - 1, 2))
        max_streak = max(2, min(mode_val + 1, 5))

    return (value, max_streak, detail)


# ═══════════════════════════════════════════════════════════════════════
# Derivation Method 2: STATISTICAL
# ═══════════════════════════════════════════════════════════════════════

def _derive_statistical(
    transition_runs: list[int],
    aggression: str,
) -> tuple[int, int, str] | None:
    """Use run-length distribution statistics to derive wait time.

    Builds a frequency histogram of transition run lengths and uses
    probability thresholds to decide the optimal entry/exit point.

    For martingale start_after: We want to start scaling AFTER enough
    losses that we're statistically "due" for a reversal. This means
    looking at what fraction of loss runs end at each length.

    For anti-martingale reset_after: Same logic in reverse — at what
    win-streak length does the probability of continuation drop?

    Returns (value, max_streak, detail_str) or None if not enough data.
    """
    if len(transition_runs) < MIN_TRANSITIONS_FOR_STATS:
        return None

    # Build cumulative distribution: P(run_length <= L)
    max_len = max(transition_runs)
    freq: dict[int, int] = defaultdict(int)
    for r in transition_runs:
        freq[r] += 1

    n = len(transition_runs)
    # P(streak continues past length L) = 1 - CDF(L)
    # This tells us: if we've hit L losses, what's the chance of more?
    cdf: dict[int, float] = {}
    cumsum = 0
    for length in range(1, max_len + 1):
        cumsum += freq.get(length, 0)
        cdf[length] = cumsum / n

    # Find the "sweet spot" where continuation probability is still meaningful
    # For conservative: wait until ~60% of runs would have already ended
    # For aggressive: start when only ~30% would have ended
    if aggression == "aggressive":
        target_cdf = 0.30
    elif aggression == "conservative":
        target_cdf = 0.60
    else:
        target_cdf = 0.45

    sweet_spot = 1
    for length in range(1, max_len + 1):
        if cdf.get(length, 0) >= target_cdf:
            sweet_spot = length
            break
    else:
        sweet_spot = max_len

    avg_val = sum(transition_runs) / n
    detail = (
        f"Statistical: {n} transitions, avg={avg_val:.1f}, max={max_len}, "
        f"CDF@sweet_spot={cdf.get(sweet_spot, 0):.0%} → wait={sweet_spot}"
    )

    value = max(0, sweet_spot - 1)
    max_streak_val = max(2, min(sweet_spot + 1, 5 if aggression == "aggressive" else 4))

    return (value, max_streak_val, detail)


# ═══════════════════════════════════════════════════════════════════════
# Derivation Method 3: FALLBACK (static tiers)
# ═══════════════════════════════════════════════════════════════════════

def _derive_fallback(aggression: str) -> tuple[int, int, str]:
    """When not enough transition data, use fixed tiers."""
    start_after, reset_after, max_streak = FALLBACK_TIERS.get(
        aggression, FALLBACK_TIERS["conservative"]
    )
    # For martingale context, return start_after; for anti-mart, caller uses reset_after
    # We return start_after here; the caller decides which field to assign
    detail = f"Fallback-{aggression}: not enough transitions for pattern analysis"
    return (start_after, max_streak, detail)


# ═══════════════════════════════════════════════════════════════════════
# Unified derivation: try methods in order
# ═══════════════════════════════════════════════════════════════════════

def derive_mart_start_after(
    tp: TransitionPattern, aggression: str,
) -> tuple[int, int, str, str]:
    """Derive martingale start_after from transition patterns.

    Looks at win_to_loss_runs: after wins end, how long are the loss runs?
    We want start_after = the point to begin scaling during a loss streak.

    Returns: (start_after, max_streak, detail_reason, method_name)
    """
    runs = tp.win_to_loss_runs

    # Try pattern-mirror first (needs ≥5 transitions)
    result = _derive_pattern_mirror(runs, aggression)
    if result is not None:
        val, ms, detail = result
        return (val, ms, detail, "pattern")

    # Try statistical (needs ≥3 transitions)
    result = _derive_statistical(runs, aggression)
    if result is not None:
        val, ms, detail = result
        return (val, ms, detail, "statistical")

    # Fallback
    val, ms, detail = _derive_fallback(aggression)
    return (val, ms, detail, "fallback")


def derive_anti_mart_reset_after(
    tp: TransitionPattern, aggression: str,
) -> tuple[int, int, str, str]:
    """Derive anti-martingale reset_after from transition patterns.

    Looks at loss_to_win_runs: after losses end, how long are the win runs?
    We want reset_after = the point to stop riding the win streak and reset.

    Returns: (reset_after, max_streak, detail_reason, method_name)
    """
    runs = tp.loss_to_win_runs

    # Try pattern-mirror first
    result = _derive_pattern_mirror(runs, aggression)
    if result is not None:
        val, ms, detail = result
        # For anti-mart, the "value" from pattern-mirror is the run length
        # to reset at. We want reset_after = typical win streak length.
        # Pattern-mirror already gives us: conservative = P50, aggressive = P75
        sorted_runs = sorted(runs)
        n = len(sorted_runs)
        p50 = sorted_runs[n // 2]
        p75 = sorted_runs[max(0, min(n - 1, int(n * 0.75)))]
        mode_val = max(set(sorted_runs), key=sorted_runs.count)

        if aggression == "aggressive":
            reset_val = max(3, min(p75, 8))
        elif aggression == "conservative":
            reset_val = max(2, min(p50, 5))
        else:
            reset_val = max(2, min(mode_val, 6))

        return (reset_val, ms, detail, "pattern")

    # Try statistical
    result = _derive_statistical(runs, aggression)
    if result is not None:
        val, ms, detail = result
        # For reset_after, the sweet_spot IS the point to reset
        reset_val = max(2, val + 1)
        return (reset_val, ms, detail, "statistical")

    # Fallback
    _, _, reset_after_fallback = FALLBACK_TIERS.get(
        aggression, FALLBACK_TIERS["conservative"]
    )
    _, ms_fb, detail = _derive_fallback(aggression)
    return (reset_after_fallback, ms_fb, detail, "fallback")


# ═══════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class StreakStats:
    """Streak statistics for one asset's IS trades."""
    symbol: str
    batch_id: int = 0
    strategy_name: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    # Streak distributions: streak_length → count
    win_streaks: dict[int, int] = field(default_factory=dict)
    loss_streaks: dict[int, int] = field(default_factory=dict)
    # Derived probabilities
    p_win_streak_2plus: float = 0.0
    p_loss_streak_2plus: float = 0.0
    max_win_streak: int = 0
    max_loss_streak: int = 0
    avg_win_streak: float = 0.0
    avg_loss_streak: float = 0.0
    median_win_streak: int = 0
    median_loss_streak: int = 0
    # Transition patterns
    transitions: TransitionPattern | None = None


@dataclass
class AutoRiskRecommendation:
    """Recommended risk profile for one asset in one batch."""
    symbol: str
    batch_id: int = 0
    strategy_name: str = ""
    mode: str = "fixed"
    max_streak: int = 3
    martingale_start_after: int = 0
    anti_martingale_reset_after: int = 0
    confidence: float = 0.0
    reason: str = ""
    derivation_method: str = ""   # "pattern" | "statistical" | "fallback" | ""
    stats: StreakStats | None = None


# ═══════════════════════════════════════════════════════════════════════
# Core computation
# ═══════════════════════════════════════════════════════════════════════

def compute_streak_stats(
    pnls: list[float],
    symbol: str = "",
    batch_id: int = 0,
    strategy_name: str = "",
) -> StreakStats:
    """Compute streak statistics + transition patterns from a chronological PnL list."""
    s = StreakStats(symbol=symbol, batch_id=batch_id, strategy_name=strategy_name,
                    total_trades=len(pnls))
    if not pnls:
        return s

    s.wins = sum(1 for p in pnls if p > BE_ZONE)
    s.losses = sum(1 for p in pnls if p < -BE_ZONE)
    s.win_rate = s.wins / s.total_trades if s.total_trades > 0 else 0.0

    # Build streak runs
    win_runs: list[int] = []
    loss_runs: list[int] = []
    current_run = 0
    current_is_win: bool | None = None

    for pnl in pnls:
        if abs(pnl) <= BE_ZONE:
            continue
        is_win = pnl > BE_ZONE
        if current_is_win is None or is_win != current_is_win:
            if current_is_win is not None and current_run > 0:
                if current_is_win:
                    win_runs.append(current_run)
                else:
                    loss_runs.append(current_run)
            current_run = 1
            current_is_win = is_win
        else:
            current_run += 1

    if current_is_win is not None and current_run > 0:
        if current_is_win:
            win_runs.append(current_run)
        else:
            loss_runs.append(current_run)

    # Distributions
    for r in win_runs:
        s.win_streaks[r] = s.win_streaks.get(r, 0) + 1
    for r in loss_runs:
        s.loss_streaks[r] = s.loss_streaks.get(r, 0) + 1

    if win_runs:
        s.max_win_streak = max(win_runs)
        s.avg_win_streak = sum(win_runs) / len(win_runs)
        sorted_w = sorted(win_runs)
        s.median_win_streak = sorted_w[len(sorted_w) // 2]
    if loss_runs:
        s.max_loss_streak = max(loss_runs)
        s.avg_loss_streak = sum(loss_runs) / len(loss_runs)
        sorted_l = sorted(loss_runs)
        s.median_loss_streak = sorted_l[len(sorted_l) // 2]

    # P(win/loss is part of a 2+ streak)
    wins_in_2plus = sum(length * count for length, count in s.win_streaks.items() if length >= 2)
    s.p_win_streak_2plus = wins_in_2plus / s.wins if s.wins > 0 else 0.0
    losses_in_2plus = sum(length * count for length, count in s.loss_streaks.items() if length >= 2)
    s.p_loss_streak_2plus = losses_in_2plus / s.losses if s.losses > 0 else 0.0

    # Transition patterns
    s.transitions = build_transition_patterns(pnls)

    return s


# ═══════════════════════════════════════════════════════════════════════
# Recommendation engine
# ═══════════════════════════════════════════════════════════════════════

def recommend_risk_profile(
    stats: StreakStats,
    multiplier: float = 2.0,
    aggression: str = "conservative",
    min_confidence: float = 0.0,
    strategy_hint: str = "auto",
) -> AutoRiskRecommendation:
    """Given streak stats for one asset, recommend the best risk profile.

    Decision tree:
      1. If not enough trades → fixed (low confidence)
      2. If high P(2+ consecutive wins) → anti_martingale
      3. If high P(2+ consecutive losses) → martingale
      4. Otherwise → fixed (streaks are random, no edge)
      5. If confidence < min_confidence → override to fixed

    start_after / reset_after / max_streak are derived from transition
    pattern analysis (pattern-mirror → statistical → fallback).
    """
    rec = AutoRiskRecommendation(
        symbol=stats.symbol, batch_id=stats.batch_id,
        strategy_name=stats.strategy_name, stats=stats,
    )

    # ── Resolve thresholds based on strategy hint ──
    if strategy_hint == "trend":
        anti_mart_thresh = TREND_ANTI_MART_THRESHOLD
        mart_thresh = TREND_MART_THRESHOLD
    elif strategy_hint == "meanrev":
        anti_mart_thresh = MEANREV_ANTI_MART_THRESHOLD
        mart_thresh = MEANREV_MART_THRESHOLD
    else:
        anti_mart_thresh = ANTI_MART_P2_THRESHOLD
        mart_thresh = MART_P2_THRESHOLD

    # ── Insufficient data ──
    if stats.total_trades < MIN_TRADES_FOR_AUTO:
        rec.mode = "fixed"
        rec.confidence = 0.2
        rec.derivation_method = "insufficient"
        rec.reason = (
            f"Only {stats.total_trades} IS trades (need ≥{MIN_TRADES_FOR_AUTO}) "
            f"— defaulting to fixed risk."
        )
        return rec

    tp = stats.transitions or build_transition_patterns([])

    # ── Check eligibility ──
    anti_mart_eligible = stats.p_win_streak_2plus >= anti_mart_thresh
    mart_eligible = stats.p_loss_streak_2plus >= mart_thresh

    # Both eligible: pick the stronger signal (with hint tiebreaker)
    if anti_mart_eligible and mart_eligible:
        if strategy_hint == "trend":
            mart_eligible = False
        elif strategy_hint == "meanrev":
            anti_mart_eligible = False
        elif stats.p_win_streak_2plus >= stats.p_loss_streak_2plus:
            mart_eligible = False
        else:
            anti_mart_eligible = False

    # ── Anti-Martingale ──
    if anti_mart_eligible:
        rec.mode = "anti_martingale"
        excess = stats.p_win_streak_2plus - anti_mart_thresh
        trade_factor = min(1.0, stats.total_trades / 50)
        rec.confidence = min(0.95, 0.5 + excess * 2.0) * trade_factor

        # Derive reset_after + max_streak from transition patterns
        reset_val, max_streak_val, derive_detail, method = derive_anti_mart_reset_after(
            tp, aggression,
        )
        rec.anti_martingale_reset_after = reset_val
        rec.max_streak = max_streak_val
        rec.derivation_method = method

        # Confidence gate
        if min_confidence > 0 and rec.confidence < min_confidence:
            rec.mode = "fixed"
            rec.reason = (
                f"P(2+ win streak) = {stats.p_win_streak_2plus:.0%} suggested anti_martingale "
                f"but confidence {rec.confidence:.0%} < threshold {min_confidence:.0%}. "
                f"Forced to Fixed Risk."
            )
            return rec

        # Transition summary for reason text
        n_trans = len(tp.loss_to_win_runs)
        trans_summary = ""
        if n_trans >= MIN_TRANSITIONS_FOR_STATS:
            trans_summary = (
                f" Transitions (L→W): {n_trans}x, "
                f"win-run-after-loss lengths={tp.loss_to_win_runs[:6]}"
                f"{'…' if n_trans > 6 else ''}."
            )

        rec.reason = (
            f"P(2+ win streak) = {stats.p_win_streak_2plus:.0%} "
            f"(threshold {anti_mart_thresh:.0%}"
            f"{', trend-biased' if strategy_hint == 'trend' else ''})."
            f"{trans_summary} "
            f"Derivation: {method}. "
            f"→ Anti-Martingale max_streak={rec.max_streak}, "
            f"reset_after={rec.anti_martingale_reset_after} ({aggression}). "
            f"[{derive_detail}]"
        )
        return rec

    # ── Martingale ──
    if mart_eligible:
        rec.mode = "martingale"
        excess = stats.p_loss_streak_2plus - mart_thresh
        trade_factor = min(1.0, stats.total_trades / 50)
        rec.confidence = min(0.95, 0.5 + excess * 2.0) * trade_factor

        # Derive start_after + max_streak from transition patterns
        start_val, max_streak_val, derive_detail, method = derive_mart_start_after(
            tp, aggression,
        )
        rec.martingale_start_after = start_val
        rec.max_streak = max_streak_val
        rec.derivation_method = method

        # Confidence gate
        if min_confidence > 0 and rec.confidence < min_confidence:
            rec.mode = "fixed"
            rec.reason = (
                f"P(2+ loss streak) = {stats.p_loss_streak_2plus:.0%} suggested martingale "
                f"but confidence {rec.confidence:.0%} < threshold {min_confidence:.0%}. "
                f"Forced to Fixed Risk."
            )
            return rec

        # Transition summary
        n_trans = len(tp.win_to_loss_runs)
        trans_summary = ""
        if n_trans >= MIN_TRANSITIONS_FOR_STATS:
            trans_summary = (
                f" Transitions (W→L): {n_trans}x, "
                f"loss-run-after-win lengths={tp.win_to_loss_runs[:6]}"
                f"{'…' if n_trans > 6 else ''}."
            )

        rec.reason = (
            f"P(2+ loss streak) = {stats.p_loss_streak_2plus:.0%} "
            f"(threshold {mart_thresh:.0%}"
            f"{', meanrev-biased' if strategy_hint == 'meanrev' else ''})."
            f"{trans_summary} "
            f"Derivation: {method}. "
            f"→ Martingale max_streak={rec.max_streak}, "
            f"start_after={rec.martingale_start_after} ({aggression}). "
            f"[{derive_detail}]"
        )
        return rec

    # ── Regular / Fixed ──
    rec.mode = "fixed"
    rec.derivation_method = ""
    rec.confidence = 0.6 + min(0.3, stats.total_trades / 200)
    rec.reason = (
        f"P(2+ win streak) = {stats.p_win_streak_2plus:.0%}, "
        f"P(2+ loss streak) = {stats.p_loss_streak_2plus:.0%}. "
        f"Both below threshold "
        f"(anti-mart {anti_mart_thresh:.0%}, mart {mart_thresh:.0%}"
        f"{', ' + strategy_hint + ' hint' if strategy_hint != 'auto' else ''}) — "
        f"streaks appear random. Fixed risk recommended."
    )
    return rec


# ═══════════════════════════════════════════════════════════════════════
# Batch analysis + config builder (unchanged interface)
# ═══════════════════════════════════════════════════════════════════════

def analyze_batch_trades(
    trades: list,
    date_to: str | None = None,
    multiplier: float = 2.0,
    aggression: str = "conservative",
    min_confidence: float = 0.0,
    strategy_hint: str = "auto",
    run_batch_map: dict[int, int] | None = None,
    batch_strategy_names: dict[int, str] | None = None,
) -> dict[str, AutoRiskRecommendation]:
    """Analyze IS trades and return per-asset-per-batch risk recommendations.

    Key: each trade's (symbol, batch_id) combination gets its own recommendation.
    """
    by_sym_batch: dict[tuple[str, int], list[tuple]] = defaultdict(list)

    for t in trades:
        sym = getattr(t, 'symbol', None) or ''
        entry = getattr(t, 'entry_time', None)
        pnl = getattr(t, 'net_profit', None) or 0.0
        run_id = getattr(t, 'run_id', None)

        if date_to and entry:
            entry_str = str(entry)[:10]
            if entry_str > date_to:
                continue

        bid = 0
        if run_batch_map and run_id is not None:
            bid = run_batch_map.get(run_id, 0)

        by_sym_batch[(sym, bid)].append((entry, pnl))

    all_bids = {bid for _, bid in by_sym_batch.keys()}
    is_single_batch = len(all_bids) <= 1

    results: dict[str, AutoRiskRecommendation] = {}
    for (sym, bid), entries in by_sym_batch.items():
        entries.sort(key=lambda x: x[0] or "")
        pnls = [pnl for _, pnl in entries]

        strat_name = (batch_strategy_names or {}).get(bid, "")
        stats = compute_streak_stats(pnls, symbol=sym, batch_id=bid, strategy_name=strat_name)
        rec = recommend_risk_profile(
            stats, multiplier=multiplier, aggression=aggression,
            min_confidence=min_confidence, strategy_hint=strategy_hint,
        )

        key = sym if is_single_batch else f"{sym}__batch{bid}"
        results[key] = rec

        logger.info(
            "auto_risk[%s batch=%d strat=%s]: mode=%s conf=%.2f deriv=%s "
            "max_streak=%d start_after=%d reset_after=%d | %s",
            sym, bid, strat_name, rec.mode, rec.confidence, rec.derivation_method,
            rec.max_streak, rec.martingale_start_after,
            rec.anti_martingale_reset_after, rec.reason,
        )

    return results


def build_auto_risk_config(
    recommendations: dict[str, AutoRiskRecommendation],
    base_risk_fixed: float = 100.0,
    base_risk_mart: float = 50.0,
    multiplier: float = 2.0,
    initial_capital: float = 10_000.0,
    aggression: str = "conservative",
) -> dict:
    """Build a summary config dict from per-asset recommendations."""
    if not recommendations:
        return {
            "dominant_mode": "fixed",
            "per_asset": {},
            "config": {
                "mode": "fixed", "value": base_risk_fixed,
                "valueFixed": base_risk_fixed, "valueMartingale": base_risk_mart,
                "initialCapital": initial_capital,
                "multiplier": multiplier, "maxStreak": 3,
                "martingaleStartAfter": 0, "antiMartingaleResetAfter": 0,
            },
        }

    mode_weights: dict[str, float] = defaultdict(float)
    for rec in recommendations.values():
        mode_weights[rec.mode] += rec.confidence

    dominant_mode = max(mode_weights, key=mode_weights.get)
    is_mart_dominant = dominant_mode in (
        "martingale", "anti_martingale", "inv_martingale", "inv_anti_martingale",
    )
    active_value = base_risk_mart if is_mart_dominant else base_risk_fixed

    dominant_recs = [r for r in recommendations.values() if r.mode == dominant_mode]
    if dominant_recs:
        avg_max_streak = round(sum(r.max_streak for r in dominant_recs) / len(dominant_recs))
        avg_start_after = round(sum(r.martingale_start_after for r in dominant_recs) / len(dominant_recs))
        avg_reset_after = round(sum(r.anti_martingale_reset_after for r in dominant_recs) / len(dominant_recs))
    else:
        avg_max_streak, avg_start_after, avg_reset_after = 3, 0, 0

    per_asset = {}
    for key, rec in recommendations.items():
        per_asset[key] = {
            "mode": rec.mode,
            "max_streak": rec.max_streak,
            "start_after": rec.martingale_start_after,
            "reset_after": rec.anti_martingale_reset_after,
            "confidence": round(rec.confidence, 3),
            "reason": rec.reason,
            "derivation_method": rec.derivation_method,
            "batch_id": rec.batch_id,
            "strategy_name": rec.strategy_name,
        }

    return {
        "dominant_mode": dominant_mode,
        "per_asset": per_asset,
        "config": {
            "mode": dominant_mode,
            "value": active_value,
            "valueFixed": base_risk_fixed,
            "valueMartingale": base_risk_mart,
            "initialCapital": initial_capital,
            "multiplier": multiplier,
            "maxStreak": avg_max_streak,
            "martingaleStartAfter": avg_start_after,
            "antiMartingaleResetAfter": avg_reset_after,
        },
    }
