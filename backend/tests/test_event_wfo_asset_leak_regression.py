"""Regression guard for the 2026-04-19 `run_event_wfo` asset loop-variable leak.

Bug summary:
  Inside `run_event_wfo`'s main event loop, `asset = _scoring_key(trade)`
  was shadowed by inner for-loops that reused the name `asset` as their
  loop variable. Python leaks for-loop variables into the enclosing scope,
  so after the rebalance block `asset` referenced the last iterated portfolio
  key, not the current trade's scoring key. The downstream portfolio-membership
  gate (`if asset in current_portfolio`) became a near-tautology and admitted
  ~70% wrong-asset trades, inflating Hybrid Step 2 PnL ~3.3×.

See: Notion → 🐛 Known Bugs → 📈 WFAS → ✅ Fixed Bugs → "event_wfo.py asset
loop-variable leak inflates Hybrid Step 2 PnL ~3.3×"

This file ships two defenses:

  1. `test_no_asset_loop_variable_in_run_event_wfo` — static AST guard.
     Parses `backend/analysis/event_wfo.py`, walks only the `run_event_wfo`
     function body, and fails if ANY `for ... in ...:` uses `asset` as a
     loop-variable target. This catches the mistake BEFORE the code runs.

  2. `test_asset_parity_end_to_end_rebalance_path` — integration guard.
     Builds a multi-batch fixture that forces the rebalance code path to
     fire (category_filter_enabled, rebalance_every=1, n_assets<count) and
     asserts that every record in `selected_records` has
     `record["asset"] == canonical_key(batch_id, symbol, side)`. This
     catches any variant of the bug — even ones the AST guard misses.

A third defense lives in `backend/analysis/wfo_event_dedup.py::
_assert_record_asset_parity`, which raises in production if this invariant
is ever violated.
"""
from __future__ import annotations

import ast
import inspect
from collections import namedtuple
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.analysis import event_wfo
from backend.analysis.step2_service import run_step2
from backend.analysis.wfo_event_dedup import (
  _canonical_asset_key,
  _assert_record_asset_parity,
  _record_asset_is_consistent,
)


# ── Defense 1: AST static guard ─────────────────────────────────────

EVENT_WFO_PATH = Path(event_wfo.__file__)


def _find_func_def(tree: ast.Module, name: str) -> ast.FunctionDef:
  for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name == name:
      return node
  raise AssertionError(f"Could not find function {name!r} at module level")


def _flatten_target_names(target: ast.AST):
  """Yield every bare `Name` id reachable from a for-loop target.

  Handles arbitrarily nested Tuple/List targets — e.g. `(i, (asset, s, c))`
  unpacks to {i, asset, s, c}. Starred targets (`*rest`) are also handled.
  """
  if isinstance(target, ast.Name):
    yield target.id
  elif isinstance(target, (ast.Tuple, ast.List)):
    for elt in target.elts:
      yield from _flatten_target_names(elt)
  elif isinstance(target, ast.Starred):
    yield from _flatten_target_names(target.value)


def _iter_for_targets(node: ast.AST):
  """Yield (for_node, target_name) pairs for every `for` loop below `node`.

  Flattens arbitrarily nested tuple/list targets so `for i, (asset, s, c) in ...`
  is caught the same as `for asset, s, c in ...`. Both async and sync loops
  are walked (AsyncFor inherits For-like structure).
  """
  for child in ast.walk(node):
    if isinstance(child, (ast.For, ast.AsyncFor)):
      for name in _flatten_target_names(child.target):
        yield child, name


def test_no_asset_loop_variable_in_run_event_wfo():
  """FAIL if any for-loop inside run_event_wfo uses `asset` as a loop var.

  This is the exact bug class that caused the 2026-04-19 incident. Do NOT
  silence this test — use the renaming convention shown in the fix comments
  (`_rl_asset`, `_l1_asset`, `_gm_asset`, `_l3_asset`, or any other non-
  colliding local name). See the module docstring.
  """
  src = EVENT_WFO_PATH.read_text()
  tree = ast.parse(src)
  run_event_wfo_def = _find_func_def(tree, "run_event_wfo")

  offenders: list[tuple[int, str]] = []
  for for_node, target_name in _iter_for_targets(run_event_wfo_def):
    if target_name == "asset":
      offenders.append((for_node.lineno, ast.unparse(for_node.target)))

  if offenders:
    lines = "\n".join(
      f"    line {lineno}: `for {tgt} in ...`"
      for lineno, tgt in offenders
    )
    raise AssertionError(
      "run_event_wfo contains for-loop(s) that re-bind the outer `asset` "
      "variable (set from `_scoring_key(trade)`). This is the classic "
      "2026-04-19 loop-variable leak. Rename the loop variable (e.g. "
      "`_rl_asset`, `_l1_asset`, `_gm_asset`, `_l3_asset`) — never reuse "
      "`asset` as a for-loop target inside run_event_wfo.\n"
      f"Offenders:\n{lines}"
    )


def test_ast_guard_flags_synthetic_buggy_code():
  """Self-check: the AST walker must flag a for-loop with target `asset`."""
  snippet = """
def run_event_wfo(trade):
    asset = "outer"
    for asset, score in [("a", 1), ("b", 2)]:
        pass
    return asset
"""
  tree = ast.parse(snippet)
  run_event_wfo_def = _find_func_def(tree, "run_event_wfo")
  targets = [name for _, name in _iter_for_targets(run_event_wfo_def)]
  assert "asset" in targets, "AST walker failed to flag buggy synthetic code"


def test_ast_guard_accepts_synthetic_fixed_code():
  """Self-check: renamed loop variables must pass."""
  snippet = """
def run_event_wfo(trade):
    asset = "outer"
    for _rl_asset, score in [("a", 1), ("b", 2)]:
        pass
    return asset
"""
  tree = ast.parse(snippet)
  run_event_wfo_def = _find_func_def(tree, "run_event_wfo")
  targets = [name for _, name in _iter_for_targets(run_event_wfo_def)]
  assert "asset" not in targets


def test_ast_guard_flags_nested_tuple_target():
  """Self-check: `for i, (asset, s, c) in enumerate(...)` must be flagged.

  This is the exact shape of the second non-directional loop in the pre-fix
  code (line 1045 of the .bak). A naive walker that only unpacks top-level
  tuples would miss it.
  """
  snippet = """
def run_event_wfo(trade):
    asset = "outer"
    for i, (asset, score, components) in enumerate(items):
        pass
    return asset
"""
  tree = ast.parse(snippet)
  run_event_wfo_def = _find_func_def(tree, "run_event_wfo")
  targets = [name for _, name in _iter_for_targets(run_event_wfo_def)]
  assert "asset" in targets, (
    "nested-tuple target `(i, (asset, score, components))` was not flagged "
    "— walker is missing deep-unpack support"
  )


# ── Defense 2: runtime invariant in wfo_event_dedup ────────────────

def test_canonical_asset_key_batch_prefixed():
  rec = {"batch_id": 43, "symbol": "btcusdt", "side": "long"}
  assert _canonical_asset_key(rec) == "B43_BTCUSDT_Long"


def test_canonical_asset_key_short():
  rec = {"batch_id": 7, "symbol": "ethusdt", "side": "short"}
  assert _canonical_asset_key(rec) == "B7_ETHUSDT_Short"


def test_canonical_asset_key_no_batch():
  rec = {"batch_id": 0, "symbol": "solusdt", "side": "long"}
  assert _canonical_asset_key(rec) == "SOLUSDT_Long"


def test_parity_invariant_passes_on_clean_records():
  records = [
    {"batch_id": 1, "symbol": "BTCUSDT", "side": "long",  "asset": "B1_BTCUSDT_Long"},
    {"batch_id": 2, "symbol": "ETHUSDT", "side": "short", "asset": "B2_ETHUSDT_Short"},
  ]
  assert _assert_record_asset_parity(records) == 0


def test_parity_invariant_raises_on_shadowed_asset_symbol():
  """Simulate the exact bug: record says B43_COMPUSDT_Short but the trade
  fields say batch_id=105 symbol=CKBUSDT side=long (the UI run 260 pattern).
  Symbol disagreement is the leak's smoking gun.
  """
  records = [
    {"batch_id": 105, "symbol": "CKBUSDT", "side": "long",
     "asset": "B43_COMPUSDT_Short"},  # <-- leaked / stale loop variable
  ]
  with pytest.raises(AssertionError, match=r"asset-parity invariant violated"):
    _assert_record_asset_parity(records)


def test_parity_invariant_raises_on_shadowed_asset_side():
  """Side-only disagreement is also a valid leak signature."""
  records = [
    {"batch_id": 1, "symbol": "BTCUSDT", "side": "long",
     "asset": "B1_BTCUSDT_Short"},  # <-- side flipped
  ]
  with pytest.raises(AssertionError, match=r"asset-parity invariant violated"):
    _assert_record_asset_parity(records)


def test_parity_invariant_raises_on_batch_id_mismatch():
  """If both record.batch_id and asset B{n}_ prefix are present and disagree,
  that's also a leak signature."""
  records = [
    {"batch_id": 42, "symbol": "BTCUSDT", "side": "long",
     "asset": "B99_BTCUSDT_Long"},  # <-- batch disagreement
  ]
  with pytest.raises(AssertionError, match=r"asset-parity invariant violated"):
    _assert_record_asset_parity(records)


def test_parity_invariant_tolerates_missing_batch_id_in_record():
  """Dedup test fixtures sometimes use batch_id=None with asset='B1_BTC_Long'.
  That's a legal synthetic convention — symbol+side still agree — and must
  not trigger the invariant.
  """
  records = [
    {"batch_id": None, "symbol": "BTC", "side": "Long", "asset": "B1_BTC_Long"},
    {"batch_id": 0,    "symbol": "ETH", "side": "Long", "asset": "ETH_Long"},
  ]
  assert _assert_record_asset_parity(records) == 0


def test_parity_invariant_tolerates_case_variations():
  """Symbol and side comparisons must be case-insensitive."""
  records = [
    {"batch_id": 1, "symbol": "btcusdt", "side": "LONG", "asset": "B1_BTCUSDT_Long"},
  ]
  assert _assert_record_asset_parity(records) == 0


def test_parity_invariant_empty_records_noop():
  assert _assert_record_asset_parity([]) == 0


def test_record_asset_is_consistent_reason_strings():
  """Helper's reason strings must be specific enough to debug with."""
  ok, reason = _record_asset_is_consistent(
    {"batch_id": 1, "symbol": "BTC", "side": "long", "asset": "B1_BTC_Long"}
  )
  assert ok and reason == ""

  ok, reason = _record_asset_is_consistent(
    {"batch_id": 1, "symbol": "BTC", "side": "long", "asset": "B1_ETH_Long"}
  )
  assert not ok and "symbol mismatch" in reason

  ok, reason = _record_asset_is_consistent(
    {"batch_id": 1, "symbol": "BTC", "side": "long", "asset": "B1_BTC_Short"}
  )
  assert not ok and "side mismatch" in reason

  ok, reason = _record_asset_is_consistent(
    {"batch_id": 1, "symbol": "BTC", "side": "long", "asset": "B2_BTC_Long"}
  )
  assert not ok and "batch_id mismatch" in reason

  ok, reason = _record_asset_is_consistent(
    {"batch_id": 1, "symbol": "BTC", "side": "long", "asset": "garbage"}
  )
  assert not ok and "does not match" in reason


# ── Defense 3: E2E integration — Step 2 through rebalance path ─────

LightTrade = namedtuple(
  "LightTrade",
  ["run_id", "symbol", "side", "net_profit", "entry_time", "exit_time", "timeframe"],
)


def _build_rebalance_fixture():
  """Multi-batch fixture that exercises the rebalance code path.

  Design:
    - 2 batches, 4 (symbol, side) assets per batch => 8 distinct scoring keys.
    - rebalance_every=1 => rebalance fires on every non-warmup trade.
    - n_assets=3 << 8 => portfolio membership gate is *meaningful*.
    - category_filter_enabled=False (streak_event is orthogonal to this leak).
    - Single OOS window so the test runs in <1s.
  """
  # run_id -> batch_id
  run_batch_map = {
    # Batch 1
    101: 1, 102: 1, 103: 1, 104: 1,
    # Batch 2
    201: 2, 202: 2, 203: 2, 204: 2,
  }

  # Generate 8 trades per asset (>= n_trades window), spanning Jan-Feb 2024
  # with varied PnL so scoring produces different rankings.
  symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
  sides = ["long", "short", "long", "short"]
  pnls_menu = [100.0, -40.0, 80.0, -20.0, 60.0, -10.0, 50.0, 30.0]

  trades: list[LightTrade] = []
  base_dt = datetime(2024, 1, 2, 9, 0, 0)
  # Interleave trades across assets for a realistic event stream
  day = 0
  for i in range(8):  # 8 trades per asset
    for bid, run_offset in [(1, 100), (2, 200)]:
      for a, (sym, side) in enumerate(zip(symbols, sides)):
        run_id = run_offset + a + 1
        # de-synchronise asset timing so rebalance fires on distinct keys
        dt = base_dt + timedelta(days=day, hours=a)
        # Give batch 2 slightly better profile so it ranks differently
        pnl = pnls_menu[i] * (1.2 if bid == 2 else 1.0)
        trades.append(LightTrade(
          run_id=run_id, symbol=sym, side=side,
          net_profit=pnl, entry_time=dt, exit_time=dt + timedelta(hours=1),
          timeframe="30",
        ))
        day += 1

  # Single OOS window covering all trades
  step1 = {
    "windows": [
      {
        "window_num": 1,
        "is_start":  "2023-01-01",
        "oos_start": "2024-01-01",
        "oos_end":   "2024-12-31",
        "candidates": [{"selected_batches": [1, 2]}],
      },
    ],
  }

  # Rebalance-forcing config
  config = {
    "hybrid_mode": True,
    "hybrid_n_trades": 4,
    "hybrid_n_assets": 3,
    "hybrid_min_pf": 0.0,
    "hybrid_min_wr": 0.0,
    "hybrid_staleness_days": 365,
    "hybrid_rebalance_every": 1,         # every non-warmup step rebalances
    "hybrid_rebalance_mode": "global",   # NON-directional path (bug site #1)
    "hybrid_warmup_trades": 4,
    "hybrid_min_hold": 0,
    "hybrid_scoring_method": "composite",
    "hybrid_objective": "net_profit",
    "hybrid_composite_weights": {},
    "hybrid_exec_day_profit": False,
    "hybrid_sample_count_floor": 0,
    "hybrid_rescue_enabled": False,
    "hybrid_rescue_quota_pct": 0.0,
    "hybrid_rescue_min_trades": 30,
    "hybrid_rescue_min_score": 0.0,
    "hybrid_rescue_confidence_gap": 0.0,
    "session_filter_enabled": False,
    "session_filter_mode": "exclude",
    "session_filter_exclude_hours": [],
    "session_filter_exclude_days": [],
    "category_filter_enabled": False,
    "category_influence": 0.0,
    "category_lookback_trades": 20,
    "category_max_per_cat": 0,
    "category_score_metric": "wr",
    "category_mode": "soft_weight",
    "category_streak_required": 3,
    "category_fire_multiplier": 1.0,
    "category_fire_duration_trades": 10,
    "oos_months": 12,
    "preset_id": None,
  }

  return step1, trades, run_batch_map, config


def test_asset_parity_end_to_end_rebalance_path():
  """Full Step 2 run through the global-rebalance code path must produce
  zero asset mismatches. If the loop-variable leak is ever reintroduced,
  either `_assert_record_asset_parity` (inside dedup_and_rebuild) fires or
  this explicit assertion fires — both are hard failures.
  """
  step1, trades, rbm, cfg = _build_rebalance_fixture()
  out = run_step2(step1, trades, rbm, cfg, db=None)
  assert out is not None, "run_step2 returned None — hybrid_mode not honoured"

  records = out.get("selected_records", [])
  assert records, (
    "Fixture produced zero selected_records — the fixture isn't exercising "
    "the rebalance path; adjust warmup/rebalance params and rerun."
  )

  mismatches = []
  for r in records:
    ok, reason = _record_asset_is_consistent(r)
    if not ok:
      mismatches.append((r.get("asset"), reason, r.get("batch_id"),
                         r.get("symbol"), r.get("side")))
  assert not mismatches, (
    f"run_event_wfo produced {len(mismatches)}/{len(records)} records whose "
    f"stored asset disagrees with their own trade fields. "
    f"First: {mismatches[0]}. The asset loop-variable leak has been "
    f"re-introduced."
  )


def test_rebalance_fixture_actually_fires_rebalance():
  """Safety check: ensure the fixture really does trigger rebalance events.

  If rebalance_count is 0, the E2E test above would trivially pass even on
  broken code. This test guards the fixture itself.
  """
  step1, trades, rbm, cfg = _build_rebalance_fixture()
  out = run_step2(step1, trades, rbm, cfg, db=None)
  assert out is not None
  assert out.get("rebalance_count", 0) > 0, (
    "Fixture did not fire any rebalance events — adjust rebalance_every, "
    "warmup_trades, or trade volume. Without rebalances the E2E test has "
    "no power to detect the leak."
  )
