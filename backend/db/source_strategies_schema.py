"""CS-047: Pydantic schema for ``Portfolio.source_strategies`` JSON.

The ``source_strategies`` column stores the full portfolio configuration
blob (risk profile, Kelly settings, martingale options, per-batch
overrides, etc.) as free-form JSON. 36+ call sites read it with
``json.loads(p.source_strategies or "{}")`` followed by chained
``.get()`` calls with defaults — every site duplicates the defaults and
there is no single place that documents the contract.

This module is the **contract of record**. It does two things:

  1. Declares a typed Pydantic schema (``SourceStrategies`` with nested
     ``RiskProfile`` and ``BatchConfig``) enumerating every field the
     live engine and API routes actually read. ``extra="allow"`` keeps
     unknown keys so the schema never blocks a legacy portfolio.

  2. Exposes ``parse_source_strategies()`` — a defensive parser that
     accepts either a JSON string, a dict, or None and always returns a
     validated ``SourceStrategies`` model (empty on parse failure, with
     a warning logged).

This module is ADDITIVE. It does not replace any existing
``json.loads`` / ``.get()`` call — those sites continue to work as
before. New code can opt in; existing reads can migrate file by file
with their own dedicated tests.

Key sources (see grep 2026-04-21):
  - ``engine_coordinator._get_portfolio_risk`` — mode, initial_risk_usd,
    risk_pct, multiplier, max_streak, martingale_compound,
    martingale_scope, portfolio_wide, initial_capital, override_enabled,
    apply_group_multiplier, sizing_master, batch_configs
  - ``engine_coordinator._get_position_margin_cap`` — batch_configs
    per-batch maxMargin
  - Top-level fallback path — risk_mode, risk_per_trade_usd,
    risk_per_trade_pct
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.utils.logger import get_logger

logger = get_logger(__name__)


# ── Sub-models ────────────────────────────────────────────────────────


class BatchConfig(BaseModel):
  """Per-batch risk override stored under
  ``source_strategies.risk_profile.batch_configs[<batch_id>]``.

  Values arrive from the optimizer's saved run and from the portfolio
  UI. Field names use the camelCase form written by the frontend; they
  are kept as-is so this schema round-trips through the DB blob without
  a rename layer.
  """
  model_config = ConfigDict(extra="allow")

  mode: Literal[
    "fixed", "pct", "kelly",
    "martingale", "anti_martingale",
    "inv_martingale", "inv_anti_martingale",
  ] = "fixed"
  value: float = 0.0
  initialCapital: float = 10000.0
  multiplier: float = 2.0
  maxStreak: int = 3
  martingaleCompound: Literal["off", "pct", "dollar"] = "off"
  kellyN: float = 2.0
  kellyWarmup: int = 20
  kellyScope: Literal["portfolio", "per_strategy"] = "portfolio"
  kellyLookback: int = 0
  kellyCap: float = 0.0
  # Per-group margin cap (USD). Optional — falls back to global max_position_pct.
  maxMargin: float | None = None


class RiskProfile(BaseModel):
  """``source_strategies.risk_profile`` block. When
  ``override_enabled`` is True, these values override the global
  ``.env`` settings at live trade time.
  """
  model_config = ConfigDict(extra="allow")

  override_enabled: bool = True
  mode: Literal[
    "fixed", "pct", "kelly",
    "martingale", "anti_martingale",
    "inv_martingale", "inv_anti_martingale",
  ] = "fixed"

  initial_risk_usd: float = 0.0
  risk_pct: float | None = None
  initial_capital: float = 10000.0

  # Martingale / anti-martingale knobs (ignored when mode is fixed/pct/kelly).
  multiplier: float = 2.0
  max_streak: int = 3
  martingale_compound: Literal["off", "pct", "dollar"] = "off"
  martingale_scope: Literal["asset", "portfolio", "asset_batch", "batch"] = "asset"
  # Legacy flag — older portfolios used portfolio_wide=True instead of
  # martingale_scope="portfolio". Kept for backwards compatibility.
  portfolio_wide: bool = False

  # Kelly (scheduled recompute — implemented, see _run_kelly_recompute).
  kelly_n: float = 2.0
  kelly_warmup: int = 20
  kelly_scope: Literal["portfolio", "per_strategy"] = "portfolio"
  kelly_lookback: int = 0
  kelly_cap: float = 0.0

  # Per-batch sizing master: when "per_batch", per-batch configs are
  # resolved from ``batch_configs`` instead of the flat fields above.
  sizing_master: Literal["portfolio", "per_batch"] = "portfolio"
  batch_configs: dict[str, BatchConfig] = Field(default_factory=dict)

  # Auto-scale risk by the batch's group pnl_multiplier (e.g. Group A 2x).
  apply_group_multiplier: bool = False


class SourceStrategies(BaseModel):
  """Top-level ``Portfolio.source_strategies`` JSON blob."""
  model_config = ConfigDict(extra="allow")

  # Live fallback path — used when ``risk_profile.override_enabled`` is False.
  risk_mode: Literal["fixed", "pct"] | None = None
  risk_per_trade_usd: float | None = None
  risk_per_trade_pct: float | None = None

  risk_profile: RiskProfile = Field(default_factory=RiskProfile)


# ── Safe parser ────────────────────────────────────────────────────────


def parse_source_strategies(raw: str | dict | None) -> SourceStrategies:
  """Parse a ``source_strategies`` JSON blob into a validated model.

  Accepts:
    - ``None`` or empty string → empty ``SourceStrategies`` with defaults
    - ``str`` → ``json.loads`` then validate
    - ``dict`` → validate directly

  On parse or validation failure, logs a warning and returns an empty
  ``SourceStrategies``. This mirrors the behaviour of the existing
  ``json.loads(... or "{}")`` sites so adopters keep their fallback
  semantics.
  """
  if raw is None or raw == "":
    return SourceStrategies()

  if isinstance(raw, str):
    try:
      data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
      logger.warning("parse_source_strategies: JSON decode failed (%s) — returning defaults", e)
      return SourceStrategies()
  else:
    data = raw

  if not isinstance(data, dict):
    logger.warning(
      "parse_source_strategies: expected object, got %s — returning defaults",
      type(data).__name__,
    )
    return SourceStrategies()

  try:
    return SourceStrategies.model_validate(data)
  except Exception as e:
    # Pydantic's ValidationError chain — log the type+message but never
    # let bad stored data crash the live engine. Every site using this
    # helper has .get()-style defensive fallbacks today; preserve that.
    logger.warning(
      "parse_source_strategies: validation failed (%s: %s) — returning defaults",
      type(e).__name__, e,
    )
    return SourceStrategies()
