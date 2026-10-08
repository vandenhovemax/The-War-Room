/**
 * Typed API client for The War Room backend.
 * All fetches go through Next.js rewrites -> localhost:8055.
 */

export interface DashboardStats {
  strategies: number;
  portfolios: number;
  open_positions: number;
  total_trades: number;
}

export interface TradeStats {
  total_trades: number;
  total_pnl: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
}

export interface SymbolsResponse {
  count: number;
  symbols: string[];
}

export interface DataStatusItem {
  symbol: string;
  timeframe: string;
  candle_count: number;
  earliest: string | null;
  latest: string | null;
  avg_daily_volume: number;
}

export interface DownloadProgress {
  status: "queued" | "downloading" | "done" | "error" | "cancelled";
  pages_done: number;
  candles_so_far: number;
  error: string | null;
}

export interface RebuildProgress {
  status: "idle" | "running" | "completed" | "error";
  phase: "" | "downloading" | "loading_candles" | "rebuilding";
  total_runs: number;
  completed_runs: number;
  failed_runs: number;
  current: string;
  started_at: string | null;
  ended_at: string | null;
  errors: { run_id: number; symbol: string; batch_id: number; reason: string }[];
  l0_summary: {
    pairs_total?: number;
    pairs_updated?: number;
    pairs_up_to_date?: number;
    pairs_failed?: number;
    new_candles?: number;
    error?: string;
  } | null;
  candles_cached: number;
}

export interface WatchlistItem {
  symbol: string;
  added_at: string;
}

export interface AppSettings {
  // Bybit API
  bybit_api_key: string;
  bybit_api_secret: string;
  bybit_testnet: boolean;
  testnet_use_mainnet_prices: boolean;
  has_api_keys: boolean;
  // Risk Management
  risk_mode: string;
  risk_per_trade_pct: number;
  risk_per_trade_usd: number;
  max_risk_per_trade_usd: number;
  max_risk_per_trade_pct: number;
  skip_capped_entries: boolean;
  total_equity_override: number;
  reserve_account_usd: number;
  margin_breathing_room_pct: number;
  max_position_pct: number;
  max_positions_long: number;
  max_positions_short: number;
  max_positions_total: number;
  daily_loss_limit_pct: number;
  default_leverage: number;
  // Order Types
  entry_order_type: string;
  entry_limit_offset_pct: number;
  tp_order_type: string;
  // Trading Mode
  paper_trading: boolean;
  paper_initial_balance: number;
  paper_slippage_pct: number;
  // Live Engine
  live_engine_enabled: boolean;
  entry_order_ttl_seconds: number;
  tp_order_ttl_seconds: number;
  max_position_notional: number;
  default_margin_mode: string;
  max_daily_loss_usd: number;
  // Data
  default_timeframe: string;
  // Telegram Bot
  telegram_bot_token: string;
  telegram_chat_id: string;
  has_telegram: boolean;
  telegram_notifications_enabled: boolean;
  telegram_event_poll_seconds: number;
  telegram_health_check_seconds: number;
  telegram_confirm_timeout_seconds: number;
}

export interface TaxRecord {
  id: number;
  source: string;
  live_trade_id: number | null;
  portfolio_id: number | null;
  portfolio_name: string;
  symbol: string;
  side: string;
  strategy_name: string;
  quantity: number;
  leverage: number;
  margin_used: number;
  entry_price: number;
  entry_time: string | null;
  entry_order_type: string;
  exit_price: number | null;
  exit_time: string | null;
  exit_order_type: string;
  exit_reason: string;
  entry_commission: number;
  exit_commission: number;
  total_commission: number;
  funding_fee: number;
  gross_pnl: number;
  net_pnl: number;
  net_pnl_pct: number;
  outcome: string;
  notes: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface TaxSummary {
  total_records: number;
  total_net_pnl: number;
  total_fees: number;
  total_funding: number;
  wins: number;
  losses: number;
}

export interface TaxWithholdingMonth {
  month: number;
  month_name: string;
  pnl_usd: number;
  pnl_cad: number;
  cumulative_cad: number;
  marginal_rate: number;
  effective_rate: number;
  action_amount: number;
  action: "set_aside" | "move_back";
}

export interface TaxWithholdingQuarter {
  quarter: string;
  pnl_usd: number;
  pnl_cad: number;
  action_amount: number;
  action: "set_aside" | "move_back";
}

export interface TaxWithholdingResponse {
  year: number;
  usd_cad_rate: number;
  months: TaxWithholdingMonth[];
  quarters: TaxWithholdingQuarter[];
  annual: {
    total_pnl_usd: number;
    total_pnl_cad: number;
    total_tax: number;
    federal_tax: number;
    quebec_tax: number;
    effective_rate: number;
    marginal_rate: number;
    side_account_balance: number;
  };
  after_expenses: {
    business_expenses: number;
    taxable_income: number;
    total_tax: number;
    federal_tax: number;
    quebec_tax: number;
    effective_rate: number;
    tax_savings: number;
  };
}

export interface TaxExpense {
  id: number;
  year: number;
  category: string;
  description: string;
  amount_cad: number;
  receipt_note: string;
  created_at: string | null;
}

export interface TaxReportResponse {
  year: number;
  usd_cad_rate: number;
  rate_info: {
    mode: "daily" | "fixed";
    avg: number;
    min: number;
    max: number;
    source: string;
  };
  generated_at: string;
  income_summary: {
    total_trades: number;
    gross_pnl_usd: number;
    gross_pnl_cad: number;
    total_commissions_usd: number;
    total_commissions_cad: number;
    total_funding_fees_usd: number;
    total_funding_fees_cad: number;
    total_trading_expenses_usd: number;
    total_trading_expenses_cad: number;
    net_pnl_usd: number;
    net_pnl_cad: number;
    net_business_income_cad: number;
  };
  per_asset: {
    symbol: string;
    trade_count: number;
    gross_pnl_usd: number;
    gross_pnl_cad: number;
    commissions_usd: number;
    commissions_cad: number;
    funding_usd: number;
    funding_cad: number;
    net_pnl_usd: number;
    net_pnl_cad: number;
    wins: number;
    losses: number;
    win_rate: number;
    first_trade_date: string | null;
    last_trade_date: string | null;
  }[];
  monthly: {
    month: number;
    month_name: string;
    trade_count: number;
    gross_pnl_usd: number;
    gross_pnl_cad: number;
    commissions_usd: number;
    commissions_cad: number;
    funding_usd: number;
    funding_cad: number;
    net_pnl_usd: number;
    net_pnl_cad: number;
    wins: number;
    losses: number;
  }[];
  performance: {
    total_wins: number;
    total_losses: number;
    total_breakeven: number;
    win_rate: number;
    avg_win_usd: number;
    avg_loss_usd: number;
    largest_win_usd: number;
    largest_loss_usd: number;
    avg_hold_time_hours: number;
    long_trades: number;
    short_trades: number;
    long_pnl_usd: number;
    short_pnl_usd: number;
  };
  tax_estimate: {
    net_business_income_cad: number;
    federal_tax: number;
    quebec_tax: number;
    total_tax: number;
    effective_rate: number;
    marginal_rate: number;
  };
  warnings: string[];
}

export interface SettingsUpdateResponse {
  status: string;
  updated: string[];
  restart_required?: boolean;
}

// API key for authenticated requests (set via NEXT_PUBLIC_API_KEY env var).
// When empty, no auth header is sent (backend auth disabled by default).
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  if (API_KEY) {
    headers.set("X-API-Key", API_KEY);
  }
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    // Extract the backend's error detail message instead of generic status text
    let detail = "";
    try {
      const body = await res.json();
      const raw = body.detail || body.message || "";
      // FastAPI validation errors return detail as an array of objects
      detail = typeof raw === "string" ? raw : JSON.stringify(raw);
    } catch { /* response body isn't JSON */ }
    throw new Error(detail || `API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// Strategy types
export interface StrategyItem {
  id: number;
  name: string;
  description: string;
  source_type: string;
  has_pine: boolean;
  has_python: boolean;
  param_count: number;
  config_count: number;
  created_at: string | null;
  category: string | null;
}

export interface ParameterDefinition {
  name: string;
  type: "int" | "float" | "bool" | "string";
  default: number | boolean | string;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
}

export interface StrategyConfigItem {
  id: number;
  strategy_id: number;
  name: string;
  description: string;
  tag: string | null;
  parameter_values: Record<string, number | boolean | string>;
  created_at: string | null;
  updated_at: string | null;
}

export interface StrategyDetail extends StrategyItem {
  pine_source: string;
  python_source: string;
  parameters: ParameterDefinition[];
  updated_at: string | null;
}

// Backtest types
export interface BacktestRunItem {
  id: number;
  strategy_id: number;
  batch_id: number | null;
  symbol: string;
  timeframe: string;
  status: string;
  start_date: string | null;
  end_date: string | null;
  error_message: string;
  created_at: string | null;
  net_profit?: number | null;
  total_trades?: number | null;
  win_rate?: number | null;
  profit_factor?: number | null;
  config_name?: string | null;
}

export interface BacktestRunDetail extends BacktestRunItem {
  metrics: Record<string, number>;
  parameters: Record<string, unknown>;
}

export interface TradeExit {
  entry_time: string | null;
  exit_time: string | null;
  exit_price: number;
  quantity: number;
  net_profit: number;
  exit_reason: string;
}

export interface BacktestTradeItem {
  trade_num: number;
  position_id: string;
  symbol: string;
  side: string;
  entry_time: string | null;
  entry_price: number;
  sl_price: number | null;
  tp_price: number | null;
  total_qty: number;
  net_profit: number;
  net_profit_pct: number;
  exit_count: number;
  exits: TradeExit[];
  strategy_name?: string;
  config_name?: string;
  size_pct?: number;
}

export interface CandleData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface IndicatorDataPoint {
  time: number;
  value: number;
}

export interface IndicatorSeries {
  name: string;
  color: string;
  data: IndicatorDataPoint[];
  levels?: number[];
}

export interface ChartIndicators {
  overlays: IndicatorSeries[];
  oscillators: IndicatorSeries[];
}

export interface ChartTrigger {
  time: number;
  direction: "long" | "short";
  price: number;
  comment?: string;
}

export interface ChartReplayData {
  triggers: ChartTrigger[];
  structural_overlays: IndicatorSeries[];
  trigger_count: { long: number; short: number };
  error?: string;
}

export interface ChartCandlesResponse {
  candles: CandleData[];
  count: number;
  indicators: ChartIndicators;
  replay?: ChartReplayData;
}

export interface BacktestProgress {
  status: string;
  bars_done: number;
  total_bars: number;
  metrics?: Record<string, number>;
  error?: string;
}

// Batch Group types
export interface BatchGroupItem {
  id: number;
  name: string;
  description: string;
  sort_order: number;
  batch_count: number;
  created_at: string | null;
  timeframe: string | null;
  bybit_limit: number | null;
  max_margin_per_trade: number | null;
  min_adv: number | null;
  slippage_pct: number;
  fee_pct: number;
  pnl_multiplier: number;
}

// Batch backtest types
export interface BatchBacktestRunItem {
  id: number;
  strategy_id: number;
  timeframe: string;
  direction_filter: string;
  total_symbols: number;
  completed_symbols: number;
  status: string;
  created_at: string | null;
  name: string | null;
  notes: string;
  config_group: string | null;
  source_batch_ids: number[] | null;
  refreshed_from_id: number | null;
  group_id?: number | null;
  parameters?: Record<string, unknown>;
  data_end_date?: string | null;
  batch_grade?: string | null;
  batch_grade_score?: number | null;
  avg_profit_factor?: number | null;
  avg_win_rate?: number | null;
  symbols_with_trades?: number | null;
  total_net_profit?: number | null;
  is_archived?: boolean;
  is_compacted?: boolean;
  personal_grade?: string | null;
  total_trades_all?: number | null;
  avg_max_drawdown_pct?: number | null;
  profitable_symbols?: number | null;
  best_symbol?: string | null;
  worst_symbol?: string | null;
  total_symbols_tested?: number | null;
}

export interface BatchProgress {
  status: string;
  total_symbols: number;
  completed_symbols: number;
  current_symbol: string;
  symbol_statuses: Record<string, string>;
  started_at?: string;
  phase?: "loading" | "backtesting";
  queue_position?: number;
  size_filtered_count?: number;
  margin_filtered_count?: number;
}

export interface OptimizeTaskResponse {
  task_id: string;
  status: string;
}

export interface OptimizeProgressResponse {
  status: "running" | "completed" | "error" | "unknown" | "cancelled";
  result?: unknown;
  error?: string | null;
  step?: number;
  total_steps?: number;
  step_label?: string;
  timing?: Record<string, number>;
}

export interface BatchSymbolResult {
  symbol: string;
  run_id: number;
  status: string;
  metrics: Record<string, number>;
}

export interface BatchResultSummary {
  batch: BatchBacktestRunItem;
  aggregate_metrics: Record<string, number | string>;
  per_symbol: BatchSymbolResult[];
}

// Sweep types
export interface SweepParam {
  name: string;
  min: number;
  max: number;
  step: number;
}

export interface SweepRunItem {
  id: number;
  strategy_id: number;
  name: string;
  total_combos: number;
  completed_combos: number;
  status: string;
  created_at: string | null;
  sweep_config: {
    params: SweepParam[];
    mode: string;
    base_values: Record<string, unknown>;
  };
  batch_config: Record<string, unknown>;
}

export interface SweepComboResult {
  combo_index: number;
  combo_values: Record<string, number>;
  batches: Array<{
    batch_id: number;
    direction: string;
    status: string;
    aggregate_metrics: Record<string, number | string>;
    batch_grade: string | null;
    batch_grade_score: number | null;
    name: string | null;
    total_symbols: number;
  }>;
}

export interface SweepDetail {
  sweep: SweepRunItem;
  combos: SweepComboResult[];
}

export interface SweepProgress {
  sweep_id: number;
  total_combos: number;
  completed: number;
  total_batches: number;
  status: string;
  combos: Array<{
    batch_id: number;
    direction: string;
    status: string;
    combo_label: string;
    combo_index: number;
    completed_symbols: number;
    total_symbols: number;
    current_symbol: string;
  }>;
}

// Analysis types
export interface CorrelationResult {
  symbols: string[];
  matrix: number[][];
  stats: {
    mean: number;
    median: number;
    min?: number;
    max?: number;
    pct_25?: number;
    pct_75?: number;
    p95?: number;
    p80?: number;
    p25?: number;
    p5?: number;
  };
}

export interface CandidateMetrics {
  net_profit: number;
  profit_factor: number;
  max_drawdown: number;
  stability: number;
  n_assets: number;
  win_rate?: number;
  total_trades?: number;
  sortino?: number;
  avg_corr?: number;
  recovery_factor?: number;
  avg_trades_per_month?: number;
  calmar?: number;
}

export interface OptimizerCandidate {
  assets: string[];
  metrics: CandidateMetrics;
  equity_curve?: number[];
}

export interface PoolStats {
  total: number;
  median_pf: number;
  mean_pf: number;
  pf_above_1_5: number;
  pf_above_2_0: number;
  pf_values: number[];
}

export interface OptimizerResult {
  candidates: OptimizerCandidate[];
  total_symbols: number;
  pool_stats?: PoolStats;
}

export interface LSOptimizerCandidate {
  longs: string[];
  shorts: string[];
  assets: string[];
  metrics: CandidateMetrics;
  equity_curve?: number[];
  score: number;
}

export interface LSOptimizerResult {
  candidates: LSOptimizerCandidate[];
  total_symbols: number;
  n_longs_available: number;
  n_shorts_available: number;
  pool_stats?: PoolStats;
}

export interface MonteCarloStats {
  median_return: number;
  worst_case_return: number;
  best_case_return: number;
  prob_profit: number;
  prob_ruin: number;
  median_max_dd: number;
  worst_case_max_dd: number;
  best_case_max_dd: number;
  median_max_dd_pct: number;
  worst_case_max_dd_pct: number;
  median_sharpe: number;
  sharpe_5th: number;
  sharpe_95th: number;
  original_max_dd: number;
  original_max_dd_pct: number;
  original_return: number;
  n_trades: number;
  n_simulations: number;
  ruin_threshold_pct: number;
}

export interface MonteCarloResult {
  stats: MonteCarloStats;
  cone: Record<string, number[]>;
  original_equity: number[];
}

export interface FatTailBootstrapResult {
  stats: {
    survival_prob: number;
    fragility_ratio: number;
    median_return: number;
    worst_case_return: number;
    best_case_return: number;
    original_return: number;
    original_max_dd: number;
    median_max_dd: number;
    n_trades: number;
    n_simulations: number;
    outlier_pct: number;
  };
  cone: Record<string, number[]>;
  original_equity: number[];
  power_trades: {
    count: number;
    total_pnl: number;
    pct_of_profit: number;
    threshold: number;
  };
}

export interface RandomExitResult {
  actual_pf: number;
  mean_random_pf: number;
  std_random_pf: number;
  z_score: number;
  p_value: number;
  alpha_gap: number;
  is_significant: boolean;
  random_pfs: number[];
  n_trades_tested: number;
  n_iterations: number;
  exit_mode: string;
}

export interface SessionIntelCellMetrics {
  trade_count: number;
  total_pnl: number;
  avg_pnl: number;
  median_pnl: number;
  win_rate: number;
  profit_factor: number;
  expectancy: number;
  avg_win: number;
  avg_loss: number;
  payoff_ratio: number;
  best_trade: number;
  worst_trade: number;
  avg_mae: number;
  avg_mfe: number;
  mfe_mae_ratio: number;
  symbol_count: number;
  profitable_symbol_pct: number;
  is_confident: boolean;
}

export interface SessionIntelHourMetrics extends SessionIntelCellMetrics {
  pnl_contribution_pct: number;
  trade_share_pct: number;
  edge_density: number;
  label: string;
  session: string;
}

export interface SessionIntelDayMetrics extends SessionIntelCellMetrics {
  pnl_contribution_pct: number;
  trade_share_pct: number;
  avg_trades_per_session: number;
  consistency: number;
  best_day_pnl: number;
  worst_day_pnl: number;
  label: string;
  is_weekend: boolean;
}

export interface SessionIntelResult {
  heatmap: SessionIntelCellMetrics[][];
  hour_profile: SessionIntelHourMetrics[];
  day_profile: SessionIntelDayMetrics[];
  summary: {
    total_trades_in_range: number;
    total_pnl_in_range: number;
    date_range_days: number;
    symbols_in_batch: number;
  };
  time_labels: string[];
  day_labels: string[];
  session_labels: Record<string, string>;
  granularity: "hour" | "half_hour";
}

export interface SessionTimeMaskCell {
  hour: number;
  days: number[];
}

export interface SessionTimeMask {
  id: number;
  name: string;
  description: string;
  mode: "include" | "exclude";
  cells: SessionTimeMaskCell[];
  created_at: string | null;
  updated_at: string | null;
}

export interface MaskPreviewResult {
  mask_name: string;
  ok: boolean;
  positions_before: number;
  positions_after: number;
  pct_retained: number;
  n_min_computed: number;
  sigma_over_mu: number;
  mean_pnl: number;
  primary_gate_pass: boolean;
  secondary_gate_pass: boolean;
  secondary_gate_required: number;
  secondary_gate_actual: number;
  dropped_assets: string[];
  surviving_asset_count: number;
  symbol_dropout_pct: number;
  symbol_dropout_warning: boolean;
  gate_fail_reason: string;
  per_asset_position_counts: Record<string, number>;
}

export type RobustnessGrade = "A" | "B" | "C" | "F" | null;

export interface CompareStrategy {
  batch_id: number;
  strategy_name: string;
  timeframe: string;
  direction_filter: string;
  created_at: string | null;
  metrics: Record<string, number | string>;
}

export interface CategoryCmpStats {
  assets: number;
  net_profit: number;
  profit_factor: number | null;
  win_rate: number;
  trades: number;
}

export interface CategoryComparison {
  all_categories: string[];
  batches: {
    batch_id: number;
    categories: Record<string, CategoryCmpStats>;
  }[];
}

export interface SymbolComparisonStats {
  asset: string;
  trades: number;
  net_profit: number;
  profit_factor: number | null;
  win_rate: number;
}

export interface SymbolComparisonData {
  all_symbols: string[];
  batches: {
    batch_id: number;
    symbols: Record<string, SymbolComparisonStats>;
  }[];
}

export interface CompareResult {
  strategies: CompareStrategy[];
  category_comparison?: CategoryComparison;
  symbol_comparison?: SymbolComparisonData;
}

export interface StreakStudyResult {
  trade_count: number;
  total_trades: number;
  transition_matrix: {
    p_win_after_win: number | null;
    p_loss_after_win: number | null;
    p_win_after_loss: number | null;
    p_loss_after_loss: number | null;
    counts?: Record<string, number>;
    after_win_total?: number;
    after_loss_total?: number;
  };
  streak_distributions: {
    wins: Record<string, number>;
    losses: Record<string, number>;
    max_win_streak: number;
    max_loss_streak: number;
  };
  autocorrelation: number | null;
  chi_squared_p: number | null;
  recommendation: {
    best_method: string;
    confidence: string;
    reason: string;
  };
  comparison: Record<string, { net_profit: number; final_equity: number; max_drawdown_pct: number }>;
}

export interface PortfolioItem {
  id: number;
  name: string;
  is_active: boolean;
  is_cross_strat: boolean;
  metrics_snapshot: Record<string, number>;
  source_strategies: Record<string, unknown>;
  created_at: string | null;
  asset_count: number;
  portfolio_type: "saved" | "paper" | "real" | "monitoring";
  /** "hybrid" = basket rotates per Phase 2 grading. "non_hybrid" = frozen basket. */
  mode?: "hybrid" | "non_hybrid";
  source_portfolio_id: number | null;
  activated_at: string | null;
  deactivated_at: string | null;
  live_metrics_snapshot: Record<string, number>;
  last_refreshed_at: string | null;
  is_hidden: boolean;
  is_deleted: boolean;
  deleted_at: string | null;
  optimization_end_date: string | null;
  is_start_date: string | null;
  portfolio_source: string;
  description: string;
  swap_history: SwapHistoryEntry[];
}

export interface PortfolioDetail extends PortfolioItem {
  assets: {
    id: number;
    symbol: string;
    side: string;
    strategy_name: string;
    is_active: boolean;
    batch_id: number | null;
    config_name: string | null;
  }[];
  batch_names: string[];
  batch_id_map: Record<number, string>;
  batch_settings?: {
    initial_capital?: number;
    commission_pct?: number;
    leverage?: number;
    risk_mode?: string;
    risk_value?: number;
    slippage_value?: number;
  };
  batch_risk_values?: Record<string, { name: string; risk_value: number; risk_mode: string }>;
}

// TV Dashboard types
export interface TVStrategyListItem {
  filename: string;
  name: string;
}

export interface TVStrategyConfig {
  name: string;
  dir_mode: string;
  long_dir: string;
  short_dir: string;
  single_dir: string;
  group_multi_tp: boolean;
  min_trades: number;
  min_pf: number;
  [key: string]: unknown;
}

export interface DashboardAnalysisRequest {
  strategy_filename?: string;
  batch_id?: number;
  run_id?: number;
  portfolio_id?: number;
  side_filter?: string;
  date_from?: string;
  date_to?: string;
  min_trades?: number;
  exclude_top_n?: number;
  pf_min?: number;
  pf_max?: number;
  wr_min?: number;
}

export interface DashboardMetricsResult {
  metrics: Record<string, number | string | null>;
  trade_count: number;
}

export interface EquityCurvePoint {
  date: string;
  equity: number;
  drawdown: number;
  drawdown_pct: number;
  pnl: number;
  asset: string;
}

export interface TVAnalysisRow {
  label: string;
  all_val?: number | string | null;
  all_pct?: number | null;
  long_val?: number | string | null;
  long_pct?: number | null;
  short_val?: number | string | null;
  short_pct?: number | null;
}

export interface TVFullAnalysis {
  performance: TVAnalysisRow[];
  trades_analysis: TVAnalysisRow[];
  risk_adjusted: TVAnalysisRow[];
}

export interface MonthlyMatrixData {
  matrix: Record<number, Record<number, number>>;
  years: number[];
  row_totals: Record<number, number>;
  col_totals: Record<number, number>;
  grand_total: number;
}

export interface AssetStructuralResult {
  symbol: string;
  regime: string;
  trend_score: number;
  trend_grade: string;
  meanrev_score: number;
  meanrev_grade: string;
  TER: number;
  Hurst: number;
  GPR: number;
  AC1: number;
  DSR: number;
  MRI: number;
  VR5: number;
  HalfLife: number;
  CrossRate: number;
  last_close?: number;
}

export interface AssetBreakdownItem {
  asset: string;
  side: string;
  trades: number;
  net_profit: number;
  profit_factor: number | null;
  win_rate: number;
  avg_trade: number;
  max_win: number;
  max_loss: number;
}

export interface WFEAssetResult {
  asset: string;
  strategy_name?: string;
  direction?: string;
  WFE: number;
  WFE_PF?: number;
  IS_PF: number;
  OOS_PF: number;
  OOS_WR?: number;
  IS_PnL: number;
  OOS_PnL: number;
  OOS_GP: number;
  OOS_GL: number;
  IS_Trades: number;
  OOS_Trades: number;
}

export interface StrategyGroupResult {
  strategy_name: string;
  direction: string;
  assets: string[];
  group_metrics: {
    net_profit: number;
    gross_profit: number;
    gross_loss: number;
    profit_factor: number | null;
    win_rate: number;
    trades: number;
  };
}

export interface OOSBatchResult {
  batch_id: number;
  batch_name: string;
  assets: number;
  OOS_PnL: number;
  IS_PnL: number;
  OOS_PF: number;
  WFE_PF: number;
  IS_Trades: number;
  OOS_Trades: number;
}

export interface OOSCompareResult {
  is_metrics: Record<string, number | string | null>;
  oos_metrics: Record<string, number | string | null>;
  is_trades: number;
  oos_trades: number;
  aggregate_wfe: number;
  aggregate_wfe_pf?: number;
  aggregate_is_pf: number | null;
  aggregate_oos_pf: number | null;
  per_asset: WFEAssetResult[];
  per_batch?: OOSBatchResult[];
  cutoff: string;
  trade_date_range?: { from: string | null; to: string | null };
  debug?: Record<string, unknown>;
  return_to_dd_ratio?: number;
  portfolio_grade?: string;
  portfolio_grade_score?: number;
  /** Unified OOS metrics (daily-PnL DD, position-merged) — same formula as portfolio tabs */
  oos_quick_metrics?: Record<string, number>;
  /** Unified IS metrics (daily-PnL DD, position-merged) — same formula as portfolio tabs */
  is_quick_metrics?: Record<string, number>;
  /** Details of trades clipped at OOS end boundary */
  clipped_trades?: ClippedTradeDetail[];
  /** Concurrent trade stats for the OOS period */
  oos_concurrent?: { max_concurrent: number; avg_concurrent: number };
}

export interface ClippedTradeDetail {
  asset: string;
  entry_time: string;
  original_exit_time: string;
  clipped_exit_time: string;
  original_pnl: number;
  clipped_pnl: number;
  entry_price: number;
  boundary_close_price: number;
  original_exit_price: number;
  status: "clipped" | "reconciled";
}

export interface BenchmarkAssetResult {
  H1_PF: number | null;
  H2_PF: number | null;
  h1_trades: number;
  h2_trades: number;
  status: string;
  delta_pf: number | null;
}

export interface SignificanceResult {
  t_stat: number | null;
  p_value: number | null;
  bonferroni_p: number | null;
  significant: boolean;
  n_trades: number;
  n_assets: number;
}

export interface AggregatedPnLBar {
  period_start: string;
  period_label: string;
  gross_profit: number;
  gross_loss: number;
  net: number;
}

export interface ScanResult {
  directory: string;
  files: string[];
  file_count: number;
  trade_count: number;
  assets: string[];
  date_range: { start: string | null; end: string | null };
}

export interface MarketTicker {
  symbol: string;
  last_price: number;
  price_change_pct: number;
  high_24h: number;
  low_24h: number;
  volume_24h: number;
  funding_rate: number;
}

export interface ActivityEvent {
  type: "backtest" | "portfolio" | "journal";
  timestamp: string | null;
  title: string;
  detail: string;
  icon: string;
}

// Batch Dashboard types
export interface BatchSymbolSummary {
  asset: string;
  side: string;
  trades: number;
  net_profit: number;
  profit_factor: number | null;
  win_rate: number;
}

export interface FilterSummaryResult {
  total_assets: number;
  filtered_assets: number;
  total_pnl: number;
  real_pf: number | null;
  mean_pf: number | null;
  avg_wr: number | null;
  agg_wr: number | null;
  total_trades: number;
}

export interface BatchOverviewResult {
  aggregate: Record<string, number | string | null>;
  trade_count: number;
  symbols: BatchSymbolSummary[];
  parameters?: Record<string, unknown>;
}

export interface OutlierTrade {
  entry_time: string;
  exit_time: string;
  side: string;
  net_profit: number;
  entry_price: number;
  exit_price: number;
}

export interface OutlierAssetGroup {
  asset: string;
  trade_count: number;
  total_pnl: number;
  trades: OutlierTrade[];
}

export interface OutlierResult {
  avg_loss: number;
  avg_win: number;
  flagged_losses: OutlierAssetGroup[];
  flagged_wins: OutlierAssetGroup[];
}

export interface SideMetrics {
  trades: number;
  net_profit: number;
  profit_factor: number | null;
  win_rate: number;
  avg_trade: number;
  max_dd: number;
}

export interface StructuralMetrics {
  regime?: string;
  trend_grade?: string;
  meanrev_grade?: string;
  trend_score?: number;
  meanrev_score?: number;
  Hurst?: number;
  avg_consec_bull?: number;
  avg_consec_bear?: number;
  avg_consec_all?: number;
}

export interface LSBreakdownSymbol {
  symbol: string;
  long: SideMetrics | null;
  short: SideMetrics | null;
  total_trades: number;
  total_pnl: number;
  structural?: StructuralMetrics;
  risk_mode?: string;
}

export interface LSBreakdownResult {
  symbols: LSBreakdownSymbol[];
}

// Portfolio Analysis types
export interface PortfolioFullMetricsResult {
  metrics: Record<string, number | string | null>;
  long_metrics: Record<string, number | string | null>;
  short_metrics: Record<string, number | string | null>;
  equity_curve: { date: string; equity: number }[];
  monthly_matrix: Record<string, Record<string, number>>;
  // WFO-only: flags whether the PnLs in this payload were rescaled from the
  // DB $100-base to the portfolio's user-defined risk (fixed $/% equity).
  // `supported=false` means the risk mode (martingale/Kelly) isn't adjusted
  // yet and the payload is unchanged — frontend disables the toggle.
  risk_adjust_applied?: boolean;
  risk_adjust_supported?: boolean;
}

export interface OOSAnalysisResult {
  is_metrics: Record<string, number | null>;
  oos_metrics: Record<string, number | null>;
  wfe: number;
  cutoff: string | null;
  is_trades: number;
  oos_trades: number;
}

export interface OOSDateResult {
  is_metrics: Record<string, number | null>;
  oos_metrics: Record<string, number | null>;
  wfe: number;
  wfe_pf?: number;
  cutoff: string | null;
  is_trades: number;
  oos_trades: number;
  per_asset: WFEAssetResult[];
  strategy_groups?: StrategyGroupResult[];
  is_full_metrics: Record<string, number | string | null>;
  oos_full_metrics: Record<string, number | string | null>;
  oos_equity_curve: { date: string; equity: number }[];
  is_monthly_matrix: Record<string, Record<string, number | { pnl: number; trades: number; win_rate: number }>>;
  oos_monthly_matrix: Record<string, Record<string, number | { pnl: number; trades: number; win_rate: number }>>;
  return_to_dd_ratio?: number;
  portfolio_grade?: string;
  portfolio_grade_score?: number;
  oos_end_date?: string | null;
  /** Details of trades clipped at OOS end boundary */
  clipped_trades?: ClippedTradeDetail[];
  cost_summary?: CostSummary;
  oos_cost_summary?: CostSummary;
}

export interface PortfolioCompareEntry {
  id: number;
  name: string;
  asset_count: number;
  metrics: Record<string, number | null>;
  full_metrics: Record<string, number | string | null>;
  created_at: string | null;
  // OOS comparison fields
  oos_metrics: Record<string, number | null>;
  oos_full_metrics: Record<string, number | string | null>;
  wfe_pf: number;
  cutoff: string | null;
  is_trades: number;
  oos_trades: number;
  oos_equity_curve: { date: string; equity: number }[];
  portfolio_grade: string | null;
  portfolio_grade_score: number | null;
  return_to_dd_ratio: number | null;
}

// Portfolio Health Monitor types
export interface PortfolioHealthWindow {
  window_days: number;
  label: string;
  start_date: string;
  end_date: string;
  trades: number;
  net_profit: number;
  profit_factor: number;
  win_rate: number;
  max_drawdown: number;
  stability: number;
  sortino: number;
  avg_trade: number;
  grade: string;
  grade_score: number;
}

export interface PortfolioHealthAsset {
  asset: string;
  trades: number;
  net_profit: number;
  profit_factor: number;
  win_rate: number;
  avg_trade: number;
  contribution_pct: number;
  contribution_score: number;
  pf_trend: number;
  wr_trend: number;
  profit_trend: number;
  status: "healthy" | "warning" | "critical" | "inactive";
  decay_reason: string;
}

export interface PortfolioHealthResult {
  portfolio_id: number;
  portfolio_type: string;
  analysis_date: string;
  optimization_end_date: string | null;
  overall_status: "healthy" | "warning" | "critical";
  overall_grade: string;
  overall_grade_score: number;
  trend: "improving" | "stable" | "declining";
  trend_detail: string;
  windows: PortfolioHealthWindow[];
  assets: PortfolioHealthAsset[];
  summary: {
    total_trades: number;
    total_assets: number;
    healthy_assets: number;
    warning_assets: number;
    critical_assets: number;
    inactive_assets: number;
    data_start: string;
    data_end: string;
    oos_trades: number;
    is_trades: number;
  };
}

export interface PortfolioHealthSummary {
  portfolio_id: number;
  name: string;
  portfolio_type: string;
  overall_status: "healthy" | "warning" | "critical";
  overall_grade: string;
  overall_grade_score: number;
  trend: "improving" | "stable" | "declining";
  trend_detail: string;
  total_assets: number;
  healthy_assets: number;
  warning_assets: number;
  critical_assets: number;
  total_trades: number;
  oos_trades: number;
  latest_pf: number;
  latest_stability: number;
  latest_net_profit: number;
  full_pf: number;
  full_net_profit: number;
  pf_7d: number;
  wr_7d: number;
  pf_30d: number;
  wr_30d: number;
  pf_90d: number;
  wr_90d: number;
}

export interface RebalanceProposal {
  id: number;
  portfolio_id: number;
  trigger_type: "oos_boundary" | "manual";
  status: "pending" | "approved" | "rejected" | "expired";
  created_at: string | null;
  current_basket: string[];
  proposed_basket: string[];
  diff: {
    added: string[];
    removed: string[];
    unchanged: string[];
  };
  metrics_preview: Record<string, unknown>;
  reviewed_at: string | null;
  reviewed_by: string | null;
  resulting_portfolio_id: number | null;
}

export interface SwapCandidate {
  asset: string;
  corr_score: number;
  score: number;
  seed_rank: number;
  net_profit: number;
  profit_factor: number;
  win_rate: number;
  trades: number;
  avg_trade: number;
  batch_name: string;
  batch_id: number | null;
}

export interface SwapHistoryEntry {
  date: string;
  removed: string;
  added: string;
  reason: string;
}

// T-002 — Phase-2 nightly recorder events (live portfolio state log)
export interface Phase2EventDetails {
  decided_at_iso: string | null;
  trigger: string | null;
  recorder_version: number | null;
  phase1_window_num: number | null;
  phase1_window_num_synth_for_stub: boolean;
  phase1_window_oos_start_iso: string | null;
  phase1_window_oos_end_iso: string | null;
  prior_basket: string[];
  chosen_basket: string[];
  added: string[];
  removed: string[];
  basket_unchanged: boolean;
  dispatch_outcome: string | null;
  dispatch_verified_at_iso: string | null;
  reconstructed: boolean;
  decision_inputs_hash: string | null;
}

export interface Phase2Event {
  id: number;
  portfolio_id: number;
  event_type: "hybrid_refresh" | "hybrid_rotation" | "phase2_evaluated_no_change";
  symbol: string;
  created_at: string | null;
  details: Phase2EventDetails;
}

export interface Phase2EventsResponse {
  portfolio_id: number;
  limit: number;
  offset: number;
  total: number;
  event_type_filter: string | null;
  events: Phase2Event[];
}

// T-003 Delta 8 — Unified portfolio history feed (manual swaps + phase-2 events)
export type PortfolioHistoryType =
  | "manual_swap"
  | "phase2_refresh"
  | "phase2_rotation"
  | "phase2_no_change";

export type PortfolioHistorySourceStore = "swap_history" | "live_event";

export interface PortfolioHistoryEntry {
  type: PortfolioHistoryType;
  timestamp_iso: string | null;
  summary: string;
  actor: string;
  details: Record<string, unknown>;
  reconstructed: boolean;
  source_store: PortfolioHistorySourceStore;
}

export interface PortfolioHistoryResponse {
  portfolio_id: number;
  limit: number;
  offset: number;
  total: number;
  type_filter: PortfolioHistoryType | null;
  entries: PortfolioHistoryEntry[];
}

// Portfolio Builder types
export interface BuilderBatch {
  batch_id: number;
  name: string;
  strategy_name: string;
  strategy_id: number;
  direction_filter: string;
  total_symbols: number;
  completed_symbols: number;
  created_at: string | null;
}

export interface BuilderAsset {
  asset_name: string;
  symbol: string;
  side: string;
  trades: number;
  net_profit: number;
  profit_factor: number | null;
  win_rate: number;
  avg_trade: number;
}

// Cross-Strategy types
export interface CrossStrategyCandidate {
  longs: string[];
  shorts: string[];
  assets: string[];
  display_assets?: string[];  // Batch-tag-stripped names for UI display
  metrics: {
    net_profit: number;
    profit_factor: number;
    max_drawdown: number;
    stability: number;
    n_assets: number;
    win_rate?: number;
    total_trades?: number;
    avg_trade?: number;
    sortino?: number;
    calmar_ratio?: number;
    recovery_factor?: number;
  };
  score: number;
  attribution: Record<string, string>;
  selected_variants?: string[];
  blueprint?: Record<string, unknown>[];
}

export interface CrossBuildResult {
  candidates: CrossStrategyCandidate[];
  total_symbols: number;
  n_longs_available?: number;
  n_shorts_available?: number;
  total_variants?: number;
  strategies: Record<number, string>;
  timing?: Record<string, number>;
}

// Settings Recommender types
export interface RecommenderRecommendation {
  setting: string;
  value: unknown;
  confidence: number;
  reason: string;
  category: string;
}

export interface AffinityPairMetrics {
  id_a: number | string;
  id_b: number | string;
  return_correlation: number;
  correlation_stability: number;
  drawdown_overlap: number;
  stress_divergence: number;
  complementarity: number;
  recovery_offset: number;
  shape_diversity: number;
  composite_score: number;
}

export interface AffinityBatchRole {
  batch_id: number | string;
  role: string;  // anchor | strong_complement | conditional | redundant | outlier
  team_player_score: number;   // pure affinity score
  quality_score: number;       // individual batch quality (0-1)
  combined_score: number;      // blend of affinity + quality
  avg_profit_factor: number;
  avg_win_rate: number;
  total_net_profit: number;
  batch_grade: string;
  best_partner: number | string | null;
  worst_partner: number | string | null;
  best_partner_score: number;
  worst_partner_score: number;
  redundant_with: number | string | null;
  cluster_id: number;
}

export interface AffinityOOSValidation {
  id_a: number | string;
  id_b: number | string;
  metric: string;
  is_value: number;
  oos_value: number;
  delta: number;
  status: string;  // certified | degraded | improved | spurious
}

export interface AffinityReportResult {
  pairs: AffinityPairMetrics[];
  roles: AffinityBatchRole[];
  clusters: Array<{ cluster_id: number; batch_ids: (number | string)[]; avg_internal_harmony: number }>;
  metric_leaderboard: Record<string, Array<{ pair: (number | string)[]; score: number }>>;
  oos_validations: AffinityOOSValidation[] | null;
  metric_reliability: Record<string, number> | null;
  recommendation: {
    anchor?: { batch_id: number | string; score: number; affinity?: number; quality?: number } | null;
    keep?: (number | string)[];
    conditional?: (number | string)[];
    drop?: (number | string)[];
    drop_reasons?: Record<string, string>;
    current_avg_harmony?: number;
    recommended_avg_harmony?: number;
    improvement_pct?: number;
    oos_warnings?: string[];
    error?: string;
  };
  batch_names: Record<string, string>;
  batch_ids: number[];
  oos_window?: { start: string | null; end: string | null; oos2_excluded_from: string | null };
}

export interface SmartGroupFamily {
  family_id: number;
  batch_ids: number[];
  representative_id: number;
  avg_internal_corr: number;
  quality_scores: Record<number, number>;
  side: string;  // "Long" | "Short" | ""
}

export interface SmartGroupResult {
  families: SmartGroupFamily[];
  l2_groups: number[][];
  recommended_ids: number[];
  drop_ids: number[];
  drop_reasons: Record<string, string>;
  batch_names: Record<string, string>;
  correlation_matrix: { batch_ids: number[]; matrix: number[][] };
  greedy_order: Array<{
    batch_id: number; step: number;
    sharpe_before: number; sharpe_after: number;
    sharpe_raw?: number;           // raw IS Sharpe (before deflation)
    deflation_mult?: number;       // per-batch deflation multiplier
    marginal: number; action: string;  // seed | add | skip
    net_marginal?: number;         // V2: after holdout + concentration + tilt adjustments
    concentration_mult?: number;   // V2: asset pool concentration penalty
    trend_tilt?: number;           // V2: trend direction tilt multiplier
    holdout_marginal?: number;     // V2: holdout Sharpe marginal
  }>;
  cv_stability?: {
    n_folds: number;
    selection_counts?: Record<string, number>;
    stable_selections?: number[];
    min_stable_threshold?: number;
    skipped?: boolean;
    reason?: string;
  };
  assets_filtered?: Record<string, [number, number]>;  // batch_id → [total, surviving]
  // V2 additions
  bootstrap_stability?: Record<string, number>;  // batch_id → stability score 0.0-1.0
  trend_regime?: {
    regime: string;      // "bullish" | "bearish" | "neutral"
    bull_pct: number;
    bear_pct: number;
    streak_ratio: number;
    total_ranges?: number;
  };
}

export interface RecommenderResult {
  mode: string;
  elapsed_seconds: number;
  profile: {
    n_assets: number;
    n_batches: number;
    n_longs: number;
    n_shorts: number;
    trade_count_median: number;
    trade_count_mean: number;
    trade_count_min: number;
    trade_count_max: number;
    trade_count_p25: number;
    trade_count_p75: number;
    pct_below_10_trades: number;
    pct_below_20_trades: number;
    metric_stats: Record<string, Record<string, number>>;
    corr_mean_abs: number;
    corr_median_abs: number;
    corr_p90_abs: number;
    corr_density_above_50: number;
    corr_density_above_30: number;
    pf_spread: number;
    quality_gini: number;
    batch_sizes: Record<string, number>;
    batch_balance_ratio: number;
    n_suspicious: number;
    suspicious_tags: string[];
  };
  recommendations: RecommenderRecommendation[];
  cv_results: Array<{
    settings: Record<string, unknown>;
    avg_val_stability: number;
    avg_val_pf: number;
    avg_val_sortino: number;
    avg_val_sharpe: number;
    avg_val_max_dd: number;
    avg_overfit_ratio: number;
    std_val_stability: number;
    composite_score: number;
  }>;
  top_presets: Array<{
    settings: Record<string, unknown>;
    full_settings: Record<string, unknown>;
    composite_score: number;
    avg_val_stability: number;
    avg_val_pf: number;
    avg_overfit_ratio: number;
  }>;
}

// Saved Runs types
export interface SavedRunSummary {
  filename: string;
  type: "build" | "recommend_quick" | "recommend_thorough";
  saved_at: string;
  summary: {
    n_candidates?: number;
    n_assets?: number;
    batch_ids?: number[];
    mode?: string;
    elapsed_seconds?: number;
    date_from?: string;
    date_to?: string;
    top_pf?: number;
    top_stability?: number;
    n_recommendations?: number;
    n_top_presets?: number;
    div_algo?: string;
    combined_objective?: string;
    seed_metrics?: string[];
    n_final_assets?: number;
  };
}

export interface SavedRunDetail {
  type: string;
  saved_at: string;
  summary: Record<string, unknown>;
  params: Record<string, unknown>;
  result: Record<string, unknown>;
}

// Journal types
export interface JournalTradeItem {
  id: number;
  symbol: string;
  side: string;
  status: string;
  source: string;
  strategy_id: number | null;
  strategy_name: string | null;
  config_id: number | null;
  config_name: string | null;
  backtest_trade_id: number | null;
  portfolio_id: number | null;
  entry_price: number;
  quantity: number;
  risk_amount: number | null;
  sl_price: number | null;
  tp_price: number | null;
  tp2_price: number | null;
  entry_time: string | null;
  exit_price: number | null;
  exit_time: string | null;
  exit_reason: string | null;
  outcome: string | null;
  expected_outcome: string | null;
  expected_entry: number | null;
  expected_sl: number | null;
  expected_tp1: number | null;
  expected_tp2: number | null;
  expected_exit: number | null;
  expected_pnl: number | null;
  net_profit: number | null;
  net_profit_pct: number | null;
  commission_rate: number;
  expected_commission: number;
  actual_commission: number | null;
  notes: string;
  tags: string[];
  rating: number;
  is_test: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface JournalTradeCreate {
  symbol: string;
  side: string;
  entry_price: number;
  quantity: number;
  risk_amount?: number;
  source?: string;
  strategy_id?: number;
  config_id?: number;
  backtest_trade_id?: number;
  portfolio_id?: number;
  sl_price?: number;
  tp_price?: number;
  tp2_price?: number;
  entry_time?: string;
  commission_rate?: number;
  outcome?: string;
  expected_outcome?: string;
  expected_entry?: number;
  expected_sl?: number;
  expected_tp1?: number;
  expected_tp2?: number;
  expected_exit?: number;
  expected_commission?: number;
  notes?: string;
  tags?: string[];
  rating?: number;
}

export interface JournalTradeUpdatePayload {
  symbol?: string;
  side?: string;
  entry_price?: number;
  quantity?: number;
  risk_amount?: number;
  sl_price?: number;
  tp_price?: number;
  tp2_price?: number;
  entry_time?: string;
  exit_price?: number;
  exit_time?: string;
  exit_reason?: string;
  status?: string;
  outcome?: string;
  expected_outcome?: string;
  actual_commission?: number;
  commission_rate?: number;
  net_profit?: number;
  notes?: string;
  tags?: string[];
  rating?: number;
  strategy_id?: number;
  config_id?: number;
  portfolio_id?: number;
}

export interface JournalStatsResult {
  total_trades: number;
  open_trades: number;
  closed_trades: number;
  total_pnl: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  total_expected_fees: number;
  total_actual_fees: number;
  fee_delta: number;
}

export interface LookupTradeEntry {
  trade_num: number;
  side: string;
  entry_time: string | null;
  exit_time: string | null;
  entry_price: number;
  exit_price: number;
  quantity: number;
  net_profit: number;
  exit_reason: string | null;
  sl_price: number | null;
  tp_price: number | null;
  commission: number;
}

export interface LookupTradesResult {
  symbol: string;
  timeframe: string;
  total_trades: number;
  trades: LookupTradeEntry[];
}

export interface DashboardSymbolStats {
  symbol: string;
  trades: number;
  pnl: number;
  win_rate: number;
  fees_expected: number;
  fees_actual: number;
}

export interface DashboardStrategyStats {
  strategy_id: number;
  name: string;
  trades: number;
  pnl: number;
  win_rate: number;
}

export interface TradingDashboardResult {
  total_trades: number;
  open_trades: number;
  closed_trades: number;
  total_pnl: number;
  win_rate: number;
  total_expected_fees: number;
  total_actual_fees: number;
  fee_delta: number;
  by_symbol: DashboardSymbolStats[];
  by_side: Record<string, { trades: number; pnl: number; win_rate: number }>;
  by_strategy: DashboardStrategyStats[];
  monthly_pnl: Record<string, number>;
}

// WFO types
export interface EquityCurvePoint {
  date: string;
  equity: number;
  drawdown_pct: number;
  pnl: number;
  drawdown: number;
}

export interface MonthlyMatrixData {
  years: string[];
  matrix: Record<string, Record<string, number>>;
  row_totals: Record<string, number>;
  col_totals: Record<string, number>;
  grand_total: number;
}

export interface OosConcentration {
  max_trade_pnl: number;
  cum_oos_pnl: number;
  max_trade_pct_of_oos: number;
  top3_trade_pct_of_oos: number;
  herfindahl_gross_profit: number;
  per_quarter_max_pct: Record<string, number>;
  per_month_max_pct: Record<string, number>;
  passes_concentration_gate: boolean;
  violations: string[];
  n_trades: number;
  n_quarters: number;
  n_months: number;
}

export interface WFOWindowCandidate {
  rank: number;
  assets: string[];
  is_metrics: Record<string, number | null>;
  oos_metrics: Record<string, number | null>;
  oos_wfe: number;
  // Concentration diagnostics (25%-of-OOS-cum rule + per-period gates)
  oos_concentration?: OosConcentration | null;
  // Overlapping mode extras
  oos_h1_metrics?: Record<string, number | null>;
  oos_h2_metrics?: Record<string, number | null>;
  wfe_h1?: number;
  degradation_ratio?: number;
  oos_h1_concentration?: OosConcentration | null;
  oos_h2_concentration?: OosConcentration | null;
  // Batch Selection mode extras
  selected_batches?: number[];
  calibration_results?: Record<string, { score: number; net: number; passed: boolean }>;
  asset_details?: { key: string; batch_id: number; symbol: string; side: string; is_score: number; tpm_est: number; timeframe?: string; group?: string }[];
  asset_breakdown?: {
    key: string; batch_id: number; symbol: string; side: string;
    timeframe: string; group: string;
    is_wr: number; is_pf: number; is_pnl: number; is_trades: number;
    oos_wr: number; oos_pf: number; oos_pnl: number; oos_trades: number;
  }[];
  estimated_tpm?: number;
  n_batches?: number;
  n_assets?: number;
  // Full analysis data (equity curves, monthly matrix)
  is_equity_curve?: { date: string; equity: number; drawdown_pct: number }[];
  is_monthly_matrix?: MonthlyMatrixData;
  oos_equity_curve?: { date: string; equity: number; drawdown_pct: number }[];
  oos_monthly_matrix?: MonthlyMatrixData;
  // Live window flag
  is_live?: boolean;
}

export interface WFOWindowResult {
  window_num: number;
  is_start: string;
  is_end: string;
  oos_start: string | null;
  oos_end: string | null;
  is_trade_count: number;
  is_symbol_count: number;
  is_pool_size?: number;
  candidates: WFOWindowCandidate[];
  // Overlapping mode H1/H2 dates
  oos_h1_start?: string | null;
  oos_h1_end?: string | null;
  oos_h2_start?: string | null;
  oos_h2_end?: string | null;
  degradation_ratio?: number;
  // Live window flag
  is_live?: boolean;
}

export interface WFOTrack {
  rank: number;
  per_window: {
    window_num: number;
    assets: string[];
    oos_pnl: number;
    oos_wfe: number;
    // Overlapping extras
    oos_h1_pnl?: number;
    oos_h2_pnl?: number;
    wfe_h1?: number;
    degradation_ratio?: number;
  }[];
  compiled_metrics: Record<string, number | null>;
  compiled_equity: number[];
  final_is_assets: string[];
}

export interface WFOTimelineSegment {
  // Time-based fields
  window_num?: number;
  oos_start?: string;
  oos_end?: string;
  expected_oos_pnl?: number | null;
  expected_oos_wfe?: number | null;
  // Event-based fields
  phase?: "research" | "live";
  date_from?: string | null;
  date_to?: string | null;
  // Shared
  assets?: string[];
  basket?: string[];
  trades: number;
  realized_pnl: number;
}

export interface WFOTimelineResponse {
  mode: "time" | "event" | "none";
  optimization_end_date: string | null;
  metrics: Record<string, number | null>;
  equity_curve: Array<{ date: string; equity: number }>;
  segments: WFOTimelineSegment[];
  has_walkforward: boolean;
}

export interface WFOResult {
  wfo_run_id: number;
  windows: WFOWindowResult[];
  tracks: WFOTrack[];
  cumulative_is: Record<string, number | null>;
  cumulative_oos: Record<string, number | null>;
  expected_oos?: Record<string, number | null>;
  summary: {
    total_windows: number;
    windows_with_oos: number;
    aggregate_wfe: number;
    avg_degradation?: number;
    // Batch Selection mode extras
    mode?: string;
    calibrated_wr?: string;
    calibrated_wr_pct?: number;
    calibrated_pnl?: number;
    full_wr?: string;
    full_wr_pct?: number;
    full_pnl?: number;
    exec_wr?: string;
    exec_wr_pct?: number;
    exec_pnl?: number;
    delta_pp?: number;
    ef_ratio_pct?: number;
    total_batches_pool?: number;
    active_batches_pool?: number;
    has_exec_filter?: boolean;
    q_starts?: number;
    q_overlap_divisor?: number;
    l2_groups?: boolean;
    pruning_enabled?: boolean;
    past_oos_confirm?: boolean;
    expected_calibrated_pnl?: number;
    expected_full_pnl?: number;
    expected_exec_pnl?: number;
  };
  // Overlapping mode
  time_mode?: string;
  cumulative_oos_h1?: Record<string, number | null>;
  cumulative_oos_h2?: Record<string, number | null>;
  // 2-Step Hybrid: event-based results from Step 2 (when hybrid_mode=true)
  step2?: EventWFOResult & {
    winning_batches: number[];
    step2_config: Record<string, unknown>;
    aggregate_equity_curve?: EquityCurvePoint[];
    aggregate_monthly_matrix?: MonthlyMatrixData;
    aggregate_breakdown?: NonNullable<WFOWindowCandidate["asset_breakdown"]>;
    cumulative_metrics?: Record<string, number | null>;
    selection_changes?: { date: string; added: string[]; removed: string[]; window_num?: number }[];
    selected_records?: { dt: string; pnl: number; asset: string; symbol: string; side: string; timeframe: string; batch_id?: number }[];
    // Config digest: exactly what Step 2 used (diagnose stale-preset issues)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    config_used?: Record<string, any>;
  };
  // Aggregate OOS analysis (stitched across all windows)
  aggregate_oos_equity_curve?: EquityCurvePoint[];
  aggregate_oos_monthly_matrix?: MonthlyMatrixData;
  aggregate_oos_breakdown?: WFOWindowCandidate["asset_breakdown"];
  // Health check
  health_check?: { healthy: boolean; warnings: string[] };
  // Risk profile diagnostic (position sizing impact)
  risk_profile_diagnostic?: {
    applied: boolean;
    mode: string;
    value: number;
    initial_capital: number;
    pre_total_pnl: number;
    post_total_pnl: number;
    pnl_change_pct: number;
    scaling_worked: boolean;
    sample_before: [string, number][];
    sample_after: [string, number][];
    note: string | null;
  };
  // PnL Scaling diagnostic (position sizing multipliers)
  pnl_scaling_diagnostic?: {
    mode: string;
    custom_value: number | null;
    batch_multipliers: Record<string, number>;
    any_scaling: boolean;
  };
  // Session filter diagnostic
  session_filter_diagnostic?: {
    enabled: boolean;
    mode: string;
    exclude_hours: number[];
    exclude_days: number[];
    trades_total: number;
    trades_exec_eligible: number;
    pct_exec: number;
    note: string;
  };
  // Window generation diagnostic
  window_diagnostic?: {
    data_start: string;
    last_oos_end: string;
    date_from_param: string | null;
    total_windows: number;
    oos_windows: number;
    first_window: { is_start: string; is_end: string; oos_start: string; oos_end: string } | null;
    last_oos_window: { is_start: string; is_end: string; oos_start: string; oos_end: string } | null;
    total_trades_loaded: number;
    batch_ids_used: number[];
    earliest_trade: string | null;
    latest_trade: string | null;
  };
}

export interface WFORunItem {
  id: number;
  batch_ids: number[];
  is_months: number;
  oos_months: number;
  last_oos_end: string;
  n_candidates: number;
  optimizer_settings: Record<string, unknown>;
  status: string;
  created_at: string;
  time_mode: string;
  event_mode: boolean;
  portfolio_ids: number[];
  avg_degradation?: number;
  parent_run_id?: number;
  window_count?: number;
  cumulative_oos?: {
    net_profit?: number;
    profit_factor?: number;
    win_rate?: number;
    max_drawdown?: number;
    total_trades?: number;
    sortino?: number;
    avg_window_pnl?: number;
    total_windows_evaluated?: number;
  } | null;
}

export interface EventWFOResult {
  mode: "event";
  equity_curve: number[];
  equity_dates: string[];
  all_trade_count: number;
  selected_trade_count: number;
  summary: {
    net_profit: number;
    pf: number;
    wr: number;
    max_dd: number;
    sortino: number;
    recovery: number;
    ulcer: number;
    calmar: number;
    total_trades: number;
    avg_portfolio_size: number;
    total_steps: number;
  };
  asset_frequency: Record<string, { selected: number; eligible: number; pct: number }>;
  selection_changes: { step: number; date: string; added: string[]; removed: string[] }[];
  config: Record<string, unknown>;
  error?: string;
  // Risk profile diagnostic (position sizing impact)
  risk_profile_diagnostic?: {
    applied: boolean;
    mode: string;
    value: number;
    initial_capital: number;
    pre_total_pnl: number;
    post_total_pnl: number;
    pnl_change_pct: number;
    scaling_worked: boolean;
    sample_before: [string, number][];
    sample_after: [string, number][];
    note: string | null;
  };
}

// Portfolio Test types
export interface PortfolioTestProgress {
  status: string;
  total_symbols: number;
  completed_symbols: number;
  current_group: string;
  result?: PortfolioTestResult;
  error?: string;
}

export interface PortfolioTestResult {
  combined_equity: number[];
  combined_metrics: Record<string, number | null>;
  per_symbol_metrics: Record<string, Record<string, number | null>>;
  bars_processed: number;
  total_symbols: number;
  oos_only?: boolean;
}

export interface SynergyAuditMarginalRow {
  Strategy: string;
  "Net Profit": number;
  "Profit Factor": number;
  "Sharpe Ratio": number;
  "Max Drawdown ($)": number;
  SQN: number;
  Trades: number;
  "Friction Cost": number;
}

export interface SynergyAuditResult {
  marginal: SynergyAuditMarginalRow[];
  diversification: {
    DM: number;
    synergistic: boolean;
    combined_std: number;
    avg_individual_std: number;
    per_strategy_std: Record<string, number>;
    reason?: string;
  };
  conflicts: {
    conflict_count: number;
    conflict_trade_count: number;
    wasted_commission_estimate: number;
    net_offset_pnl: number;
    conflicts: {
      "Base Symbol": string;
      "Long Strategy": string;
      "Short Strategy": string;
      "Overlap Hours": number;
      "Long PnL": number;
      "Short PnL": number;
      "Net Offset PnL": number;
    }[];
  };
  breakeven: {
    breakeven_pct: number;
    pf_at_zero: number;
    pf_at_current: number;
    margin_of_safety: number;
  };
  trade_count: number;
  oos_only: boolean;
}

export interface LiveViewData {
  metrics: Record<string, number | null>;
  full_metrics: Record<string, number | string | null>;
  trade_count: number;
  start_date: string | null;
  end_date: string | null;
  equity_curve: { date: string; equity: number }[];
  monthly_matrix: Record<string, Record<string, number | { pnl: number; trades: number; win_rate: number }>>;
}

export interface CostSummary {
  total_stress_cost: number;
  avg_cost_per_trade: number;
  cost_breakdown: { slippage: number; commission: number; spread: number; funding: number };
  rates: { slippage_pct: number; commission_pct: number; spread_pct: number; funding_daily_pct: number };
  trade_count: number;
}

export interface LiveViewsResult {
  in_sample: LiveViewData;
  oos: LiveViewData;
  oos_full: LiveViewData;
  live: LiveViewData;
  since_saved: LiveViewData;
  since_optimized: LiveViewData;
  /** Details of trades clipped at OOS end boundary */
  clipped_trades?: ClippedTradeDetail[];
  cost_summary?: CostSummary;
}

// --- Live Trading Engine Types ---

export interface LiveEngineStatus {
  running: boolean;
  enabled: boolean;
  testnet: boolean;
  testnet_use_mainnet_prices: boolean;
  paper_trading: boolean;
  websocket: { public: boolean; private: boolean; kline_symbols: number; ticker_symbols: number };
  active_portfolios: number[];
  tsl_tracked: number;
  equity: number;
  available_balance: number;
}

export interface LivePosition {
  id: number;
  portfolio_id: number | null;
  symbol: string;
  side: string;
  strategy_name: string;
  entry_price: number;
  quantity: number;
  sl_price: number | null;
  tp_price: number | null;
  unrealized_pnl: number;
  leverage: number;
  margin_mode: string;
  trailing_stop_distance: number | null;
  trailing_extreme: number | null;
  funding_accumulated: number;
  entry_commission: number;
  exit_commission: number;
  risk_removed: boolean;
  opened_at: string | null;
}

export interface LiveOrder {
  id: number;
  symbol: string;
  side: string;
  order_type: string;
  role: string;
  status: string;
  intended_qty: number;
  filled_qty: number;
  price: number | null;
  avg_fill_price: number | null;
  sl_price: number | null;
  tp_price: number | null;
  commission: number;
  cancel_reason: string;
  order_link_id: string;
  portfolio_id: number | null;
  created_at: string | null;
}

export interface LiveTrade {
  id: number;
  portfolio_id: number | null;
  symbol: string;
  side: string;
  strategy_name: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  realized_pnl: number;
  commission: number;
  entry_commission: number;
  exit_commission: number;
  funding_fee: number;
  close_reason: string;
  opened_at: string | null;
  closed_at: string | null;
}

export interface LiveEquity {
  equity: number;
  available_balance: number;
  testnet: boolean;
}

export interface LiveEvent {
  id: number;
  portfolio_id: number | null;
  event_type: string;
  symbol: string;
  details: string;
  created_at: string | null;
}

export interface LiveSignal {
  id: number;
  portfolio_id: number;
  portfolio_name: string;
  strategy_name: string;
  batch_name: string;
  timeframe: string;
  symbol: string;
  action: string;
  direction: string;
  entry_id: string;
  bar_timestamp: string | null;
  close_price: number;
  sl_price: number;
  tp_price: number;
  trail_points: number;
  comment: string;
  status: string;
  in_window: boolean | null;
  trade_pnl: number | null;
  trade_status: string | null;
  created_at: string | null;
  filled_at: string | null;
}

export interface LiveTSLEntry {
  position_id: number;
  portfolio_id: number;
  symbol: string;
  side: string;
  entry_price: number;
  extreme_price: number;
  trail_points: number;
  trail_offset: number;
  stop_level: number;
  active: boolean;
}

export interface AccountSummary {
  equity: number;
  available_balance: number;
  margin_used: number;
  unrealized_pnl: number;
  total_notional: number;
  position_count: number;
  long_count: number;
  short_count: number;
  daily_pnl: number;
  daily_trades: number;
  daily_wins: number;
  daily_losses: number;
  daily_commission: number;
  circuit_breaker: boolean;
  risk_mode: string;
  risk_display: string;
  testnet: boolean;
  paper_trading: boolean;
}

export interface PnLSummary {
  today: number;
  week: number;
  month: number;
  all_time: number;
  total_trades: number;
  winning_trades: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
}

// Trash / Deleted Items types
export interface DeletedStrategy {
  id: number;
  name: string;
  description: string;
  source_type: string;
  created_at: string | null;
  deleted_at: string | null;
}

export interface DeletedBacktest {
  id: number;
  strategy_id: number;
  symbol: string;
  timeframe: string;
  status: string;
  created_at: string | null;
  deleted_at: string | null;
  metrics: Record<string, number>;
}

export interface DeletedBatch {
  id: number;
  name: string;
  strategy_id: number;
  timeframe: string;
  total_symbols: number;
  status: string;
  created_at: string | null;
  deleted_at: string | null;
}

export interface DeletedWFO {
  id: number;
  batch_id: number;
  is_months: number;
  oos_months: number;
  status: string;
  created_at: string | null;
  deleted_at: string | null;
}

// Calendar + Personal Journal types
export interface CalendarDaySummary {
  date: string;
  trade_count: number;
  pnl: number;
  wins: number;
  losses: number;
}

export interface JournalNoteItem {
  id: number;
  note_type: string;
  title: string;
  subject: string;
  content: string;
  note_date: string | null;
  period_start: string | null;
  period_end: string | null;
  color: string;
  images: string[];
  tags: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface JournalNoteCreate {
  note_type?: string;
  title: string;
  subject?: string;
  content?: string;
  note_date: string;
  period_start?: string;
  period_end?: string;
  color?: string;
  images?: string[];
  tags?: string[];
}

export interface JournalNoteUpdate {
  title?: string;
  subject?: string;
  content?: string;
  note_date?: string;
  note_type?: string;
  period_start?: string;
  period_end?: string;
  color?: string;
  images?: string[];
  tags?: string[];
}

export interface NoteCalendarSummary {
  date: string;
  note_count: number;
  types: string[];
  colors: string[];
}

export interface MonthlyRecapData {
  year: number;
  month: number;
  total_trades: number;
  closed_trades: number;
  open_trades: number;
  total_pnl: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_trade_pnl: number;
  best_day: { date: string; pnl: number } | null;
  worst_day: { date: string; pnl: number } | null;
  total_fees: number;
  assets: { symbol: string; trades: number; pnl: number; win_rate: number }[];
}

export interface JournalTemplateItem {
  template_type: string;
  content: string;
  is_default: boolean;
}

export interface ChecklistItem {
  text: string;
  checked: boolean;
  completed_at?: string | null;
}

export interface MonthlyObjectiveItem {
  id: number;
  title: string;
  content: string;
  checklist: ChecklistItem[];
  period: string; // "daily" | "weekly" | "monthly"
  year: number | null;
  month: number | null;
  is_active: boolean;
  sort_order: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface MonthlyObjectiveCreate {
  title?: string;
  content?: string;
  checklist?: ChecklistItem[];
  period?: string;
  year: number;
  month: number;
}

export interface MonthlyObjectiveUpdate {
  title?: string;
  content?: string;
  checklist?: ChecklistItem[];
  period?: string;
  is_active?: boolean;
  sort_order?: number;
}

export interface CustomTemplateItem {
  id: number;
  name: string;
  content: string;
  color: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface PortfolioAssetSummary {
  asset: string;
  strategy_name: string;
  config_name: string;
  direction: string;
  net_profit: number;
  profit_factor: number | null;
  win_rate: number;
  trades: number;
  avg_trade: number;
  max_drawdown: number;
  is_boundary_straddles: number;
  oos_boundary_straddles: number;
  has_boundary_straddles: boolean;
  structural?: StructuralMetrics;
}

export interface BybitSizeCheckSymbol {
  trades_total: number;
  trades_exceed_max: number;
  trades_below_min: number;
  max_qty_needed: number;
  bybit_max_qty: number;
  bybit_min_qty: number;
}

export interface BybitSizeCheckResult {
  results: Record<string, BybitSizeCheckSymbol>;
  totals: {
    trades_checked: number;
    trades_exceed_max: number;
    trades_below_min: number;
    assets_with_issues: number;
  };
}

export interface TradeFill {
  exit_time: string | null;
  exit_price: number | null;
  qty: number | null;
  net_profit: number;
  exit_reason: string;
}

export interface PortfolioAssetTrade {
  num: number;
  side: string;
  entry_time: string | null;
  exit_time: string | null;
  entry_price: number | null;
  exit_price: number | null;
  net_profit: number;
  mae: number | null;
  mfe: number | null;
  exit_reason: string;
  commission: number | null;
  stress_cost: number | null;
  fills: TradeFill[];
  strategy_name?: string;
  config_name?: string;
  size_pct?: number;
}

export interface CostSummary {
  extra_slippage_pct: number;
  extra_commission_pct: number;
  funding_daily_pct: number;
  spread_pct: number;
  total_stress_cost: number;
}

export interface MonthTradeExit {
  exit_time: string | null;
  exit_price: number | null;
  net_profit: number;
  quantity: number | null;
  reason: string;
  commission: number | null;
  sl_price?: number | null;
  tp_price?: number | null;
}

export interface MonthPosition {
  num: number;
  asset: string;
  asset_display: string;
  side: string;
  entry_time: string | null;
  entry_price: number | null;
  net_profit: number;
  batch_id?: number;
  batch_name?: string;
  batch_group?: string;
  pnl_multiplier?: number;
  strategy_id?: number;
  timeframe?: string;
  run_id?: number | null;
  exits: MonthTradeExit[];
}

/* Legacy flat trade (kept for portfolio month-trades) */
export interface MonthTrade {
  num: number;
  asset: string;
  asset_display: string;
  side: string;
  entry_time: string | null;
  exit_time: string | null;
  entry_price: number | null;
  exit_price: number | null;
  net_profit: number;
  commission: number | null;
  batch_id?: number | null;
  batch_name?: string;
  batch_group?: string;
  pnl_multiplier?: number;
  strategy_id?: number | null;
}

export interface MonthTradesResponse {
  trades?: MonthTrade[];
  positions?: MonthPosition[];
  position_count?: number;
  trade_count?: number;
  year: number;
  month: number;
  period?: string;
  // Hybrid / WFAS-Phase-2 portfolios include basket rotation events so the
  // modal can surface which assets rotated in/out during this month.
  selection_changes?: { date: string; added: string[]; removed: string[]; window_num?: number }[];
}

/**
 * Submit a task to an async endpoint, then poll until complete.
 * Backend returns {task_id, status:"running"}, then we poll
 * GET /api/analysis/optimize/progress/{task_id} every 2s.
 *
 * @param maxMinutes — polling timeout (default 60). Thorough mode uses 480 (8h).
 */
async function _submitAndPoll<T>(
  endpoint: string,
  payload: Record<string, unknown>,
  onProgress?: (step: number, totalSteps: number, label: string) => void,
  maxMinutes = 60,
  onTaskId?: (taskId: string) => void,
): Promise<T> {
  const { task_id } = await fetchApi<OptimizeTaskResponse>(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (onTaskId) onTaskId(task_id);

  // Poll until done — track last progress for timeout message
  const POLL_MS = 2000;
  const MAX_POLLS = Math.ceil((maxMinutes * 60 * 1000) / POLL_MS);
  let lastStep = 0;
  let lastTotal = 0;
  let lastLabel = "";
  for (let i = 0; i < MAX_POLLS; i++) {
    await new Promise((r) => setTimeout(r, POLL_MS));
    const prog = await fetchApi<OptimizeProgressResponse>(
      `/api/analysis/optimize/progress/${task_id}`,
    );
    // Track last known progress
    if (prog.step != null && prog.total_steps != null) {
      lastStep = prog.step;
      lastTotal = prog.total_steps;
      lastLabel = prog.step_label ?? "";
    }
    // Report progress to caller
    if (onProgress && prog.step != null && prog.total_steps != null && prog.step_label) {
      onProgress(prog.step, prog.total_steps, prog.step_label);
    }
    if (prog.status === "completed" && prog.result != null) {
      return prog.result as T;
    }
    if (prog.status === "error") {
      throw new Error(prog.error ?? "Optimization failed");
    }
    if (prog.status === "cancelled") {
      throw new DOMException("Task cancelled by user", "AbortError");
    }
  }
  const pct = lastTotal > 0 ? ((lastStep / lastTotal) * 100).toFixed(1) : "?";
  throw new Error(`Optimization timed out after ${maxMinutes} minutes (was at step ${lastStep}/${lastTotal} — ${pct}%${lastLabel ? `, ${lastLabel}` : ""})`);
}

export const api = {
  health: () => fetchApi<{ status: string }>("/api/health"),
  dashboardStats: () => fetchApi<DashboardStats>("/api/dashboard/stats"),
  accountEquity: (period: string = "3m", portfolioId?: number) => {
    const params = new URLSearchParams({ period });
    if (portfolioId) params.set("portfolio_id", String(portfolioId));
    return fetchApi<{ data: Array<{ date: string; pnl: number; equity: number; drawdown: number }>; period: string }>(
      `/api/dashboard/account-equity?${params}`,
    );
  },
  accountKpis: (portfolioId?: number) => {
    const params = new URLSearchParams();
    if (portfolioId) params.set("portfolio_id", String(portfolioId));
    const qs = params.toString();
    return fetchApi<{
      total_trades: number; total_pnl: number; win_rate: number; avg_trade: number;
      profit_factor: number | null; best_trade: number; worst_trade: number;
      max_drawdown: number; win_count: number; loss_count: number;
      avg_win: number; avg_loss: number; open_positions: number;
    }>(`/api/dashboard/account-kpis${qs ? `?${qs}` : ""}`);
  },
  dailyPnlHeatmap: (months: number = 3, portfolioId?: number) => {
    const params = new URLSearchParams({ months: String(months) });
    if (portfolioId) params.set("portfolio_id", String(portfolioId));
    return fetchApi<{
      days: Array<{ date: string; pnl: number; trades: number; wins: number; losses: number }>;
      months: number;
    }>(`/api/dashboard/daily-pnl-heatmap?${params}`);
  },
  performanceBreakdown: (portfolioId?: number) => {
    const params = new URLSearchParams();
    if (portfolioId) params.set("portfolio_id", String(portfolioId));
    const qs = params.toString();
    return fetchApi<{
      long_pnl: number; short_pnl: number; long_trades: number; short_trades: number;
      long_win_rate: number; short_win_rate: number;
      best_trade: { symbol: string; pnl: number; side: string; date: string | null } | null;
      worst_trade: { symbol: string; pnl: number; side: string; date: string | null } | null;
      current_streak: number; max_win_streak: number; max_loss_streak: number;
      win_distribution: Array<{ min: number; max: number; count: number }>;
      loss_distribution: Array<{ min: number; max: number; count: number }>;
    }>(`/api/dashboard/performance-breakdown${qs ? `?${qs}` : ""}`);
  },
  tradeStats: () => fetchApi<TradeStats>("/api/trades/stats"),
  tradesList: (limit = 500, includeTest = false) =>
    fetchApi<Array<{
      id: number; symbol: string; side: string; entry_price: number;
      exit_price: number; realized_pnl: number; close_reason: string;
      closed_at: string | null; is_test: boolean;
    }>>(`/api/trades?limit=${limit}&include_test=${includeTest}`),
  tradesBulkMarkTest: (req: { trade_ids?: number[]; date_before?: string; is_test: boolean }) =>
    fetchApi<{ updated: number }>("/api/trades/bulk-mark-test", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  tradesBulkDelete: (trade_ids: number[]) =>
    fetchApi<{ deleted: number }>("/api/trades/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trade_ids }),
    }),
  settings: () => fetchApi<AppSettings>("/api/settings/"),
  updateSettings: (data: Partial<AppSettings>) =>
    fetchApi<SettingsUpdateResponse>("/api/settings/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),

  // Data collection
  symbols: () => fetchApi<SymbolsResponse>("/api/data/symbols"),
  dataStatus: () => fetchApi<DataStatusItem[]>("/api/data/status"),
  downloadProgress: () =>
    fetchApi<Record<string, DownloadProgress>>("/api/data/download/progress"),
  cancelDownloads: () =>
    fetchApi<{ cancelled: boolean; count: number }>("/api/data/download/cancel", { method: "POST" }),

  download: (symbol: string, timeframe: string = "30", startDate?: string) =>
    fetchApi<{ status: string; symbol: string }>("/api/data/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, timeframe, ...(startDate ? { start_date: startDate } : {}) }),
    }),

  downloadBatch: (symbols: string[], timeframe: string = "30") =>
    fetchApi<{ status: string; queued: number }>("/api/data/download/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols, timeframe }),
    }),

  // Watchlist
  watchlist: () => fetchApi<WatchlistItem[]>("/api/data/watchlist"),
  addToWatchlist: (symbols: string[]) =>
    fetchApi<{ added: number }>("/api/data/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbols }),
    }),
  removeFromWatchlist: (symbol: string) =>
    fetchApi<{ removed: string }>(`/api/data/watchlist/${symbol}`, {
      method: "DELETE",
    }),
  downloadWatchlist: (timeframe: string = "30") =>
    fetchApi<{ status: string; queued: number }>(
      `/api/data/watchlist/download-all?timeframe=${timeframe}`,
      { method: "POST" }
    ),

  // Strategies
  strategies: () => fetchApi<StrategyItem[]>("/api/strategies/"),
  strategyDetail: (id: number) => fetchApi<StrategyDetail>(`/api/strategies/${id}`),
  deleteStrategy: (id: number) =>
    fetchApi<{ deleted: boolean }>(`/api/strategies/${id}`, { method: "DELETE" }),
  updateStrategyCategory: (id: number, category: string | null) =>
    fetchApi<{ id: number; category: string | null }>(`/api/strategies/${id}/category`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category }),
    }),
  createPythonStrategy: (
    name: string, description: string, python_source: string,
    pine_source: string = "", parameters?: ParameterDefinition[],
  ) =>
    fetchApi<{ id: number; name: string }>("/api/strategies/python", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description, python_source, pine_source, parameters }),
    }),
  updateStrategySource: (
    id: number, python_source: string, re_extract_params: boolean = true,
  ) =>
    fetchApi<{ id: number; name: string; param_count: number; updated_at: string }>(
      `/api/strategies/${id}/source`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ python_source, re_extract_params }),
      }
    ),
  // Backtests
  backtestRuns: (opts?: { singleOnly?: boolean }) =>
    fetchApi<BacktestRunItem[]>(`/api/backtest/runs${opts?.singleOnly ? "?single_only=true" : ""}`),
  backtestRunDetail: (id: number) => fetchApi<BacktestRunDetail>(`/api/backtest/runs/${id}`),
  backtestRunTrades: (id: number) => fetchApi<BacktestTradeItem[]>(`/api/backtest/runs/${id}/trades`),
  deleteBacktestRun: (id: number) =>
    fetchApi<{ deleted: boolean; id: number }>(`/api/backtest/runs/${id}`, { method: "DELETE" }),
  backtestProgress: (id: number) => fetchApi<BacktestProgress>(`/api/backtest/progress/${id}`),
  startBacktest: (
    strategy_id: number, symbol: string, timeframe: string,
    direction_filter: string = "both",
    initial_capital: number = 10000, commission_pct: number = 0.055,
    leverage: number = 1, min_avg_daily_volume: number = 0,
    config_id?: number, strategy_inputs?: Record<string, number | boolean | string>,
    risk_pct: number = 0,
    start_date?: string, end_date?: string,
    slippage_mode: string = "pct", slippage_value: number = 0,
    risk_mode: string = "pct", risk_value: number = 0,
    martingale_enabled: boolean = false, martingale_type: string = "anti",
    martingale_multiplier: number = 2, martingale_max_streak: number = 3,
    vol_filter_enabled: boolean = false, vol_filter_atr_ceiling: number = 0,
    vol_filter_atr_length: number = 14,
    max_pyramiding: number = 1,
    max_avg_daily_volume: number = 0,
    process_orders_on_close: boolean = false,
    enforce_bybit_limits: boolean = false,
  ) =>
    fetchApi<{ run_id: number; status: string; candles: number }>("/api/backtest/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        strategy_id, symbol, timeframe, direction_filter,
        initial_capital, commission_pct, leverage, min_avg_daily_volume,
        max_avg_daily_volume,
        config_id, strategy_inputs, risk_pct,
        start_date: start_date || undefined,
        end_date: end_date || undefined,
        slippage_mode, slippage_value,
        risk_mode, risk_value,
        martingale_enabled, martingale_type, martingale_multiplier, martingale_max_streak,
        vol_filter_enabled, vol_filter_atr_ceiling, vol_filter_atr_length,
        max_pyramiding,
        process_orders_on_close,
        enforce_bybit_limits,
      }),
    }),

  // Batch backtests
  startBatchBacktest: (
    strategy_id: number, symbols: string[], timeframe: string,
    direction_filter: string = "both",
    initial_capital: number = 10000, commission_pct: number = 0.055,
    leverage: number = 1, min_avg_daily_volume: number = 0,
    config_id?: number, strategy_inputs?: Record<string, number | boolean | string>,
    risk_pct: number = 0, mode: string = "individual",
    slippage_mode: string = "pct", slippage_value: number = 0,
    risk_mode: string = "pct", risk_value: number = 0,
    martingale_enabled: boolean = false, martingale_type: string = "anti",
    martingale_multiplier: number = 2, martingale_max_streak: number = 3,
    vol_filter_enabled: boolean = false, vol_filter_atr_ceiling: number = 0,
    vol_filter_atr_length: number = 14,
    max_pyramiding: number = 1,
    min_positions: number = 0,
    max_avg_daily_volume: number = 0,
    normalized: boolean = true,
    include_costs: boolean = false,
    funding_rate_daily_pct: number = 0,
    spread_pct: number = 0,
    prefilter_enabled: boolean = false,
    prefilter_mode: string = "trend",
    prefilter_hurst_min: number = 0.50,
    prefilter_ter_percentile: number = 25,
    prefilter_hurst_max: number = 0.50,
    prefilter_vr_max: number = 1.0,
    prefilter_min_crossing_rate: number = 0.05,
    min_data_date?: string,
    process_orders_on_close: boolean = false,
    enforce_bybit_limits: boolean = false,
    expected_live_size: number = 0,
    max_margin_per_trade: number = 0,
    margin_filter_tolerance: number = 10,
    bybit_reject_pct: number = 0,
  ) =>
    fetchApi<{ batch_id: number; batch_ids?: number[]; status: string; total_symbols: number; skipped_low_adv?: number; skipped_data_date?: number; prefiltered?: number }>("/api/backtest/batch/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        strategy_id, symbols, timeframe, direction_filter,
        initial_capital, commission_pct, leverage, min_avg_daily_volume,
        max_avg_daily_volume,
        config_id, strategy_inputs, risk_pct, mode,
        slippage_mode, slippage_value,
        risk_mode, risk_value,
        martingale_enabled, martingale_type, martingale_multiplier, martingale_max_streak,
        vol_filter_enabled, vol_filter_atr_ceiling, vol_filter_atr_length,
        max_pyramiding, min_positions, normalized, include_costs,
        funding_rate_daily_pct, spread_pct,
        prefilter_enabled, prefilter_mode,
        prefilter_hurst_min, prefilter_ter_percentile,
        prefilter_hurst_max, prefilter_vr_max, prefilter_min_crossing_rate,
        min_data_date: min_data_date || undefined,
        process_orders_on_close,
        enforce_bybit_limits,
        expected_live_size,
        max_margin_per_trade,
        margin_filter_tolerance,
        bybit_reject_pct,
      }),
    }),
  batchProgress: (id: number) =>
    fetchApi<BatchProgress>(`/api/backtest/batch/progress/${id}`),
  batchRuns: (opts?: { includeArchived?: boolean }) =>
    fetchApi<BatchBacktestRunItem[]>(`/api/backtest/batch/runs${opts?.includeArchived ? "?include_archived=true" : ""}`),
  batchRunDetail: (id: number, opts?: { dateTo?: string }) => {
    const params = new URLSearchParams();
    if (opts?.dateTo) params.set("date_to", opts.dateTo);
    const qs = params.toString();
    return fetchApi<BatchResultSummary>(`/api/backtest/batch/runs/${id}${qs ? `?${qs}` : ""}`);
  },
  cancelBatch: (id: number) =>
    fetchApi<{ cancelled: boolean; id: number }>(`/api/backtest/batch/runs/${id}/cancel`, { method: "POST" }),
  cancelAllBatches: () =>
    fetchApi<{ cancelled: boolean; ids: number[]; count: number }>("/api/backtest/batch/cancel-all", { method: "POST" }),
  archiveBatch: (id: number) =>
    fetchApi<{ id: number; is_archived: boolean }>(`/api/backtest/batch/runs/${id}/archive`, { method: "PATCH" }),
  compactBatch: (id: number) =>
    fetchApi<{ id: number; is_compacted: boolean; runs_deleted: number; trades_deleted: number }>(
      `/api/backtest/batch/runs/${id}/compact`, { method: "POST" },
    ),
  deleteBatchRun: (id: number) =>
    fetchApi<{ deleted: boolean; id: number }>(`/api/backtest/batch/runs/${id}`, { method: "DELETE" }),
  deleteAllBatchRuns: () =>
    fetchApi<{ deleted: boolean; batches_deleted: number }>("/api/backtest/batch/runs", { method: "DELETE" }),
  cleanupDuplicateBatches: () =>
    fetchApi<{ duplicates_removed: number; child_runs_removed: number }>(
      "/api/backtest/batch/runs/cleanup-duplicates", { method: "POST" },
    ),
  updateBatchNotes: (batchId: number, name?: string, notes?: string, personal_grade?: string) =>
    fetchApi<BatchBacktestRunItem>(`/api/backtest/batch/runs/${batchId}/notes`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, notes, personal_grade }),
    }),
  mergeBatches: (batchIds: number[], name?: string) =>
    fetchApi<{ batch_id: number; name: string; source_batch_ids: number[]; total_symbols: number; status: string }>("/api/backtest/batch/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_ids: batchIds, name }),
    }),
  refreshBatch: (batchId: number) =>
    fetchApi<{ batch_id: number; status: string; total_symbols: number; refreshed_from_id: number }>(
      `/api/backtest/batch/runs/${batchId}/refresh`, { method: "POST" },
    ),
  repairStructuralMetrics: (batchId: number) =>
    fetchApi<{ batch_id: number; repaired: number; skipped: number; total: number }>(
      `/api/backtest/batch/runs/${batchId}/repair-structural`, { method: "POST" },
    ),
  repairAllStructuralMetrics: () =>
    fetchApi<{ batches_processed: number; total_repaired: number; total_skipped: number }>(
      "/api/backtest/batch/repair-all-structural", { method: "POST" },
    ),

  // Strategy parameters & configs
  strategyParams: (id: number) =>
    fetchApi<ParameterDefinition[]>(`/api/strategies/${id}/parameters`),
  updateStrategyParams: (id: number, parameters: ParameterDefinition[]) =>
    fetchApi<{ updated: boolean }>(`/api/strategies/${id}/parameters`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parameters }),
    }),
  strategyConfigs: (id: number) =>
    fetchApi<StrategyConfigItem[]>(`/api/strategies/${id}/configs`),
  createStrategyConfig: (
    id: number, name: string, description: string,
    parameter_values: Record<string, number | boolean | string>,
    tag?: string | null,
  ) =>
    fetchApi<StrategyConfigItem>(`/api/strategies/${id}/configs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description, parameter_values, tag: tag ?? null }),
    }),
  updateStrategyConfig: (
    strategyId: number, configId: number,
    payload: { name?: string; description?: string; tag?: string | null; parameter_values?: Record<string, number | boolean | string> },
  ) =>
    fetchApi<StrategyConfigItem>(`/api/strategies/${strategyId}/configs/${configId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteStrategyConfig: (strategyId: number, configId: number) =>
    fetchApi<{ deleted: boolean }>(`/api/strategies/${strategyId}/configs/${configId}`, {
      method: "DELETE",
    }),

  // Analysis
  correlation: (batch_id: number, corr_method: string = "standard", min_trades = 0, min_pf = 0, min_wr = 0, date_to?: string) =>
    fetchApi<CorrelationResult>("/api/analysis/correlation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_id, corr_method, min_trades, min_pf, min_wr, date_to }),
    }),
  optimize: (
    batch_id: number, n_assets: number = 5,
    objective: string = "hybrid", corr_method: string = "standard",
    n_portfolios: number = 3, date_to?: string,
    min_trades: number = 0, min_pf: number = 0, min_wr: number = 0,
    extra_slippage_pct: number = 0,
    opts?: {
      seed_metrics?: string[]; max_seeds?: number; seed_weights?: Record<string, number>;
      prioritize_stability?: boolean; date_from?: string;
      tc_low_threshold?: number; tc_max_low?: number; tc_high_threshold?: number; tc_min_high?: number;
      tc_longs?: { low_threshold: number; max_low: number; high_threshold: number; min_high: number };
      tc_shorts?: { low_threshold: number; max_low: number; high_threshold: number; min_high: number };
      benchmark_enabled?: boolean; benchmark_source?: string; benchmark_split?: string;
      benchmark_threshold?: number; benchmark_elite_target?: number; benchmark_exclude_fragile?: boolean;
      benchmark_consistency_blend?: number; benchmark_fragility_blend?: number; benchmark_wr_harmony_blend?: number;
      // Post-hoc trading costs
      extra_commission_pct?: number; funding_daily_pct?: number; spread_pct?: number;
      // Event-based L1 scoring
      event_mode?: boolean; n_trades?: number; staleness_days?: number;
      div_algo?: string;
      category_filter?: string[];
      max_overlap?: number;
      // Risk profile
      risk_profile_mode?: string;
      risk_profile_value?: number;
      risk_profile_initial_capital?: number;
      risk_profile_multiplier?: number;
      risk_profile_max_streak?: number;
      risk_profile_kelly_n?: number;
      risk_profile_kelly_warmup?: number;
      risk_profile_martingale_compound?: string;
      risk_profile_auto_asset_configs?: string;
      // Pre-filter (seed_ranker v2)
      prefilter_enabled?: boolean;
      prefilter_mode?: string;
      prefilter_hurst_min?: number;
      prefilter_ter_percentile?: number;
      prefilter_hurst_max?: number;
      prefilter_vr_max?: number;
      prefilter_min_crossing_rate?: number;
      // DSR benchmark gate
      benchmark_dsr_gate?: boolean;
      // AC post-filter
      ac_postfilter?: boolean;
      ac_postfilter_type?: string;
      ac_lag?: number;
      // Recency
      recency_enabled?: boolean;
      recency_mode?: string;
      recency_batch_size?: number;
      recency_boost_factor?: number;
      // Boundary clipping
      clip_is?: boolean;
      // Session Time Mask
      session_mask_id?: number;
    },
    onProgress?: (step: number, totalSteps: number, label: string) => void,
    onTaskId?: (taskId: string) => void,
  ) => _submitAndPoll<OptimizerResult>("/api/analysis/optimize", {
      batch_id, n_assets, objective, corr_method, n_portfolios, date_to,
      min_trades, min_pf, min_wr, extra_slippage_pct,
      ...(opts ?? {}),
    }, onProgress, undefined, onTaskId),
  optimizeLS: (
    batch_id: number, n_longs: number = 3, n_shorts: number = 3,
    within_objective: string = "uncorrelated", combined_objective: string = "stability",
    corr_method: string = "standard", n_portfolios: number = 3, date_to?: string,
    min_trades: number = 0, min_pf: number = 0, min_wr: number = 0,
    extra_slippage_pct: number = 0,
    opts?: {
      seed_metrics?: string[]; max_seeds?: number; seed_weights?: Record<string, number>;
      prioritize_stability?: boolean; date_from?: string;
      tc_low_threshold?: number; tc_max_low?: number; tc_high_threshold?: number; tc_min_high?: number;
      tc_longs?: { low_threshold: number; max_low: number; high_threshold: number; min_high: number };
      tc_shorts?: { low_threshold: number; max_low: number; high_threshold: number; min_high: number };
      benchmark_enabled?: boolean; benchmark_source?: string; benchmark_split?: string;
      benchmark_threshold?: number; benchmark_elite_target?: number; benchmark_exclude_fragile?: boolean;
      benchmark_consistency_blend?: number; benchmark_fragility_blend?: number; benchmark_wr_harmony_blend?: number;
      // Post-hoc trading costs
      extra_commission_pct?: number; funding_daily_pct?: number; spread_pct?: number;
      // Event-based L1 scoring
      event_mode?: boolean; n_trades?: number; staleness_days?: number;
      div_algo?: string;
      category_filter?: string[];
      max_overlap?: number;
      // Risk profile
      risk_profile_mode?: string;
      risk_profile_value?: number;
      risk_profile_initial_capital?: number;
      risk_profile_multiplier?: number;
      risk_profile_max_streak?: number;
      risk_profile_kelly_n?: number;
      risk_profile_kelly_warmup?: number;
      risk_profile_martingale_compound?: string;
      risk_profile_auto_asset_configs?: string;
      // Pre-filter (seed_ranker v2)
      prefilter_enabled?: boolean;
      prefilter_mode?: string;
      prefilter_hurst_min?: number;
      prefilter_ter_percentile?: number;
      prefilter_hurst_max?: number;
      prefilter_vr_max?: number;
      prefilter_min_crossing_rate?: number;
      // DSR benchmark gate
      benchmark_dsr_gate?: boolean;
      // AC post-filter
      ac_postfilter?: boolean;
      ac_postfilter_type?: string;
      ac_lag?: number;
      // Recency
      recency_enabled?: boolean;
      recency_mode?: string;
      recency_batch_size?: number;
      recency_boost_factor?: number;
      // Boundary clipping
      clip_is?: boolean;
      // Session Time Mask
      session_mask_id?: number;
    },
    onProgress?: (step: number, totalSteps: number, label: string) => void,
    onTaskId?: (taskId: string) => void,
  ) => _submitAndPoll<LSOptimizerResult>("/api/analysis/optimize-ls", {
      batch_id, n_longs, n_shorts,
      within_objective, combined_objective, corr_method, n_portfolios, date_to,
      min_trades, min_pf, min_wr, extra_slippage_pct,
      ...(opts ?? {}),
    }, onProgress, undefined, onTaskId),
  recalculateCandidate: (
    batch_id: number, assets: string[], date_to?: string, date_from?: string,
  ) =>
    fetchApi<{ assets: string[]; metrics: CandidateMetrics; equity_curve?: number[] }>(
      "/api/analysis/recalculate-candidate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_id, assets, date_to, date_from }),
    },
    ),
  monteCarlo: (
    batch_id?: number, run_id?: number, n_simulations: number = 1000,
    ruin_threshold_pct: number = 50, date_to?: string, min_trades?: number,
    portfolio_id?: number, exclude_top_n?: number, pf_min?: number, pf_max?: number,
    date_from?: string, wr_min?: number,
  ) =>
    fetchApi<MonteCarloResult>("/api/analysis/monte-carlo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_id, run_id, n_simulations, ruin_threshold_pct, date_to, date_from, min_trades, portfolio_id, exclude_top_n, pf_min, pf_max, wr_min }),
    }),
  fatTailBootstrap: (
    portfolio_id: number, n_simulations: number = 1000,
    outlier_pct: number = 5, with_replacement: boolean = true,
    date_from?: string, date_to?: string,
  ) =>
    fetchApi<FatTailBootstrapResult>("/api/analysis/fat-tail-bootstrap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_id, n_simulations, outlier_pct, with_replacement, date_from, date_to }),
    }),
  randomExit: (
    portfolio_id: number, n_iterations: number = 500,
    exit_mode: string = "random_bars", max_hold_bars: number = 50,
    date_from?: string, date_to?: string,
  ) =>
    fetchApi<RandomExitResult>("/api/analysis/random-exit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_id, n_iterations, exit_mode, max_hold_bars, date_from, date_to }),
    }),
  compareStrategies: (batch_ids: number[]) =>
    fetchApi<CompareResult>("/api/analysis/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_ids }),
    }),
  streakStudy: (batch_ids: number[], date_to?: string, date_from?: string) =>
    fetchApi<StreakStudyResult>("/api/analysis/streak-study", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_ids, date_to, date_from }),
    }),
  autoRiskProfile: (params: {
    batch_ids: number[];
    date_to?: string;
    date_from?: string;
    base_risk?: number;
    base_risk_mart?: number;
    multiplier?: number;
    initial_capital?: number;
    aggression?: string;
    min_confidence?: number;
    strategy_hint?: string;
  }) =>
    fetchApi<{
      dominant_mode: string;
      per_asset: Record<string, { mode: string; max_streak: number; start_after: number; reset_after: number; confidence: number; reason: string; derivation_method?: string; batch_id?: number; strategy_name?: string }>;
      config: { mode: string; value: number; valueFixed: number; valueMartingale: number; initialCapital: number; multiplier: number; maxStreak: number; martingaleStartAfter: number; antiMartingaleResetAfter: number };
    }>("/api/analysis/auto-risk-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }),

  // Session Intelligence — time-of-day / day-of-week edge analysis
  sessionIntel: (
    batchId: number,
    params: {
      start_date: string;
      end_date: string;
      granularity?: "hour" | "half_hour";
      group_by?: "entry" | "exit";
      side_filter?: "both" | "long" | "short";
      min_trades?: number;
    },
  ) => {
    const qs = new URLSearchParams({
      start_date: params.start_date,
      end_date: params.end_date,
      ...(params.granularity ? { granularity: params.granularity } : {}),
      ...(params.group_by ? { group_by: params.group_by } : {}),
      ...(params.side_filter ? { side_filter: params.side_filter } : {}),
      ...(params.min_trades ? { min_trades: String(params.min_trades) } : {}),
    });
    return fetchApi<SessionIntelResult>(`/api/analysis/session-intel/${batchId}?${qs}`);
  },

  // Batch Groups — folder-style organization
  batchGroups: () => fetchApi<BatchGroupItem[]>("/api/batch-groups"),
  batchGroupCreate: (params: {
    name: string; description?: string; sort_order?: number;
    timeframe?: string; bybit_limit?: number; max_margin_per_trade?: number;
    min_adv?: number; slippage_pct?: number; fee_pct?: number; pnl_multiplier?: number;
  }) =>
    fetchApi<BatchGroupItem>("/api/batch-groups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }),
  batchGroupUpdate: (id: number, params: {
    name?: string; description?: string; sort_order?: number;
    timeframe?: string; bybit_limit?: number; max_margin_per_trade?: number;
    min_adv?: number; slippage_pct?: number; fee_pct?: number; pnl_multiplier?: number;
  }) =>
    fetchApi<BatchGroupItem>(`/api/batch-groups/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }),
  batchGroupDelete: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/batch-groups/${id}`, { method: "DELETE" }),
  batchGroupAssign: (params: { batch_ids: number[]; group_id: number | null }) =>
    fetchApi<{ ok: boolean; updated: number }>("/api/batch-groups/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }),

  // Session Time Masks — CRUD + helpers
  sessionMasks: () => fetchApi<SessionTimeMask[]>("/api/session-masks"),
  sessionMaskDetail: (id: number) => fetchApi<SessionTimeMask>(`/api/session-masks/${id}`),
  sessionMaskCreate: (params: {
    name: string; description?: string; mode?: string; cells: SessionTimeMaskCell[];
  }) =>
    fetchApi<SessionTimeMask>("/api/session-masks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }),
  sessionMaskUpdate: (id: number, params: {
    name?: string; description?: string; mode?: string; cells?: SessionTimeMaskCell[];
  }) =>
    fetchApi<SessionTimeMask>(`/api/session-masks/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }),
  sessionMaskDelete: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/session-masks/${id}`, { method: "DELETE" }),
  sessionMaskFromHeatmap: (params: {
    name: string; description?: string; batch_id: number;
    start_date: string; end_date: string;
    min_profitable_symbol_pct?: number; min_trades_per_cell?: number; min_profit_factor?: number;
  }) =>
    fetchApi<SessionTimeMask>("/api/session-masks/from-heatmap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }),
  sessionMaskPreview: (params: {
    batch_id: number; mask_id: number; date_to?: string; date_from?: string; n_free_params?: number;
  }) =>
    fetchApi<MaskPreviewResult>("/api/session-masks/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }),

  // Portfolios (full CRUD)
  portfolios: () => fetchApi<PortfolioItem[]>("/api/portfolios/"),
  portfolioDetail: (id: number) => fetchApi<PortfolioDetail>(`/api/portfolios/${id}`),
  savePortfolio: (
    name: string, assets: { symbol: string; side: string; strategy_name?: string; batch_id?: number; config_name?: string }[],
    batch_id?: number, optimizer_settings?: Record<string, unknown>,
    metrics_snapshot?: Record<string, number | null | undefined>,
    extra?: { optimization_end_date?: string; batch_ids?: number[]; portfolio_source?: string; is_start_date?: string; optimization_timing?: Record<string, number> },
  ) =>
    fetchApi<PortfolioItem>("/api/portfolios/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, assets, batch_id, optimizer_settings, metrics_snapshot, ...extra }),
    }),
  updatePortfolio: (id: number, payload: { name?: string; is_active?: boolean; description?: string }) =>
    fetchApi<PortfolioItem>(`/api/portfolios/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deletePortfolio: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/portfolios/${id}`, { method: "DELETE" }),
  archivePortfolio: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/portfolios/${id}/archive`, { method: "PATCH" }),
  unarchivePortfolio: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/portfolios/${id}/unarchive`, { method: "PATCH" }),

  // Portfolio Hub — promote, refresh, deactivate
  portfoliosByType: (type: string) =>
    fetchApi<PortfolioItem[]>(`/api/portfolios/?type=${type}`),
  promotePortfolio: (id: number, portfolioType: "paper" | "real") =>
    fetchApi<PortfolioItem>(`/api/portfolios/${id}/promote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_type: portfolioType }),
    }),
  paperOverview: (sparklinePoints: number = 30) =>
    fetchApi<{
      portfolios: {
        id: number;
        name: string;
        mode: string;
        portfolio_type: string;
        activated_at: string | null;
        last_refreshed_at: string | null;
        trades_count: number;
        net_pnl: number;
        win_rate: number;
        avg_trade_pnl: number;
        sortino: number | null;
        profit_factor: number | null;
        max_dd: number | null;
        sparkline: { t: string; pnl: number }[];
        needs_refresh: boolean;
        current_month: { trades: number; pnl: number; win_rate: number };
        prev_month: { trades: number; pnl: number; win_rate: number };
        current_month_label: string | null;
        prev_month_label: string | null;
        engine: {
          trades: number;
          pnl: number;
          win_rate: number;
          first_at: string | null;
          last_at: string | null;
        };
      }[];
      sparkline_points: number;
    }>(`/api/portfolios/paper-overview?sparkline_points=${sparklinePoints}`),
  refreshPortfolio: (id: number) =>
    fetchApi<{ task_id: string }>(`/api/portfolios/${id}/refresh`, { method: "POST" }),
  refreshStatus: (taskId: string) =>
    fetchApi<{ status: string; current_asset?: string; total_assets?: number; live_metrics?: Record<string, number> }>(
      `/api/portfolios/refresh-status/${taskId}`,
    ),
  deactivatePortfolio: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/portfolios/${id}/deactivate`, { method: "POST" }),

  // Backfill historical basket events for hybrid portfolios — fills the
  // gap between OOS end and first nightly refresh by walking Phase 2
  // grading day-by-day. See backend/scripts/backfill_hybrid_basket_events.py.
  backfillBasketPlan: (id: number) =>
    fetchApi<{
      portfolio_id: number;
      name?: string;
      mode?: string;
      is_active?: boolean;
      ok: boolean;
      oos_end?: string;
      first_existing_event?: string | null;
      gap_start?: string;
      gap_end?: string;
      total_days_planned: number;
      estimated_minutes?: number;
      reason?: string;
    }>(`/api/portfolios/${id}/backfill-basket-plan`),
  backfillBasketHistory: (id: number) =>
    fetchApi<{ task_id: string; portfolio_id: number; status: string }>(
      `/api/portfolios/${id}/backfill-basket-history`,
      { method: "POST" },
    ),
  backfillStatus: (taskId: string) =>
    fetchApi<{
      status: string; // "starting" | "running" | "completed" | "error"
      portfolio_id?: number;
      started_at?: string;
      ended_at?: string;
      days_done?: number;
      days_total?: number;
      current_day?: string | null;
      last_day_ok?: boolean;
      last_day_reason?: string | null;
      error?: string;
      result?: {
        portfolio_id: number;
        name?: string;
        oos_end?: string;
        first_existing_event?: string | null;
        gap_start?: string;
        gap_end?: string;
        total_days_planned: number;
        days_succeeded: number;
        days_failed: number;
      };
    }>(`/api/portfolios/backfill-status/${taskId}`),

  // Exchange
  exchangeStatus: () =>
    fetchApi<{ connected: boolean; testnet: boolean; error?: string }>("/api/exchange/status"),
  exchangeBalance: () =>
    fetchApi<{ total_equity: number; available: number; currencies: Record<string, number>[] }>("/api/exchange/balance"),
  exchangePositions: () =>
    fetchApi<{ positions: Record<string, unknown>[] }>("/api/exchange/positions"),

  // TV Strategy Configs
  tvStrategies: () =>
    fetchApi<{ strategies: TVStrategyListItem[] }>("/api/tv-strategies/list"),
  tvStrategyDetail: (filename: string) =>
    fetchApi<{ config: TVStrategyConfig; filename: string }>(`/api/tv-strategies/${encodeURIComponent(filename)}`),
  tvStrategyDefault: () =>
    fetchApi<{ config: TVStrategyConfig }>("/api/tv-strategies/default"),
  tvStrategySave: (config: TVStrategyConfig) =>
    fetchApi<{ filename: string; status: string }>("/api/tv-strategies/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    }),
  tvStrategyDelete: (filename: string) =>
    fetchApi<{ status: string }>(`/api/tv-strategies/${encodeURIComponent(filename)}`, { method: "DELETE" }),
  tvImportFolder: (folder_path: string, name?: string) =>
    fetchApi<{ filename: string; name: string; dir_mode: string; xlsx_count: number; status: string }>(
      "/api/tv-strategies/import-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_path, name: name || "" }),
    },
    ),

  // TV Import
  tvImportScan: (directory: string, force_side?: string, group_multi_tp: boolean = true) =>
    fetchApi<ScanResult>("/api/tv-import/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ directory, force_side, group_multi_tp }),
    }),

  // Dashboard Analysis
  dashboardMetrics: (req: DashboardAnalysisRequest) =>
    fetchApi<DashboardMetricsResult>("/api/dashboard/metrics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardEquityCurve: (req: DashboardAnalysisRequest) =>
    fetchApi<{ data: EquityCurvePoint[] }>("/api/dashboard/equity-curve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardMonthlyMatrix: (req: DashboardAnalysisRequest) =>
    fetchApi<MonthlyMatrixData>("/api/dashboard/monthly-matrix", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardAssetBreakdown: (req: DashboardAnalysisRequest) =>
    fetchApi<{ assets: AssetBreakdownItem[] }>("/api/dashboard/asset-breakdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardFullAnalysis: (req: DashboardAnalysisRequest) =>
    fetchApi<TVFullAnalysis>("/api/dashboard/full-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardWfe: (req: DashboardAnalysisRequest & { wfe_tf?: string; unseen_candles?: number; total_candles?: number }) =>
    fetchApi<{ assets: WFEAssetResult[] }>("/api/dashboard/wfe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardBenchmark: (req: DashboardAnalysisRequest & { split_method?: string; validation_threshold?: number; elite_target?: number }) =>
    fetchApi<{ assets: Record<string, BenchmarkAssetResult>; source_pf: number }>("/api/dashboard/benchmark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardBtcChange: (start: string, end: string) =>
    fetchApi<{ start: string; end: string; btc_change_pct: number | null }>(`/api/dashboard/btc-change?start=${start}&end=${end}`),
  dashboardSignificance: (req: DashboardAnalysisRequest) =>
    fetchApi<SignificanceResult>("/api/dashboard/significance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardAggregatedPnL: (req: DashboardAnalysisRequest, period: string = "W") =>
    fetchApi<{ bars: AggregatedPnLBar[] }>("/api/dashboard/aggregated-pnl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...req, period }),
    }),

  // Filter summary (quick asset counts + PnL + PF + WR for filter bar)
  dashboardFilterSummary: (req: DashboardAnalysisRequest) =>
    fetchApi<FilterSummaryResult>("/api/dashboard/filter-summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),

  // Health outlier detection
  dashboardHealthOutliers: (req: DashboardAnalysisRequest & {
    loss_threshold?: number;
    win_threshold?: number;
    loss_multiplier?: number;
    win_multiplier?: number;
  }) =>
    fetchApi<OutlierResult>("/api/dashboard/health/outliers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),

  // Batch Dashboard
  dashboardBatchOverview: (req: DashboardAnalysisRequest) =>
    fetchApi<BatchOverviewResult>("/api/dashboard/batch-overview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardBatchLSBreakdown: (req: DashboardAnalysisRequest) =>
    fetchApi<LSBreakdownResult>("/api/dashboard/batch-ls-breakdown", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  dashboardBatchSymbolTrades: (symbol: string, opts: { batch_id?: number; portfolio_id?: number }) =>
    fetchApi<BacktestTradeItem[]>("/api/dashboard/batch-symbol-trades", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...opts, symbol }),
    }),
  dashboardOOSCompare: (
    oos_date: string, batch_id?: number, run_id?: number,
    strategy_filename?: string, assets?: string[], side_filter?: string,
    batch_ids?: number[], min_trades?: number,
    clip_is?: boolean, clip_oos?: boolean, oos_end_date?: string,
    extraCosts?: { extra_slippage_pct?: number; extra_commission_pct?: number; funding_daily_pct?: number; spread_pct?: number },
    riskProfile?: {
      mode: string; value: number; initialCapital?: number;
      multiplier?: number; maxStreak?: number; compound?: string;
      scope?: string; countMode?: string; oosReset?: boolean;
      kellyN?: number; kellyWarmup?: number;
      startAfter?: number; resetAfter?: number;
      autoAssetConfigs?: string;
    },
    perBatchConfig?: { sizingMaster: string; batchConfigs: string },
  ) =>
    fetchApi<OOSCompareResult>("/api/dashboard/oos-compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        oos_date, batch_id, run_id, strategy_filename, assets, side_filter, batch_ids, min_trades, clip_is, clip_oos, oos_end_date,
        ...(extraCosts || {}),
        ...(riskProfile ? {
          risk_profile_mode: riskProfile.mode,
          risk_profile_value: riskProfile.value,
          risk_profile_initial_capital: riskProfile.initialCapital,
          risk_profile_multiplier: riskProfile.multiplier,
          risk_profile_max_streak: riskProfile.maxStreak,
          risk_profile_martingale_compound: riskProfile.compound,
          risk_profile_martingale_scope: riskProfile.scope,
          risk_profile_martingale_count_mode: riskProfile.countMode,
          risk_profile_martingale_oos_reset: riskProfile.oosReset,
          risk_profile_martingale_start_after: riskProfile.startAfter,
          risk_profile_anti_martingale_reset_after: riskProfile.resetAfter,
          risk_profile_kelly_n: riskProfile.kellyN,
          risk_profile_kelly_warmup: riskProfile.kellyWarmup,
          ...(riskProfile.autoAssetConfigs ? { risk_profile_auto_asset_configs: riskProfile.autoAssetConfigs } : {}),
        } : {}),
        ...(perBatchConfig ? {
          risk_profile_sizing_master: perBatchConfig.sizingMaster,
          risk_profile_batch_configs: perBatchConfig.batchConfigs,
        } : {}),
      }),
    }),

  // Portfolio Assets
  portfolioAssets: (id: number) =>
    fetchApi<{ assets: PortfolioAssetSummary[]; has_wfo_boundary: boolean; bybit_rejections?: Record<string, number> | null }>(`/api/portfolios/${id}/assets`),
  portfolioDataFreshness: (id: number) =>
    fetchApi<{ portfolio_id: number; all_fresh: boolean; timeframe: string; assets: { symbol: string; timeframe: string; latest_candle: string | null; candles_behind: number; is_fresh: boolean }[] }>(`/api/portfolios/${id}/data-freshness`),
  portfolioDDStatus: (id: number) =>
    fetchApi<{ in_drawdown: boolean; dd_days: number; dd_depth_usd: number; dd_depth_pct: number; peak_date: string | null; peak_equity: number; current_equity: number; oos_worst_dd_usd: number; oos_worst_dd_pct: number }>(`/api/portfolios/${id}/dd-status`),
  portfolioAssetTrades: (id: number, asset: string) =>
    fetchApi<{ trades: PortfolioAssetTrade[]; cost_summary?: CostSummary }>(`/api/portfolios/${id}/assets/${encodeURIComponent(asset)}/trades`),
  portfolioMonthTrades: (id: number, year: number, month: number, cutoffDate?: string, period: string = "oos") => {
    const params = new URLSearchParams({ year: String(year), month: String(month), period });
    if (cutoffDate) params.set("cutoff_date", cutoffDate);
    return fetchApi<MonthTradesResponse>(`/api/portfolios/${id}/month-trades?${params}`);
  },
  portfolioCorrelationStats: (id: number) =>
    fetchApi<{ stats: Record<string, number>; n_pairs: number }>(`/api/portfolios/${id}/correlation-stats`),
  portfolioBybitSizeCheck: (id: number, riskAmount: number) =>
    fetchApi<BybitSizeCheckResult>(`/api/portfolios/${id}/bybit-size-check?risk_amount=${riskAmount}`),

  // Portfolio Analysis
  portfolioFullMetrics: (id: number) =>
    fetchApi<PortfolioFullMetricsResult>(`/api/portfolios/${id}/full-metrics`),
  portfolioFullMetricsWfo: (id: number, riskAdjust: boolean = false) =>
    fetchApi<PortfolioFullMetricsResult>(
      `/api/portfolios/${id}/full-metrics-wfo${riskAdjust ? "?risk_adjust=1" : ""}`
    ),
  portfolioLiveMetrics: (id: number, mode: "real" | "paper" | "both" = "real") =>
    fetchApi<PortfolioFullMetricsResult & { trade_count: number; since: string | null; mode: string }>(
      `/api/portfolios/${id}/live-metrics?mode=${mode}`
    ),
  portfolioOOS: (id: number, split_pct: number = 70, clip_is?: boolean, clip_oos?: boolean) =>
    fetchApi<OOSAnalysisResult>(`/api/portfolios/${id}/oos`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ split_pct, clip_is, clip_oos }),
    }),
  portfolioOOSDate: (id: number, cutoff_date?: string, clip_is?: boolean, clip_oos?: boolean, oos_end_date?: string) =>
    fetchApi<OOSDateResult>(`/api/portfolios/${id}/oos-date`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cutoff_date: cutoff_date || null, clip_is, clip_oos, oos_end_date: oos_end_date || null }),
    }),
  portfolioMonthlyFiltered: (id: number, opts?: {
    cutoff_date?: string; batch_ids?: number[]; direction?: string;
  }) =>
    fetchApi<{
      is_matrix: Record<string, Record<string, { pnl: number; trades: number; win_rate: number }>>;
      oos_matrix: Record<string, Record<string, { pnl: number; trades: number; win_rate: number }>>;
      batches: { id: number; name: string; strategy: string }[];
    }>(`/api/portfolios/${id}/monthly-filtered`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opts || {}),
    }),
  portfolioSynergyAudit: (id: number, friction_pct: number = 0.20, use_oos_only: boolean = true) =>
    fetchApi<SynergyAuditResult>(`/api/portfolios/${id}/synergy-audit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ friction_pct, use_oos_only }),
    }),
  portfolioCompare: (portfolio_ids: number[]) =>
    fetchApi<{ portfolios: PortfolioCompareEntry[] }>("/api/portfolios/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_ids }),
    }),
  portfolioSwapCandidates: (id: number, remove_asset: string, filters?: {
    min_trades?: number; min_wr?: number; min_pf?: number;
    corr_method?: string; is_only?: boolean;
    use_seed_ranking?: boolean; max_pool_size?: number;
  }) =>
    fetchApi<{ candidates: SwapCandidate[]; is_only: boolean; method: string; seed_metrics: string[]; pool_size: number }>(
      `/api/portfolios/${id}/swap-candidates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remove_asset, ...filters }),
    }
    ),
  swapExecute: (id: number, remove_asset: string, add_asset: string, reason?: string, batch_id?: number | null, remove_batch_id?: number | null) =>
    fetchApi<{ status: string; removed: string; added: string; swap_history: SwapHistoryEntry[]; active_assets: string[] }>(
      `/api/portfolios/${id}/swap-execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ remove_asset, add_asset, reason: reason || "", batch_id: batch_id ?? undefined, remove_batch_id: remove_batch_id ?? undefined }),
    }
    ),
  portfolioLiveViews: (id: number) =>
    fetchApi<LiveViewsResult>(`/api/portfolios/${id}/live-views`, { method: "POST" }),
  // T-002 — Phase-2 nightly recorder feed (basket changes / no-change ticks / rotations).
  portfolioPhase2Events: (id: number, opts?: {
    limit?: number; offset?: number;
    event_type?: "hybrid_refresh" | "hybrid_rotation" | "phase2_evaluated_no_change";
  }) => {
    const qs = new URLSearchParams();
    if (opts?.limit !== undefined) qs.set("limit", String(opts.limit));
    if (opts?.offset !== undefined) qs.set("offset", String(opts.offset));
    if (opts?.event_type !== undefined) qs.set("event_type", opts.event_type);
    const tail = qs.toString() ? `?${qs.toString()}` : "";
    return fetchApi<Phase2EventsResponse>(`/api/portfolios/${id}/phase2-events${tail}`);
  },
  // T-003 Delta 8 — Unified history feed: manual swaps + phase-2 events merged
  // chronologically, newest first, with a per-row type discriminator.
  portfolioHistory: (id: number, opts?: {
    limit?: number; offset?: number;
    type?: PortfolioHistoryType;
  }) => {
    const qs = new URLSearchParams();
    if (opts?.limit !== undefined) qs.set("limit", String(opts.limit));
    if (opts?.offset !== undefined) qs.set("offset", String(opts.offset));
    if (opts?.type !== undefined) qs.set("type", opts.type);
    const tail = qs.toString() ? `?${qs.toString()}` : "";
    return fetchApi<PortfolioHistoryResponse>(`/api/portfolios/${id}/history${tail}`);
  },
  testPortfolio: (id: number, config: {
    initial_capital?: number; commission_pct?: number; leverage?: number;
    risk_mode?: string; risk_value?: number;
    slippage_pct?: number; oos_only?: boolean;
  }) =>
    fetchApi<{ task_id: string; status: string; total_symbols: number }>(`/api/portfolios/${id}/test-portfolio`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    }),
  testPortfolioProgress: (taskId: string) =>
    fetchApi<PortfolioTestProgress>(`/api/portfolios/test-progress/${taskId}`),
  cancelPortfolioTest: (taskId: string) =>
    fetchApi<{ cancelled: boolean; task_id: string }>(`/api/portfolios/test-progress/${taskId}/cancel`, { method: "POST" }),
  riskComparison: (id: number, config: {
    profiles: {
      name: string; mode: string; risk_value: number;
      initial_capital: number; multiplier?: number;
      max_streak?: number; kelly_fraction?: number;
    }[];
    oos_only?: boolean;
  }) =>
    fetchApi<{
      profiles: { name: string; mode: string; metrics: Record<string, number>; equity_curve: number[] }[];
      batch_risk_amount: number; is_normalized: boolean; trades_used: number;
    }>(`/api/portfolios/${id}/risk-comparison`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    }),
  getRiskProfile: (id: number) =>
    fetchApi<{
      portfolio_id: number;
      risk_profile: {
        mode?: string; initial_risk_usd?: number; initial_capital?: number;
        multiplier?: number; max_streak?: number; kelly_n?: number;
        kelly_warmup?: number; override_enabled?: boolean;
        tp1_pct?: number; tp2_pct?: number; risk_pct?: number;
      };
      batch_risk_amount: number;
      guardrails: {
        daily_loss_limit_pct: number; max_daily_loss_usd: number;
        max_positions_long: number; max_positions_short: number;
        max_positions_total: number; max_position_pct: number;
        margin_breathing_room_pct: number;
      };
      asset_groups?: { group_name: string; timeframe: string; batch_ids: number[]; asset_count: number }[];
    }>(`/api/portfolios/${id}/risk-profile`),
  updateRiskProfile: (id: number, update: {
    initial_risk_usd?: number;
    override_enabled?: boolean;
    group_sizing?: { batch_ids: number[]; risk_usd: number; max_margin?: number }[];
    max_position_pct?: number;
  }) =>
    fetchApi<{ portfolio_id: number; risk_profile: Record<string, unknown>; message: string }>(
      `/api/portfolios/${id}/risk-profile`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(update),
      },
    ),
  hidePortfolio: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/portfolios/${id}/hide`, { method: "PATCH" }),
  unhidePortfolio: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/portfolios/${id}/unhide`, { method: "PATCH" }),
  portfoliosHidden: () =>
    fetchApi<PortfolioItem[]>("/api/portfolios/?is_hidden=true"),
  portfoliosDeleted: () =>
    fetchApi<PortfolioItem[]>("/api/portfolios/?is_deleted=true"),
  restorePortfolio: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/portfolios/${id}/restore`, { method: "PATCH" }),
  permanentlyDeletePortfolio: (id: number) =>
    fetchApi<{ ok: boolean }>(`/api/portfolios/${id}/permanent`, { method: "DELETE" }),
  builderBatches: () =>
    fetchApi<{ batches: BuilderBatch[] }>("/api/portfolios/builder/batches"),
  builderBatchAssets: (batchId: number, direction: string = "both") =>
    fetchApi<{ assets: BuilderAsset[]; batch_name: string; strategy_name: string }>(
      `/api/portfolios/builder/batch-assets?batch_id=${batchId}&direction=${direction}`
    ),
  createPortfolioManual: (data: {
    name: string;
    batch_ids: number[];
    assets: { symbol: string; side: string; strategy_name: string; batch_id?: number; config_name?: string }[];
    is_start_date?: string;
    optimization_end_date?: string;
    portfolio_source: string;
    // Live-loop update semantics: "hybrid" = basket + weights refresh nightly
    // via Step-2; "non_hybrid" = basket frozen until OOS boundary. Omit to
    // let the backend auto-detect from optimizer_settings.hybrid_mode.
    mode?: "hybrid" | "non_hybrid";
    optimizer_settings?: Record<string, unknown>;
    metrics_snapshot?: Record<string, unknown>;
  }) =>
    fetchApi<PortfolioItem>("/api/portfolios/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),

  // Portfolio Health Monitor
  portfolioHealth: (portfolioId: number, opts?: {
    windows?: number[]; recent_days?: number; include_is?: boolean;
  }) =>
    fetchApi<PortfolioHealthResult>("/api/portfolios/portfolio-health", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_id: portfolioId, ...opts }),
    }),
  portfolioHealthOverview: (opts?: {
    portfolio_ids?: number[];
    include_saved?: boolean; include_paper?: boolean; include_real?: boolean;
  }) =>
    fetchApi<{ portfolios: PortfolioHealthSummary[]; total: number }>("/api/portfolios/portfolio-health-overview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opts || {}),
    }),

  // Cross-Strategy
  crossStrategyBuild: (
    batch_ids: number[], n_longs: number = 5, n_shorts: number = 5,
    within_objective: string = "uncorrelated", combined_objective: string = "stability",
    corr_method: string = "standard", n_portfolios: number = 3,
    opts?: {
      seed_metrics?: string[]; max_seeds?: number; prioritize_stability?: boolean;
      date_from?: string; date_to?: string; extra_slippage_pct?: number;
      extra_commission_pct?: number; funding_daily_pct?: number; spread_pct?: number;
      per_batch?: Record<string, { min_trades?: number; min_pf?: number; min_wr?: number; max_longs?: number; max_shorts?: number; event_mode?: boolean; n_trades?: number }>;
      tc_low_threshold?: number; tc_max_low?: number; tc_high_threshold?: number; tc_min_high?: number;
      portfolio_ids?: number[];
      split_mode?: boolean; n_assets?: number; objective?: string;
      // Event-based L1 scoring
      event_mode?: boolean; n_trades?: number; staleness_days?: number;
      div_algo?: string;
      pool_per_batch?: number;
      streamlit_logic?: boolean;
      // Recency
      recency_enabled?: boolean; recency_mode?: string; recency_batch_size?: number; recency_boost_factor?: number;
      // Benchmark
      benchmark_enabled?: boolean; benchmark_source?: string; benchmark_split?: string;
      benchmark_threshold?: number; benchmark_elite_target?: number; benchmark_exclude_fragile?: boolean;
      benchmark_consistency_blend?: number; benchmark_fragility_blend?: number; benchmark_wr_harmony_blend?: number;
      benchmark_dsr_gate?: boolean;
      category_filter?: string[];
      // Portfolio exclusion filter
      exclude_portfolio_ids?: number[];
      exclude_portfolio_mode?: string;
      exclude_portfolio_keep_pct?: number;
      exclude_portfolio_method?: string;
      max_overlap?: number;
      // Pre-filter (seed_ranker v2)
      prefilter_enabled?: boolean;
      prefilter_mode?: string;
      prefilter_hurst_min?: number;
      prefilter_ter_percentile?: number;
      prefilter_hurst_max?: number;
      prefilter_vr_max?: number;
      prefilter_min_crossing_rate?: number;
      // AC post-filter
      ac_postfilter?: boolean;
      ac_postfilter_type?: string;
      ac_lag?: number;
      // Risk profile
      risk_profile_mode?: string;
      risk_profile_value?: number;
      risk_profile_initial_capital?: number;
      risk_profile_multiplier?: number;
      risk_profile_max_streak?: number;
      risk_profile_kelly_n?: number;
      risk_profile_kelly_warmup?: number;
      risk_profile_martingale_compound?: string;
      risk_profile_auto_asset_configs?: string;
      // Advanced
      expand_greedy_pool?: boolean;
      seed_weights?: Record<string, number>;
      seed_prior_weight?: number;
      // Boundary clipping
      clip_is?: boolean;
      // Optimizer engine: "current" (principal), "fast" (batch-vectorized), "classic"
      optimizer_engine?: string;
      // Harmony gate (L1.5 / L2.5)
      harmony_enabled?: boolean;
      harmony_strength_l15?: number;
      harmony_strength_l25?: number;
      harmony_weights?: Record<string, number>;
      harmony_per_group?: Record<string, boolean>;
    },
    onProgress?: (step: number, totalSteps: number, label: string) => void,
    onTaskId?: (taskId: string) => void,
  ) => _submitAndPoll<CrossBuildResult>("/api/analysis/cross-strategy-build", {
      batch_ids, n_longs, n_shorts,
      within_objective, combined_objective, corr_method, n_portfolios,
      ...(opts ?? {}),
    }, onProgress, undefined, onTaskId),
  crossStrategyBuildManual: (params: {
    batch_ids: number[];
    portfolio_ids?: number[];
    allocations?: Record<string, { n_longs?: number; n_shorts?: number }>;
    l2_groups?: number[][];
    obj_layer1?: string; obj_layer2?: string; obj_layer3?: string;
    candidates_per_strategy?: number; n_final_strats?: number; n_portfolios?: number;
    corr_method?: string; seed_metrics?: string[]; max_seeds?: number;
    prioritize_stability?: boolean; date_from?: string; date_to?: string; extra_slippage_pct?: number;
    extra_commission_pct?: number; funding_daily_pct?: number; spread_pct?: number;
    per_batch?: Record<string, { min_trades?: number; min_pf?: number; min_wr?: number; max_longs?: number; max_shorts?: number; event_mode?: boolean; n_trades?: number }>;
    tc_low_threshold?: number; tc_max_low?: number; tc_high_threshold?: number; tc_min_high?: number;
    l1_quality_gate?: boolean; n_per_group?: number;
    // Event-based L1 scoring
    event_mode?: boolean; n_trades?: number; staleness_days?: number;
    div_algo?: string;
    category_filter?: string[];
    // Portfolio exclusion filter
    exclude_portfolio_ids?: number[];
    exclude_portfolio_mode?: string;
    exclude_portfolio_keep_pct?: number;
    exclude_portfolio_method?: string;
    max_overlap?: number;
    // Pre-filter (seed_ranker v2)
    prefilter_enabled?: boolean;
    prefilter_mode?: string;
    prefilter_hurst_min?: number;
    prefilter_ter_percentile?: number;
    prefilter_hurst_max?: number;
    prefilter_vr_max?: number;
    prefilter_min_crossing_rate?: number;
    // DSR benchmark gate
    benchmark_dsr_gate?: boolean;
    benchmark_enabled?: boolean; benchmark_source?: string; benchmark_split?: string;
    benchmark_threshold?: number; benchmark_elite_target?: number; benchmark_exclude_fragile?: boolean;
    benchmark_consistency_blend?: number; benchmark_fragility_blend?: number; benchmark_wr_harmony_blend?: number;
    // AC post-filter
    ac_postfilter?: boolean;
    ac_postfilter_type?: string;
    ac_lag?: number;
    // Recency
    recency_enabled?: boolean; recency_mode?: string; recency_batch_size?: number; recency_boost_factor?: number;
    // Risk profile
    risk_profile_mode?: string;
    risk_profile_value?: number;
    risk_profile_initial_capital?: number;
    risk_profile_multiplier?: number;
    risk_profile_max_streak?: number;
    risk_profile_kelly_n?: number;
    risk_profile_kelly_warmup?: number;
    risk_profile_martingale_compound?: string;
    // Advanced
    expand_greedy_pool?: boolean;
    seed_weights?: Record<string, number>;
    seed_prior_weight?: number;
    streamlit_logic?: boolean;
    // Boundary clipping
    clip_is?: boolean;
    // Optimizer engine: "current" (principal), "fast" (batch-vectorized), "classic"
    optimizer_engine?: string;
    // Pool mode
    pool_mode?: boolean;
    pool_per_batch?: number;
    l2_n_assets?: number;
    l2_n_variants?: number;
    l2_max_overlap?: number;
    l3_max_overlap?: number;
    l2_symbol_dedup?: string;
    l2_dedup_tiebreaker?: string;
    // Harmony gate (L1.5 / L2.5)
    harmony_enabled?: boolean;
    harmony_strength_l15?: number;
    harmony_strength_l25?: number;
    harmony_weights?: Record<string, number>;
    harmony_per_group?: Record<string, boolean>;
  }, onProgress?: (step: number, totalSteps: number, label: string) => void, onTaskId?: (taskId: string) => void) => _submitAndPoll<CrossBuildResult>("/api/analysis/cross-strategy-build-manual", params, onProgress, undefined, onTaskId),
  crossStrategyBuildDirectional: (params: {
    batch_ids: number[];
    portfolio_ids?: number[];
    l2_groups?: number[][];
    n_per_batch?: number;
    obj_l1?: string; obj_l2?: string; obj_l3?: string;
    k_l1?: number; k_l2?: number; k_l3?: number;
    corr_method?: string; seed_metrics?: string[]; max_seeds?: number;
    prioritize_stability?: boolean; date_from?: string; date_to?: string; extra_slippage_pct?: number;
    extra_commission_pct?: number; funding_daily_pct?: number; spread_pct?: number;
    per_batch?: Record<string, { min_trades?: number; min_pf?: number; min_wr?: number; max_longs?: number; max_shorts?: number; event_mode?: boolean; n_trades?: number }>;
    l1_quality_gate?: boolean; n_per_group?: number;
    tc_low_threshold?: number; tc_max_low?: number; tc_high_threshold?: number; tc_min_high?: number;
    // Event-based L1 scoring
    event_mode?: boolean; n_trades?: number; staleness_days?: number;
    div_algo?: string;
    category_filter?: string[];
    // Portfolio exclusion filter
    exclude_portfolio_ids?: number[];
    exclude_portfolio_mode?: string;
    exclude_portfolio_keep_pct?: number;
    exclude_portfolio_method?: string;
    max_overlap?: number;
    // Pre-filter (seed_ranker v2)
    prefilter_enabled?: boolean;
    prefilter_mode?: string;
    prefilter_hurst_min?: number;
    prefilter_ter_percentile?: number;
    prefilter_hurst_max?: number;
    prefilter_vr_max?: number;
    prefilter_min_crossing_rate?: number;
    // DSR benchmark gate
    benchmark_dsr_gate?: boolean;
    benchmark_enabled?: boolean; benchmark_source?: string; benchmark_split?: string;
    benchmark_threshold?: number; benchmark_elite_target?: number; benchmark_exclude_fragile?: boolean;
    benchmark_consistency_blend?: number; benchmark_fragility_blend?: number; benchmark_wr_harmony_blend?: number;
    // AC post-filter
    ac_postfilter?: boolean;
    ac_postfilter_type?: string;
    ac_lag?: number;
    // Recency
    recency_enabled?: boolean; recency_mode?: string; recency_batch_size?: number; recency_boost_factor?: number;
    // Risk profile
    risk_profile_mode?: string;
    risk_profile_value?: number;
    risk_profile_initial_capital?: number;
    risk_profile_multiplier?: number;
    risk_profile_max_streak?: number;
    risk_profile_kelly_n?: number;
    risk_profile_kelly_warmup?: number;
    risk_profile_martingale_compound?: string;
    // Advanced
    expand_greedy_pool?: boolean;
    seed_weights?: Record<string, number>;
    seed_prior_weight?: number;
    streamlit_logic?: boolean;
    // Boundary clipping
    clip_is?: boolean;
    // Optimizer engine: "current" (principal), "fast" (batch-vectorized), "classic"
    optimizer_engine?: string;
    // Harmony gate (L1.5 / L2.5)
    harmony_enabled?: boolean;
    harmony_strength_l15?: number;
    harmony_strength_l25?: number;
    harmony_weights?: Record<string, number>;
    harmony_per_group?: Record<string, boolean>;
  }, onProgress?: (step: number, totalSteps: number, label: string) => void, onTaskId?: (taskId: string) => void) => _submitAndPoll<CrossBuildResult>("/api/analysis/cross-strategy-build-directional", params, onProgress, undefined, onTaskId),
  crossStrategyCorrelation: (batch_ids: number[], corr_method: string = "standard", portfolio_ids: number[] = [], date_from?: string, date_to?: string) =>
    fetchApi<CorrelationResult>("/api/analysis/cross-strategy-correlation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_ids, corr_method, portfolio_ids, date_from, date_to }),
    }),

  // Harmony Analysis — standalone endpoint for manual mode
  harmonyAnalyze: (params: {
    batch_ids: number[];
    portfolio_ids?: number[];
    strength?: number;
    weights?: Record<string, number>;
    date_from?: string;
    date_to?: string;
    clip_is?: boolean;
  }) => fetchApi<{
    pairs: Array<{
      id_a: number; id_b: number;
      return_correlation: number; correlation_stability: number;
      drawdown_overlap: number; stress_correlation: number;
      complementarity_ratio: number; harmony_score: number;
      passes_gate: boolean;
    }>;
    mean_harmony: number;
    all_pass: boolean;
    failed_pairs: number[][];
    batch_names: Record<string, string>;
    batch_ids: number[];
  }>("/api/analysis/harmony-analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  }),

  // Smart Groups — pre-optimization family detection & L2 grouping
  smartGroups: (params: {
    batch_ids: number[];
    date_from?: string;
    date_to?: string;
    clip_is?: boolean;
    family_threshold?: number;
    max_groups?: number;
    rolling_window?: number;
    use_deflated?: boolean;
    base_haircut?: number;
    min_pf?: number;
    min_wr?: number;
    min_trades?: number;
    cv_folds?: number;
    // V2: IS holdout
    holdout_ratio?: number;
    holdout_weight?: number;
    // V2: Bootstrap
    skip_bootstrap?: boolean;
    bootstrap_n?: number;
    bootstrap_drop_pct?: number;
    // V2: Regime-weighted families
    regime_ref_days?: number;
    regime_bandwidth?: number;
    // V2: Trend tilt
    trend_strength?: string;
    trend_lookback?: number;
    trend_regime_pct?: number;
  }) => fetchApi<SmartGroupResult>("/api/analysis/smart-groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  }),

  // Batch Affinity Report — post-optimization analysis
  affinityReport: (params: {
    batch_ids: number[];
    portfolio_ids?: number[];
    date_from?: string;
    date_to?: string;
    clip_is?: boolean;
    include_oos?: boolean;
    oos1_months?: number;
    oos2_months?: number;
    weights?: Record<string, number>;
    pinned_anchors?: number[];
  }) => fetchApi<AffinityReportResult>("/api/analysis/affinity-report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  }),

  // Settings Recommender — Quick mode (synchronous) returns directly
  crossStrategyRecommendQuick: (params: Record<string, unknown>) =>
    fetchApi<RecommenderResult>("/api/analysis/cross-strategy-recommend-settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...params, mode: "quick" }),
    }),

  // Settings Recommender — Thorough mode (async, polls background task)
  // 72-hour timeout — large grids with many objectives can run very long
  crossStrategyRecommendThorough: (
    params: Record<string, unknown>,
    onProgress?: (step: number, totalSteps: number, label: string) => void,
    onTaskId?: (taskId: string) => void,
  ) => {
    return _submitAndPoll<RecommenderResult>("/api/analysis/cross-strategy-recommend-settings", {
      ...params, mode: "thorough",
    }, onProgress, 4320, onTaskId);
  },

  // Cancel a running optimization/recommender background task
  cancelOptimizeTask: (backendTaskId: string) =>
    fetchApi<{ cancelled: boolean; task_id: string }>(
      `/api/analysis/optimize/${backendTaskId}/cancel`,
      { method: "POST" },
    ),

  // Saved Runs — auto-saved optimization & recommender results
  savedRunsList: () =>
    fetchApi<SavedRunSummary[]>("/api/analysis/cross-strategy-saved-runs"),
  savedRunsLoad: (filename: string) =>
    fetchApi<SavedRunDetail>(`/api/analysis/cross-strategy-saved-runs/${encodeURIComponent(filename)}`),
  savedRunsDelete: (filename: string) =>
    fetchApi<{ deleted: boolean; filename: string }>(
      `/api/analysis/cross-strategy-saved-runs/${encodeURIComponent(filename)}`,
      { method: "DELETE" },
    ),
  savedRunsCleanup: (criteria: {
    min_batches?: number; min_candidates?: number; min_assets?: number; dry_run?: boolean;
  }) =>
    fetchApi<{
      flagged: { filename: string; type: string; saved_at: string; n_batches: number; n_candidates: number; n_assets: number }[];
      kept: number; deleted: number;
    }>("/api/analysis/cross-strategy-saved-runs/cleanup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(criteria),
    }),

  // Journal
  journalCalendarSummary: (year: number, month: number) =>
    fetchApi<CalendarDaySummary[]>(`/api/journal/calendar-summary?year=${year}&month=${month}`),
  journalList: (filters?: { status?: string; symbol?: string; strategy_id?: number; portfolio_id?: number; limit?: number; trade_date?: string; include_test?: boolean }) => {
    const params = new URLSearchParams();
    if (filters?.status) params.set("status", filters.status);
    if (filters?.symbol) params.set("symbol", filters.symbol);
    if (filters?.strategy_id) params.set("strategy_id", String(filters.strategy_id));
    if (filters?.portfolio_id) params.set("portfolio_id", String(filters.portfolio_id));
    if (filters?.limit) params.set("limit", String(filters.limit));
    if (filters?.trade_date) params.set("trade_date", filters.trade_date);
    if (filters?.include_test) params.set("include_test", "true");
    const qs = params.toString();
    return fetchApi<JournalTradeItem[]>(`/api/journal/list${qs ? `?${qs}` : ""}`);
  },
  journalStats: () => fetchApi<JournalStatsResult>("/api/journal/stats"),
  journalDetail: (id: number) => fetchApi<JournalTradeItem>(`/api/journal/${id}`),
  journalCreate: (trade: JournalTradeCreate) =>
    fetchApi<JournalTradeItem>("/api/journal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(trade),
    }),
  journalUpdate: (id: number, update: JournalTradeUpdatePayload) =>
    fetchApi<JournalTradeItem>(`/api/journal/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
  journalDelete: (id: number) =>
    fetchApi<{ deleted: boolean; id: number }>(`/api/journal/${id}`, { method: "DELETE" }),
  journalBulkMarkTest: (req: { trade_ids?: number[]; date_before?: string; date_after?: string; is_test: boolean }) =>
    fetchApi<{ updated: number; is_test: boolean }>("/api/journal/bulk-mark-test", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  journalBulkDelete: (trade_ids: number[]) =>
    fetchApi<{ deleted: number }>("/api/journal/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trade_ids }),
    }),
  journalLookupTrades: (req: { strategy_id: number; config_id?: number; symbol: string; timeframe?: string; auto_download?: boolean }) =>
    fetchApi<LookupTradesResult>("/api/journal/lookup-trades", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  journalDashboard: (filters?: { symbol?: string; side?: string; strategy_id?: number; portfolio_id?: number }) => {
    const params = new URLSearchParams();
    if (filters?.symbol) params.set("symbol", filters.symbol);
    if (filters?.side) params.set("side", filters.side);
    if (filters?.strategy_id) params.set("strategy_id", String(filters.strategy_id));
    if (filters?.portfolio_id) params.set("portfolio_id", String(filters.portfolio_id));
    const qs = params.toString();
    return fetchApi<TradingDashboardResult>(`/api/journal/dashboard${qs ? `?${qs}` : ""}`);
  },

  journalMonthlyRecap: (year: number, month: number) =>
    fetchApi<MonthlyRecapData>(`/api/journal/monthly-recap?year=${year}&month=${month}`),

  // Journal Notes (Personal Journal)
  journalNotesList: (year: number, month: number) =>
    fetchApi<JournalNoteItem[]>(`/api/journal-notes/list?year=${year}&month=${month}`),
  journalNotesCalendarSummary: (year: number, month: number) =>
    fetchApi<NoteCalendarSummary[]>(`/api/journal-notes/calendar-summary?year=${year}&month=${month}`),
  journalNoteGet: (id: number) =>
    fetchApi<JournalNoteItem>(`/api/journal-notes/detail/${id}`),
  journalNoteCreate: (note: JournalNoteCreate) =>
    fetchApi<JournalNoteItem>("/api/journal-notes/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(note),
    }),
  journalNoteUpdate: (id: number, update: JournalNoteUpdate) =>
    fetchApi<JournalNoteItem>(`/api/journal-notes/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
  journalNoteDelete: (id: number) =>
    fetchApi<{ deleted: boolean; id: number }>(`/api/journal-notes/${id}`, { method: "DELETE" }),
  journalTemplates: () =>
    fetchApi<JournalTemplateItem[]>("/api/journal-notes/templates"),
  journalTemplateGet: (type: string) =>
    fetchApi<JournalTemplateItem>(`/api/journal-notes/templates/${type}`),
  journalTemplateSave: (type: string, content: string) =>
    fetchApi<{ template_type: string; saved: boolean }>(`/api/journal-notes/templates/${type}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),
  journalUploadImage: async (file: File): Promise<{ path: string; filename: string }> => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/journal-notes/upload-image", { method: "POST", body: formData });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    return res.json();
  },

  // Monthly Objectives
  monthlyObjectivesList: (year: number, month: number) =>
    fetchApi<MonthlyObjectiveItem[]>(`/api/journal-notes/monthly-objectives?year=${year}&month=${month}`),
  monthlyObjectiveCreate: (obj: MonthlyObjectiveCreate) =>
    fetchApi<MonthlyObjectiveItem>("/api/journal-notes/monthly-objectives", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(obj),
    }),
  monthlyObjectiveUpdate: (id: number, update: MonthlyObjectiveUpdate) =>
    fetchApi<MonthlyObjectiveItem>(`/api/journal-notes/monthly-objectives/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
  monthlyObjectiveDelete: (id: number) =>
    fetchApi<{ deleted: boolean; id: number }>(`/api/journal-notes/monthly-objectives/${id}`, {
      method: "DELETE",
    }),

  // Custom Templates
  customTemplatesList: () =>
    fetchApi<CustomTemplateItem[]>("/api/journal-notes/custom-templates"),
  customTemplateCreate: (name: string, content: string, color: string = "indigo") =>
    fetchApi<CustomTemplateItem>("/api/journal-notes/custom-templates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, content, color }),
    }),
  customTemplateUpdate: (id: number, update: { name?: string; content?: string; color?: string }) =>
    fetchApi<CustomTemplateItem>(`/api/journal-notes/custom-templates/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
  customTemplateDelete: (id: number) =>
    fetchApi<{ deleted: boolean; id: number }>(`/api/journal-notes/custom-templates/${id}`, {
      method: "DELETE",
    }),

  // Market data
  marketTickers: (symbols?: string) =>
    fetchApi<{ tickers: MarketTicker[]; error?: string }>(
      `/api/market/tickers${symbols ? `?symbols=${symbols}` : ""}`,
    ),

  // Activity feed
  activityRecent: (limit: number = 8) =>
    fetchApi<{ events: ActivityEvent[] }>(`/api/activity/recent?limit=${limit}`),

  // WFO
  wfoAssetSelection: (req: {
    batch_ids: number[];
    portfolio_ids?: number[];
    is_months: number;
    oos_months: number;
    last_oos_end: string;
    n_assets?: number;
    objective?: string;
    corr_method?: string;
    n_candidates?: number;
    min_trades?: number;
    min_pf?: number;
    min_wr?: number;
    extra_slippage_pct?: number;
    extra_commission_pct?: number;
    funding_daily_pct?: number;
    spread_pct?: number;
    seed_metrics?: string[];
    max_seeds?: number;
    prioritize_stability?: boolean;
    date_from?: string;
    split_mode?: boolean;
    n_longs?: number;
    n_shorts?: number;
    within_objective?: string;
    combined_objective?: string;
    tc_low_threshold?: number;
    tc_max_low?: number;
    tc_high_threshold?: number;
    tc_min_high?: number;
    tc_longs?: { low_threshold: number; max_low: number; high_threshold: number; min_high: number };
    tc_shorts?: { low_threshold: number; max_low: number; high_threshold: number; min_high: number };
    benchmark_enabled?: boolean;
    benchmark_source?: string;
    benchmark_split?: string;
    benchmark_threshold?: number;
    benchmark_elite_target?: number;
    benchmark_exclude_fragile?: boolean;
    benchmark_consistency_blend?: number;
    benchmark_fragility_blend?: number;
    benchmark_wr_harmony_blend?: number;
    streamlit_logic?: boolean;
    // Manual mode (3-layer optimization, requires 2+ batches)
    wfo_mode?: string;
    obj_layer1?: string;
    obj_layer2?: string;
    obj_layer3?: string;
    candidates_per_strategy?: number;
    n_final_strats?: number;
    // Directional batches mode (beam search)
    directional_batches?: boolean;
    l2_groups?: number[][];
    n_per_batch?: number;
    obj_l1?: string;
    obj_l2?: string;
    obj_l3?: string;
    k_l1?: number;
    k_l2?: number;
    k_l3?: number;
    // Event-based mode
    event_mode?: boolean;
    n_trades?: number;
    staleness_days?: number;
    n_per_group?: number;
    // L1 quality gate
    l1_quality_gate?: boolean;
    // Category filter
    category_filter?: string[];
    // Boundary clipping
    clip_is?: boolean;
    // PnL Scaling (position sizing)
    pnl_scaling_mode?: string;
    pnl_scaling_custom?: number;
    pnl_scaling_per_batch?: Record<string, number>;
  }) =>
    fetchApi<WFOResult | EventWFOResult>("/api/wfo/asset-selection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  wfoAssetSelectionAsync: (req: Parameters<typeof api.wfoAssetSelection>[0]) =>
    fetchApi<{ run_id: string; async: boolean }>("/api/wfo/asset-selection?async=1", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  wfoProgress: (runId: string) =>
    fetchApi<{ run_id: string; step: number; total: number; label: string; done: boolean }>(`/api/wfo/progress/${runId}`),
  wfoResult: (runId: string) =>
    fetchApi<WFOResult>(`/api/wfo/result/${runId}`),
  wfoRuns: () =>
    fetchApi<WFORunItem[]>("/api/wfo/runs"),
  wfoRunDetail: (id: number) =>
    fetchApi<WFOResult>(`/api/wfo/runs/${id}`),
  wfoWindowDetail: (runId: number, windowNum: number) =>
    fetchApi<WFOWindowResult>(`/api/wfo/runs/${runId}/window/${windowNum}`),
  wfoSaveTrack: (req: {
    name: string;
    batch_ids: number[];
    track_rank: number;
    assets: { symbol: string; side: string; strategy_name?: string; batch_id?: number; config_name?: string }[];
    wfo_settings?: Record<string, unknown>;
    metrics_snapshot?: Record<string, number>;
    optimization_end_date?: string;
    per_window?: Array<Record<string, unknown>>;
    windows_meta?: Array<Record<string, unknown>>;
  }) =>
    fetchApi<PortfolioItem>("/api/wfo/save-track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  wfoSavePhase2Track: (req: {
    name: string;
    batch_ids: number[];
    assets: { symbol: string; side: string; batch_id?: number; timeframe?: string; group_name?: string; asset_key?: string }[];
    preset_config: Record<string, unknown>;
    metrics_snapshot: Record<string, unknown>;
    winning_batch_ids: number[];
    optimization_end_date?: string;
    preset_id?: string;
    selection_changes?: Array<{ step?: number; date: string; added?: string[]; removed?: string[] }>;
    initial_basket?: string[];
    selected_records?: Array<Record<string, unknown>>;
    aggregate_monthly_matrix?: Record<string, unknown>;
    aggregate_equity_curve?: Array<Record<string, unknown>>;
    aggregate_breakdown?: Array<Record<string, unknown>>;
    cumulative_metrics?: Record<string, unknown>;
    windows_meta?: Array<Record<string, unknown>>;
    wfo_per_window?: Array<Record<string, unknown>>;
  }) =>
    fetchApi<{ id: number; name: string; asset_count: number; created_at: string }>("/api/wfo/save-phase2-track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  portfolioWfoTimeline: (portfolioId: number) =>
    fetchApi<WFOTimelineResponse>(`/api/portfolios/${portfolioId}/wfo-timeline`),
  wfoRefreshPhase2Track: (req: {
    portfolio_id: number;
    assets: { symbol: string; side: string; batch_id?: number; timeframe?: string; group_name?: string; asset_key?: string }[];
    metrics_snapshot: Record<string, unknown>;
    winning_batch_ids: number[];
    optimization_end_date?: string;
    selection_changes?: Array<{ step?: number; date: string; added?: string[]; removed?: string[] }>;
    initial_basket?: string[];
    selected_records?: Array<Record<string, unknown>>;
    aggregate_monthly_matrix?: Record<string, unknown>;
    aggregate_equity_curve?: Array<Record<string, unknown>>;
    aggregate_breakdown?: Array<Record<string, unknown>>;
    cumulative_metrics?: Record<string, unknown>;
  }) =>
    fetchApi<{ id: number; asset_count: number; added: string[]; removed: string[]; unchanged_count: number }>("/api/wfo/refresh-phase2-track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  wfoDeleteRun: (id: number) =>
    fetchApi<{ deleted: boolean }>(`/api/wfo/runs/${id}`, { method: "DELETE" }),
  wfoRerunLastWindow: (runId: number) =>
    fetchApi<WFOResult>(`/api/wfo/runs/${runId}/rerun`, { method: "POST" }),
  // Replay Exact: re-run a saved WFAS run with the identical config and
  // identical batch_ids stored on that run. Produces a new saved run that
  // is guaranteed reproducible (bypasses the form's lossy settings loader).
  wfoReplayExact: (runId: number) =>
    fetchApi<WFOResult | EventWFOResult>(`/api/wfo/runs/${runId}/replay-exact`, {
      method: "POST",
    }),
  wfoMonthTrades: (runId: number, year: number, month: number, windowNum?: number) => {
    const params = new URLSearchParams({ year: String(year), month: String(month) });
    if (windowNum != null) params.set("window_num", String(windowNum));
    return fetchApi<MonthTradesResponse>(`/api/wfo/runs/${runId}/month-trades?${params}`);
  },

  // Live Trading Engine
  liveStatus: () =>
    fetchApi<LiveEngineStatus>("/api/live/status"),
  liveStart: () =>
    fetchApi<{ status: string }>("/api/live/start", { method: "POST" }),
  liveStop: () =>
    fetchApi<{ status: string }>("/api/live/stop", { method: "POST" }),

  // Paper Trading Engine (dual-backend) — these proxy from the live
  // backend (port 8055) to the paper backend (port 8056). Only the live
  // backend has /api/paper/* routes; calling them on the paper backend
  // is rejected with a clear 400.
  paperStatus: () =>
    fetchApi<LiveEngineStatus>("/api/paper/status"),
  paperStart: () =>
    fetchApi<{ status: string }>("/api/paper/start", { method: "POST" }),
  paperStop: () =>
    fetchApi<{ status: string }>("/api/paper/stop", { method: "POST" }),
  liveMonitor: (portfolio_id: number) =>
    fetchApi<{ status: string; portfolio_id: number }>("/api/live/monitor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_id }),
    }),
  liveStopMonitoring: (portfolio_id: number) =>
    fetchApi<{ status: string; portfolio_id: number }>("/api/live/stop-monitoring", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_id }),
    }),
  liveActivate: (portfolio_id: number) =>
    fetchApi<{ status: string; portfolio_id: number }>("/api/live/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_id }),
    }),
  liveDeactivate: (portfolio_id: number, close_positions: boolean = false) =>
    fetchApi<{ status: string; portfolio_id: number }>("/api/live/deactivate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_id, close_positions }),
    }),
  liveRefreshPortfolio: (portfolio_id: number) =>
    fetchApi<{ added: string[]; removed: string[]; kept: string[] }>("/api/live/refresh-portfolio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ portfolio_id }),
    }),
  livePositions: () =>
    fetchApi<LivePosition[]>("/api/live/positions"),
  liveOrders: (status: string = "all", limit: number = 50) =>
    fetchApi<LiveOrder[]>(`/api/live/orders?status=${status}&limit=${limit}`),
  liveTrades: (limit: number = 50) =>
    fetchApi<LiveTrade[]>(`/api/live/trades?limit=${limit}`),
  liveEquity: () =>
    fetchApi<LiveEquity>("/api/live/equity"),
  liveEvents: (limit: number = 100) =>
    fetchApi<LiveEvent[]>(`/api/live/events?limit=${limit}`),
  liveClosePosition: (position_id: number) =>
    fetchApi<{ status: string; position_id: number }>("/api/live/close-position", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ position_id }),
    }),
  liveCloseAll: () =>
    fetchApi<{ status: string; positions: number }>("/api/live/close-all", { method: "POST" }),
  liveEmergencyStop: () =>
    fetchApi<{ status: string }>("/api/live/emergency-stop", { method: "POST" }),
  liveTslStatus: () =>
    fetchApi<LiveTSLEntry[]>("/api/live/tsl-status"),

  liveAccountSummary: () =>
    fetchApi<AccountSummary>("/api/live/account-summary"),

  livePnlSummary: () =>
    fetchApi<PnLSummary>("/api/live/pnl-summary"),

  liveSignals: (params?: { portfolio_id?: number; status?: string; symbol?: string; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.portfolio_id !== undefined) sp.set("portfolio_id", String(params.portfolio_id));
    if (params?.status) sp.set("status", params.status);
    if (params?.symbol) sp.set("symbol", params.symbol);
    if (params?.limit !== undefined) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return fetchApi<LiveSignal[]>(`/api/live/signals${qs ? `?${qs}` : ""}`);
  },

  updateSignalStatus: (signalId: number, status: string) =>
    fetchApi<{ id: number; status: string }>(`/api/live/signals/${signalId}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }),

  // Trash / Deleted Items
  trashStrategies: () =>
    fetchApi<DeletedStrategy[]>("/api/trash/strategies"),
  trashBacktests: () =>
    fetchApi<DeletedBacktest[]>("/api/trash/backtests"),
  trashBatches: () =>
    fetchApi<DeletedBatch[]>("/api/trash/batches"),
  trashWfo: () =>
    fetchApi<DeletedWFO[]>("/api/trash/wfo"),
  trashRestore: (type: string, id: number) =>
    fetchApi<{ restored: boolean; id: number }>(`/api/trash/${type}/${id}/restore`, { method: "POST" }),
  trashPermanentDelete: (type: string, id: number) =>
    fetchApi<{ permanently_deleted: boolean; id: number }>(`/api/trash/${type}/${id}`, { method: "DELETE" }),
  trashEmpty: () =>
    fetchApi<{ emptied: boolean; counts: Record<string, number> }>("/api/trash/empty", { method: "DELETE" }),
  trashEmptyTab: (tab: string) =>
    fetchApi<{ emptied: boolean; counts: Record<string, number> }>(`/api/trash/empty/${tab}`, { method: "DELETE" }),

  // Asset Categories (manually maintained)
  categories: () =>
    fetchApi<Record<string, string[]>>("/api/data/categories"),
  updateCategories: (symbol: string, categories: string[]) =>
    fetchApi<{ symbol: string; categories: string[] }>(`/api/data/categories/${symbol}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ categories }),
    }),

  // OHLCV Data Management
  deleteOhlcv: (symbol: string, timeframe?: string) =>
    fetchApi<{ deleted: number; symbol: string; timeframe: string }>(
      `/api/data/ohlcv/${symbol}${timeframe ? `?timeframe=${timeframe}` : ""}`,
      { method: "DELETE" },
    ),

  // Backup & Restore
  backupExport: async (): Promise<Blob> => {
    const resp = await fetch("/api/data/backup");
    if (!resp.ok) throw new Error("Backup export failed");
    return resp.blob();
  },
  backupRestore: async (file: File): Promise<{ status: string; counts: Record<string, number> }> => {
    const formData = new FormData();
    formData.append("file", file);
    const resp = await fetch("/api/data/restore", { method: "POST", body: formData });
    if (!resp.ok) throw new Error("Backup restore failed");
    return resp.json();
  },

  // Category Performance Dashboard
  categoryReturns: (source: string = "watchlist") =>
    fetchApi<{ categories: Record<string, { d: number; w: number; m: number; y: number; asset_count: number; symbols: string[] }>; source: string }>(`/api/data/category-returns?source=${source}`),
  categoryChart: (categories: string[], timeframe: string = "1M", source: string = "watchlist") =>
    fetchApi<{ dates: string[]; series: Record<string, number[]> }>(`/api/data/category-chart?categories=${categories.join(",")}&timeframe=${timeframe}&source=${source}`),
  categoryTradingPnl: (source: string = "watchlist") =>
    fetchApi<{ categories: Record<string, { d: number; w: number; m: number; y: number; trade_count: number }> }>(`/api/data/category-trading-pnl?source=${source}`),
  filteredAssetReturns: (includeCats: string[], excludeCats: string[], timeframe: string = "1M", source: string = "watchlist") =>
    fetchApi<{ d: number; w: number; m: number; y: number; asset_count: number; symbols: string[]; chart: { dates: string[]; values: number[] } }>(
      `/api/data/filtered-asset-returns?include_cats=${includeCats.join(",")}&exclude_cats=${excludeCats.join(",")}&timeframe=${timeframe}&source=${source}`
    ),

  // Asset structural metrics (OHLCV-based, strategy-independent)
  assetAvailableTimeframes: () =>
    fetchApi<{ timeframe: string; symbol_count: number }[]>("/api/assets/available-timeframes"),
  assetStructuralMetrics: (timeframe: string) =>
    fetchApi<{ timeframe: string; results: AssetStructuralResult[]; cached_at?: number }>(`/api/assets/structural-metrics?timeframe=${timeframe}`),
  assetRefreshStructuralMetrics: (timeframe: string) =>
    fetchApi<{ status: string; timeframe: string }>(`/api/assets/structural-metrics/refresh?timeframe=${timeframe}`, { method: "POST" }),
  assetInstrumentSpecs: () =>
    fetchApi<{ specs: Record<string, { min_qty: number; max_qty: number; lot_size: number }> }>("/api/assets/instrument-specs"),

  // Database Maintenance
  dbSize: () => fetchApi<{ size_mb: number }>("/api/maintenance/db-size"),
  dbCleanup: () => fetchApi<{ status: string }>("/api/maintenance/cleanup", { method: "POST" }),
  cleanupProgress: () => fetchApi<{
    step: string; done: boolean; error: string | null;
    result: {
      before_mb: number; after_mb: number; saved_mb: number;
      deleted_trades: number; deleted_runs: number; deleted_batches: number;
      deleted_strategies: number; deleted_wfo: number; deleted_portfolios: number;
      duplicate_ohlcv_removed: number;
      vacuum_error?: string | null;
    } | null;
  }>("/api/maintenance/cleanup-progress"),

  // Chart data
  chartCandles: (
    symbol: string,
    timeframe: string,
    start: string,
    end: string,
    limit?: number,
    strategyId?: number,
    runId?: number | null,
  ) =>
    fetchApi<ChartCandlesResponse>(
      `/api/chart/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}${limit ? `&limit=${limit}` : ""}${strategyId ? `&strategy_id=${strategyId}` : ""}${runId ? `&run_id=${runId}` : ""}`
    ),

  // ── Kelly Portfolio Allocation ──────────────────────────────────
  getKellyAllocation: (portfolioId: number) =>
    fetchApi<{
      portfolio_id: number;
      enabled: boolean;
      weights: Record<string, number>;
      config: Record<string, unknown>;
      result: Record<string, unknown>;
      computed_at: string | null;
      trigger: string | null;
    }>(`/api/kelly-allocation/${portfolioId}`),

  computeKellyAllocation: (portfolioId: number) =>
    fetchApi<{
      portfolio_id: number;
      weights: Record<string, number>;
      result: Record<string, unknown>;
      config: Record<string, unknown>;
      computed_at: string;
    }>(`/api/kelly-allocation/${portfolioId}/compute`, { method: "POST" }),

  updateKellyConfig: (portfolioId: number, config: {
    mode?: string;
    half_kelly_multiplier?: number;
    sr_threshold?: number;
    max_cap_pct?: number;
    corr_warning_threshold?: number;
    regime_dampening_factor?: number;
    min_trades_per_strategy?: number;
    is_lookback_days?: number;
    schedule_type?: string;
    schedule_trade_count?: number;
  }) =>
    fetchApi<{ ok: boolean; config: Record<string, unknown> }>(
      `/api/kelly-allocation/${portfolioId}/config`,
      { method: "PUT", body: JSON.stringify(config) },
    ),

  getKellyHistory: (portfolioId: number) =>
    fetchApi<Array<{
      id: number;
      weights: Record<string, number>;
      computed_at: string | null;
      trigger: string;
      is_current: boolean;
      hhi: number | null;
      regime: string | null;
    }>>(`/api/kelly-allocation/${portfolioId}/history`),

  // ── Rebalance Proposals (Ship 2 — OOS boundary rotation) ────────
  listRebalanceProposals: (opts?: { status?: string; portfolio_id?: number }) => {
    const params = new URLSearchParams();
    if (opts?.status) params.set("status", opts.status);
    if (opts?.portfolio_id) params.set("portfolio_id", String(opts.portfolio_id));
    const qs = params.toString();
    return fetchApi<{
      proposals: RebalanceProposal[];
      count: number;
    }>(`/api/rebalance/proposals${qs ? `?${qs}` : ""}`);
  },
  getRebalanceProposal: (id: number) =>
    fetchApi<RebalanceProposal>(`/api/rebalance/proposals/${id}`),
  approveRebalanceProposal: (id: number, reviewedBy?: string) =>
    fetchApi<{ ok: boolean; proposal: RebalanceProposal; new_portfolio_id: number }>(
      `/api/rebalance/proposals/${id}/approve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewed_by: reviewedBy || "user" }),
      },
    ),
  rejectRebalanceProposal: (id: number, reason?: string, reviewedBy?: string) =>
    fetchApi<{ ok: boolean; proposal: RebalanceProposal }>(
      `/api/rebalance/proposals/${id}/reject`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason, reviewed_by: reviewedBy || "user" }),
      },
    ),
  forceRebalanceProposal: (portfolioId: number) =>
    fetchApi<{
      ok: boolean;
      proposal_id: number | null;
      reason: string | null;
      diff: { added: string[]; removed: string[]; unchanged: string[] } | null;
    }>(
      `/api/rebalance/portfolios/${portfolioId}/propose`,
      { method: "POST" },
    ),

  // Live Loop (Ship 3) — nightly scheduler status + manual catch-up
  getLiveLoopStatus: () =>
    fetchApi<{
      active_batches: number;
      active_live_portfolios: number;
      hybrid_portfolios: number;
      pending_proposals: number;
      schedule: Record<string, string>;
      last_canary: {
        id: number;
        run_date: string;
        total_combos: number;
        combos_with_diff: number;
        created_at: string | null;
      } | null;
    }>("/api/live-loop/status"),
  runLiveLoopCatchUp: (
    includeCanary = false,
    downloadCandles = false,
  ) => {
    const params = new URLSearchParams();
    if (includeCanary) params.set("include_canary", "1");
    if (downloadCandles) params.set("download_candles", "1");
    const qs = params.toString();
    return fetchApi<{
      ok: boolean;
      layers: {
        layer: string;
        ok: boolean;
        started_at: string;
        ended_at: string;
        result?: unknown;
        error?: string;
      }[];
    }>(
      `/api/live-loop/catch-up${qs ? `?${qs}` : ""}`,
      { method: "POST" },
    );
  },
  runLiveLoopLayer: (layer: "l0" | "l1" | "l2" | "l3" | "l4") =>
    fetchApi<{
      layer: string;
      ok: boolean;
      started_at: string;
      ended_at: string;
      result?: unknown;
      error?: string;
    }>(`/api/live-loop/run/${layer}`, { method: "POST" }),
  getStaleBatches: () =>
    fetchApi<{
      batches: { batch_id: number; name: string; total_runs: number; stale_runs: number }[];
      batch_count: number;
      run_count: number;
    }>("/api/live-loop/stale-batches"),
  startRebuildStaleBatches: (downloadCandles = false) => {
    const qs = downloadCandles ? "?download_candles=1" : "";
    return fetchApi<{ started: boolean; reason?: string; progress: RebuildProgress }>(
      `/api/live-loop/rebuild-stale-batches${qs}`,
      { method: "POST" },
    );
  },
  getRebuildStatus: () =>
    fetchApi<RebuildProgress>("/api/live-loop/rebuild-status"),

  // Tax Records
  taxRecords: (symbol?: string, year?: number) => {
    const params = new URLSearchParams();
    if (symbol) params.set("symbol", symbol);
    if (year) params.set("year", String(year));
    const qs = params.toString();
    return fetchApi<{ count: number; records: TaxRecord[] }>(`/api/tax-records${qs ? `?${qs}` : ""}`);
  },
  taxSummary: (year?: number) =>
    fetchApi<TaxSummary>(`/api/tax-records/summary${year ? `?year=${year}` : ""}`),
  createTaxRecord: (data: Partial<TaxRecord>) =>
    fetchApi<{ status: string; record: TaxRecord }>("/api/tax-records", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  updateTaxRecord: (id: number, data: Partial<TaxRecord>) =>
    fetchApi<{ status: string; record: TaxRecord }>(`/api/tax-records/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  deleteTaxRecord: (id: number) =>
    fetchApi<{ status: string; id: number }>(`/api/tax-records/${id}`, { method: "DELETE" }),
  importLiveTrades: () =>
    fetchApi<{ status: string; imported: number; already_existed: number }>(
      "/api/tax-records/import-live", { method: "POST" }
    ),
  importJournalTrades: () =>
    fetchApi<{ status: string; imported: number; already_existed: number }>(
      "/api/tax-records/import-journal", { method: "POST" }
    ),
  exportTaxCsv: async (year?: number): Promise<Blob> => {
    const res = await fetch(`/api/tax-records/export-csv${year ? `?year=${year}` : ""}`);
    if (!res.ok) throw new Error("Export failed");
    return res.blob();
  },
  taxWithholding: (year?: number, usdCadRate?: number, businessExpenses?: number) => {
    const params = new URLSearchParams();
    if (year) params.set("year", String(year));
    if (usdCadRate && usdCadRate > 0) params.set("usd_cad_rate", String(usdCadRate));
    if (businessExpenses && businessExpenses > 0) params.set("business_expenses", String(businessExpenses));
    const qs = params.toString();
    return fetchApi<TaxWithholdingResponse>(`/api/tax-records/tax-withholding${qs ? `?${qs}` : ""}`);
  },
  taxReport: (year?: number, usdCadRate?: number) => {
    const params = new URLSearchParams();
    if (year) params.set("year", String(year));
    if (usdCadRate && usdCadRate > 0) params.set("usd_cad_rate", String(usdCadRate));
    const qs = params.toString();
    return fetchApi<TaxReportResponse>(`/api/tax-records/tax-report${qs ? `?${qs}` : ""}`);
  },
  exportTaxReport: async (year?: number, usdCadRate?: number): Promise<Blob> => {
    const params = new URLSearchParams();
    if (year) params.set("year", String(year));
    if (usdCadRate && usdCadRate > 0) params.set("usd_cad_rate", String(usdCadRate));
    const qs = params.toString();
    const res = await fetch(`/api/tax-records/export-tax-report${qs ? `?${qs}` : ""}`);
    if (!res.ok) throw new Error("Export failed");
    return res.blob();
  },
  taxExpenses: (year?: number) => {
    const qs = year ? `?year=${year}` : "";
    return fetchApi<{ count: number; expenses: TaxExpense[] }>(`/api/tax-records/expenses${qs}`);
  },
  createTaxExpense: (data: { year: number; category: string; description: string; amount_cad: number; receipt_note: string }) =>
    fetchApi<{ status: string; expense: TaxExpense }>("/api/tax-records/expenses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  updateTaxExpense: (id: number, data: Record<string, unknown>) =>
    fetchApi<{ status: string; expense: TaxExpense }>(`/api/tax-records/expenses/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  deleteTaxExpense: (id: number) =>
    fetchApi<{ status: string; id: number }>(`/api/tax-records/expenses/${id}`, {
      method: "DELETE",
    }),

  // Sweep backtests
  startSweep: (data: {
    strategy_id: number;
    symbols: string[];
    timeframe: string;
    direction_filter: string;
    sweep_params: SweepParam[];
    sweep_mode: string;
    max_combos?: number;
    initial_capital: number;
    commission_pct: number;
    slippage_mode: string;
    slippage_value: number;
    leverage: number;
    min_avg_daily_volume: number;
    max_avg_daily_volume: number;
    min_positions: number;
    config_id?: number;
    strategy_inputs?: Record<string, number | boolean | string>;
    max_pyramiding: number;
    risk_pct: number;
    risk_mode: string;
    risk_value: number;
    martingale_enabled: boolean;
    martingale_type: string;
    martingale_multiplier: number;
    martingale_max_streak: number;
    vol_filter_enabled: boolean;
    vol_filter_atr_ceiling: number;
    vol_filter_atr_length: number;
    mode: string;
    normalized: boolean;
    include_costs: boolean;
    funding_rate_daily_pct: number;
    spread_pct: number;
    prefilter_enabled: boolean;
    prefilter_mode: string;
    prefilter_hurst_min: number;
    prefilter_ter_percentile: number;
    prefilter_hurst_max: number;
    prefilter_vr_max: number;
    prefilter_min_crossing_rate: number;
    min_data_date?: string;
    process_orders_on_close: boolean;
    enforce_bybit_limits: boolean;
    expected_live_size: number;
    max_margin_per_trade: number;
    margin_filter_tolerance: number;
    bybit_reject_pct?: number;
  }) =>
    fetchApi<{ sweep_id: number; total_combos: number; batch_ids: number[]; status: string }>(
      "/api/backtest/sweep/run",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) },
    ),
  sweepProgress: (sweepId: number) =>
    fetchApi<SweepProgress>(`/api/backtest/sweep/progress/${sweepId}`),
  sweepRuns: () =>
    fetchApi<SweepRunItem[]>("/api/backtest/sweep/runs"),
  sweepDetail: (sweepId: number) =>
    fetchApi<SweepDetail>(`/api/backtest/sweep/runs/${sweepId}`),
  cancelSweep: (sweepId: number) =>
    fetchApi<{ cancelled: boolean; sweep_id: number; batches_cancelled: number; completed_count?: number; status?: string }>(
      `/api/backtest/sweep/runs/${sweepId}/cancel`, { method: "POST" },
    ),
  deleteSweep: (sweepId: number) =>
    fetchApi<{ deleted: boolean; sweep_id: number; batches_deleted: number }>(
      `/api/backtest/sweep/runs/${sweepId}`, { method: "DELETE" },
    ),
  resumeSweep: (sweepId: number) =>
    fetchApi<{ sweep_id: number; already_completed: number; remaining: number; status: string }>(
      `/api/backtest/sweep/runs/${sweepId}/resume`, { method: "POST" },
    ),
};
