"""CS-047: Pydantic schema for ``Portfolio.source_strategies`` JSON.

These tests pin the schema's defensive contract so the helper is safe
to use from any caller in the 36+ sites that currently do
``json.loads(p.source_strategies or "{}") ... .get("x", default)``.

Contract:
  1. None / empty / malformed JSON → defaults, never raise.
  2. Unknown keys kept (extra="allow") — no backwards-compat break.
  3. Valid data round-trips through model_validate.
  4. Nested risk_profile and batch_configs parse correctly.
  5. Typed literals (mode, scope) reject obvious bad values per-field
     but the safe parser swallows the ValidationError and returns
     defaults, matching the existing .get() fallback semantics.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.db.source_strategies_schema import (
  BatchConfig,
  RiskProfile,
  SourceStrategies,
  parse_source_strategies,
)


# ── Safe parser: empty/None handling ─────────────────────────────────


def test_parse_none_returns_defaults():
  m = parse_source_strategies(None)
  assert isinstance(m, SourceStrategies)
  assert m.risk_profile.mode == "fixed"
  assert m.risk_profile.override_enabled is True


def test_parse_empty_string_returns_defaults():
  m = parse_source_strategies("")
  assert isinstance(m, SourceStrategies)


def test_parse_malformed_json_returns_defaults():
  """Never raise on bad JSON — preserve .get()-style fallback semantics."""
  m = parse_source_strategies("{not valid")
  assert isinstance(m, SourceStrategies)


def test_parse_non_object_json_returns_defaults():
  """JSON arrays/numbers/strings at top level → defaults."""
  assert isinstance(parse_source_strategies("[]"), SourceStrategies)
  assert isinstance(parse_source_strategies("42"), SourceStrategies)
  assert isinstance(parse_source_strategies('"x"'), SourceStrategies)


def test_parse_empty_object():
  m = parse_source_strategies("{}")
  assert m.risk_profile.mode == "fixed"
  assert m.risk_profile.batch_configs == {}


# ── Round-trip known-good blobs ──────────────────────────────────────


def test_parse_minimal_martingale_profile():
  raw = json.dumps({
    "risk_profile": {
      "mode": "martingale",
      "initial_risk_usd": 50.0,
      "multiplier": 2.0,
      "max_streak": 4,
      "martingale_scope": "asset_batch",
      "martingale_compound": "pct",
    },
  })
  m = parse_source_strategies(raw)
  rp = m.risk_profile
  assert rp.mode == "martingale"
  assert rp.initial_risk_usd == 50.0
  assert rp.multiplier == 2.0
  assert rp.max_streak == 4
  assert rp.martingale_scope == "asset_batch"
  assert rp.martingale_compound == "pct"


def test_parse_per_batch_config():
  raw = json.dumps({
    "risk_profile": {
      "sizing_master": "per_batch",
      "batch_configs": {
        "17": {
          "mode": "martingale",
          "value": 25.0,
          "initialCapital": 5000.0,
          "multiplier": 1.5,
          "maxStreak": 5,
          "martingaleCompound": "dollar",
        },
      },
    },
  })
  m = parse_source_strategies(raw)
  assert m.risk_profile.sizing_master == "per_batch"
  bc = m.risk_profile.batch_configs["17"]
  assert isinstance(bc, BatchConfig)
  assert bc.mode == "martingale"
  assert bc.value == 25.0
  assert bc.maxStreak == 5


def test_parse_top_level_fallback_path():
  """When override_enabled=False, engine reads the flat top-level keys."""
  raw = json.dumps({
    "risk_mode": "pct",
    "risk_per_trade_pct": 1.5,
    "risk_per_trade_usd": 100.0,
    "risk_profile": {"override_enabled": False},
  })
  m = parse_source_strategies(raw)
  assert m.risk_mode == "pct"
  assert m.risk_per_trade_pct == 1.5
  assert m.risk_per_trade_usd == 100.0
  assert m.risk_profile.override_enabled is False


# ── Backwards-compat: extra keys kept ────────────────────────────────


def test_unknown_top_level_keys_preserved():
  """extra='allow' → unknown keys don't break existing portfolios."""
  raw = json.dumps({
    "risk_profile": {"mode": "fixed"},
    "some_legacy_field": [1, 2, 3],
    "future_experiment": {"nested": True},
  })
  m = parse_source_strategies(raw)
  extra = m.model_dump()
  assert "some_legacy_field" in extra
  assert extra["some_legacy_field"] == [1, 2, 3]


def test_unknown_risk_profile_keys_preserved():
  raw = json.dumps({
    "risk_profile": {"mode": "fixed", "legacy_knob": "x"},
  })
  m = parse_source_strategies(raw)
  dumped = m.risk_profile.model_dump()
  assert dumped.get("legacy_knob") == "x"


# ── Defensive: bad typed values → parser returns defaults, not raise ──


def test_parser_swallows_bad_mode_literal():
  raw = json.dumps({"risk_profile": {"mode": "unknown_mode"}})
  m = parse_source_strategies(raw)
  # Parser returns defaults (logged warning) rather than raising, matching
  # existing .get() fallback semantics at the 36+ read sites.
  assert m.risk_profile.mode == "fixed"


def test_parser_swallows_bad_scope_literal():
  raw = json.dumps({
    "risk_profile": {"mode": "martingale", "martingale_scope": "nonsense"},
  })
  m = parse_source_strategies(raw)
  assert m.risk_profile.martingale_scope == "asset"


# ── Direct model validation still enforces (without the safe wrapper) ─


def test_direct_model_validate_rejects_bad_mode():
  with pytest.raises(ValidationError):
    RiskProfile.model_validate({"mode": "nonsense"})


def test_direct_model_validate_rejects_bad_scope():
  with pytest.raises(ValidationError):
    RiskProfile.model_validate({"martingale_scope": "nonsense"})


# ── Accepts dict input (not only strings) ────────────────────────────


def test_parse_accepts_dict():
  m = parse_source_strategies({"risk_profile": {"mode": "pct", "risk_pct": 2.0}})
  assert m.risk_profile.mode == "pct"
  assert m.risk_profile.risk_pct == 2.0
