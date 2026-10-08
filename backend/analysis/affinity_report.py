"""Batch Affinity Report — post-optimization analysis of batch compatibility.

Computes 7 pairwise metrics, classifies batch roles (anchor / complement /
redundant / outlier), clusters natural affinity groups, and optionally
validates IS-detected affinities against OOS1 data.

All functions are **pure** — they operate only on provided Series,
no DB access, no side effects.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from itertools import combinations

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Metric weights (default) — used for composite score ────────────────
DEFAULT_METRIC_WEIGHTS = {
    "return_correlation": 0.10,
    "correlation_stability": 0.15,
    "drawdown_overlap": 0.20,
    "stress_divergence": 0.20,
    "complementarity": 0.10,
    "recovery_offset": 0.15,
    "shape_diversity": 0.10,
}

# ── Dataclasses ─────────────────────────────────────────────────────────

@dataclass
class PairMetrics:
    """All 7 metrics for one pair of batch equity curves."""
    id_a: int | str
    id_b: int | str
    return_correlation: float      # raw pearson (lower = better diversification)
    correlation_stability: float   # 1 - std(rolling corr); higher = more predictable
    drawdown_overlap: float        # 1 - overlap_rate; higher = less concurrent DD
    stress_divergence: float       # 1 - stress_corr scaled; higher = diverge under stress
    complementarity: float         # opposite-sign day fraction; higher = take turns
    recovery_offset: float         # DD trough offset; higher = recover at different times
    shape_diversity: float         # equity curve shape difference; higher = different profiles
    composite_score: float = 0.0   # weighted blend of all 7

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


@dataclass
class BatchQuality:
    """Individual performance metrics for a batch (from backtest results)."""
    batch_id: int | str
    avg_profit_factor: float = 0.0
    avg_win_rate: float = 0.0
    total_net_profit: float = 0.0
    batch_grade: str = ""        # A+ ... F
    quality_score: float = 0.0   # normalized 0-1 computed from PF/WR/grade


def _compute_quality_score(pf: float, wr: float, grade: str) -> float:
    """Normalize batch quality into a 0-1 score.

    Blends profit factor, win rate, and letter grade into a single number.
    """
    # PF component: 0-1 mapped from 0.5-3.0 range
    pf_score = max(0, min(1, (pf - 0.5) / 2.5)) if pf > 0 else 0

    # WR component: 0-1 mapped from 30%-70% range
    wr_score = max(0, min(1, (wr - 30) / 40)) if wr > 0 else 0

    # Grade component: A+=1.0, F=0.0
    grade_map = {
        "A+": 1.0, "A": 0.92, "A-": 0.85,
        "B+": 0.77, "B": 0.69, "B-": 0.62,
        "C+": 0.54, "C": 0.46, "C-": 0.38,
        "D+": 0.31, "D": 0.23, "D-": 0.15,
        "F": 0.05,
    }
    grade_score = grade_map.get(grade, 0.5)

    # Blend: PF 40%, WR 25%, Grade 35%
    return pf_score * 0.40 + wr_score * 0.25 + grade_score * 0.35


@dataclass
class BatchRole:
    """Classification and summary for a single batch."""
    batch_id: int | str
    role: str                       # anchor | strong_complement | conditional | redundant | outlier
    team_player_score: float        # avg composite with all others (pure affinity)
    quality_score: float = 0.0      # individual batch quality (0-1)
    combined_score: float = 0.0     # blend of affinity + quality
    avg_profit_factor: float = 0.0
    avg_win_rate: float = 0.0
    total_net_profit: float = 0.0
    batch_grade: str = ""
    best_partner: int | str | None = None
    worst_partner: int | str | None = None
    best_partner_score: float = 0.0
    worst_partner_score: float = 0.0
    redundant_with: int | str | None = None  # if role == "redundant", who they duplicate
    cluster_id: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


@dataclass
class OOSValidation:
    """IS vs OOS1 comparison for one pair + one metric."""
    id_a: int | str
    id_b: int | str
    metric: str
    is_value: float
    oos_value: float
    delta: float
    status: str   # certified | degraded | improved | spurious

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("is_value", "oos_value", "delta"):
            d[k] = round(d[k], 4)
        return d


@dataclass
class AffinityCluster:
    """A group of batches that form a natural affinity cluster."""
    cluster_id: int
    batch_ids: list[int | str]
    avg_internal_harmony: float

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "batch_ids": self.batch_ids,
            "avg_internal_harmony": round(self.avg_internal_harmony, 4),
        }


@dataclass
class AffinityReport:
    """Complete batch affinity report."""
    pairs: list[PairMetrics]
    roles: list[BatchRole]
    clusters: list[AffinityCluster]
    metric_leaderboard: dict[str, list[dict]]      # metric -> sorted list of pairs
    oos_validations: list[OOSValidation] | None = None
    metric_reliability: dict[str, float] | None = None  # metric -> IS→OOS correlation
    recommendation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "pairs": [p.to_dict() for p in self.pairs],
            "roles": [r.to_dict() for r in self.roles],
            "clusters": [c.to_dict() for c in self.clusters],
            "metric_leaderboard": self.metric_leaderboard,
            "oos_validations": [v.to_dict() for v in self.oos_validations] if self.oos_validations else None,
            "metric_reliability": {k: round(v, 4) for k, v in self.metric_reliability.items()} if self.metric_reliability else None,
            "recommendation": self.recommendation,
        }


# ── Individual metric functions ─────────────────────────────────────────

def _m_return_correlation(a: pd.Series, b: pd.Series) -> float:
    """Pearson correlation of daily PnL. Score: (1 - corr) / 2.
    -1 corr → 1.0 (perfect), 0 corr → 0.5, +1 corr → 0.0 (bad)."""
    if len(a) < 10 or len(b) < 10:
        return 0.5
    corr = a.corr(b)
    if np.isnan(corr):
        return 0.5
    return float((1.0 - corr) / 2.0)


def _m_correlation_stability(a: pd.Series, b: pd.Series, window: int = 60) -> float:
    """Stability of rolling correlation. Score: 1 - min(std*3, 1).
    Low variance = stable relationship = high score."""
    if len(a) < window + 10:
        return 0.5
    rolling = a.rolling(window).corr(b).dropna()
    if len(rolling) < 5:
        return 0.5
    std = float(rolling.std())
    return float(max(0.0, 1.0 - min(std * 3.0, 1.0)))


def _m_drawdown_overlap(a: pd.Series, b: pd.Series, dd_threshold: float = 0.02) -> float:
    """Fraction of days both curves are in drawdown simultaneously.
    Score: 1 - overlap_rate. Higher = better."""
    cum_a = a.cumsum()
    cum_b = b.cumsum()
    peak_a = cum_a.cummax()
    peak_b = cum_b.cummax()
    dd_a = (cum_a - peak_a) / peak_a.clip(lower=1e-9).abs()
    dd_b = (cum_b - peak_b) / peak_b.clip(lower=1e-9).abs()
    in_dd_a = dd_a < -dd_threshold
    in_dd_b = dd_b < -dd_threshold
    either = in_dd_a | in_dd_b
    both = in_dd_a & in_dd_b
    if either.sum() == 0:
        return 1.0
    overlap = float(both.sum()) / float(either.sum())
    return float(1.0 - overlap)


def _m_stress_divergence(a: pd.Series, b: pd.Series, quantile: float = 0.20) -> float:
    """Correlation during worst days. Score: (1 - stress_corr) / 2.
    Negative stress corr = great (diverge under stress)."""
    combined = a + b
    threshold = combined.quantile(quantile)
    stress_mask = combined <= threshold
    sa, sb = a[stress_mask], b[stress_mask]
    if len(sa) < 5:
        return 0.5
    corr = sa.corr(sb)
    if np.isnan(corr):
        return 0.5
    return float((1.0 - corr) / 2.0)


def _m_complementarity(a: pd.Series, b: pd.Series) -> float:
    """Fraction of days with opposite signs (one up, one down).
    Score: scaled to 0-1 (theoretical max ~0.5 for random, scale 2x)."""
    if len(a) < 10:
        return 0.5
    opposite = ((a > 0) & (b < 0)) | ((a < 0) & (b > 0))
    ratio = float(opposite.sum()) / len(a)
    return float(min(ratio * 2.0, 1.0))  # scale so 50% opposite = 1.0


def _m_recovery_offset(a: pd.Series, b: pd.Series) -> float:
    """How offset are drawdown troughs between two curves.
    Score: 0 = synchronized troughs, 1 = completely offset."""
    cum_a = a.cumsum()
    cum_b = b.cumsum()
    # Compute drawdown series
    dd_a = cum_a - cum_a.cummax()
    dd_b = cum_b - cum_b.cummax()
    if dd_a.std() < 1e-9 or dd_b.std() < 1e-9:
        return 0.5
    # Cross-correlation at lag 0 vs best offset lag
    # Use normalized DD series
    norm_a = (dd_a - dd_a.mean()) / (dd_a.std() + 1e-9)
    norm_b = (dd_b - dd_b.mean()) / (dd_b.std() + 1e-9)
    # Check lag 0 correlation
    corr_0 = float(norm_a.corr(norm_b))
    if np.isnan(corr_0):
        return 0.5
    # Score: anti-correlated DD = offset recovery = good
    # corr_0 = 1.0 (synchronized) → 0.0, corr_0 = -1.0 (opposite) → 1.0
    return float((1.0 - corr_0) / 2.0)


def _m_shape_diversity(a: pd.Series, b: pd.Series, window: int = 30) -> float:
    """Diversity of equity curve shapes — compare rolling Sharpe distributions
    and gain/loss asymmetry. Score: 0 = identical, 1 = very different."""
    if len(a) < window + 5:
        return 0.5
    # Rolling Sharpe
    roll_a = a.rolling(window).mean() / a.rolling(window).std().clip(lower=1e-9)
    roll_b = b.rolling(window).mean() / b.rolling(window).std().clip(lower=1e-9)
    roll_a = roll_a.dropna()
    roll_b = roll_b.dropna()
    if len(roll_a) < 5 or len(roll_b) < 5:
        return 0.5

    # 1. Rolling Sharpe distribution divergence (KL-divergence approximation)
    # Bin rolling Sharpe into histogram and compare
    bins = np.linspace(-3, 3, 20)
    hist_a, _ = np.histogram(roll_a.clip(-3, 3), bins=bins, density=True)
    hist_b, _ = np.histogram(roll_b.clip(-3, 3), bins=bins, density=True)
    hist_a = hist_a + 1e-9
    hist_b = hist_b + 1e-9
    hist_a /= hist_a.sum()
    hist_b /= hist_b.sum()
    # Jensen-Shannon divergence (symmetric, bounded 0-1)
    m = (hist_a + hist_b) / 2
    kl_a = float(np.sum(hist_a * np.log(hist_a / m)))
    kl_b = float(np.sum(hist_b * np.log(hist_b / m)))
    js_div = (kl_a + kl_b) / 2.0

    # 2. Gain/loss asymmetry difference
    pos_ratio_a = float((a > 0).sum()) / max(len(a), 1)
    pos_ratio_b = float((b > 0).sum()) / max(len(b), 1)
    asym_diff = abs(pos_ratio_a - pos_ratio_b)

    # 3. Volatility ratio (different vol profiles = different shapes)
    vol_a = float(a.std())
    vol_b = float(b.std())
    vol_ratio = min(vol_a, vol_b) / max(vol_a, vol_b, 1e-9)
    vol_diff = 1.0 - vol_ratio  # 0 = same vol, 1 = very different

    # Combine: JS divergence (50%), asymmetry diff (25%), vol diff (25%)
    score = js_div * 0.50 + asym_diff * 0.25 + vol_diff * 0.25
    return float(min(max(score, 0.0), 1.0))


# ── Core computation ────────────────────────────────────────────────────

METRIC_FUNCS = {
    "return_correlation": _m_return_correlation,
    "correlation_stability": _m_correlation_stability,
    "drawdown_overlap": _m_drawdown_overlap,
    "stress_divergence": _m_stress_divergence,
    "complementarity": _m_complementarity,
    "recovery_offset": _m_recovery_offset,
    "shape_diversity": _m_shape_diversity,
}


def compute_pair_metrics(
    a: pd.Series,
    b: pd.Series,
    id_a: int | str,
    id_b: int | str,
    weights: dict[str, float] | None = None,
) -> PairMetrics:
    """Compute all 7 metrics for a pair of batch equity curves."""
    w = weights or DEFAULT_METRIC_WEIGHTS
    aligned = pd.concat([a, b], axis=1).dropna()
    if len(aligned) < 20:
        return PairMetrics(
            id_a=id_a, id_b=id_b,
            return_correlation=0.5, correlation_stability=0.5,
            drawdown_overlap=0.5, stress_divergence=0.5,
            complementarity=0.5, recovery_offset=0.5,
            shape_diversity=0.5, composite_score=0.5,
        )
    sa, sb = aligned.iloc[:, 0], aligned.iloc[:, 1]

    metrics = {}
    for name, func in METRIC_FUNCS.items():
        try:
            metrics[name] = func(sa, sb)
        except Exception:
            metrics[name] = 0.5

    # Composite score
    total_w = sum(w.get(k, 0) for k in metrics)
    composite = sum(metrics[k] * w.get(k, 0) for k in metrics) / max(total_w, 1e-9)

    return PairMetrics(
        id_a=id_a, id_b=id_b,
        return_correlation=metrics["return_correlation"],
        correlation_stability=metrics["correlation_stability"],
        drawdown_overlap=metrics["drawdown_overlap"],
        stress_divergence=metrics["stress_divergence"],
        complementarity=metrics["complementarity"],
        recovery_offset=metrics["recovery_offset"],
        shape_diversity=metrics["shape_diversity"],
        composite_score=composite,
    )


def compute_all_pairs(
    curves: dict[int | str, pd.Series],
    weights: dict[str, float] | None = None,
) -> list[PairMetrics]:
    """Compute metrics for all pairs of curves."""
    ids = sorted(curves.keys(), key=str)
    pairs = []
    for id_a, id_b in combinations(ids, 2):
        pm = compute_pair_metrics(curves[id_a], curves[id_b], id_a, id_b, weights)
        pairs.append(pm)
    return pairs


# ── Batch role classification ───────────────────────────────────────────

def classify_roles(
    pairs: list[PairMetrics],
    batch_ids: list[int | str],
    pinned_anchors: list[int | str] | None = None,
    batch_quality: dict[int | str, BatchQuality] | None = None,
    quality_weight: float = 0.40,
) -> list[BatchRole]:
    """Classify each batch as anchor/complement/conditional/redundant/outlier.

    If *pinned_anchors* is provided, those batches are forced to role="anchor"
    and are never marked redundant/outlier.

    If *batch_quality* is provided, role classification uses a **combined score**
    that blends affinity (team_player_score) with individual quality.
    *quality_weight* controls the blend (0.0 = pure affinity, 1.0 = pure quality).
    Default 0.40 = 60% affinity + 40% quality.
    """
    pinned = set(pinned_anchors or [])
    bq = batch_quality or {}

    if not pairs:
        return [BatchRole(batch_id=b, role="anchor", team_player_score=0.5,
                          quality_score=bq[b].quality_score if b in bq else 0.0,
                          combined_score=0.5,
                          avg_profit_factor=bq[b].avg_profit_factor if b in bq else 0.0,
                          avg_win_rate=bq[b].avg_win_rate if b in bq else 0.0,
                          total_net_profit=bq[b].total_net_profit if b in bq else 0.0,
                          batch_grade=bq[b].batch_grade if b in bq else "",
                          ) for b in batch_ids]

    # Build per-batch stats
    batch_composites: dict[int | str, list[float]] = {b: [] for b in batch_ids}
    batch_pairs: dict[int | str, list[PairMetrics]] = {b: [] for b in batch_ids}
    for p in pairs:
        batch_composites[p.id_a].append(p.composite_score)
        batch_composites[p.id_b].append(p.composite_score)
        batch_pairs[p.id_a].append(p)
        batch_pairs[p.id_b].append(p)

    avg_affinity = {}
    for b in batch_ids:
        vals = batch_composites[b]
        avg_affinity[b] = float(np.mean(vals)) if vals else 0.0

    # Combined score: blend affinity + quality
    combined = {}
    for b in batch_ids:
        qs = bq[b].quality_score if b in bq else 0.5  # default mid if no quality data
        combined[b] = avg_affinity[b] * (1 - quality_weight) + qs * quality_weight

    # Find auto-discovered anchor (highest COMBINED score, not just affinity)
    anchor_id = max(batch_ids, key=lambda b: combined[b])

    # Merge pinned anchors with discovered anchor
    anchor_ids = set(pinned) | {anchor_id}

    # Detect redundant pairs (high positive return correlation → raw corr > 0.7)
    # Among redundant pairs, keep the one with higher COMBINED score
    redundant_map: dict[int | str, int | str] = {}
    for p in pairs:
        # return_correlation score: (1-corr)/2, so score < 0.15 means corr > 0.7
        if p.return_correlation < 0.15:
            if combined.get(p.id_a, 0) < combined.get(p.id_b, 0):
                if p.id_a not in anchor_ids:
                    redundant_map[p.id_a] = p.id_b
            else:
                if p.id_b not in anchor_ids:
                    redundant_map[p.id_b] = p.id_a

    # Classify using combined scores
    roles = []
    median_combined = float(np.median(list(combined.values()))) if combined else 0.5

    for b in batch_ids:
        bp = batch_pairs[b]
        best = max(bp, key=lambda p: p.composite_score) if bp else None
        worst = min(bp, key=lambda p: p.composite_score) if bp else None

        best_partner = best.id_b if best and best.id_a == b else (best.id_a if best else None)
        worst_partner = worst.id_b if worst and worst.id_a == b else (worst.id_a if worst else None)
        best_score = best.composite_score if best else 0.0
        worst_score = worst.composite_score if worst else 0.0

        q = bq.get(b)
        qs = q.quality_score if q else 0.0

        if b in anchor_ids:
            role = "anchor"
        elif b in redundant_map:
            role = "redundant"
        elif combined[b] >= median_combined * 1.1:
            anchor_pair = next((p for p in bp if p.id_a in anchor_ids or p.id_b in anchor_ids), None)
            if anchor_pair and anchor_pair.composite_score >= median_combined:
                role = "strong_complement"
            else:
                role = "conditional"
        elif combined[b] < median_combined * 0.7:
            role = "outlier"
        else:
            role = "conditional"

        roles.append(BatchRole(
            batch_id=b, role=role,
            team_player_score=avg_affinity[b],
            quality_score=qs,
            combined_score=combined[b],
            avg_profit_factor=q.avg_profit_factor if q else 0.0,
            avg_win_rate=q.avg_win_rate if q else 0.0,
            total_net_profit=q.total_net_profit if q else 0.0,
            batch_grade=q.batch_grade if q else "",
            best_partner=best_partner, best_partner_score=best_score,
            worst_partner=worst_partner, worst_partner_score=worst_score,
            redundant_with=redundant_map.get(b),
        ))

    return roles


# ── Clustering ──────────────────────────────────────────────────────────

def find_clusters(
    pairs: list[PairMetrics],
    batch_ids: list[int | str],
    threshold: float = 0.55,
) -> list[AffinityCluster]:
    """Simple agglomerative clustering: batches with pairwise composite > threshold
    are in the same cluster."""
    # Build adjacency
    adj: dict[int | str, set] = {b: set() for b in batch_ids}
    pair_map: dict[tuple, float] = {}
    for p in pairs:
        pair_map[(p.id_a, p.id_b)] = p.composite_score
        pair_map[(p.id_b, p.id_a)] = p.composite_score
        if p.composite_score >= threshold:
            adj[p.id_a].add(p.id_b)
            adj[p.id_b].add(p.id_a)

    # BFS to find connected components
    visited: set = set()
    clusters = []
    cluster_id = 0
    for b in batch_ids:
        if b in visited:
            continue
        # BFS
        queue = [b]
        component: list[int | str] = []
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            for neighbor in adj[node]:
                if neighbor not in visited:
                    queue.append(neighbor)
        # Compute internal harmony
        internal_scores = []
        for i, a in enumerate(component):
            for bb in component[i + 1:]:
                key = (a, bb) if (a, bb) in pair_map else (bb, a)
                if key in pair_map:
                    internal_scores.append(pair_map[key])
        avg_h = float(np.mean(internal_scores)) if internal_scores else 0.0
        clusters.append(AffinityCluster(
            cluster_id=cluster_id, batch_ids=component,
            avg_internal_harmony=avg_h,
        ))
        cluster_id += 1

    # Assign cluster IDs to roles (done externally)
    return clusters


# ── Metric leaderboard ──────────────────────────────────────────────────

def build_leaderboard(pairs: list[PairMetrics]) -> dict[str, list[dict]]:
    """For each metric, rank all pairs best→worst."""
    metrics = ["return_correlation", "correlation_stability", "drawdown_overlap",
               "stress_divergence", "complementarity", "recovery_offset", "shape_diversity"]
    leaderboard = {}
    for m in metrics:
        ranked = sorted(pairs, key=lambda p: getattr(p, m), reverse=True)
        leaderboard[m] = [
            {"pair": [p.id_a, p.id_b], "score": round(getattr(p, m), 4)}
            for p in ranked
        ]
    return leaderboard


# ── OOS validation ──────────────────────────────────────────────────────

def validate_oos(
    is_pairs: list[PairMetrics],
    oos_pairs: list[PairMetrics],
) -> tuple[list[OOSValidation], dict[str, float]]:
    """Compare IS and OOS1 metrics for each pair. Returns validations + reliability scores."""
    metrics = ["return_correlation", "correlation_stability", "drawdown_overlap",
               "stress_divergence", "complementarity", "recovery_offset", "shape_diversity"]

    # Index OOS pairs
    oos_map: dict[tuple, PairMetrics] = {}
    for p in oos_pairs:
        oos_map[(p.id_a, p.id_b)] = p
        oos_map[(p.id_b, p.id_a)] = p

    validations: list[OOSValidation] = []
    metric_is_vals: dict[str, list[float]] = {m: [] for m in metrics}
    metric_oos_vals: dict[str, list[float]] = {m: [] for m in metrics}

    for is_p in is_pairs:
        oos_p = oos_map.get((is_p.id_a, is_p.id_b))
        if not oos_p:
            continue
        for m in metrics:
            is_val = getattr(is_p, m)
            oos_val = getattr(oos_p, m)
            delta = oos_val - is_val

            # Classify
            if is_val >= 0.5 and oos_val >= 0.45:
                status = "certified"
            elif is_val >= 0.5 and oos_val < 0.35:
                status = "spurious"
            elif oos_val > is_val + 0.1:
                status = "improved"
            elif oos_val < is_val - 0.15:
                status = "degraded"
            else:
                status = "certified"

            validations.append(OOSValidation(
                id_a=is_p.id_a, id_b=is_p.id_b,
                metric=m, is_value=is_val, oos_value=oos_val,
                delta=delta, status=status,
            ))
            metric_is_vals[m].append(is_val)
            metric_oos_vals[m].append(oos_val)

    # Compute metric reliability: correlation of IS values with OOS values
    reliability: dict[str, float] = {}
    for m in metrics:
        if len(metric_is_vals[m]) >= 3:
            corr = float(pd.Series(metric_is_vals[m]).corr(pd.Series(metric_oos_vals[m])))
            reliability[m] = max(0.0, corr) if not np.isnan(corr) else 0.0
        else:
            reliability[m] = 0.0

    return validations, reliability


# ── Recommendation engine ───────────────────────────────────────────────

def build_recommendation(
    roles: list[BatchRole],
    pairs: list[PairMetrics],
    clusters: list[AffinityCluster],
    oos_validations: list[OOSValidation] | None = None,
    pinned_anchors: list[int | str] | None = None,
) -> dict:
    """Generate next-run recommendation."""
    anchors = [r for r in roles if r.role == "anchor"]
    # Primary anchor = first pinned or the auto-discovered one
    anchor = None
    if pinned_anchors:
        anchor = next((r for r in anchors if r.batch_id in set(pinned_anchors)), None)
    if not anchor and anchors:
        anchor = anchors[0]
    keep = [r.batch_id for r in roles if r.role in ("anchor", "strong_complement")]
    conditional = [r.batch_id for r in roles if r.role == "conditional"]
    drop = []
    drop_reasons = {}

    for r in roles:
        if r.role == "outlier":
            drop.append(r.batch_id)
            qual_note = f", quality {r.quality_score:.2f}" if r.quality_score > 0 else ""
            drop_reasons[str(r.batch_id)] = f"Outlier — combined {r.combined_score:.2f} (affinity {r.team_player_score:.2f}{qual_note})"
        elif r.role == "redundant":
            drop.append(r.batch_id)
            # Among redundant pair, explain why this one loses
            qual_note = f" (PF {r.avg_profit_factor:.2f})" if r.avg_profit_factor > 0 else ""
            drop_reasons[str(r.batch_id)] = f"Redundant with batch {r.redundant_with}{qual_note}"

    # OOS-degraded warnings
    oos_warnings: list[str] = []
    if oos_validations:
        # Find pairs where multiple metrics degraded or were spurious
        pair_issues: dict[tuple, int] = {}
        for v in oos_validations:
            if v.status in ("degraded", "spurious"):
                key = (v.id_a, v.id_b)
                pair_issues[key] = pair_issues.get(key, 0) + 1
        for (a, b), count in pair_issues.items():
            if count >= 3:
                oos_warnings.append(
                    f"Batch {a} + {b}: {count}/7 metrics degraded in OOS — relationship may be unreliable"
                )

    # Current vs recommended harmony
    all_composites = [p.composite_score for p in pairs]
    current_avg = float(np.mean(all_composites)) if all_composites else 0.0

    recommended_ids = set(keep + conditional)
    rec_composites = [p.composite_score for p in pairs
                      if p.id_a in recommended_ids and p.id_b in recommended_ids]
    recommended_avg = float(np.mean(rec_composites)) if rec_composites else current_avg

    return {
        "anchor": {"batch_id": anchor.batch_id, "score": round(anchor.combined_score, 4),
                   "affinity": round(anchor.team_player_score, 4), "quality": round(anchor.quality_score, 4)} if anchor else None,
        "keep": keep,
        "conditional": conditional,
        "drop": drop,
        "drop_reasons": drop_reasons,
        "current_avg_harmony": round(current_avg, 4),
        "recommended_avg_harmony": round(recommended_avg, 4),
        "improvement_pct": round((recommended_avg - current_avg) / max(current_avg, 0.01) * 100, 1),
        "oos_warnings": oos_warnings,
    }


# ── Main entry point ────────────────────────────────────────────────────

def generate_affinity_report(
    curves: dict[int | str, pd.Series],
    oos_curves: dict[int | str, pd.Series] | None = None,
    weights: dict[str, float] | None = None,
    pinned_anchors: list[int | str] | None = None,
    batch_quality: dict[int | str, BatchQuality] | None = None,
) -> AffinityReport:
    """Generate the full Batch Affinity Report.

    Args:
        curves: IS-period batch-level equity curves (batch_id → daily PnL Series)
        oos_curves: Optional OOS1-period batch-level curves for validation
        weights: Custom metric weights (defaults to DEFAULT_METRIC_WEIGHTS)
        pinned_anchors: User-pinned anchor batch IDs (forced to anchor role)
        batch_quality: Per-batch quality data (PF, WR, grade) for quality-adjusted scoring

    Returns:
        AffinityReport with all sections populated.
    """
    batch_ids = sorted(curves.keys(), key=str)
    if len(batch_ids) < 2:
        return AffinityReport(
            pairs=[], roles=[], clusters=[], metric_leaderboard={},
            recommendation={"error": "Need at least 2 batches"},
        )

    # 1. Compute all pairwise metrics (IS)
    logger.info("Affinity report: computing %d pairs for %d batches",
                len(batch_ids) * (len(batch_ids) - 1) // 2, len(batch_ids))
    is_pairs = compute_all_pairs(curves, weights)

    # 2. Classify roles (quality-adjusted)
    roles = classify_roles(is_pairs, batch_ids, pinned_anchors=pinned_anchors,
                           batch_quality=batch_quality)

    # 3. Find clusters
    clusters = find_clusters(is_pairs, batch_ids)
    # Assign cluster IDs to roles
    batch_cluster = {}
    for c in clusters:
        for b in c.batch_ids:
            batch_cluster[b] = c.cluster_id
    for r in roles:
        r.cluster_id = batch_cluster.get(r.batch_id, 0)

    # 4. Build leaderboard
    leaderboard = build_leaderboard(is_pairs)

    # 5. OOS validation (optional)
    oos_validations = None
    metric_reliability = None
    if oos_curves and len(oos_curves) >= 2:
        oos_pairs = compute_all_pairs(oos_curves, weights)
        oos_validations, metric_reliability = validate_oos(is_pairs, oos_pairs)
        logger.info("Affinity report: OOS validation computed for %d pairs", len(oos_pairs))

    # 6. Build recommendation
    recommendation = build_recommendation(roles, is_pairs, clusters, oos_validations, pinned_anchors=pinned_anchors)

    return AffinityReport(
        pairs=is_pairs,
        roles=roles,
        clusters=clusters,
        metric_leaderboard=leaderboard,
        oos_validations=oos_validations,
        metric_reliability=metric_reliability,
        recommendation=recommendation,
    )
