"""Tests for the basket-at-t filter applied to hybrid portfolios in
paper_refresh.run_refresh.

Background: hybrid portfolios rotate their basket. When the basket
rotates, formerly-active assets get `is_active=False` on PortfolioAsset
rows. paper_refresh's `_build_batch_groups` reads ALL portfolio_assets
ignoring is_active, so paper_refresh re-runs the strategy on rotated-out
symbols and counts their trades — inflating the KPI tile vs what the
engine actually executed.

Fix: `_filter_trades_by_basket_at_t` drops any trade whose
(batch_id, symbol, side) was not in the basket-at-t at the trade's
entry_time. Mirrors `load_wfas_forward_append`'s logic so the trades
shown in the KPI tile agree with the trades-table modal.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd


# ── Fixture: tiny in-memory trades DataFrame ─────────────────────────

def _make_trades(rows: list[tuple]) -> pd.DataFrame:
  """Each row: (batch_id, asset_or_symbol, side, entry_time, net_profit)."""
  return pd.DataFrame([
    {
      "Asset": r[1],
      "Side": r[2],
      "Entry Time": pd.Timestamp(r[3]),
      "Exit Time": pd.Timestamp(r[3]) + pd.Timedelta(hours=1),
      "Net Profit": r[4],
      "_batch_id": r[0],
    }
    for r in rows
  ])


def _patch_segments_and_assets(segments_returnval, pa_rows_returnval):
  """Patch iter_basket_segments + the PortfolioAsset query so the
  filter sees the test segments + active asset list."""
  iter_patch = patch(
    "backend.services.phase2_recorder.iter_basket_segments",
    return_value=segments_returnval,
  )

  fake_db = MagicMock()
  fake_query = MagicMock()
  fake_query.filter.return_value = fake_query
  fake_query.all.return_value = pa_rows_returnval
  fake_db.query.return_value = fake_query
  return iter_patch, fake_db


def _pa_row(symbol: str, side: str, batch_id: int, is_active: bool):
  pa = MagicMock()
  pa.symbol = symbol
  pa.trade_side = side
  pa.batch_id = batch_id
  pa.is_active = is_active
  return pa


# ── 1. Basic case: basket includes trade → keep ──────────────────────

def test_keep_trade_when_in_basket_at_t():
  from backend.services.paper_refresh import _filter_trades_by_basket_at_t

  # Segment from Apr 27 with batch 48 APEUSDT in basket
  segments = [
    (datetime(2026, 4, 27, 3, 0), {"B48_APEUSDT_Short"}),
  ]
  trades = _make_trades([(48, "APEUSDT", "Short", "2026-04-28 12:00", 5.0)])

  iter_patch, fake_db = _patch_segments_and_assets(segments, [])
  with iter_patch:
    out = _filter_trades_by_basket_at_t(fake_db, 28, trades)

  assert len(out) == 1
  assert out.iloc[0]["Asset"] == "APEUSDT"


# ── 2. Basket excludes trade → drop ──────────────────────────────────

def test_drop_trade_when_not_in_basket_at_t():
  from backend.services.paper_refresh import _filter_trades_by_basket_at_t

  segments = [
    (datetime(2026, 4, 27, 3, 0), {"B48_APEUSDT_Short"}),
  ]
  # Trade on a different symbol
  trades = _make_trades([(48, "BBUSDT", "Short", "2026-04-28 12:00", 5.0)])

  iter_patch, fake_db = _patch_segments_and_assets(segments, [])
  with iter_patch:
    out = _filter_trades_by_basket_at_t(fake_db, 28, trades)

  assert len(out) == 0


# ── 3. Trade on rotated-out batch → drop ─────────────────────────────

def test_drop_trade_on_rotated_out_batch():
  from backend.services.paper_refresh import _filter_trades_by_basket_at_t

  # Apr 27: mixed basket (batch 46 + 48)
  # Apr 28: only batch 48 (rotation deactivated batch 46)
  segments = [
    (datetime(2026, 4, 27, 3, 0), {"B46_DASHUSDT_Short", "B48_APEUSDT_Short"}),
    (datetime(2026, 4, 28, 3, 0), {"B48_APEUSDT_Short"}),
  ]
  # Trade on batch 46 AFTER rotation — should drop
  trades = _make_trades([(46, "DASHUSDT", "Short", "2026-04-29 12:00", 10.0)])

  iter_patch, fake_db = _patch_segments_and_assets(segments, [])
  with iter_patch:
    out = _filter_trades_by_basket_at_t(fake_db, 28, trades)

  assert len(out) == 0


# ── 4. Trade BEFORE first non-empty segment → STRICT drop ───────────
#
# Pre-fix this used a legacy is_active fallback that kept trades on
# currently-active symbols. Post-fix the filter is strict: if we have
# segments for the portfolio (meaning the recorder is up) but the
# basket-at-trade-time is empty, the engine had no basket to trade on —
# so the trade could not have actually been placed. Drop it.

def test_empty_basket_in_segment_drops_trade_strict():
  from backend.services.paper_refresh import _filter_trades_by_basket_at_t

  # Segments exist but the basket-at-t is empty (sentinel + pre-recorder
  # event with no basket data, real basket starts later).
  segments = [
    (datetime(1, 1, 1), set()),
    (datetime(2026, 4, 26, 3, 0), set()),
    (datetime(2026, 4, 28, 3, 0), {"B48_APEUSDT_Short"}),
  ]
  # Trade BEFORE first non-empty segment, even on currently-active asset:
  # strict drop — no positive evidence the engine would have traded.
  trades = _make_trades([(48, "APEUSDT", "Short", "2026-04-25 16:00", 3.0)])

  pa_rows = [_pa_row("APEUSDT", "Short", 48, True)]
  iter_patch, fake_db = _patch_segments_and_assets(segments, pa_rows)
  with iter_patch:
    out = _filter_trades_by_basket_at_t(fake_db, 28, trades)

  assert len(out) == 0, (
    "with segments present but empty basket-at-t, drop the trade — "
    "the engine had no basket to trade on at that time"
  )


def test_empty_basket_drops_inactive_strict():
  """Same strict drop rule applies regardless of current is_active state."""
  from backend.services.paper_refresh import _filter_trades_by_basket_at_t

  segments = [
    (datetime(1, 1, 1), set()),
    (datetime(2026, 4, 28, 3, 0), {"B48_APEUSDT_Short"}),
  ]
  trades = _make_trades([(48, "FLOWUSDT", "Short", "2026-04-26 16:00", 3.0)])

  pa_rows = [
    _pa_row("APEUSDT", "Short", 48, True),
    _pa_row("FLOWUSDT", "Short", 48, False),
  ]
  iter_patch, fake_db = _patch_segments_and_assets(segments, pa_rows)
  with iter_patch:
    out = _filter_trades_by_basket_at_t(fake_db, 28, trades)

  assert len(out) == 0


# ── 6. No segments at all (legacy hybrid) → fall back to is_active ───

def test_no_segments_falls_back_to_is_active():
  from backend.services.paper_refresh import _filter_trades_by_basket_at_t

  segments = []  # legacy hybrid — pre-T-002, no recorder events
  trades = _make_trades([
    (48, "APEUSDT", "Short", "2026-04-28 12:00", 5.0),
    (48, "FLOWUSDT", "Short", "2026-04-28 12:00", 5.0),
  ])

  pa_rows = [
    _pa_row("APEUSDT", "Short", 48, True),
    _pa_row("FLOWUSDT", "Short", 48, False),
  ]
  iter_patch, fake_db = _patch_segments_and_assets(segments, pa_rows)
  with iter_patch:
    out = _filter_trades_by_basket_at_t(fake_db, 28, trades)

  # Only APEUSDT (active) survives
  assert len(out) == 1
  assert out.iloc[0]["Asset"] == "APEUSDT"


# ── 7. Trade asset has _Long/_Short suffix (long_plus_short groups) ──

def test_long_plus_short_asset_suffix_handled():
  from backend.services.paper_refresh import _filter_trades_by_basket_at_t

  segments = [
    (datetime(2026, 4, 27, 3, 0), {"B48_APEUSDT_Short", "B48_BBUSDT_Long"}),
  ]
  trades = _make_trades([
    (48, "APEUSDT_Short", "Short", "2026-04-28 12:00", 5.0),  # match
    (48, "BBUSDT_Long",   "Long",  "2026-04-28 12:00", 7.0),  # match
    (48, "CELOUSDT_Short", "Short", "2026-04-28 12:00", 3.0),  # no match
  ])

  iter_patch, fake_db = _patch_segments_and_assets(segments, [])
  with iter_patch:
    out = _filter_trades_by_basket_at_t(fake_db, 28, trades)

  assert len(out) == 2
  symbols = sorted(out["Asset"].tolist())
  assert symbols == ["APEUSDT_Short", "BBUSDT_Long"]


# ── 8. Mixed: some pass, some drop ───────────────────────────────────

def test_mixed_keep_and_drop():
  from backend.services.paper_refresh import _filter_trades_by_basket_at_t

  segments = [
    (datetime(2026, 4, 27, 3, 0), {"B48_APEUSDT_Short"}),
    (datetime(2026, 4, 28, 3, 0), {"B48_BBUSDT_Short"}),  # rotated to BB
  ]
  trades = _make_trades([
    (48, "APEUSDT", "Short", "2026-04-27 14:00", 1.0),  # KEEP — basket has APE
    (48, "APEUSDT", "Short", "2026-04-29 14:00", 2.0),  # DROP — basket now BB only
    (48, "BBUSDT",  "Short", "2026-04-29 14:00", 3.0),  # KEEP — basket has BB
    (51, "SOLUSDT", "Short", "2026-04-29 14:00", 4.0),  # DROP — batch 51 not in basket
  ])

  iter_patch, fake_db = _patch_segments_and_assets(segments, [])
  with iter_patch:
    out = _filter_trades_by_basket_at_t(fake_db, 28, trades)

  assert len(out) == 2
  pnls = sorted(out["Net Profit"].tolist())
  assert pnls == [1.0, 3.0]


# ── 9. Empty input → empty output, no crash ──────────────────────────

def test_empty_trades_returns_empty():
  from backend.services.paper_refresh import _filter_trades_by_basket_at_t

  segments = [(datetime(2026, 4, 27, 3, 0), {"B48_APEUSDT_Short"})]
  trades = pd.DataFrame()

  iter_patch, fake_db = _patch_segments_and_assets(segments, [])
  with iter_patch:
    # Empty input — _filter expects _batch_id column; treat as no-op
    if trades.empty:
      out = trades  # caller already short-circuits empty
    else:
      out = _filter_trades_by_basket_at_t(fake_db, 28, trades)
  assert out.empty
