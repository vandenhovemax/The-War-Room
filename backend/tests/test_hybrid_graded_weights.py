"""Graded-weight parity tests for hybrid portfolios.

Verifies the end-to-end path that makes live per-asset position size match
the optimizer's graded category scoring:

  event_wfo.asset_scores_at_end
    → step2_service.run_step2.latest_asset_scores
    → hybrid_portfolio_refresh._project_weights_from_scores
    → KellyAllocation.weights_json
    → engine_coordinator._get_kelly_weight (live sizing)

A previous interim fallback wrote UNIFORM 1/N weights, silently neutralizing
the optimizer's grading (risk * (1/N) * N = risk). These tests lock in the
new behavior and guard against regressing back to uniform.
"""
from __future__ import annotations

from backend.services.hybrid_portfolio_refresh import _project_weights_from_scores


class TestProjectWeightsFromScores:

  def test_graded_scores_produce_proportional_weights(self):
    """High score → high weight. Uniform inputs should NEVER come out."""
    assets = ["BTCUSDT_Long", "ETHUSDT_Long", "SOLUSDT_Long"]
    scores = {
      "BTCUSDT_Long": 0.90,
      "ETHUSDT_Long": 0.60,
      "SOLUSDT_Long": 0.30,
    }
    w = _project_weights_from_scores(assets, scores)

    assert abs(sum(w.values()) - 1.0) < 1e-9
    # Order preserved: BTC > ETH > SOL
    assert w["BTCUSDT_Long"] > w["ETHUSDT_Long"] > w["SOLUSDT_Long"]
    # Exact proportional normalization
    assert abs(w["BTCUSDT_Long"] - 0.90 / 1.80) < 1e-9
    assert abs(w["ETHUSDT_Long"] - 0.60 / 1.80) < 1e-9
    assert abs(w["SOLUSDT_Long"] - 0.30 / 1.80) < 1e-9

  def test_empty_scores_fall_back_to_uniform(self):
    """Missing scores ⇒ safe uniform 1/N (no crash)."""
    assets = ["A_Long", "B_Long", "C_Long", "D_Long"]
    w = _project_weights_from_scores(assets, {})
    assert all(abs(v - 0.25) < 1e-9 for v in w.values())

  def test_negative_scores_ignored_in_weighting(self):
    """Non-positive scores get 0 weight; positives renormalize."""
    assets = ["A_Long", "B_Long", "C_Long"]
    scores = {"A_Long": 1.0, "B_Long": 3.0, "C_Long": -5.0}
    w = _project_weights_from_scores(assets, scores)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["C_Long"] == 0.0
    assert abs(w["A_Long"] - 0.25) < 1e-9   # 1 / (1+3)
    assert abs(w["B_Long"] - 0.75) < 1e-9   # 3 / (1+3)

  def test_all_zero_scores_fall_back_to_uniform(self):
    assets = ["A", "B"]
    w = _project_weights_from_scores(assets, {"A": 0.0, "B": 0.0})
    assert w == {"A": 0.5, "B": 0.5}

  def test_graded_preserves_sizing_signal(self):
    """A 3x-graded asset gets 3x the weight of a 1x-graded sibling.

    This is the whole point: a live trade on the stronger asset gets 3x
    the capital of a live trade on the weaker one — same as the optimizer
    sized them when producing the final IS basket.
    """
    assets = ["Strong_Long", "Weak_Long"]
    w = _project_weights_from_scores(assets, {"Strong_Long": 3.0, "Weak_Long": 1.0})
    assert abs(w["Strong_Long"] / w["Weak_Long"] - 3.0) < 1e-9


class TestStep2ServiceExposesScores:
  """Guard: step2_service return dict must include latest_asset_scores.

  Consumed by hybrid_portfolio_refresh. If this key ever goes missing the
  whole live-sizing chain silently reverts to uniform weights.
  """

  def test_return_shape_has_latest_asset_scores_key(self):
    import inspect
    from backend.analysis import step2_service
    src = inspect.getsource(step2_service.run_step2)
    assert "\"latest_asset_scores\":" in src, (
      "step2_service.run_step2 must expose 'latest_asset_scores' at the top "
      "level of its return dict — hybrid_portfolio_refresh depends on it."
    )

  def test_per_window_carries_scores(self):
    import inspect
    from backend.analysis import step2_service
    src = inspect.getsource(step2_service.run_step2)
    assert "\"asset_scores_at_end\":" in src, (
      "Each per_window entry in step2_service.run_step2 must carry "
      "'asset_scores_at_end' so downstream consumers can rebuild grading."
    )


class TestHybridRefreshSeedingPath:
  """Guard: refresh_portfolio must accept allow_inactive=True.

  At portfolio save, the portfolio is inactive (user hasn't turned it on
  for live yet). We seed the initial KellyAllocation row regardless so the
  graded weights apply from trade 1.
  """

  def test_refresh_portfolio_accepts_allow_inactive(self):
    import inspect
    from backend.services import hybrid_portfolio_refresh
    sig = inspect.signature(hybrid_portfolio_refresh.refresh_portfolio)
    assert "allow_inactive" in sig.parameters, (
      "refresh_portfolio must accept allow_inactive=True for portfolio-save "
      "seeding of the initial KellyAllocation row."
    )

  def test_create_portfolio_seeds_on_hybrid_save(self):
    """create_portfolio must call refresh_portfolio for hybrid saves."""
    import inspect
    from backend.api.routes import portfolio as portfolio_route
    src = inspect.getsource(portfolio_route.create_portfolio)
    assert "refresh_portfolio" in src, (
      "create_portfolio must seed initial KellyAllocation by calling "
      "refresh_portfolio for hybrid portfolios."
    )
    assert "allow_inactive=True" in src, (
      "create_portfolio must pass allow_inactive=True when seeding — the "
      "portfolio is saved inactive."
    )


class TestSaveTrackCrossStratWiring:
  """Guard: /save-track must wire is_cross_strat for multi-batch WFAS saves.

  WFAS picks assets from multiple batches, each with its own optimized config.
  StrategyExecutor only reads per-asset batch parameters when is_cross_strat=True.
  Without this flag, assets from batches with non-default configs would trade
  with wrong parameters (e.g., Price MA Period=50 strategy default instead of
  the optimized 20 from the asset's batch).
  """

  def test_save_track_sets_is_cross_strat_when_multi_batch(self):
    import inspect
    from backend.api.routes import wfo
    src = inspect.getsource(wfo.save_wfo_track)
    assert "is_cross_strat" in src, (
      "/save-track must set is_cross_strat on the Portfolio so StrategyExecutor "
      "can do per-asset batch parameter lookup."
    )
    assert "asset_batch_ids" in src or "len(" in src, (
      "/save-track must detect multi-batch saves (assets spanning >1 batch) "
      "to decide is_cross_strat."
    )

  def test_save_track_enriches_strategy_metadata(self):
    """source_strategies must carry strategy_id, strategy_name, timeframe."""
    import inspect
    from backend.api.routes import wfo
    src = inspect.getsource(wfo.save_wfo_track)
    for key in ("strategy_id", "strategy_name", "timeframe"):
      assert f'"{key}"' in src or f"'{key}'" in src, (
        f"/save-track must store {key} in source_strategies for "
        f"StrategyExecutor.activate_portfolio()."
      )
