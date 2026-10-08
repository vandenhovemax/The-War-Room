"""Split Long/Short portfolio optimizer.

Optimizes Long and Short groups separately with dual objectives:
- within_objective: diversification within each group
- combined_objective: quality of the full L+S combination
"""
from __future__ import annotations

import logging
import time as _time

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

from backend.analysis.optimizer_seeds import compute_seed_scores
from backend.analysis.scoring import (
    SCORING_FUNCTIONS,
    _fast_stability,
    calculate_stability,
)
from backend.analysis.shared import _asset_pf  # noqa: F401


def optimize_split_portfolio(
    corr_matrix: pd.DataFrame,
    n_longs: int,
    n_shorts: int,
    within_objective: str = "uncorrelated",
    combined_objective: str = "stability",
    n_portfolios: int = 3,
    daily_returns: pd.DataFrame | None = None,
    prioritize_stability: bool = False,
    seed_metrics: list[str] | None = None,
    max_seeds: int = 20,
    external_scores: dict | None = None,
    streamlit_logic: bool = False,
    expand_pool: bool = True,
    seed_prior_weight: float = 0.0,
    seed_weights: dict[str, float] | None = None,
    precomputed_seeds: dict[str, float] | None = None,
    fast_mode: bool = False,
) -> list[dict]:
    """Optimize Long + Short portfolios using dual objectives.

    within_objective : diversify within each group
        'uncorrelated', 'anti_correlated', 'hybrid'
    combined_objective : score the full L+S combination
        'stability', 'anti_correlated', 'min_drawdown', or SCORING_FUNCTIONS key
    expand_pool : when True, greedy picks from ALL profitable assets (not just seeds).
    seed_prior_weight : soft tiebreaker weight for seed quality (0 = off).
    seed_weights : per-metric weights for seed scoring {metric: weight}.
    fast_mode : when True, use batch-vectorized scoring (faster but slight FP drift).
    """
    _sp_t0 = _time.perf_counter()
    all_assets = corr_matrix.columns.tolist()
    long_assets = [a for a in all_assets if a.endswith("_Long")]
    short_assets = [a for a in all_assets if a.endswith("_Short")]

    # --- Pre-extract numpy arrays for fast inner-loop access ---
    _corr_vals = corr_matrix.values
    _corr_idx: dict[str, int] = {a: i for i, a in enumerate(all_assets)}
    long_set = set(long_assets)

    # Pre-compute asset PF cache (avoids recalculating per-asset profit factor)
    _pf_cache: dict[str, float] = {}
    if daily_returns is not None and not daily_returns.empty:
        for a in daily_returns.columns:
            _pf_cache[a] = _asset_pf(daily_returns[a])

    # Pre-extract daily PnL as numpy 2D array for fast stability/combined computation
    _daily_vals: np.ndarray | None = None
    _daily_col_idx: dict[str, int] = {}
    if daily_returns is not None and not daily_returns.empty:
        _daily_vals = daily_returns.values
        _daily_col_idx = {a: i for i, a in enumerate(daily_returns.columns)}

    # Fast correlation objectives that can use pure numpy
    _corr_objectives = {"uncorrelated", "anti_correlated", "hybrid"}

    log.info("  SPLIT-OPT: pool=%d (%dL %dS), target=%dL/%dS, max_seeds=%d",
             len(all_assets), len(long_assets), len(short_assets),
             n_longs, n_shorts, max_seeds)

    if len(long_assets) < n_longs or len(short_assets) < n_shorts:
        log.warning("  SPLIT-OPT: NOT ENOUGH ASSETS — need %dL/%dS, have %dL/%dS",
                     n_longs, n_shorts, len(long_assets), len(short_assets))
        return []

    # Seed ranking to limit search space
    _metrics = seed_metrics or ["sortino"]
    if daily_returns is not None and not daily_returns.empty:
        if precomputed_seeds is not None:
            l_scores = {a: precomputed_seeds.get(a, float("inf")) for a in long_assets}
            s_scores = {a: precomputed_seeds.get(a, float("inf")) for a in short_assets}
        else:
            l_scores = compute_seed_scores(daily_returns, long_assets, _metrics, external_scores, metric_weights=seed_weights)
            s_scores = compute_seed_scores(daily_returns, short_assets, _metrics, external_scores, metric_weights=seed_weights)
        seed_assets = (
            sorted(long_assets, key=lambda a: l_scores.get(a, float("inf")))[:max_seeds]
            + sorted(short_assets, key=lambda a: s_scores.get(a, float("inf")))[:max_seeds]
        )
    else:
        seed_assets = all_assets[: max_seeds * 2]

    _seeds_before_pf = len(seed_assets)
    _seeds_l_before = sum(1 for a in seed_assets if a in long_assets)
    _seeds_s_before = _seeds_before_pf - _seeds_l_before

    # Hard gate: exclude any asset with PF < 1.0 (net losers never in pool)
    if daily_returns is not None and not daily_returns.empty:
        seed_assets = [
            a for a in seed_assets
            if _pf_cache.get(a, 0.0) >= 1.0
        ]

    _seeds_after_pf = len(seed_assets)
    _seeds_l_after = sum(1 for a in seed_assets if a in long_assets)
    _seeds_s_after = _seeds_after_pf - _seeds_l_after
    log.info("  SPLIT-OPT: Seeds before PF gate: %d (%dL %dS) → after: %d (%dL %dS) [removed %d]",
             _seeds_before_pf, _seeds_l_before, _seeds_s_before,
             _seeds_after_pf, _seeds_l_after, _seeds_s_after,
             _seeds_before_pf - _seeds_after_pf)

    # Seed sets control starting points
    seeded_longs = set(a for a in seed_assets if a in long_assets)
    seeded_shorts = set(a for a in seed_assets if a in short_assets)

    # When expand_pool=True, greedy picks from ALL profitable assets (not just seeds)
    if expand_pool and daily_returns is not None and not daily_returns.empty:
        greedy_longs = {a for a in long_assets if _pf_cache.get(a, 0.0) >= 1.0}
        greedy_shorts = {a for a in short_assets if _pf_cache.get(a, 0.0) >= 1.0}
    else:
        greedy_longs = seeded_longs
        greedy_shorts = seeded_shorts

    # Merge seed scores for prior injection
    _all_seed_scores: dict[str, float] = {}
    if seed_prior_weight > 0:
        _all_seed_scores = {**l_scores, **s_scores} if daily_returns is not None and not daily_returns.empty else {}
    _max_seed = max(_all_seed_scores.values()) if _all_seed_scores else 1.0
    _max_seed = _max_seed if _max_seed > 0 else 1.0

    log.info("  SPLIT-OPT: Greedy pool: %dL %dS (seeds: %dL %dS), expand=%s, prior=%.2f",
             len(greedy_longs), len(greedy_shorts), len(seeded_longs), len(seeded_shorts),
             expand_pool, seed_prior_weight)

    candidates = []

    for start in seed_assets:
        is_long = start in long_set
        cur_l = [start] if is_long else []
        cur_s = [start] if not is_long else []
        cur_l_set: set[str] = set(cur_l)
        cur_s_set: set[str] = set(cur_s)
        # Running combined PnL as numpy array for stability computation
        _combined_vals = _daily_vals[:, _daily_col_idx[start]].copy() if (_daily_vals is not None and start in _daily_col_idx) else None

        while len(cur_l) < n_longs or len(cur_s) < n_shorts:
            pool = []
            if len(cur_l) < n_longs:
                pool.extend(a for a in greedy_longs if a not in cur_l_set)
            if len(cur_s) < n_shorts:
                pool.extend(a for a in greedy_shorts if a not in cur_s_set)
            if not pool:
                break

            best_c, best_sc = None, float("inf")
            selection = cur_l + cur_s

            for cand in pool:
                # --- Fast numpy path for correlation-based within_objective ---
                if within_objective in _corr_objectives:
                    own = cur_l if cand in long_set else cur_s
                    # Exclude self from own group (cand may not be in own yet, but safe)
                    own_others = [a for a in own if a != cand]
                    if own_others:
                        cand_ci = _corr_idx[cand]
                        own_ci = [_corr_idx[o] for o in own_others]
                        corr_vals = _corr_vals[cand_ci, own_ci]
                        if within_objective == "uncorrelated":
                            w = float(np.abs(corr_vals).mean())
                        elif within_objective == "anti_correlated":
                            w = float(corr_vals.mean())
                        else:  # hybrid
                            w = float(corr_vals.mean() + 0.2 * np.abs(corr_vals).mean())
                    else:
                        w = 0.0
                else:
                    own = (
                        [a for a in cur_l if a != cand]
                        if cand in long_set
                        else [a for a in cur_s if a != cand]
                    )
                    w = _within_score(corr_matrix, cand, own, within_objective, daily_returns)

                if streamlit_logic:
                    sc = w
                    if prioritize_stability and _combined_vals is not None and cand in _daily_col_idx:
                        test_combined = _combined_vals + _daily_vals[:, _daily_col_idx[cand]]
                        if fast_mode:
                            r2 = _fast_stability(test_combined)
                        else:
                            r2 = calculate_stability(pd.Series(test_combined))
                        sc = w + 1.5 * (1 - r2)
                else:
                    cb = _combined_score(
                        daily_returns, corr_matrix,
                        selection + [cand], long_assets, combined_objective,
                    )
                    wt_w = 0.15 if prioritize_stability else 0.4
                    wt_c = 0.85 if prioritize_stability else 0.6
                    sc = wt_w * w + wt_c * cb

                # Inject seed prior as soft tiebreaker
                if seed_prior_weight > 0 and _all_seed_scores:
                    normalized_prior = _all_seed_scores.get(cand, _max_seed) / _max_seed
                    sc += seed_prior_weight * normalized_prior

                if sc < best_sc:
                    best_sc = sc
                    best_c = cand

            if best_c:
                if best_c in long_set:
                    cur_l.append(best_c)
                    cur_l_set.add(best_c)
                else:
                    cur_s.append(best_c)
                    cur_s_set.add(best_c)
                if _combined_vals is not None and best_c in _daily_col_idx:
                    _combined_vals = _combined_vals + _daily_vals[:, _daily_col_idx[best_c]]

        if len(cur_l) == n_longs and len(cur_s) == n_shorts:
            final = cur_l + cur_s
            r2, f_score = 0.0, 0.0
            metrics: dict = {}
            if daily_returns is not None and not daily_returns.empty:
                combined = daily_returns[final].sum(axis=1)
                r2 = calculate_stability(combined)
                f_score = _combined_score(
                    daily_returns, corr_matrix, final, long_assets, combined_objective,
                )
                cum = combined.cumsum()
                peak = np.maximum.accumulate(cum.values)
                max_dd = float((cum.values - peak).min())
                gp = float(combined[combined > 0].sum())
                gl = abs(float(combined[combined < 0].sum()))
                pf = min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
                metrics = {
                    "net_profit": float(cum.iloc[-1]) if not cum.empty else 0,
                    "profit_factor": float(pf),
                    "max_drawdown": float(max_dd),
                    "stability": float(r2),
                    "n_assets": len(final),
                }
            candidates.append({
                "longs": sorted(cur_l), "shorts": sorted(cur_s),
                "assets": sorted(final), "score": f_score, "stability_r2": r2,
                "metrics": metrics,
            })

    log.info("  SPLIT-OPT: Greedy done — %d raw candidates from %d seeds (%.2fs)",
             len(candidates), len(seed_assets), _time.perf_counter() - _sp_t0)

    # Dedup and sort
    unique: dict[tuple, dict] = {}
    for c in candidates:
        key = tuple(sorted(c["longs"] + c["shorts"]))
        if key not in unique:
            if prioritize_stability:
                c["score"] = 0.3 * c["score"] + 0.7 * (1 - c.get("stability_r2", 0))
            unique[key] = c

    sorted_candidates = sorted(unique.values(), key=lambda x: x["score"])
    log.info("  SPLIT-OPT: After dedup: %d unique portfolios", len(unique))

    # Diversity filter: only accept portfolios that differ by ≥2 assets
    accepted: list[dict] = []
    for c in sorted_candidates:
        c_set = set(c["assets"])
        if all(len(c_set.symmetric_difference(set(a["assets"]))) >= 2 for a in accepted):
            accepted.append(c)
        if len(accepted) >= n_portfolios:
            break

    # Exclusion-based re-runs: exclude assets from best portfolio and re-run greedy
    if len(accepted) < n_portfolios and accepted and daily_returns is not None:
        for attempt in range(n_portfolios - len(accepted)):
            # Exclude the most-used assets across accepted portfolios
            asset_freq: dict[str, int] = {}
            for a in accepted:
                for x in a["assets"]:
                    asset_freq[x] = asset_freq.get(x, 0) + 1
            # Pick 1-2 most frequent to exclude
            exclude = set(sorted(asset_freq, key=asset_freq.get, reverse=True)[:attempt + 1])  # type: ignore[arg-type]
            reduced_longs = greedy_longs - exclude
            reduced_shorts = greedy_shorts - exclude
            if len(reduced_longs) < n_longs or len(reduced_shorts) < n_shorts:
                break
            # Run single greedy from best remaining seed
            for start in (list(reduced_longs) + list(reduced_shorts))[:5]:
                is_long = start in long_set
                cur_l = [start] if is_long else []
                cur_s = [start] if not is_long else []
                cur_l_set_ex: set[str] = set(cur_l)
                cur_s_set_ex: set[str] = set(cur_s)
                _comb_ex = _daily_vals[:, _daily_col_idx[start]].copy() if (_daily_vals is not None and start in _daily_col_idx) else None
                while len(cur_l) < n_longs or len(cur_s) < n_shorts:
                    pool = []
                    if len(cur_l) < n_longs:
                        pool.extend(a for a in reduced_longs if a not in cur_l_set_ex)
                    if len(cur_s) < n_shorts:
                        pool.extend(a for a in reduced_shorts if a not in cur_s_set_ex)
                    if not pool:
                        break
                    best_c, best_sc = None, float("inf")
                    selection = cur_l + cur_s
                    for cand in pool:
                        # Fast numpy path for correlation-based within_objective
                        if within_objective in _corr_objectives:
                            own = cur_l if cand in long_set else cur_s
                            own_others = [a for a in own if a != cand]
                            if own_others:
                                cand_ci = _corr_idx[cand]
                                own_ci = [_corr_idx[o] for o in own_others]
                                cv = _corr_vals[cand_ci, own_ci]
                                if within_objective == "uncorrelated":
                                    w = float(np.abs(cv).mean())
                                elif within_objective == "anti_correlated":
                                    w = float(cv.mean())
                                else:
                                    w = float(cv.mean() + 0.2 * np.abs(cv).mean())
                            else:
                                w = 0.0
                        else:
                            own = ([a for a in cur_l if a != cand] if cand in long_set
                                   else [a for a in cur_s if a != cand])
                            w = _within_score(corr_matrix, cand, own, within_objective, daily_returns)
                        if streamlit_logic:
                            sc = w
                            if prioritize_stability and _comb_ex is not None and cand in _daily_col_idx:
                                test_c = _comb_ex + _daily_vals[:, _daily_col_idx[cand]]
                                if fast_mode:
                                    sc += 1.5 * (1 - _fast_stability(test_c))
                                else:
                                    sc += 1.5 * (1 - calculate_stability(pd.Series(test_c)))
                        else:
                            cb = _combined_score(daily_returns, corr_matrix,
                                                 selection + [cand], long_assets, combined_objective)
                            wt_w = 0.15 if prioritize_stability else 0.4
                            wt_c = 0.85 if prioritize_stability else 0.6
                            sc = wt_w * w + wt_c * cb
                        # Inject seed prior as soft tiebreaker
                        if seed_prior_weight > 0 and _all_seed_scores:
                            normalized_prior = _all_seed_scores.get(cand, _max_seed) / _max_seed
                            sc += seed_prior_weight * normalized_prior
                        if sc < best_sc:
                            best_sc = sc
                            best_c = cand
                    if best_c:
                        if best_c in long_set:
                            cur_l.append(best_c)
                            cur_l_set_ex.add(best_c)
                        else:
                            cur_s.append(best_c)
                            cur_s_set_ex.add(best_c)
                        if _comb_ex is not None and best_c in _daily_col_idx:
                            _comb_ex = _comb_ex + _daily_vals[:, _daily_col_idx[best_c]]
                if len(cur_l) == n_longs and len(cur_s) == n_shorts:
                    final = cur_l + cur_s
                    c_set = set(final)
                    if all(len(c_set.symmetric_difference(set(a["assets"]))) >= 2 for a in accepted):
                        combined = daily_returns[final].sum(axis=1)
                        r2 = calculate_stability(combined)
                        f_score = _combined_score(daily_returns, corr_matrix, final, long_assets, combined_objective)
                        cum = combined.cumsum()
                        peak = np.maximum.accumulate(cum.values)
                        max_dd = float((cum.values - peak).min())
                        gp = float(combined[combined > 0].sum())
                        gl = abs(float(combined[combined < 0].sum()))
                        pf = min(gp / gl, 10.0) if gl > 0 else (10.0 if gp > 0 else 0.0)
                        accepted.append({
                            "longs": sorted(cur_l), "shorts": sorted(cur_s),
                            "assets": sorted(final), "score": f_score, "stability_r2": r2,
                            "metrics": {"net_profit": float(cum.iloc[-1]) if not cum.empty else 0,
                                        "profit_factor": float(pf), "max_drawdown": float(max_dd),
                                        "stability": float(r2), "n_assets": len(final)},
                        })
                        break
            if len(accepted) >= n_portfolios:
                break

    # Fallback: fill with remaining unique if diversity too strict
    if len(accepted) < n_portfolios:
        for c in sorted_candidates:
            if c not in accepted:
                accepted.append(c)
            if len(accepted) >= n_portfolios:
                break
    log.info("  SPLIT-OPT: FINAL → %d accepted portfolios (%.2fs total)",
             len(accepted), _time.perf_counter() - _sp_t0)
    return accepted


def _within_score(
    corr: pd.DataFrame, cand: str, group: list[str], obj: str,
    daily_returns: pd.DataFrame | None = None,
) -> float:
    """Score candidate's fit within its own group (lower = better)."""
    if not group:
        return 0.0
    corrs = corr.loc[cand, group]
    # Fast path: correlation-based objectives (original 3)
    if obj == "uncorrelated":
        return float(corrs.abs().mean())
    if obj == "anti_correlated":
        return float(corrs.mean())
    if obj == "hybrid":
        return float(corrs.mean() + 0.2 * corrs.abs().mean())
    # All other objectives: compute quality score using daily PnL
    if daily_returns is not None:
        selection = group + [cand]
        valid = [s for s in selection if s in daily_returns.columns]
        if len(valid) >= 2:
            combined = daily_returns[valid].sum(axis=1)
            if obj == "stability":
                return 1 - calculate_stability(combined)
            # Lazy-init structural objectives if needed
            from backend.analysis.scoring import _init_structural_objectives
            if SCORING_FUNCTIONS.get(obj) is None and obj.startswith("max_"):
                _init_structural_objectives()
            if obj in SCORING_FUNCTIONS and SCORING_FUNCTIONS[obj] is not None:
                return SCORING_FUNCTIONS[obj](combined)
    # Fallback: hybrid correlation
    return float(corrs.mean() + 0.2 * corrs.abs().mean())


def _combined_score(
    daily: pd.DataFrame | None, corr: pd.DataFrame,
    selection: list[str], long_assets: list[str], obj: str,
) -> float:
    """Score the full L+S portfolio (lower = better)."""
    if daily is None or len(selection) < 2:
        return 0.0
    combined = daily[selection].sum(axis=1)

    if obj == "stability":
        return 1 - calculate_stability(combined)
    if obj == "anti_correlated":
        sel_l = [a for a in selection if a in long_assets]
        sel_s = [a for a in selection if a not in long_assets]
        if sel_l and sel_s:
            return float(corr.loc[sel_l, sel_s].values.mean())
        return 0.0
    if obj == "uncorrelated":
        sel_corrs = corr.loc[selection, selection]
        mask = np.triu(np.ones(sel_corrs.shape), k=1).astype(bool)
        upper = sel_corrs.where(mask).stack()
        return float(upper.abs().mean()) if not upper.empty else 0.0
    if obj == "hybrid":
        sel_corrs = corr.loc[selection, selection]
        mask = np.triu(np.ones(sel_corrs.shape), k=1).astype(bool)
        upper = sel_corrs.where(mask).stack()
        if upper.empty:
            return 0.0
        return float(upper.mean() + 0.2 * upper.abs().mean())
    if obj == "min_drawdown":
        cum = combined.cumsum()
        peak = np.maximum.accumulate(cum.values)
        max_dd = abs(min((cum.values - peak).min(), 0))
        total = cum.values[-1] if cum.values[-1] > 0 else 1
        return max_dd / abs(total)
    if obj in SCORING_FUNCTIONS:
        return SCORING_FUNCTIONS[obj](combined)
    return 0.0
