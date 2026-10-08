"""Step 5 (T-002) — `get_effective_step2()` merge semantics.

Covers:
  * Save-only blob → returned as-is (selected_records preserved).
  * Refresh-only blob → returned as the effective view.
  * Both present, no key collision → union, sorted by (dt, run_id).
  * Both present, key collision → refresh-time record wins.
  * aggregate_monthly_matrix union (refresh wins on overlapping month).
  * Aggregate metrics fallback: refresh wins when present, else save.
  * Side-case dedup (Long vs long) collapses to one record.
  * Empty / non-dict settings → empty dict.
  * `has_wfo_replay_metadata` accepts refresh-only portfolios.
  * `get_wfas_forward_cutoff` extends past save-time when refresh has later dt.
  * `load_wfas_replay` consumes the merged view (covered indirectly via
    forward-cutoff, since the hybrid path requires DB rows we don't seed
    here).
"""
from __future__ import annotations

from datetime import date

from backend.analysis.portfolio_wfo_replay import (
  get_effective_step2,
  get_wfas_forward_cutoff,
  has_wfo_replay_metadata,
)


def _save_blob() -> dict:
  return {
    "selected_records": [
      {"run_id": 1, "dt": "2022-06-01", "symbol": "BTCUSDT", "side": "long",  "pnl": 10.0},
      {"run_id": 1, "dt": "2023-03-15", "symbol": "ETHUSDT", "side": "long",  "pnl": 20.0},
      {"run_id": 1, "dt": "2024-01-10", "symbol": "BTCUSDT", "side": "short", "pnl": -5.0},
    ],
    "aggregate_monthly_matrix": {"matrix": {
      "2022": {"6": 10.0},
      "2023": {"3": 20.0},
      "2024": {"1": -5.0},
    }},
    "aggregate_equity_curve": [{"dt": "2022-06-01", "equity": 10000}],
    "aggregate_breakdown": {"by_symbol": {"BTCUSDT": 5.0, "ETHUSDT": 20.0}},
    "cumulative_metrics": {"net_profit": 25.0, "trades": 3},
  }


def _refresh_blob() -> dict:
  return {
    "selected_records": [
      {"run_id": 2, "dt": "2026-04-20", "symbol": "YGG",   "side": "short", "pnl": 30.0},
      {"run_id": 2, "dt": "2026-04-21", "symbol": "LUMIA", "side": "long",  "pnl": 50.0},
    ],
    "aggregate_monthly_matrix": {"matrix": {
      "2026": {"4": 80.0},
    }},
    "aggregate_equity_curve": [{"dt": "2026-04-20", "equity": 12000}],
    "cumulative_metrics": {"net_profit": 105.0, "trades": 5},
    "latest_asset_scores": {"YGG": 1.2, "LUMIA": 0.9},
    "latest_portfolio_at_end": ["YGG", "LUMIA"],
  }


# ── Empty / non-dict ───────────────────────────────────────────────────

def test_get_effective_step2_handles_non_dict_input():
  assert get_effective_step2(None) == {}
  assert get_effective_step2("not a dict") == {}
  assert get_effective_step2([]) == {}


def test_get_effective_step2_returns_empty_when_no_records_anywhere():
  assert get_effective_step2({}) == {}
  assert get_effective_step2({"step2_result": {}, "refresh_step2_result": {}}) == {}
  assert get_effective_step2({"step2_result": {"selected_records": []}}) == {}


# ── Save-only ──────────────────────────────────────────────────────────

def test_get_effective_step2_save_only_returns_save_records():
  settings = {"step2_result": _save_blob()}
  out = get_effective_step2(settings)
  assert len(out["selected_records"]) == 3
  syms = {r["symbol"] for r in out["selected_records"]}
  assert syms == {"BTCUSDT", "ETHUSDT"}
  # Aggregates are REBUILT from the records (2026-06-10): counts/PnL come
  # from the record roll-up; unknown keys from the save blob are inherited.
  cm = out["cumulative_metrics"]
  assert cm["net_profit"] == 25.0
  assert cm["total_trades"] == 3
  assert cm["trades"] == 3  # legacy save-blob key survives as base
  # Breakdown is rebuilt as a per-asset list, not the save blob's dict.
  assert isinstance(out["aggregate_breakdown"], list)
  assert len(out["aggregate_breakdown"]) >= 1


# ── Refresh-only ───────────────────────────────────────────────────────

def test_get_effective_step2_refresh_only_returns_refresh_records():
  settings = {"refresh_step2_result": _refresh_blob()}
  out = get_effective_step2(settings)
  assert len(out["selected_records"]) == 2
  syms = {r["symbol"] for r in out["selected_records"]}
  assert syms == {"YGG", "LUMIA"}
  assert out["latest_portfolio_at_end"] == ["YGG", "LUMIA"]


# ── Union, no collision ────────────────────────────────────────────────

def test_get_effective_step2_unions_disjoint_records():
  settings = {
    "step2_result": _save_blob(),
    "refresh_step2_result": _refresh_blob(),
  }
  out = get_effective_step2(settings)
  assert len(out["selected_records"]) == 5  # 3 save + 2 refresh

  # Sorted ascending by dt then run_id.
  dts = [r["dt"] for r in out["selected_records"]]
  assert dts == sorted(dts)
  assert dts[0] == "2022-06-01"
  assert dts[-1] == "2026-04-21"


# ── Union with key collision: refresh wins ─────────────────────────────

def test_get_effective_step2_refresh_wins_on_record_key_collision():
  save = {
    "selected_records": [
      {"run_id": 1, "dt": "2026-04-20", "symbol": "YGG", "side": "short", "pnl": 999.0},
    ],
  }
  refresh = {
    "selected_records": [
      {"run_id": 1, "dt": "2026-04-20", "symbol": "YGG", "side": "short", "pnl": 30.0},
    ],
  }
  settings = {"step2_result": save, "refresh_step2_result": refresh}
  out = get_effective_step2(settings)
  assert len(out["selected_records"]) == 1
  assert out["selected_records"][0]["pnl"] == 30.0, "refresh-time PnL must win on collision"


def test_get_effective_step2_dedup_is_case_insensitive_on_side():
  save = {
    "selected_records": [
      {"run_id": 1, "dt": "2026-04-20", "symbol": "YGG", "side": "Long", "pnl": 1.0},
    ],
  }
  refresh = {
    "selected_records": [
      {"run_id": 1, "dt": "2026-04-20", "symbol": "YGG", "side": "long", "pnl": 2.0},
    ],
  }
  out = get_effective_step2({"step2_result": save, "refresh_step2_result": refresh})
  assert len(out["selected_records"]) == 1, "Long and long must dedup to one record"
  assert out["selected_records"][0]["pnl"] == 2.0


# ── aggregate_monthly_matrix union ─────────────────────────────────────

def test_get_effective_step2_unions_monthly_matrix_keeps_all_years():
  settings = {
    "step2_result": _save_blob(),
    "refresh_step2_result": _refresh_blob(),
  }
  out = get_effective_step2(settings)
  matrix = out["aggregate_monthly_matrix"]["matrix"]
  assert set(matrix.keys()) == {"2022", "2023", "2024", "2026"}
  assert matrix["2022"] == {"6": 10.0}
  assert matrix["2026"] == {"4": 80.0}


def test_get_effective_step2_sums_boundary_month_across_blobs():
  """2026-06-10 contract change: the matrix is REBUILT from the merged
  records. A month split across save and refresh (anchor mid-month) sums
  both sides — the old per-blob union let the refresh blob's partial
  month silently overwrite the save side's PnL."""
  save = {
    "aggregate_monthly_matrix": {"matrix": {"2026": {"4": 10.0, "5": 20.0}}},
    "selected_records": [
      {"run_id": 1, "dt": "2026-04-01", "symbol": "X", "side": "long", "pnl": 10.0},
      {"run_id": 1, "dt": "2026-05-03", "symbol": "X", "side": "long", "pnl": 20.0},
    ],
  }
  refresh = {
    "aggregate_monthly_matrix": {"matrix": {"2026": {"4": 99.0}}},
    "selected_records": [
      {"run_id": 2, "dt": "2026-04-25", "symbol": "Y", "side": "long", "pnl": 99.0},
    ],
  }
  out = get_effective_step2({"step2_result": save, "refresh_step2_result": refresh})
  matrix = out["aggregate_monthly_matrix"]["matrix"]
  assert matrix["2026"]["4"] == 109.0, "April must SUM save (10) + refresh (99)"
  assert matrix["2026"]["5"] == 20.0, "save May must survive (record-backed)"


# ── Other aggregates: rebuilt from merged records ──────────────────────

def test_get_effective_step2_rebuilds_aggregates_from_merged_records():
  settings = {
    "step2_result": _save_blob(),
    "refresh_step2_result": _refresh_blob(),
  }
  out = get_effective_step2(settings)
  cm = out["cumulative_metrics"]
  # 10 + 20 - 5 + 30 + 50 across the 5 merged records
  assert cm["net_profit"] == 105.0
  assert cm["total_trades"] == 5
  # Equity curve rebuilt across ALL records (not the refresh blob's
  # trailing-window-only curve)
  eq = out["aggregate_equity_curve"]
  assert len(eq) == 5
  assert eq[0]["date"] == "2022-06-01"
  assert eq[-1]["date"] == "2026-04-21"
  assert eq[-1]["equity"] == 105.0


def test_get_effective_step2_rebuilds_even_when_refresh_has_no_aggregates():
  refresh_no_aggs = {
    "selected_records": [
      {"run_id": 2, "dt": "2026-04-20", "symbol": "YGG", "side": "short", "pnl": 30.0},
    ],
  }
  settings = {
    "step2_result": _save_blob(),
    "refresh_step2_result": refresh_no_aggs,
  }
  out = get_effective_step2(settings)
  cm = out["cumulative_metrics"]
  assert cm["net_profit"] == 55.0  # 25 save + 30 refresh
  assert cm["total_trades"] == 4
  assert isinstance(out["aggregate_breakdown"], list)


# ── has_wfo_replay_metadata picks up refresh-only portfolios ───────────

def test_has_wfo_replay_metadata_true_for_refresh_only_portfolio():
  settings = {"refresh_step2_result": _refresh_blob()}
  assert has_wfo_replay_metadata(settings) is True


def test_has_wfo_replay_metadata_false_for_empty_settings():
  assert has_wfo_replay_metadata({}) is False
  assert has_wfo_replay_metadata(None) is False


# ── get_wfas_forward_cutoff extends past save-time ─────────────────────

def test_forward_cutoff_uses_refresh_records_when_later_than_save():
  settings = {
    "step2_result": _save_blob(),
    "refresh_step2_result": _refresh_blob(),
  }
  cutoff = get_wfas_forward_cutoff(settings)
  # Latest dt across both blobs is 2026-04-21 from the refresh side.
  assert cutoff == date(2026, 4, 21)


def test_forward_cutoff_handles_refresh_only_portfolio():
  settings = {"refresh_step2_result": _refresh_blob()}
  cutoff = get_wfas_forward_cutoff(settings)
  assert cutoff == date(2026, 4, 21)
