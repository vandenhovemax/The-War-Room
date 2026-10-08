"""Walk-Forward trade loader — reconstructs real per-window / per-rotation OOS.

When a WFO Track or hybrid Phase 2 portfolio is saved, only the final basket
is stored on PortfolioAsset. The default _load_portfolio_trades() then loads
every trade for that basket across the whole period — a fixed-basket
hypothetical, not what actually happened in walk-forward.

The new `_load_wfo_walkforward_trades()` stitches real OOS by using the
walk-forward metadata captured at save time:
  - time-based (rolling/static): `wfo_per_window` + `wfo_windows_meta`
  - event-based (hybrid Phase 2): `phase2_selection_changes` + cutoff

These tests focus on the small pure helper `_normalize_asset_tag` — its
unambiguous parsing is critical for correctness across tag formats. The
full loader is exercised by integration tests on a live DB.
"""
from __future__ import annotations

from backend.api.routes.portfolio_analysis import _normalize_asset_tag


def test_plain_tag():
  """Standard 'SYMBOL_Side' — no batch information encoded."""
  sym, side, bid = _normalize_asset_tag("BTCUSDT_Long")
  assert sym == "BTCUSDT"
  assert side == "Long"
  assert bid is None


def test_short_tag():
  sym, side, bid = _normalize_asset_tag("ETHUSDT_Short")
  assert sym == "ETHUSDT"
  assert side == "Short"
  assert bid is None


def test_canonical_tilde_batch_tag():
  """Canonical rolling/static WFO form: 'SYMBOL~B{id}_Side'."""
  sym, side, bid = _normalize_asset_tag("BTCUSDT~B42_Long")
  assert sym == "BTCUSDT"
  assert side == "Long"
  assert bid == 42


def test_canonical_tilde_batch_tag_short():
  sym, side, bid = _normalize_asset_tag("NEARUSDT~B34_Short")
  assert sym == "NEARUSDT"
  assert side == "Short"
  assert bid == 34


def test_canonical_tilde_batch_tag_negative():
  """Negative batch IDs (sentinel, e.g., -1) in canonical form."""
  sym, side, bid = _normalize_asset_tag("XRPUSDT~B-1_Short")
  assert sym == "XRPUSDT"
  assert side == "Short"
  assert bid == -1


def test_legacy_trailing_tilde_batch_tag():
  """Legacy trailing form: 'SYMBOL_Side~B{id}' — still parseable."""
  sym, side, bid = _normalize_asset_tag("BTCUSDT_Long~B42")
  assert sym == "BTCUSDT"
  assert side == "Long"
  assert bid == 42


def test_prefix_batch_tag():
  """Hybrid Phase 2 uses 'B{id}_SYMBOL_Side' (per-batch asset key)."""
  sym, side, bid = _normalize_asset_tag("B12_BTCUSDT_Long")
  assert sym == "BTCUSDT"
  assert side == "Long"
  assert bid == 12


def test_prefix_batch_short():
  sym, side, bid = _normalize_asset_tag("B99_SOLUSDT_Short")
  assert sym == "SOLUSDT"
  assert side == "Short"
  assert bid == 99


def test_tag_without_side():
  """Legacy 'both' side — no _Long / _Short suffix."""
  sym, side, bid = _normalize_asset_tag("BTCUSDT")
  assert sym == "BTCUSDT"
  assert side == "both"
  assert bid is None


def test_unfamiliar_tag_falls_through():
  """_normalize_asset_tag never raises — returns 'both' side on odd shapes."""
  sym, side, bid = _normalize_asset_tag("WEIRDTAG")
  assert sym == "WEIRDTAG"
  assert side == "both"
  assert bid is None
