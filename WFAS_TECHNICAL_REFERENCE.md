# WFAS Technical Reference — Code-Level Documentation

> How the 2-Step WFAS Pipeline Works Under the Hood
> Generated: 2026-03-27 | Source: batch_selection.py, event_wfo.py, wfo_window_gen.py

---

## CRITICAL: Timezone

**All `entry_time` values in the database are New York Time (naive, no timezone info).**

Proof chain:
1. Candles are fetched from OHLCV database in **UTC**
2. `backend/utils/candle_loader.py:59` — `to_nyt()` converts UTC → NYT (naive)
3. `backend/api/routes/batch_backtest.py:2099` — `primary = _to_nyt(...)` before strategy runs
4. `backend/backtester/engine.py:962` — `entry_ts = strategy._timestamps[pos.entry_bar]` uses the NYT DatetimeIndex
5. Therefore: `entry_time.hour` = NYT hour, `entry_time.weekday()` = NYT weekday

**Consequence**: When filtering by hour, use NYT hours directly. To keep NYT 9am–4pm,
exclude hours `[0,1,2,3,4,5,6,7,8,17,18,19,20,21,22,23]`, keep hours 9–16.

**Known Bug**: `event_wfo.py:355` does `.replace(tzinfo=UTC).astimezone(NY)` on entry_time,
which double-converts NYT → NYT (shifts by 4–5 hours). The session filter at line 556–558
correctly uses `entry.hour` directly. This inconsistency affects `exec_day_profit` weekday
detection only.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    WFAS PIPELINE                        │
│                                                         │
│  STEP 1: Batch Selection (Time-Based Windows)           │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Input: ALL batches (80+)                        │    │
│  │ Engine: batch_selection.py                      │    │
│  │ Method: Rolling IS/OOS windows                  │    │
│  │ Output: Best 3 batch IDs per window             │    │
│  │ Final window = what you trade                   │    │
│  └─────────────────────────────────────────────────┘    │
│                         │                               │
│                         ▼                               │
│  STEP 2: Event Mode (Trade-by-Trade)                    │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Input: Trades from winning batches only          │    │
│  │ Engine: event_wfo.py                            │    │
│  │ Method: Walk chronologically, rank per N trades │    │
│  │ Output: Best assets + equity curve              │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## STEP 1: Batch Selection Engine

**File**: `backend/analysis/batch_selection.py`
**Entry point**: `run_batch_selection(all_trades, windows, config, run_batch_map)`

### 1.1 Data Preparation (Lines 1150–1188)

Before any windows are processed:

1. **Dedup batches** (`dedup_batches`, line 90): Fingerprint = `{trade_count}_{round(total_pnl)}`.
   If two batches have identical fingerprints, the lower batch_id survives.

2. **Build daily PnL structures** using **ENTRY date** (when the decision was made):
   - `build_batch_daily_pnl()` → per-batch daily PnL Series (all dates, 0-filled)
     - Uses `t.entry_time` (line 174) so trades are attributed to the IS/OOS window
       where the signal fired, not where the trade closed
   - `build_asset_daily_pnl()` → per-asset daily PnL Series
     - Uses `t.entry_time` (line 220) — same principle
     - Asset key format: `"{batch_id}_{symbol}_{side}"` (e.g., `"9326_BTCUSDT_Long"`)
     - **Assets with < 3 trades are excluded** (line 224)

3. **Detect batch sides** (`detect_batch_sides`): Counts Long vs Short trades per batch.
   The side with more trades wins. Used for L2 Groups.

4. **Exec-filtered structures** (optional): If session filter hours/days are set,
   a second set of daily PnL structures is built from filtered trades.
   Used ONLY for OOS reporting — never for IS scoring or calibration.

### 1.2 Window Generation

**File**: `backend/analysis/wfo_window_gen.py`

Three time modes:

| Mode | Function | Step Size | IS | OOS |
|------|----------|-----------|-----|------|
| Rolling | `generate_rolling_windows()` | = OOS months | Fixed length | Fixed length |
| Static | `generate_static_windows()` | Configurable | Grows each step | Shrinks each step |
| Overlapping | `generate_overlapping_windows()` | = OOS/2 | Fixed length | Split H1+H2 |

**Rolling example** (IS=12mo, OOS=3mo, data_start=2020-04-29):
```
W1:  IS 2020-04-29 → 2021-04-28  |  OOS 2021-04-29 → 2021-07-28
W2:  IS 2020-07-29 → 2021-07-28  |  OOS 2021-07-29 → 2021-10-28
W3:  IS 2020-10-29 → 2021-10-28  |  OOS 2021-10-29 → 2022-01-28
...each window steps forward by OOS months (3)...
```

Final window is IS-only (oos_start=None) — excluded from batch selection.

### 1.3 The 3-Step Pipeline Per Window

For EACH window (lines 646–1006):

#### Step 1: IS Screening (line 660)

For each batch:
1. Slice batch daily PnL to IS period: `_slice_pnl(pnl, is_start, is_end)`
2. **Gate checks**:
   - Non-zero PnL days >= `min_is_days` (default: 30)
   - Net profit > 0
   - Profit factor >= `min_pf` (default: 1.0)
3. Score by `batch_rank_metric` using `score_metric()` from `batch_selection_scoring.py`
4. Sort descending (higher = better)
5. Take top `n_candidates` (default: 20)

**Optional modifiers** (applied to IS scores):
- **Recency Momentum** (`recency_weight`): Applies linear weight ramp to PnL days.
  `recency_weight=2.0` → newest day weighted 2x oldest. Applied BEFORE scoring.
- **Regime-Conditional** (`regime_weight`): Multiplies IS score by cosine similarity
  between batch's regime vector and aggregate regime vector. Regime = [volatility, trend, AC1].
- **ELO Tournament** (`elo_iterations`): Replaces metric ranking with round-robin ELO.
  Pairs compare cosine similarity to ideal equity curve on 70% random subsamples.

#### Step 2: Calibration Gate (line 730)

**Window 1**: Cold start — no calibration possible. Uses IS ranking directly.

**Windows 2+**: Evaluate candidates against PREVIOUS window's OOS period.

Single-metric mode (default):
1. For each candidate, slice batch PnL to previous OOS period
2. **Gate**: `cal_must_profit=True` → net profit in previous OOS must be > 0
3. Score by `cal_rank_metric` (default: "ulcer" — lower ulcer = better risk control)
4. Sort and take top `effective_mb` (default: max_batches=3)
5. **Fallback**: If < `min_calibrated` batches pass, fill from IS ranking

**Ensemble mode** (`ensemble_cal_k >= 2`): Instead of single metric, check top-50% rank
in K of 5 risk metrics: ulcer, recovery, sortino, pf, tail_ratio.

**L2 Groups** (`l2_groups=True`): Select ceil(max_batches/2) from Long batches and
ceil(max_batches/2) from Short batches independently. Ensures both sides represented.

**Adaptive MB** (`adaptive_mb=True`): Auto-determine max_batches by finding the
largest quality gap between ranked positions. Range: [adaptive_mb_min, adaptive_mb_max].

#### Step 3: Asset Picking (line 866)

For each selected batch:
1. Get all assets belonging to this batch: `batch_to_assets[bid]`
2. For each asset, slice to IS period
3. **Gate checks**:
   - Non-zero days >= `min_asset_days` (default: 5)
   - `asset_must_profit=True` → net > 0 in IS (default: False)
   - `past_oos_confirm=True` → must be profitable in previous OOS (default: False)
   - `asset_wr_gate` (default: 0.20) → min 20% win rate in IS, min 5 trades
4. Score by `asset_rank_metric` (default: "net_pnl")
5. Pick top `assets_per_batch` (default: 5) per batch
6. **TPM cap**: Stop adding if estimated trades/month exceeds `max_tpm` (default: 40)

#### Step 4: OOS Evaluation — Reporting Only (line 946)

The selected portfolio's performance is measured on the OOS period.
**This is NEVER used for any selection decision** — purely for reporting.

Metrics reported: net_profit, profit_factor, max_drawdown, win_rate, total_trades.
If exec filter is active, both full OOS and filtered OOS are reported.

### 1.4 Available Scoring Metrics

**File**: `backend/analysis/batch_selection_scoring.py`

All metrics: higher = better. Returns -999 if < 10 non-zero PnL days.

| Metric | What It Measures | Formula |
|--------|------------------|---------|
| `net_pnl` | Total profit | sum(daily_pnl) |
| `recovery` | Profit vs drawdown | net_pnl / abs(max_drawdown) |
| `sortino` | Risk-adjusted return | mean / downside_deviation |
| `pf` | Profit factor | gross_profit / gross_loss (capped 10) |
| `sharpe` | Sharpe ratio | mean/std * sqrt(365) |
| `calmar` | Annualized return/DD | equity[-1] / abs(max_dd) |
| `ulcer` | Drawdown risk | -RMS(drawdown%) (negated) |
| `stability` | R² of equity curve | pearson(x, equity)² |
| `lake_ratio` | Time above water | 1 - underwater_area / total_area |
| `tail_ratio` | Upside/downside tails | p95 / abs(p5) |
| `expectancy` | Expected return | (wr*avg_win - (1-wr)*avg_loss) / avg_loss |
| `sqn` | System Quality Number | mean/std * sqrt(n) |
| `gain_pain` | Gain-to-pain | sum(pnl) / abs(sum(losses)) |
| `payoff` | Average win/loss ratio | avg_win / avg_loss |
| `cde` | Complex DD Efficiency | profit / (avg_dd + max_dd * recovery_ratio) |
| `omega` | Omega ratio | gains/losses on non-zero days |
| `robust_sortino` | Median-based Sortino | median / downside_dev |
| `est_efficiency` | EST efficiency | profit / (max_dd * sqrt(bars_to_recover)) |
| `rapid_recovery` | Quick recovery score | sqrt(profit_eff) * recovery_boost |
| `ter` | Trend Efficiency Ratio | abs(net_move) / total_path |
| `keep_score` | Trend quality blend | 0.35*TER + 0.35*sig(GPR) + 0.30*AC1 |
| `mr_score` | Mean-reversion quality | 0.40*crossing_rate + 0.35*neg_AC + 0.25*VR |
| `crossing_rate` | Equity crosses mean | crossings / max_crossings |
| `gpr` | Gain-Pain Ratio | gains / abs(losses) on non-zero |

### 1.5 Advanced Features

| Feature | Config | What It Does | Level |
|---------|--------|--------------|-------|
| Progressive Pruning | `pruning_enabled`, `prune_long`, `prune_short` | Remove N worst batches by historical appearance count before pipeline | Batch pool |
| L2 Groups | `l2_groups=True` | Force diversity: ceil(mb/2) from Long, ceil(mb/2) from Short | Batch selection |
| Past OOS Confirm | `past_oos_confirm=True` | Asset must be profitable in previous OOS | Asset picking |
| Q-Aligned Averaging | `q_starts=4` | Run pipeline from 4 quarter starts, merge results | Pipeline |
| Exec Filter | `exec_exclude_hours`, `exec_exclude_days` | OOS evaluation uses filtered trades only | OOS reporting |

### 1.6 BatchSelectionConfig — All Fields

```python
@dataclass
class BatchSelectionConfig:
  max_batches: int = 4            # Batches selected per window
  assets_per_batch: int = 5       # Assets per batch
  n_candidates: int = 20          # IS screening pool size
  min_calibrated: int = 3         # Min batches from calibration before fallback
  min_pf: float = 1.0             # IS profit factor floor
  min_is_days: int = 30           # Min non-zero PnL days in IS
  min_asset_days: int = 5         # Min non-zero PnL days per asset
  asset_must_profit: bool = False  # Require asset net > 0 in IS
  cal_must_profit: bool = True     # Require batch net > 0 in previous OOS
  batch_rank_metric: str = "recovery"   # IS ranking metric
  cal_rank_metric: str = "ulcer"        # Calibration ranking metric
  asset_rank_metric: str = "net_pnl"    # Asset ranking metric
  max_tpm: int = 40               # Max trades per month cap
  asset_wr_gate: float = 0.20     # Min IS win rate for assets
  recency_weight: float = 0.0     # Recency momentum (0=off, 2.0=max)
  adaptive_mb: bool = False       # Auto-determine max_batches
  adaptive_mb_min/max/gap_pct     # Adaptive MB bounds
  ensemble_cal_k: int = 0         # Ensemble calibration K-of-5
  regime_weight: float = 0.0      # Regime-conditional modifier
  elo_iterations: int = 0         # ELO tournament iterations
  l2_groups: bool = False          # Long/Short diversity
  pruning_enabled: bool = False    # Progressive pruning
  prune_long/prune_short: int = 0  # Pruning counts
  past_oos_confirm: bool = False   # Past OOS confirmation gate
  q_starts: int = 1               # Q-aligned averaging (1 or 4)
```

---

## STEP 2: Event Mode Engine

**File**: `backend/analysis/event_wfo.py`
**Entry point**: `run_event_wfo(all_trades, config)`

### 2.1 How It Works

Walks chronologically through EVERY trade (sorted by exit_time). For each trade:

1. **Check warmup**: Skip equity tracking until date_from and/or warmup_trades met
2. **Rebalance check**: Based on mode, decide if portfolio should be re-evaluated
3. **If rebalancing** (full re-evaluation):
   a. For each asset, compute metrics on its last N trades (from buffer)
   b. Apply quality gates: min_pf, min_wr, staleness_days
   c. Rank eligible assets by scoring method (single metric or composite)
   d. Select Top K as the new portfolio
4. **Check execution**: Is this trade's asset in the portfolio? Apply exec filters.
5. **If yes**: Add trade PnL to cumulative equity
6. **Update buffer**: Append this trade's PnL (AFTER evaluation — no look-ahead)

### 2.2 Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `n_trades` | 10 | Lookback: last N trades per asset for evaluation |
| `n_assets` | 20 | Portfolio size: Top K assets selected |
| `min_pf` | 1.0 | Minimum profit factor to be eligible |
| `min_wr` | 30 | Minimum win rate % to be eligible |
| `staleness_days` | 180 | Max days since last trade before excluded |
| `objective` | "sortino" | Single-metric ranking objective |
| `date_from` | "" | Warmup cutoff date (trades before populate buffers only) |
| `warmup_trades` | 0 | Min global trades before walk-forward starts |
| `rebalance_mode` | "global" | How portfolio updates (see below) |
| `rebalance_every` | 1 | Global mode: re-evaluate every N trades |
| `portfolio_rebalance_every` | 5 | Portfolio/spot_batch mode interval |
| `min_hold` | 0 | Selection lock: min own-trades before removal eligible |
| `scoring_method` | "single" | "single" or "composite" |
| `composite_weights` | {sortino:0.4, pf:0.25, recovery:0.2, wr:0.15} | Composite blend |
| `exec_day_profit` | False | Per-asset day-of-week profitability filter |
| `directional_batches` | False | Multi-batch L1→L2→L3 mode |

### 2.3 Rebalance Modes

| Mode | Trigger | Description |
|------|---------|-------------|
| `global` | Every N global trades | Full re-rank of all eligible assets |
| `portfolio` | Every N portfolio-member trades | Full re-rank, but triggered by portfolio activity |
| `spot_single` | When a portfolio asset trades | Re-evaluate ONLY that asset vs best non-portfolio contender |
| `spot_batch` | Every N portfolio trades | Accumulate traded assets, batch re-evaluate |

### 2.4 Scoring Methods

**Single metric** (default): Rank all eligible assets by one `objective` metric.
Uses `_rank_assets()` → higher = better (except max_dd, ulcer = lower is better).

**Composite scoring** (`scoring_method="composite"`):
Normalizes 4 metrics, applies weighted blend:
```python
score = (0.40 * sortino_raw) +          # raw, typically 0-3
        (0.25 * min(pf, 5) / 5.0) +     # capped at 5, normalized 0-1
        (0.20 * min(recovery, 10) / 10) + # capped at 10, normalized 0-1
        (0.15 * wr / 100)                 # as fraction 0-1
```

Available event-mode metrics: net_profit, pf, wr, max_dd, sortino, recovery, ulcer, calmar.

### 2.5 Execution Filters (Golden Rule: NEVER filter scoring data)

All filters ONLY gate whether a trade's PnL is counted. Scoring always sees ALL trades.

#### Session Filter (Hour + Day)

- Config: `session_filter_enabled`, `session_filter_mode` ("fixed"/"auto"),
  `session_filter_exclude_hours`, `session_filter_exclude_days`
- Applied at lines 551–559: checks `entry.hour` and `entry.weekday()` DIRECTLY
- **These are NYT hours/days** (entry_time is NYT)
- Modes: "fixed" (manual lists) or "auto" (computed from trade distribution)

#### Per-Asset Day Profitability Filter (`exec_day_profit`)

- Tracks cumulative PnL per asset per weekday: `asset_day_pnl[asset][weekday]`
- Updated for EVERY trade (including warmup) — so filter has full history
- At execution: `exec_allowed = asset_day_pnl[asset][trade_weekday] > 0`
- If cumulative PnL for that asset on that weekday is <= 0, trade is skipped
- **BUG**: Lines 354-356 convert entry_time UTC→NYT, but entry_time is already NYT.
  The weekday used for exec_day_profit may be shifted by 4-5 hours from reality.

### 2.6 Directional Multi-Batch Mode

When `directional_batches=True` with `l2_groups` defined:

1. **L1 Quality Gate**: Per batch, pass all assets meeting min_pf + min_wr (no ranking cutoff)
2. **L2 Group Selection**: Per group (batch clusters), rank by `obj_l2`, take top `n_per_group`
3. **L3 Final Assembly**: Rank entire L2 pool by `obj_l3`, take top `n_assets`

### 2.7 Look-Ahead Prevention

Buffer update happens AFTER evaluation (line 571–573):
```python
# NOW update buffer (after evaluation — no look-ahead)
asset_buffers[asset].append(trade_pnl)
```

This ensures the current trade's result is NOT included in the metrics used to decide
whether it should be in the portfolio. The decision is made on the PREVIOUS N trades.

---

## DATA FLOW SUMMARY

### Step 1 (Batch Selection)
```
DB trades → dedup → build_batch_daily_pnl (EXIT date) → build_asset_daily_pnl
                                                               │
  For each window:                                             │
    IS Screening: slice batch PnL to IS → score → top 20     │
    Calibration: slice batch PnL to prev OOS → gate → top 3  │
    Asset Picking: slice asset PnL to IS → score → top 5/batch
    OOS Report: slice combined PnL to OOS → compute metrics
```

### Step 2 (Event Mode)
```
DB trades → sort by exit_time → walk chronologically
                                      │
  For each trade:                     │
    Check warmup → rebalance? → rank last N trades → select Top K
    Is this asset in portfolio? → exec filter? → count PnL → equity curve
    Update buffer (AFTER decision)
```

### Key Differences Between Step 1 and Step 2

| Aspect | Step 1 (Batch Selection) | Step 2 (Event Mode) |
|--------|-------------------------|---------------------|
| Unit | Batches (groups of assets) | Individual assets |
| Time method | Rolling date windows | Trade-by-trade |
| PnL basis | Daily PnL by EXIT date | Per-trade PnL |
| Lookback | Full IS period (months) | Last N trades (configurable) |
| Selection | Per window, per batch | Per trade (or per N trades) |
| Metrics | 23 metrics from scoring module | 8 event metrics |
| Filters | Exec filter on OOS only | Session + day filter on execution |
| Output | Best batch IDs | Equity curve + selected assets |

---

## API ROUTE

**File**: `backend/api/routes/wfo.py`

The WFO endpoint handles all modes through a single Pydantic model (`WFORequest`).
Key routing logic:

1. If `event_mode=True` → calls `run_event_wfo()` (Step 2 engine)
2. If `wfo_mode="batch_selection"` → calls `run_batch_selection()` (Step 1 engine)
3. Otherwise → calls optimizer (correlation-based, not covered here)

Both modes load trades from selected batch_ids via `_load_multi_batch_trades()`.
Shared pre-processing: category filter, slippage/commission stress, risk profile scaling.

---

## FILE MAP

| File | Role |
|------|------|
| `backend/analysis/batch_selection.py` | Step 1 engine (1350 lines) |
| `backend/analysis/batch_selection_scoring.py` | 23 scoring metrics |
| `backend/analysis/event_wfo.py` | Step 2 engine (613 lines) |
| `backend/analysis/wfo_window_gen.py` | Window generation (3 modes) |
| `backend/analysis/wfo_aggregation.py` | Track compilation + cumulative metrics |
| `backend/api/routes/wfo.py` | API endpoint + request model |
| `backend/utils/candle_loader.py` | Candle loading + UTC→NYT conversion |
| `frontend/src/components/wfo/WFOConfigPanel.tsx` | UI config panel |
