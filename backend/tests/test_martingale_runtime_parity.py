"""Runtime parity test for live martingale sizing.

Complements test_martingale_parity.py (static/structural) by exercising
the real code path end-to-end:

    seed Portfolio + Trade rows → call _get_portfolio_risk() → assert
    returned dollar amount matches base_risk * expected_factor.

This catches bugs UPSTREAM of the factor formula that the static test
cannot see:
  - Wrong streak counting (off-by-one, scope mismatch, breakeven handling)
  - Wrong mode dispatch (e.g. "martingale" accidentally reads wins)
  - Wrong profile field lookup (e.g. reading "mult" instead of "multiplier")
  - is_paper filter regression that silently changes the streak

Run before enabling martingale on real money. If any test here fires,
the live dollar output has diverged from the formula the backtester used.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import Base, Portfolio, Trade


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_factory():
  eng = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
  )
  Base.metadata.create_all(eng)
  return sessionmaker(bind=eng)


def _make_coordinator(db_factory, paper_mode: bool = False):
  """Minimal EngineCoordinator for _get_portfolio_risk — no WS, no exchange."""
  from backend.exchange.engine_coordinator import EngineCoordinator
  settings = SimpleNamespace(
    paper_trading=paper_mode,
    risk_per_trade_usd=100.0,
    risk_per_trade_pct=1.0,
    risk_mode="fixed",
  )
  coord = EngineCoordinator.__new__(EngineCoordinator)
  coord._db_factory = db_factory
  coord._settings = settings
  coord._position_manager = None
  coord._kelly_alloc_cache = {}
  return coord


def _make_portfolio(
  db, name: str, *, mode: str, base_risk: float, multiplier: float,
  max_streak: int, scope: str = "asset", portfolio_type: str = "real",
) -> int:
  profile = {
    "override_enabled": True,
    "mode": mode,
    "initial_risk_usd": base_risk,
    "multiplier": multiplier,
    "max_streak": max_streak,
    "martingale_compound": "off",
    "martingale_scope": scope,
  }
  p = Portfolio(
    name=name,
    portfolio_type=portfolio_type,
    source_strategies=json.dumps({"risk_profile": profile}),
  )
  db.add(p)
  db.commit()
  db.refresh(p)
  return int(p.id)


def _add_trade(
  db, portfolio_id: int, pnl: float, *, is_paper: bool = False,
  symbol: str = "BTCUSDT", hours_ago: int = 1,
):
  now = datetime.now(timezone.utc)
  db.add(Trade(
    portfolio_id=portfolio_id,
    symbol=symbol,
    side="Long",
    strategy_name="test",
    entry_price=100.0,
    exit_price=100.0 + (pnl / 0.1),
    quantity=0.1,
    realized_pnl=pnl,
    commission=0.0,
    opened_at=now - timedelta(hours=hours_ago, minutes=30),
    closed_at=now - timedelta(hours=hours_ago),
    close_reason="sl" if pnl < 0 else "tp",
    is_paper=is_paper,
  ))


# ---------------------------------------------------------------------------
# 1. Martingale (loss streak) — dollar amount round-trips the formula
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_losses,expected_factor", [
  (0, 1.0),
  (1, 2.0),
  (2, 4.0),
  (3, 8.0),   # at cap
  (5, 8.0),   # past cap — must stay at max_streak=3
  (10, 8.0),  # way past cap
])
def test_martingale_dollar_matches_formula(db_factory, n_losses, expected_factor):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "mg_test", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    for i in range(n_losses):
      _add_trade(db, pid, pnl=-5.0, hours_ago=i + 1)
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)
  risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")

  expected = 100.0 * expected_factor
  assert risk == pytest.approx(expected, rel=1e-9), (
    f"martingale diverged: {n_losses} losses → ${risk:.2f}, "
    f"expected ${expected:.2f} (= $100 × {expected_factor})"
  )


# ---------------------------------------------------------------------------
# 2. Anti-martingale (win streak)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_wins,expected_factor", [
  (0, 1.0),
  (1, 2.0),
  (2, 4.0),
  (3, 8.0),
  (7, 8.0),  # past cap
])
def test_anti_martingale_dollar_matches_formula(db_factory, n_wins, expected_factor):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "anti_test", mode="anti_martingale", base_risk=50.0,
      multiplier=2.0, max_streak=3,
    )
    for i in range(n_wins):
      _add_trade(db, pid, pnl=+5.0, hours_ago=i + 1)
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)
  risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")

  expected = 50.0 * expected_factor
  assert risk == pytest.approx(expected, rel=1e-9), (
    f"anti_martingale diverged: {n_wins} wins → ${risk:.2f}, "
    f"expected ${expected:.2f} (= $50 × {expected_factor})"
  )


# ---------------------------------------------------------------------------
# 3. Inverse martingale (halves after each loss)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n_losses,expected_factor", [
  (0, 1.0),
  (1, 0.5),
  (2, 0.25),
  (3, 0.125),  # at cap
  (5, 0.125),  # past cap
])
def test_inv_martingale_dollar_matches_formula(db_factory, n_losses, expected_factor):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "inv_mg", mode="inv_martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    for i in range(n_losses):
      _add_trade(db, pid, pnl=-5.0, hours_ago=i + 1)
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)
  risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")

  expected = 100.0 * expected_factor
  assert risk == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# 4. Breakeven trades do not affect streak — matches backtester (CS-048)
# ---------------------------------------------------------------------------

def test_breakeven_trades_dont_break_streak(db_factory):
  """A trade with realized_pnl≈0 must neither extend nor break the streak."""
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "be_test", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    # 2 old losses, then a breakeven (should be ignored), then another loss
    _add_trade(db, pid, pnl=-5.0, hours_ago=4)
    _add_trade(db, pid, pnl=-5.0, hours_ago=3)
    _add_trade(db, pid, pnl=0.0, hours_ago=2)  # breakeven — skipped
    _add_trade(db, pid, pnl=-5.0, hours_ago=1)
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)
  risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")

  # Streak should be 3 losses (breakeven in the middle was skipped, not a break)
  assert risk == pytest.approx(800.0, rel=1e-9)


# ---------------------------------------------------------------------------
# 5. A win in the middle DOES break the loss streak
# ---------------------------------------------------------------------------

def test_win_breaks_loss_streak(db_factory):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "wb_test", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    # Order (oldest→newest): loss, loss, WIN, loss, loss
    _add_trade(db, pid, pnl=-5.0, hours_ago=5)
    _add_trade(db, pid, pnl=-5.0, hours_ago=4)
    _add_trade(db, pid, pnl=+10.0, hours_ago=3)  # breaks streak
    _add_trade(db, pid, pnl=-5.0, hours_ago=2)
    _add_trade(db, pid, pnl=-5.0, hours_ago=1)
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)
  risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")

  # Most recent 2 are losses, then a win → streak = 2
  assert risk == pytest.approx(400.0, rel=1e-9)


# ---------------------------------------------------------------------------
# 6. Paper/live isolation — dollar output respects the is_paper filter
# ---------------------------------------------------------------------------

def test_paper_losses_do_not_inflate_live_risk(db_factory):
  """Regression guard for today's is_paper fix — dollar output must agree.

  If the filter regresses, a 3-paper-loss streak would size live at 8x base."""
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "mixed", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    # 3 PAPER losses — must be ignored by the live coordinator
    for i in range(3):
      _add_trade(db, pid, pnl=-5.0, is_paper=True, hours_ago=i + 1)
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)  # LIVE
  risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")

  # Live sees zero live losses → base risk
  assert risk == pytest.approx(100.0, rel=1e-9), (
    f"Live risk saw paper losses! got ${risk:.2f}, expected $100. "
    f"paper/live isolation regressed."
  )


def test_live_losses_do_not_inflate_paper_risk(db_factory):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "mixed2", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
      portfolio_type="paper",
    )
    # 3 LIVE losses — must be ignored by the paper coordinator
    for i in range(3):
      _add_trade(db, pid, pnl=-5.0, is_paper=False, hours_ago=i + 1)
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=True)  # PAPER
  risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")

  assert risk == pytest.approx(100.0, rel=1e-9)


# ---------------------------------------------------------------------------
# 7. Symbol scope — asset-scope streak does not cross assets
# ---------------------------------------------------------------------------

def test_asset_scope_streak_isolated_per_symbol(db_factory):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "asset_scope", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3, scope="asset",
    )
    # 3 BTC losses
    for i in range(3):
      _add_trade(db, pid, pnl=-5.0, symbol="BTCUSDT", hours_ago=i + 1)
    # 0 ETH losses
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)

  btc_risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")
  eth_risk = coord._get_portfolio_risk(pid, symbol="ETHUSDT")

  assert btc_risk == pytest.approx(800.0, rel=1e-9)  # 3 losses → 8x
  assert eth_risk == pytest.approx(100.0, rel=1e-9)  # no losses → base


# ---------------------------------------------------------------------------
# 8. Portfolio scope — losses on ANY symbol count
# ---------------------------------------------------------------------------

def test_portfolio_scope_streak_counts_all_symbols(db_factory):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "port_scope", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3, scope="portfolio",
    )
    # Mixed losses across symbols — under "portfolio" scope they all count
    _add_trade(db, pid, pnl=-5.0, symbol="BTCUSDT", hours_ago=3)
    _add_trade(db, pid, pnl=-5.0, symbol="ETHUSDT", hours_ago=2)
    _add_trade(db, pid, pnl=-5.0, symbol="SOLUSDT", hours_ago=1)
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)
  risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")

  # All 3 symbols' losses feed one streak → 3 losses → 8x
  assert risk == pytest.approx(800.0, rel=1e-9)


# ---------------------------------------------------------------------------
# 9. Cross-portfolio isolation — another portfolio's losses don't leak in
# ---------------------------------------------------------------------------

def test_other_portfolios_do_not_leak_in(db_factory):
  db = db_factory()
  try:
    target_id = _make_portfolio(
      db, "target", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    noisy_id = _make_portfolio(
      db, "noisy", mode="martingale", base_risk=100.0,
      multiplier=2.0, max_streak=3,
    )
    # 5 losses on the noisy portfolio
    for i in range(5):
      _add_trade(db, noisy_id, pnl=-5.0, hours_ago=i + 1)
    # Zero losses on target
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)
  risk = coord._get_portfolio_risk(target_id, symbol="BTCUSDT")

  assert risk == pytest.approx(100.0, rel=1e-9)


# ---------------------------------------------------------------------------
# 10. Multiplier other than 2 — verify the formula scales correctly
# ---------------------------------------------------------------------------

def test_multiplier_1_5_scales_correctly(db_factory):
  db = db_factory()
  try:
    pid = _make_portfolio(
      db, "mg15", mode="martingale", base_risk=100.0,
      multiplier=1.5, max_streak=5,
    )
    for i in range(3):
      _add_trade(db, pid, pnl=-5.0, hours_ago=i + 1)
    db.commit()
  finally:
    db.close()

  coord = _make_coordinator(db_factory, paper_mode=False)
  risk = coord._get_portfolio_risk(pid, symbol="BTCUSDT")

  # 1.5^3 = 3.375 → $337.50
  assert risk == pytest.approx(337.5, rel=1e-9)
