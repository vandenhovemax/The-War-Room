"""
SQLAlchemy ORM models for The War Room.
Covers: market data, strategies, backtests, portfolios, live trades, journal.
"""
from datetime import date, datetime, timezone

from sqlalchemy import (
  Boolean,
  Column,
  Date,
  DateTime,
  Float,
  ForeignKey,
  Index,
  Integer,
  String,
  Text,
  UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship

from backend.constants import COMMISSION_PCT


class Base(DeclarativeBase):
  pass


# --- Phase 2: Data Collection ---

class OHLCVData(Base):
  __tablename__ = "ohlcv_data"

  id = Column(Integer, primary_key=True)
  symbol = Column(String, nullable=False)
  timeframe = Column(String, nullable=False)
  timestamp = Column(DateTime, nullable=False)
  open = Column(Float, nullable=False)
  high = Column(Float, nullable=False)
  low = Column(Float, nullable=False)
  close = Column(Float, nullable=False)
  volume = Column(Float, nullable=False)

  __table_args__ = (
    UniqueConstraint("symbol", "timeframe", "timestamp", name="uq_ohlcv"),
  )


class Watchlist(Base):
  __tablename__ = "watchlist"

  id = Column(Integer, primary_key=True, index=True)
  symbol = Column(String, nullable=False, unique=True)
  added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# --- Phase 3: Strategies ---

class Strategy(Base):
  __tablename__ = "strategies"

  id = Column(Integer, primary_key=True, index=True)
  name = Column(String, nullable=False, unique=True)
  description = Column(Text, default="")
  source_type = Column(String, nullable=False, default="manual")
  pine_source = Column(Text, default="")
  python_source = Column(Text, default="")
  parameters = Column(Text, default="{}")
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
  is_deleted = Column(Boolean, default=False, index=True)
  deleted_at = Column(DateTime, nullable=True)
  category = Column(String, nullable=True, default=None)

  backtest_runs = relationship("BacktestRun", back_populates="strategy")
  configs = relationship(
    "StrategyConfig", back_populates="strategy", cascade="all, delete-orphan"
  )


class StrategyConfig(Base):
  """Daughter config — named set of parameter values for a mother strategy."""
  __tablename__ = "strategy_configs"

  id = Column(Integer, primary_key=True, index=True)
  strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
  name = Column(String, nullable=False)
  description = Column(Text, default="")
  tag = Column(String, nullable=True, default=None)
  parameter_values = Column(Text, default="{}")
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

  strategy = relationship("Strategy", back_populates="configs")

  __table_args__ = (
    UniqueConstraint("strategy_id", "name", name="uq_strategy_config_name"),
  )


# --- Phase 4: Backtesting ---

class BacktestRun(Base):
  __tablename__ = "backtest_runs"

  id = Column(Integer, primary_key=True, index=True)
  strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
  symbol = Column(String, nullable=False)
  timeframe = Column(String, nullable=False)
  start_date = Column(DateTime, nullable=False)
  end_date = Column(DateTime, nullable=False)
  parameters = Column(Text, default="{}")
  metrics = Column(Text, default="{}")
  status = Column(String, default="pending")
  error_message = Column(Text, default="")
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  is_deleted = Column(Boolean, default=False, index=True)
  deleted_at = Column(DateTime, nullable=True)

  batch_id = Column(Integer, ForeignKey("batch_backtest_runs.id", ondelete="CASCADE"), nullable=True)
  strategy_state = Column(Text, nullable=True)  # JSON: captured strategy variables at end of run (for batch refresh)

  strategy = relationship("Strategy", back_populates="backtest_runs")
  trades = relationship(
    "BacktestTrade", back_populates="run", cascade="all, delete-orphan"
  )
  batch = relationship("BatchBacktestRun", back_populates="runs")

  # CS-033: mirror the raw-SQL composite index in database.py:329 so fresh
  # test DBs + Base.metadata.create_all() paths get the index too.
  __table_args__ = (
    Index("idx_bt_runs_batch", "batch_id", "status"),
  )


class BacktestTrade(Base):
  __tablename__ = "backtest_trades"

  id = Column(Integer, primary_key=True, index=True)
  run_id = Column(Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
  trade_num = Column(Integer, nullable=False)
  position_id = Column(String, nullable=True)  # groups partial closes into one trade
  symbol = Column(String, nullable=False)
  side = Column(String, nullable=False)
  entry_time = Column(DateTime, nullable=False)
  exit_time = Column(DateTime, nullable=False)
  entry_price = Column(Float, nullable=False)
  exit_price = Column(Float, nullable=False)
  quantity = Column(Float, nullable=False)
  net_profit = Column(Float, nullable=False)
  net_profit_pct = Column(Float, default=0.0)
  commission = Column(Float, default=0.0)
  mae = Column(Float, default=0.0)
  mfe = Column(Float, default=0.0)
  sl_price = Column(Float, nullable=True)
  tp_price = Column(Float, nullable=True)
  exit_reason = Column(String, default="signal")
  sl_past_be_time = Column(DateTime, nullable=True)  # when trailing stop first moved past breakeven

  run = relationship("BacktestRun", back_populates="trades")

  # CS-033: mirror raw-SQL index in database.py:328
  __table_args__ = (
    Index("idx_bt_trades_run", "run_id"),
  )


# --- Batch Backtesting ---

class BatchBacktestRun(Base):
  __tablename__ = "batch_backtest_runs"

  id = Column(Integer, primary_key=True, index=True)
  strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
  timeframe = Column(String, nullable=False)
  direction_filter = Column(String, default="both")
  parameters = Column(Text, default="{}")
  total_symbols = Column(Integer, default=0)
  completed_symbols = Column(Integer, default=0)
  status = Column(String, default="running")
  aggregate_metrics = Column(Text, default="{}")
  error_message = Column(Text, default="")
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  name = Column(String, nullable=True)    # auto-generated: "StrategyName — ConfigName"
  notes = Column(String, default="")      # user's personal note
  personal_grade = Column(String, nullable=True)  # user's personal letter grade (A+, B, C-, etc.)
  config_group = Column(String, nullable=True)  # persisted config group for tree view (survives rename)
  source_batch_ids = Column(Text, nullable=True)  # JSON list of batch IDs this was merged from
  refreshed_from_id = Column(Integer, nullable=True)  # FK to original batch this was refreshed from
  is_deleted = Column(Boolean, default=False, index=True)
  deleted_at = Column(DateTime, nullable=True)
  is_archived = Column(Boolean, default=False, index=True)
  is_compacted = Column(Boolean, default=False)  # True = child runs/trades deleted, only KPIs remain
  extra_timeframes = Column(Text, default="[]")  # JSON list of extra TFs: ["D", "240"]
  source_name = Column(String, nullable=True)     # Original auto-generated name (immutable on rename)
  progress_detail = Column(Text, default="{}")    # JSON snapshot of per-symbol progress (survives restart)
  min_adv = Column(Float, nullable=True)  # Minimum Average Daily Volume filter used for this batch
  bybit_limit = Column(Float, nullable=True)  # Bybit position size limit used in backtest (e.g. 250, 100)
  max_margin_per_trade = Column(Float, nullable=True)  # Max margin per trade in USD
  sweep_id = Column(Integer, ForeignKey("sweep_runs.id", ondelete="SET NULL"), nullable=True)
  group_id = Column(Integer, ForeignKey("batch_groups.id", ondelete="SET NULL"), nullable=True)

  strategy = relationship("Strategy")
  runs = relationship("BacktestRun", back_populates="batch")
  sweep = relationship("SweepRun", back_populates="batches")


# --- Parameter Sweep ---

class SweepRun(Base):
  __tablename__ = "sweep_runs"

  id = Column(Integer, primary_key=True, index=True)
  strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
  name = Column(String, nullable=True)               # auto: "StrategyName — Sweep (Param1, Param2)"
  sweep_config = Column(Text, default="{}")           # JSON: {params: [{name, min, max, step}], mode, base_values}
  batch_config = Column(Text, default="{}")           # JSON: shared batch settings (symbols, TF, direction, etc.)
  total_combos = Column(Integer, default=0)
  completed_combos = Column(Integer, default=0)
  status = Column(String, default="running")          # running | completed | cancelled | error
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  is_deleted = Column(Boolean, default=False, index=True)
  deleted_at = Column(DateTime, nullable=True)

  strategy = relationship("Strategy")
  batches = relationship("BatchBacktestRun", back_populates="sweep")


# --- Session Time Masks ---

class SessionTimeMask(Base):
  """Named hour×day time mask for pre-filtering optimizer trade pools."""
  __tablename__ = "session_time_masks"

  id = Column(Integer, primary_key=True, index=True)
  name = Column(String, nullable=False, unique=True)
  description = Column(Text, default="")
  mask_data = Column(Text, nullable=False)  # JSON: {mode, cells: [{hour, days: [0..6]}]}
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class BatchGroup(Base):
  """Master group for organizing batch backtests with shared trading parameters.

  Groups define the production sizing, fees, and slippage for all batches
  they contain. Co-Work sessions reference groups by letter/name instead
  of individual batch IDs.

  Example groups:
    A — M30 batches, bybit_limit=250, no fees/slippage
    B — M15 batches, bybit_limit=100, no fees/slippage
    C — future batches with different parameters
  """
  __tablename__ = "batch_groups"

  id = Column(Integer, primary_key=True, index=True)
  name = Column(String, nullable=False, unique=True)       # Short label: "A", "B", "C" or descriptive
  description = Column(Text, default="")
  sort_order = Column(Integer, default=0)
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  # ── Trading parameters (shared by all batches in this group) ──
  timeframe = Column(String, nullable=True)                 # "15", "30", etc.
  bybit_limit = Column(Float, nullable=True)                # Position size limit ($)
  max_margin_per_trade = Column(Float, nullable=True)       # Max margin per trade ($)
  min_adv = Column(Float, nullable=True)                    # Min Average Daily Volume filter
  slippage_pct = Column(Float, default=0.0)                 # Slippage as % of entry price (0 = none)
  fee_pct = Column(Float, default=0.0)                      # Round-trip fee as % (0 = none)
  pnl_multiplier = Column(Float, default=1.0)               # PnL scaling ratio for Co-Work (e.g. 2.5 = production is 2.5x backtest)


# --- Phase 6: Portfolios ---

class Portfolio(Base):
  __tablename__ = "portfolios"

  id = Column(Integer, primary_key=True, index=True)
  name = Column(String, nullable=False)
  source_file = Column(String, nullable=True)
  is_active = Column(Boolean, default=False)
  is_cross_strat = Column(Boolean, default=False)
  source_strategies = Column(Text, default="{}")
  metrics_snapshot = Column(Text, default="{}")
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  # Portfolio Hub fields
  portfolio_type = Column(String, default="saved")      # saved | paper | real
  source_portfolio_id = Column(Integer, nullable=True)   # FK to parent saved portfolio
  activated_at = Column(DateTime, nullable=True)
  deactivated_at = Column(DateTime, nullable=True)
  live_metrics_snapshot = Column(Text, default="{}")     # refreshed live KPIs
  last_refreshed_at = Column(DateTime, nullable=True)
  is_hidden = Column(Boolean, default=False)
  is_deleted = Column(Boolean, default=False)
  deleted_at = Column(DateTime, nullable=True)
  optimization_end_date = Column(DateTime, nullable=True)
  is_start_date = Column(DateTime, nullable=True)
  portfolio_source = Column(String, default="optimizer")  # optimizer | cross_strategy | manual | wfo
  description = Column(Text, nullable=True)
  swap_history = Column(Text, default="[]")  # JSON array of swap events
  refresh_status = Column(Text, nullable=True)  # JSON progress of current/last refresh
  # Live-loop update semantics: non_hybrid = basket frozen until OOS boundary;
  # hybrid = basket + weights refreshed nightly by Step-2. See DECISIONS.md ADR-004.
  mode = Column(String, default="non_hybrid", nullable=False)  # non_hybrid | hybrid

  assets = relationship(
    "PortfolioAsset", back_populates="portfolio", cascade="all, delete-orphan"
  )


class PortfolioAsset(Base):
  __tablename__ = "portfolio_assets"

  id = Column(Integer, primary_key=True, index=True)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
  symbol = Column(String, nullable=False)
  trade_side = Column(String, nullable=False)
  strategy_name = Column(String, default="")
  is_active = Column(Boolean, default=True)
  raw_name = Column(String, default="")
  batch_id = Column(Integer, nullable=True)       # Source batch for parameter lookup
  config_name = Column(String, nullable=True)     # Strategy config name for this asset
  risk_mode = Column(String, nullable=True)       # Auto-risk mode applied: fixed/martingale/anti_martingale/etc.
  timeframe = Column(String, nullable=True)       # Canonical TF from batch group (e.g. "15", "30")
  group_name = Column(String, nullable=True)      # Batch group name (e.g. "A", "B")

  portfolio = relationship("Portfolio", back_populates="assets")

  # CS-033: mirror raw-SQL index in database.py:336
  __table_args__ = (
    Index("idx_portfolio_assets_portfolio", "portfolio_id"),
  )


# --- Walk Forward Optimization ---

class WFORun(Base):
  __tablename__ = "wfo_runs"

  id = Column(Integer, primary_key=True, index=True)
  batch_id = Column(Integer, nullable=False)  # legacy single FK (kept for DB compat)
  batch_ids = Column(Text, default="[]")  # JSON list of batch IDs (multi-batch support)
  is_months = Column(Integer, nullable=False)
  oos_months = Column(Integer, nullable=False)
  last_oos_end = Column(String, nullable=False)
  n_candidates = Column(Integer, default=3)
  optimizer_settings = Column(Text, default="{}")
  tracks = Column(Text, default="[]")          # compiled track results JSON
  cumulative_is = Column(Text, default="{}")   # aggregated IS metrics JSON
  cumulative_oos = Column(Text, default="{}")  # aggregated OOS metrics JSON
  status = Column(String, default="completed")
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  is_deleted = Column(Boolean, default=False, index=True)
  deleted_at = Column(DateTime, nullable=True)
  # WFAS overhaul: time mode + overlapping H1/H2 + re-run lineage
  time_mode = Column(String, default="rolling")    # "rolling" | "static" | "overlapping"
  event_mode = Column(Boolean, default=False)
  portfolio_ids = Column(Text, default="[]")       # JSON list of portfolio IDs
  cumulative_oos_h1 = Column(Text, nullable=True)  # overlapping: aggregated H1 metrics
  cumulative_oos_h2 = Column(Text, nullable=True)  # overlapping: aggregated H2 metrics
  avg_degradation = Column(Float, nullable=True)   # overlapping: avg H2/H1 ratio
  parent_run_id = Column(Integer, nullable=True)   # re-run lineage

  windows = relationship(
    "WFOWindow", back_populates="wfo_run", cascade="all, delete-orphan",
    order_by="WFOWindow.window_num",
  )

  # CS-033: mirror raw-SQL index in database.py:337
  __table_args__ = (
    Index("idx_wfo_runs_batch", "batch_id"),
  )


class WFOWindow(Base):
  __tablename__ = "wfo_windows"

  id = Column(Integer, primary_key=True, index=True)
  wfo_run_id = Column(Integer, ForeignKey("wfo_runs.id", ondelete="CASCADE"), nullable=False)
  window_num = Column(Integer, nullable=False)
  is_start = Column(String, nullable=False)
  is_end = Column(String, nullable=False)
  oos_start = Column(String, nullable=True)
  oos_end = Column(String, nullable=True)
  candidates = Column(Text, default="[]")     # IS optimization results JSON
  oos_results = Column(Text, default="[]")    # OOS evaluation results JSON
  is_trade_count = Column(Integer, default=0)
  is_symbol_count = Column(Integer, default=0)
  is_pool_size = Column(Integer, default=0)
  # WFAS overhaul: overlapping H1/H2 dates + results
  oos_h1_start = Column(String, nullable=True)
  oos_h1_end = Column(String, nullable=True)
  oos_h2_start = Column(String, nullable=True)
  oos_h2_end = Column(String, nullable=True)
  oos_h1_results = Column(Text, nullable=True)   # JSON: H1 evaluation per candidate
  oos_h2_results = Column(Text, nullable=True)   # JSON: H2 evaluation per candidate
  degradation_ratio = Column(Float, nullable=True)

  wfo_run = relationship("WFORun", back_populates="windows")


# --- Phase 8: Live Execution ---

class Signal(Base):
  __tablename__ = "signals"

  id = Column(Integer, primary_key=True, index=True)
  received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  source = Column(String, nullable=False, default="tradingview")
  raw_payload = Column(Text, default="{}")
  symbol = Column(String, nullable=False)
  side = Column(String, nullable=False)
  action = Column(String, nullable=False)
  strategy = Column(String, default="")
  price = Column(Float, nullable=True)
  sl_price = Column(Float, nullable=True)
  tp_price = Column(Float, nullable=True)
  status = Column(String, default="pending")
  rejection_reason = Column(String, default="")

  # CS-033: mirror raw-SQL index in database.py:339
  __table_args__ = (
    Index("idx_signals_symbol_status", "symbol", "status"),
  )


class Order(Base):
  __tablename__ = "orders"

  id = Column(Integer, primary_key=True, index=True)
  signal_id = Column(Integer, ForeignKey("signals.id", ondelete="SET NULL"), nullable=True)
  exchange_order_id = Column(String, default="")
  symbol = Column(String, nullable=False)
  side = Column(String, nullable=False)
  order_type = Column(String, nullable=False, default="Market")
  quantity = Column(Float, nullable=False)
  price = Column(Float, nullable=True)
  sl_price = Column(Float, nullable=True)
  tp_price = Column(Float, nullable=True)
  status = Column(String, default="pending")
  fill_price = Column(Float, nullable=True)
  commission = Column(Float, default=0.0)
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
  # Phase 8: Live execution fields
  order_link_id = Column(String, default="", index=True)  # client UUID for tracking
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="SET NULL"), nullable=True)
  position_id_fk = Column(Integer, ForeignKey("positions.id", ondelete="SET NULL"), nullable=True)
  role = Column(String, default="entry")  # entry | sl | tp | tsl | close
  intended_qty = Column(Float, default=0.0)
  filled_qty = Column(Float, default=0.0)
  avg_fill_price = Column(Float, nullable=True)
  time_in_force = Column(String, default="GTC")
  reduce_only = Column(Boolean, default=False)
  ttl_seconds = Column(Integer, default=0)
  expired_at = Column(DateTime, nullable=True)
  cancel_reason = Column(String, default="")  # timeout | manual | ghost_cleanup | shutdown
  funding_fee = Column(Float, default=0.0)

  # CS-033: mirror raw-SQL index in database.py:332
  __table_args__ = (
    Index("idx_orders_portfolio_status", "portfolio_id", "status"),
  )


class Position(Base):
  __tablename__ = "positions"

  id = Column(Integer, primary_key=True, index=True)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True)
  symbol = Column(String, nullable=False)
  side = Column(String, nullable=False)
  strategy_name = Column(String, default="")
  entry_price = Column(Float, nullable=False)
  quantity = Column(Float, nullable=False)
  sl_price = Column(Float, nullable=True)
  tp_price = Column(Float, nullable=True)
  unrealized_pnl = Column(Float, default=0.0)
  leverage = Column(Integer, default=1)
  is_open = Column(Boolean, default=True)
  opened_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  closed_at = Column(DateTime, nullable=True)
  # Phase 8: Live execution fields
  exchange_symbol = Column(String, default="")  # Bybit symbol (e.g. BTCUSDT)
  margin_mode = Column(String, default="isolated")
  trailing_stop_distance = Column(Float, nullable=True)
  trailing_extreme = Column(Float, nullable=True)  # best price for TSL tracking
  entry_order_link_id = Column(String, default="")
  sl_order_link_id = Column(String, default="")
  tp_order_link_id = Column(String, default="")
  funding_accumulated = Column(Float, default=0.0)
  entry_commission = Column(Float, default=0.0)
  exit_commission = Column(Float, default=0.0)
  risk_removed = Column(Boolean, default=False)  # True when SL moved to BE or better (TP1 hit)
  batch_id = Column(Integer, nullable=True)  # Source batch for per-batch martingale scope

  # CS-033: mirror raw-SQL index in database.py:331
  __table_args__ = (
    Index("idx_positions_portfolio_open", "portfolio_id", "is_open"),
  )


class Trade(Base):
  __tablename__ = "trades"

  id = Column(Integer, primary_key=True, index=True)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="SET NULL"), nullable=True)
  symbol = Column(String, nullable=False)
  side = Column(String, nullable=False)
  strategy_name = Column(String, default="")
  entry_price = Column(Float, nullable=False)
  exit_price = Column(Float, nullable=False)
  quantity = Column(Float, nullable=False)
  realized_pnl = Column(Float, nullable=False)
  commission = Column(Float, default=0.0)
  opened_at = Column(DateTime, nullable=False)
  closed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  close_reason = Column(String, default="signal")
  # Phase 8: Live execution fields
  entry_commission = Column(Float, default=0.0)
  exit_commission = Column(Float, default=0.0)
  funding_fee = Column(Float, default=0.0)
  # Test trade flag — soft-hide from stats without deleting
  is_test = Column(Boolean, default=False, index=True)
  # Source batch — for per-batch martingale scope in live trading
  batch_id = Column(Integer, nullable=True)
  # Paper vs real flag — prevents paper trades from polluting live martingale streak
  is_paper = Column(Boolean, nullable=False, default=False)

  # CS-033: mirror raw-SQL index in database.py:333
  __table_args__ = (
    Index("idx_trades_portfolio", "portfolio_id"),
    Index("idx_trades_portfolio_paper_closed", "portfolio_id", "is_paper", "closed_at"),
  )


class DailyPnL(Base):
  __tablename__ = "daily_pnl"

  id = Column(Integer, primary_key=True, index=True)
  date = Column(Date, nullable=False)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True)
  realized_pnl = Column(Float, default=0.0)
  unrealized_pnl = Column(Float, default=0.0)
  funding_fees = Column(Float, default=0.0)
  total_commission = Column(Float, default=0.0)
  trade_count = Column(Integer, default=0)
  win_count = Column(Integer, default=0)
  loss_count = Column(Integer, default=0)

  __table_args__ = (
    UniqueConstraint("date", "portfolio_id", name="uq_daily_pnl_date_portfolio"),
    # CS-033: mirror raw-SQL index in database.py:334
    Index("idx_daily_pnl_portfolio_date", "portfolio_id", "date"),
  )


# --- Phase 7: Trade Journal ---

class JournalTrade(Base):
  __tablename__ = "journal_trades"

  id = Column(Integer, primary_key=True, index=True)
  # Trade identity
  symbol = Column(String, nullable=False)
  side = Column(String, nullable=False)          # "Long" or "Short"
  status = Column(String, default="open")         # "open" or "closed"
  source = Column(String, default="manual")       # "manual", "backtest", or "live"

  # Strategy link (optional — manual trades may not have one)
  strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True)
  config_id = Column(Integer, nullable=True)
  backtest_trade_id = Column(Integer, nullable=True)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="SET NULL"), nullable=True)

  # Live trade snapshot fields (value copies, not FKs — survive source deletion)
  batch_id = Column(Integer, nullable=True)        # source batch (value copy)
  config_name = Column(String, nullable=True)      # config name snapshot
  live_trade_id = Column(Integer, nullable=True)   # Trade.id for dedup

  # Entry
  entry_price = Column(Float, nullable=False)
  quantity = Column(Float, nullable=False)
  risk_amount = Column(Float, nullable=True)        # $ risk for this trade
  sl_price = Column(Float, nullable=True)
  tp_price = Column(Float, nullable=True)
  tp2_price = Column(Float, nullable=True)           # second take profit
  entry_time = Column(DateTime, nullable=True)

  # Exit (filled when closed)
  exit_price = Column(Float, nullable=True)
  exit_time = Column(DateTime, nullable=True)
  exit_reason = Column(String, nullable=True)

  # Outcome — user-set expected vs actual
  outcome = Column(String, nullable=True)             # "Win" or "Loss" (actual)
  expected_outcome = Column(String, nullable=True)     # "Win" or "Loss" (predicted)

  # Expected values from strategy (for comparison)
  expected_entry = Column(Float, nullable=True)
  expected_sl = Column(Float, nullable=True)
  expected_tp1 = Column(Float, nullable=True)
  expected_tp2 = Column(Float, nullable=True)
  expected_exit = Column(Float, nullable=True)

  # P&L
  expected_pnl = Column(Float, nullable=True)    # auto-calculated from entry/exit (preserved)
  net_profit = Column(Float, nullable=True)       # actual P&L (user-entered or auto-calc)
  net_profit_pct = Column(Float, nullable=True)

  # Commissions — dual tracking (expected vs actual)
  commission_rate = Column(Float, default=COMMISSION_PCT)
  expected_commission = Column(Float, default=0)
  actual_commission = Column(Float, nullable=True)

  # Journal metadata
  notes = Column(Text, default="")
  tags = Column(Text, default="[]")
  rating = Column(Integer, default=0)
  # Test trade flag — soft-hide from stats without deleting
  is_test = Column(Boolean, default=False, index=True)

  # Timestamps
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# --- Phase 7b: Personal Journal ---

class JournalNote(Base):
  """Personal journal entries and recaps."""
  __tablename__ = "journal_notes"

  id = Column(Integer, primary_key=True, index=True)
  # "journal" | "recap"
  note_type = Column(String, nullable=False, default="journal")
  title = Column(String, nullable=False)
  subject = Column(String, default="")         # Short subtitle shown on calendar
  content = Column(Text, default="")           # Full note body
  note_date = Column(Date, nullable=False, index=True)  # Calendar date
  period_start = Column(Date, nullable=True)   # Recap period start
  period_end = Column(Date, nullable=True)     # Recap period end
  color = Column(String, default="indigo")     # Color marker for calendar visual
  images = Column(Text, default="[]")          # JSON array of file paths
  tags = Column(Text, default="[]")            # JSON array of strings
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class JournalTemplate(Base):
  """Editable templates for recap notes (one per recap type)."""
  __tablename__ = "journal_templates"

  id = Column(Integer, primary_key=True, index=True)
  # "w_recap" | "m_recap" | "q_recap" | "h_recap" | "y_recap" | "journal" | "todo"
  template_type = Column(String, nullable=False, unique=True)
  content = Column(Text, default="")
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class MonthlyObjective(Base):
  """Persistent objectives shown below the professional journal calendar."""
  __tablename__ = "monthly_objectives"

  id = Column(Integer, primary_key=True, index=True)
  title = Column(String, nullable=False, default="Objectives")
  content = Column(Text, default="")           # Free-form text/journal content
  checklist = Column(Text, default="[]")       # JSON: [{"text":"..","checked":false,"completed_at":null}]
  period = Column(String, default="monthly")   # "daily" | "weekly" | "monthly"
  obj_year = Column(Integer, nullable=True)    # Year this objective belongs to (e.g. 2026)
  obj_month = Column(Integer, nullable=True)   # Month this objective belongs to (1-12)
  is_active = Column(Boolean, default=True)    # Whether currently displayed
  sort_order = Column(Integer, default=0)      # Display ordering
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CustomTemplate(Base):
  """User-created reusable templates for journal notes."""
  __tablename__ = "custom_templates"

  id = Column(Integer, primary_key=True, index=True)
  name = Column(String, nullable=False)        # User-given name, e.g. "Trading Day Review"
  content = Column(Text, default="")           # Template body (markdown)
  color = Column(String, default="indigo")     # Color marker applied when using template
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


# --- Phase 8: Live Execution Audit Log ---

class LiveEvent(Base):
  """Audit log for all live trading events — orders, fills, errors, circuit breakers."""
  __tablename__ = "live_events"

  id = Column(Integer, primary_key=True, index=True)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=True)
  event_type = Column(String, nullable=False, index=True)  # order_placed, order_filled, order_cancelled, position_opened, position_closed, error, circuit_breaker, reconciliation
  symbol = Column(String, default="")
  details = Column(Text, default="{}")  # JSON blob with event-specific data
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# --- Kelly Portfolio Allocation ---

class KellyAllocation(Base):
  """Stores multivariate Kelly allocation weights for a portfolio.

  Each recompute creates a new row. Only the latest is marked is_current=True.
  The live engine reads the current row to scale risk per strategy/asset.
  """
  __tablename__ = "kelly_allocations"

  id = Column(Integer, primary_key=True, index=True)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
  weights_json = Column(Text, default="{}")       # {"BTCUSDT_Long": 0.15, ...}
  result_json = Column(Text, default="{}")        # Full KellyAllocResult as JSON
  config_json = Column(Text, default="{}")        # KellyAllocConfig as JSON
  computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  trigger = Column(String, default="manual")      # manual | scheduled | trade_count
  is_current = Column(Boolean, default=True, index=True)


# --- Live Loop: OOS-boundary Rebalance Proposals (Ship 2) ---

class RebalanceProposal(Base):
  """Ship 2 — Step-1 re-run result, awaiting human approval.

  Written when a portfolio crosses its OOS boundary (oos_boundary.py +
  step1_refresh_worker.py). Never auto-applied. The UI lets the user
  approve/reject; approval forks a new Portfolio row with the fresh
  basket (old portfolio archived, not deleted).

  This is the only legal write path for non_hybrid portfolios' baskets.
  """
  __tablename__ = "rebalance_proposals"

  id = Column(Integer, primary_key=True, index=True)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True)
  trigger_type = Column(String, nullable=False)             # 'oos_boundary' | 'manual'
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
  status = Column(String, nullable=False, default="pending", index=True)  # pending | approved | rejected | expired
  current_basket_json = Column(Text, nullable=False, default="[]")
  proposed_basket_json = Column(Text, nullable=False, default="[]")
  diff_json = Column(Text, nullable=False, default="{}")          # {added:[], removed:[], unchanged:[]}
  metrics_preview_json = Column(Text, nullable=True)              # optional KPI preview
  reviewed_at = Column(DateTime, nullable=True)
  reviewed_by = Column(String, nullable=True)
  resulting_portfolio_id = Column(Integer, nullable=True)         # new portfolio row written on approval


# --- Live Loop: Parity Canary Reports (Ship 3) ---

class ParityCanaryReport(Base):
  """Monthly audit — full from-scratch Step-1/Step-2 rebuild vs the
  incremental batch store, per portfolio. Any divergence is surfaced in
  the Activity feed for human investigation.

  Ship 3 scope: audit-only. Writes nothing else. Never mutates a portfolio.
  """
  __tablename__ = "parity_canary_reports"

  id = Column(Integer, primary_key=True, index=True)
  run_date = Column(String, nullable=False, index=True)           # YYYY-MM-DD of the canary run
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="SET NULL"), nullable=True)
  total_combos = Column(Integer, default=0)
  combos_with_diff = Column(Integer, default=0)
  report_json = Column(Text, nullable=False, default="{}")        # full per-combo diff payload
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# --- Asset Category Tags (CoinGecko) ---

class SymbolCategory(Base):
  """Cached sector categories for crypto symbols (from CoinGecko)."""
  __tablename__ = "symbol_categories"

  id = Column(Integer, primary_key=True, index=True)
  symbol = Column(String, nullable=False, unique=True, index=True)  # e.g. "BTCUSDT"
  categories = Column(Text, default="[]")  # JSON list of category strings
  manual_override = Column(Integer, default=0)  # 1 = user-assigned, never auto-wiped
  last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# --- Signal Scanner ---

class LiveStrategyState(Base):
  """Persisted strategy state for the signal scanner.

  One row per portfolio+symbol. Updated after each scan cycle.
  Stores strategy internals (market structure, counters, positions)
  so the scanner can resume from where it left off without full warmup.
  """
  __tablename__ = "live_strategy_states"

  id = Column(Integer, primary_key=True, index=True)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
  symbol = Column(String, nullable=False)
  strategy_state = Column(Text, nullable=False, default="{}")
  ongoing_positions = Column(Text, default="[]")
  last_bar_timestamp = Column(DateTime, nullable=True)
  last_bar_index = Column(Integer, default=0)
  bars_processed = Column(Integer, default=0)
  strategy_id = Column(Integer, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
  batch_id = Column(Integer, nullable=True)
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

  __table_args__ = (
    UniqueConstraint("portfolio_id", "symbol", name="uq_live_state_portfolio_symbol"),
  )


# --- Tax Records ---

class TaxRecord(Base):
  """Comprehensive trade record for tax reporting.

  Stores every field needed for tax authorities: dates/times, prices,
  fees, commissions, funding, PnL, leverage, asset, etc.
  Can be auto-imported from live trades or added manually.
  """
  __tablename__ = "tax_records"

  id = Column(Integer, primary_key=True, index=True)
  # Source tracking
  source = Column(String, default="manual")        # "live" | "manual" | "imported"
  live_trade_id = Column(Integer, nullable=True, unique=True)  # FK to trades.id for dedup
  journal_trade_id = Column(Integer, nullable=True, unique=True)  # FK to journal_trades.id for dedup
  portfolio_id = Column(Integer, nullable=True)
  portfolio_name = Column(String, default="")       # snapshot — survives portfolio deletion

  # Trade identity
  symbol = Column(String, nullable=False)           # e.g. BTCUSDT
  side = Column(String, nullable=False)             # "Long" | "Short"
  strategy_name = Column(String, default="")

  # Sizing
  quantity = Column(Float, nullable=False)           # position size in contracts/coins
  leverage = Column(Integer, default=1)
  margin_used = Column(Float, default=0.0)           # actual margin allocated

  # Entry
  entry_price = Column(Float, nullable=False)
  entry_time = Column(DateTime, nullable=False)      # includes hours + minutes
  entry_order_type = Column(String, default="")      # "Market" | "Limit"

  # Exit
  exit_price = Column(Float, nullable=True)
  exit_time = Column(DateTime, nullable=True)        # includes hours + minutes
  exit_order_type = Column(String, default="")       # "Market" | "Limit"
  exit_reason = Column(String, default="")           # "signal" | "sl" | "tp" | "tsl" | "manual"

  # Commissions & Fees
  entry_commission = Column(Float, default=0.0)      # fee paid on entry fill
  exit_commission = Column(Float, default=0.0)       # fee paid on exit fill
  total_commission = Column(Float, default=0.0)      # entry + exit combined
  funding_fee = Column(Float, default=0.0)           # accumulated funding during hold

  # PnL
  gross_pnl = Column(Float, default=0.0)            # PnL before any fees
  net_pnl = Column(Float, default=0.0)              # PnL after commissions + funding
  net_pnl_pct = Column(Float, default=0.0)          # % return on margin
  outcome = Column(String, default="")               # "Win" | "Loss" | "Breakeven"

  # Metadata
  notes = Column(Text, default="")
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class TaxExpense(Base):
  """Deductible business expenses for tax reporting (T2125)."""
  __tablename__ = "tax_expenses"

  id = Column(Integer, primary_key=True, index=True)
  year = Column(Integer, nullable=False)
  category = Column(String, nullable=False)  # software, internet, home_office, equipment, education, professional, other
  description = Column(String, default="")
  amount_cad = Column(Float, default=0.0)
  receipt_note = Column(String, default="")  # receipt reference or note
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LiveSignal(Base):
  """Signals emitted by the signal scanner."""
  __tablename__ = "live_signals"

  id = Column(Integer, primary_key=True, index=True)
  portfolio_id = Column(Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False)
  symbol = Column(String, nullable=False)
  action = Column(String, nullable=False)       # "entry" or "exit"
  direction = Column(String, nullable=False)     # "long" or "short"
  entry_id = Column(String, default="")
  bar_timestamp = Column(DateTime, nullable=False)
  close_price = Column(Float, default=0.0)
  sl_price = Column(Float, default=0.0)
  tp_price = Column(Float, default=0.0)
  trail_points = Column(Float, default=0.0)
  comment = Column(String, default="")
  status = Column(String, default="new")         # "new" | "seen" | "acted"
  created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
