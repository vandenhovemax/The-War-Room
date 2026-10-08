/**
 * Generates a comprehensive prompt for Claude.ai to convert
 * a PineScript strategy to Python for The War Room backtester.
 */

export function generateClaudePrompt(pineScript: string, matchTV = false): string {
  const tvSection = matchTV ? TV_SMOOTHING_REFERENCE : "";
  return `# Strategy Conversion — The War Room Backtester

You are helping me convert a trading strategy into Python for my backtester.

## IMPORTANT RULES
1. **Read** the PineScript code at the bottom of this message
2. **Ask me clarifying questions** before writing any code — make sure you understand:
   - Entry conditions (when to go long / short)
   - Exit conditions (take profit, stop loss, signal reversal)
   - Position sizing approach
   - Any edge cases or special logic
3. **Generate** a Python class once I've answered your questions
4. All indicator calculations MUST match TradingView exactly — use the built-in indicator library shown below

---

## Backtester API Reference

### Strategy Class Structure

Your output MUST be a Python class called \`TranspiledStrategy\` that inherits from \`BaseStrategy\`:

\`\`\`python
from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.indicators import ta
import numpy as np
import pandas as pd


class TranspiledStrategy(BaseStrategy):
    def init(self):
        """Called once before the first bar. Pre-compute all indicators here."""
        # Indicators are computed over the full price series, then accessed per-bar in next()
        # Example:
        # self.fast_sma = ta.sma(self.close_series, 20)
        # self.slow_sma = ta.sma(self.close_series, 50)
        pass

    def next(self):
        """Called on every bar. Put entry/exit logic here."""
        bar = self.bar_index

        # Access current bar prices:
        #   self.close, self.open, self.high, self.low, self.volume

        # Access indicator value at current bar:
        #   self.fast_sma.iloc[bar]

        # Access previous bar's price:
        #   self.close_at(1)  — 1 bar ago
        #   self.close_at(2)  — 2 bars ago

        # Check crossovers (returns True/False at current bar):
        #   ta.crossover(self.fast_sma, self.slow_sma).iloc[bar]

        # Enter / Exit:
        #   self.entry(id="Long", direction="long")
        #   self.entry(id="Short", direction="short")
        #   self.close_position(id="Long")
        #   self.exit(id="TP", from_entry="Long", limit=tp_price)
        #   self.exit(id="SL", from_entry="Long", stop=sl_price)
        #   self.exit(id="TSL", from_entry="Long", trail_points=100)
        pass
\`\`\`

### Available Data Properties (current bar)

| Property | Type | Description |
|----------|------|-------------|
| \`self.close\` | float | Current bar close price |
| \`self.open\` | float | Current bar open price |
| \`self.high\` | float | Current bar high price |
| \`self.low\` | float | Current bar low price |
| \`self.volume\` | float | Current bar volume |
| \`self.time\` | pd.Timestamp | Current bar timestamp (use \`.dayofweek\`, \`.hour\`, etc.) |
| \`self.bar_index\` | int | Current bar number (0-based) |
| \`self.last_bar_index\` | int | Index of the last bar |
| \`self.equity\` | float | Current equity (cash + unrealized PnL across all positions) |
| \`self.position_size\` | float | Total qty across all open positions (0 if flat) |
| \`self.position_avg_price\` | float | Entry price of first position |
| \`self.consecutive_wins\` | int | Current streak of consecutive winning trades |
| \`self.consecutive_losses\` | int | Current streak of consecutive losing trades |
| \`self.closed_trades_count\` | int | Total number of closed trades so far |

### Full Series (for indicators in init)

| Property | Type | Description |
|----------|------|-------------|
| \`self.close_series\` | pd.Series | All close prices |
| \`self.open_series\` | pd.Series | All open prices |
| \`self.high_series\` | pd.Series | All high prices |
| \`self.low_series\` | pd.Series | All low prices |
| \`self.volume_series\` | pd.Series | All volume values |

### History Access (previous bars)

| Method | Description |
|--------|-------------|
| \`self.close_at(1)\` | Close price 1 bar ago |
| \`self.close_at(n)\` | Close price n bars ago |
| \`self.open_at(n)\` | Open price n bars ago |
| \`self.high_at(n)\` | High price n bars ago |
| \`self.low_at(n)\` | Low price n bars ago |

### Order Methods

| Method | PineScript Equivalent | Description |
|--------|----------------------|-------------|
| \`self.entry(id="Long", direction="long")\` | \`strategy.entry("Long", strategy.long)\` | Enter long |
| \`self.entry(id="Short", direction="short")\` | \`strategy.entry("Short", strategy.short)\` | Enter short |
| \`self.entry(id="L1", direction="long", qty=5)\` | \`strategy.entry("L1", strategy.long, qty=5)\` | Enter with specific qty |
| \`self.exit(id="SL", from_entry="Long", stop=price)\` | \`strategy.exit("SL", "Long", stop=price)\` | Set stop loss (persistent — checked every bar) |
| \`self.exit(id="TP", from_entry="Long", limit=price)\` | \`strategy.exit("TP", "Long", limit=price)\` | Set take profit (persistent) |
| \`self.exit(id="TSL", from_entry="Long", trail_points=100)\` | \`strategy.exit("TSL", "Long", trail_points=100)\` | Trailing stop (distance in price units) |
| \`self.exit(id="TSL", from_entry="Long", trail_points=100, trail_offset=50)\` | — | Trailing stop that activates after 50 points profit |
| \`self.exit(id="TP1", from_entry="Long", limit=price, qty_percent=50)\` | \`strategy.exit("TP1", "Long", limit=price, qty_percent=50)\` | **Partial close** — close 50% at limit |
| \`self.close_position(id="Long")\` | \`strategy.close("Long")\` | Close specific position immediately |
| \`self.close_all()\` | \`strategy.close_all()\` | Close all positions |
| \`self.cancel(id="SL")\` | \`strategy.cancel("SL")\` | Cancel a pending/exit order by ID |

### Execution Model
- Orders are queued in \`next()\` and executed on the **next bar's open price**
- Slippage (0.01%) and commission (0.055%) are applied automatically
- **Multi-position (pyramiding)** is supported — the engine's \`max_pyramiding\` config controls how many same-direction positions can be open simultaneously
- If \`max_pyramiding=1\` (default) and you call \`entry("Short")\` while long, it auto-closes the long first
- **Exit orders are persistent** — once registered with \`self.exit()\`, they are checked every bar until triggered or the position closes. Calling \`self.exit()\` again with the **same \`id\`** updates/replaces that exit order
- **Stop is checked before limit** (conservative — worst case fills first)
- Gap-through: if bar opens past a stop/limit price, fills at the open price

### Partial Close (qty_percent)
Use \`qty_percent\` on \`self.exit()\` to close a percentage of a position:
\`\`\`python
# Close 50% at TP1, let trailing stop manage the rest
self.exit(id="TP1", from_entry="L1", limit=tp_price, qty_percent=50)
self.exit(id="TSL1", from_entry="L1", trail_points=atr_val * 2)
\`\`\`
- \`qty_percent=50\` closes 50% of the position's qty when the limit is hit
- The position stays open with the remaining 50%, and other exit orders (like TSL) continue to manage it
- Valid range: 1–100 (100 = full close, same as not specifying it)

### Warmup Period
Set \`self._warmup_bars\` in \`init()\` to suppress all orders during the first N bars while indicators warm up.
During warmup, \`next()\` still runs (indicators compute), but all orders are suppressed.

**CRITICAL — HTF-aware warmup formula:**
The warmup MUST account for higher-timeframe indicator convergence. HTF indicators (daily RSI, 4h MA, etc.) return NaN until they have enough bars — and those NaN values block trades anyway. The warmup should skip that dead zone.

Compute warmup like this at the END of \`init()\`:
\`\`\`python
def init(self):
    # ... all self.input() calls ...
    # ... all indicator computations ...

    # ─── WARMUP ───────────────────────────────────────────────────
    # 1. LTF indicator warmup (max of all indicator lengths on primary TF)
    indicator_warmup = max(
      my_sma_len,
      my_atr_len,
      my_rsi_len + my_rsi_ma_len if rsi_enabled else 0,
      # ... include all indicator lengths used on the primary TF ...
    )

    # 2. HTF indicator convergence (convert to LTF bars)
    #    Formula: htf_indicator_length * (htf_minutes / ltf_minutes)
    def _tf_to_minutes(tf_str):
      if tf_str == "W": return 7 * 1440
      if tf_str and tf_str.endswith("D"):
        d = int(tf_str[:-1]) if len(tf_str) > 1 else 1
        return d * 1440
      try: return int(tf_str)
      except (TypeError, ValueError): return 0

    primary_mins = 30  # default
    if len(self._df) > 1:
      primary_mins = max(1, (self._df.index[1] - self._df.index[0]).total_seconds() / 60)

    htf_warmup = 0
    # For each HTF indicator: length * (htf_mins / primary_mins)
    # This matches the NaN cutoff — no trades fire before this anyway.
    # Example for daily RSI(14) + RSI_MA(14) on 30m chart:
    #   (14 + 14) * (1440 / 30) = 1344 bars ≈ 28 days
    # Example for 4h MA(20) on 30m chart:
    #   20 * (240 / 30) = 160 bars ≈ 3.3 days

    # Add one block per active HTF indicator:
    # if rsi_on_htf:
    #   htf_mins = _tf_to_minutes(rsi_tf_key)  # e.g. "D" → 1440
    #   htf_warmup = max(htf_warmup, int((rsi_len + rsi_ma_len) * htf_mins / primary_mins))
    # if ma_filter_on_htf:
    #   htf_mins = _tf_to_minutes(ma_tf_key)
    #   htf_warmup = max(htf_warmup, int(ma_len * htf_mins / primary_mins))

    # 3. Final warmup = max of all sources
    self._warmup_bars = max(indicator_warmup, htf_warmup)
\`\`\`

### Multi-Timeframe Data (request_security)
Access higher-timeframe data in your strategy. Precompute indicators in \`init()\`, look up values in \`next()\`:
\`\`\`python
def init(self):
    # Precompute indicators on higher-TF data (if available)
    for tf in ["60", "240", "720", "D"]:
        if tf in self._tf_data:
            self._tf_data[tf]["rsi"] = ta.rsi(self._tf_data[tf]["close"], 14)
            self._tf_data[tf]["sma200"] = ta.sma(self._tf_data[tf]["close"], 200)

def next(self):
    # Look up the most recent completed higher-TF bar value
    rsi_4h = self.request_security("240", "rsi")    # 4h RSI
    sma_d = self.request_security("D", "sma200")    # Daily SMA 200

    # Returns NaN if no data available for that timeframe
    if np.isnan(rsi_4h):
        return
\`\`\`
**Available timeframe keys**: \`"1"\`, \`"5"\`, \`"15"\`, \`"30"\`, \`"60"\`, \`"120"\`, \`"240"\`, \`"720"\`, \`"D"\`, \`"W"\`
**Available columns**: \`"open"\`, \`"high"\`, \`"low"\`, \`"close"\`, \`"volume"\`, plus any custom columns you add in \`init()\`.
**Lookback**: \`request_security()\` returns the single most recent value. For lookback, precompute it:
\`\`\`python
self._tf_data["240"]["rsi_prev"] = self._tf_data["240"]["rsi"].shift(1)
\`\`\`

### Position Management
| Method/Property | Description |
|----------------|-------------|
| \`self.get_position("L1")\` | Get a specific position by entry ID (returns Position or None) |
| \`pos.side\` | "long" or "short" |
| \`pos.entry_price\` | Entry fill price |
| \`pos.qty\` | Current position quantity (reduced after partial close) |

### State Snapshot — Live Trading Compatibility (IMPORTANT)

After a backtest completes, the engine captures a **state snapshot** of all strategy variables for two purposes:
1. **Batch Refresh** — continuing a backtest forward with new data without re-running history
2. **Live Trading Warmup** — the live engine restores the snapshot so the strategy starts with full context (trend state, swing points, counters, etc.)

**Rules for strategy variables:**
- Store all strategy state as **simple Python types**: \`int\`, \`float\`, \`bool\`, \`str\`, \`list\`, \`dict\`, or \`None\`
- Variables stored as \`self.my_var\` (no underscore prefix) are automatically captured
- Variables starting with \`_\` (like \`self._df\`, \`self._bar\`) are engine internals and are NOT captured
- \`pd.Series\` and \`pd.DataFrame\` attributes are NOT captured (they are recomputed in \`init()\`)

**What this means for your code:**
- Indicators (pd.Series) go on \`self\` with any name — they're recomputed each run via \`init()\`
- Strategy state (trend direction, last prices, counters, tracking arrays) MUST be simple types
- Use \`float('nan')\` (not \`np.nan\`) for NaN values in state variables — it serializes correctly
- Use \`list\` (not numpy arrays) for tracking arrays

**Good pattern:**
\`\`\`python
def init(self):
    # These are pd.Series — recomputed in init(), NOT captured in snapshot
    self.rsi_series = ta.rsi(self.close_series, 14)
    self.atr_series = ta.atr(self.high_series, self.low_series, self.close_series, 14)

    # These are simple types — captured in snapshot for live trading continuity
    self.trend = 0           # int: 1=bull, -1=bear, 0=init
    self.last_swing_high = float('nan')  # float
    self.last_swing_low = float('nan')   # float
    self.swing_bar = -1      # int
    self.trade_ids = []      # list of strings
    self.trade_entries = []   # list of floats
\`\`\`

**Bad pattern (breaks snapshot):**
\`\`\`python
# DON'T: numpy arrays aren't JSON-serializable
self.price_history = np.array([])  # Use list instead: self.price_history = []

# DON'T: storing DataFrames as state
self.htf_cache = pd.DataFrame()  # Use dict of lists instead
\`\`\`

---

${tvSection}
## Indicator Library (import as \`ta\` — matches TradingView)

All indicators return \`pd.Series\`. Compute them in \`init()\`, access per-bar in \`next()\` via \`.iloc[self.bar_index]\`.

### Moving Averages
| Function | PineScript | Description |
|----------|-----------|-------------|
| \`ta.sma(source, length)\` | \`ta.sma(close, 20)\` | Simple Moving Average |
| \`ta.ema(source, length)\` | \`ta.ema(close, 20)\` | Exponential Moving Average |
| \`ta.wma(source, length)\` | \`ta.wma(close, 20)\` | Weighted Moving Average |
| \`ta.vwma(source, volume, length)\` | \`ta.vwma(close, volume, 20)\` | Volume-Weighted MA |
| \`ta.rma(source, length)\` | \`ta.rma(close, 14)\` | Wilder's Smoothing (RMA) |
| \`ta.hma(source, length)\` | \`ta.hma(close, 20)\` | Hull Moving Average |
| \`ta.alma(source, length, offset, sigma)\` | \`ta.alma(close, 20, 0.85, 6)\` | Arnaud Legoux MA |

### Oscillators
| Function | PineScript | Description |
|----------|-----------|-------------|
| \`ta.rsi(source, length)\` | \`ta.rsi(close, 14)\` | Relative Strength Index |
| \`ta.macd(source, fast, slow, signal)\` | \`ta.macd(close, 12, 26, 9)\` | Returns (macd, signal, histogram) |
| \`ta.stoch(high, low, close, length)\` | \`ta.stoch(high, low, close, 14)\` | Stochastic %K |
| \`ta.cci(high, low, close, length)\` | \`ta.cci(high, low, close, 20)\` | Commodity Channel Index |
| \`ta.mfi(high, low, close, vol, length)\` | \`ta.mfi(high, low, close, volume, 14)\` | Money Flow Index |
| \`ta.dmi(high, low, close, length)\` | \`ta.dmi(high, low, close, 14)\` | DMI/ADX → (plus_di, minus_di, adx) |

### Volatility
| Function | PineScript | Description |
|----------|-----------|-------------|
| \`ta.atr(high, low, close, length)\` | \`ta.atr(high, low, close, 14)\` | Average True Range |
| \`ta.tr(high, low, close)\` | \`ta.tr(high, low, close)\` | True Range |
| \`ta.bb(source, length, mult)\` | \`ta.bb(close, 20, 2.0)\` | Bollinger Bands → (upper, basis, lower) |
| \`ta.kc(high, low, close, length, mult)\` | \`ta.kc(high, low, close, 20, 1.5)\` | Keltner Channels → (upper, basis, lower) |

### Trend
| Function | PineScript | Description |
|----------|-----------|-------------|
| \`ta.supertrend(high, low, close, length, mult)\` | \`ta.supertrend(..., 10, 3.0)\` | SuperTrend → (line, direction) |
| \`ta.donchian(high, low, length)\` | \`ta.donchian(high, low, 20)\` | Donchian Channels → (upper, basis, lower) |
| \`ta.ichimoku(high, low, conv, base, span_b)\` | \`ta.ichimoku(high, low, 9, 26, 52)\` | Ichimoku → (tenkan, kijun, senkou_a, senkou_b) |
| \`ta.vwap(high, low, close, volume)\` | \`ta.vwap(high, low, close, volume)\` | VWAP (cumulative, no daily reset) |
| \`ta.sar(high, low, start, inc, max)\` | \`ta.sar(high, low, 0.02, 0.02, 0.2)\` | Parabolic SAR |

### Utility / Crossovers
| Function | PineScript | Description |
|----------|-----------|-------------|
| \`ta.crossover(a, b)\` | \`ta.crossover(fast, slow)\` | True when a crosses above b |
| \`ta.crossunder(a, b)\` | \`ta.crossunder(fast, slow)\` | True when a crosses below b |
| \`ta.highest(source, length)\` | \`ta.highest(high, 20)\` | Highest value in period |
| \`ta.lowest(source, length)\` | \`ta.lowest(low, 20)\` | Lowest value in period |
| \`ta.change(source, length)\` | \`ta.change(close, 1)\` | Difference from n bars ago |
| \`ta.barssince(condition)\` | \`ta.barssince(cond)\` | Bars since condition was true |
| \`ta.pivothigh(source, left, right)\` | \`ta.pivothigh(high, 5, 5)\` | Pivot high detection |
| \`ta.pivotlow(source, left, right)\` | \`ta.pivotlow(low, 5, 5)\` | Pivot low detection |
| \`ta.cum(source)\` | \`ta.cum(volume)\` | Cumulative sum |

### If an indicator is NOT in this list
Implement it manually in the \`init()\` method using numpy/pandas, following TradingView's exact formula. Add a comment linking to the TV documentation for that indicator.

---

## Configurable Parameters (self.input)

**CRITICAL**: All strategy parameters must be configurable using \`self.input()\`. This allows users to create different configurations without modifying code.

### Usage in init():
\`\`\`python
def init(self):
    # Integer parameter
    fast_len = self.input(defval=20, title="Fast Length")
    slow_len = self.input(defval=50, title="Slow Length")

    # Float parameter
    atr_mult = self.input(defval=1.5, title="ATR Multiplier")

    # Boolean parameter
    use_filter = self.input(defval=True, title="Use Trend Filter")

    # String parameter with options
    ma_type = self.input(defval="SMA", title="MA Type", options=["SMA", "EMA", "WMA"])

    # Use the values in indicator calculations
    self.fast = ta.sma(self.close_series, fast_len)
    self.slow = ta.sma(self.close_series, slow_len)
\`\`\`

### Rules:
- Every parameter from the PineScript \`input()\` calls MUST become a \`self.input()\` call
- \`defval\` = default value (required), \`title\` = display name (required)
- Optional kwargs: \`minval\`, \`maxval\`, \`step\`, \`options\` (list of strings)
- Place ALL \`self.input()\` calls at the TOP of \`init()\`, before indicator calculations
- Never hardcode values that are configurable in the original PineScript

### DO NOT include these parameters — they are backtester-level settings:
The backtester UI has its own controls for these. **Skip any PineScript inputs that match**:
- **Trade Direction** (long/short/both) — controlled by the Direction filter
- **Risk Mode / Risk Per Trade / Risk % / Risk Amount / Position Sizing** — controlled by the Position Sizing panel
- **Martingale / Anti-Martingale** — controlled by the Martingale toggle
- **Volatility Filter / ATR Ceiling / ATR Filter / Vol Filter** — controlled by the Volatility Filter toggle

If the PineScript has \`input()\` calls for any of these, do NOT translate them to \`self.input()\`. The engine handles them separately.

### PARAMETERS block (REQUIRED output):

After the Python code, you MUST output a \`PARAMETERS:\` block — a JSON array describing each parameter:

\`\`\`
PARAMETERS:
[
  {"name": "Fast Length", "type": "int", "default": 20, "min": 1, "max": 500},
  {"name": "Slow Length", "type": "int", "default": 50, "min": 1, "max": 500},
  {"name": "ATR Multiplier", "type": "float", "default": 1.5, "min": 0.1, "max": 10.0, "step": 0.1},
  {"name": "Use Trend Filter", "type": "bool", "default": true},
  {"name": "MA Type", "type": "string", "default": "SMA", "options": ["SMA", "EMA", "WMA"]}
]
\`\`\`

Type must be one of: \`int\`, \`float\`, \`bool\`, \`string\`. Include \`min\`, \`max\`, \`step\` where applicable.

---

## Working Example — Simple SMA Crossover

\`\`\`python
from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.indicators import ta
import numpy as np
import pandas as pd


class TranspiledStrategy(BaseStrategy):
    def init(self):
        fast_len = self.input(defval=20, title="Fast Length")
        slow_len = self.input(defval=50, title="Slow Length")

        self.fast = ta.sma(self.close_series, fast_len)
        self.slow = ta.sma(self.close_series, slow_len)
        self.cross_up = ta.crossover(self.fast, self.slow)
        self.cross_down = ta.crossunder(self.fast, self.slow)
        self._warmup_bars = slow_len

    def next(self):
        bar = self.bar_index

        if self.cross_up.iloc[bar]:
            self.entry(id="Long", direction="long")

        if self.cross_down.iloc[bar]:
            self.close_position(id="Long")
\`\`\`

## Working Example — Advanced (Multi-Position + Partial Close + Multi-TF + HTF Warmup)

\`\`\`python
from backend.backtester.base_strategy import BaseStrategy
from backend.backtester.indicators import ta
import numpy as np
import pandas as pd


class TranspiledStrategy(BaseStrategy):
    def init(self):
        atr_len = self.input(defval=14, title="ATR Length")
        rsi_len = self.input(defval=14, title="RSI Length")
        tp_mult = self.input(defval=2.0, title="TP ATR Multiplier")
        sl_mult = self.input(defval=1.0, title="SL ATR Multiplier")
        trail_mult = self.input(defval=1.5, title="Trail ATR Multiplier")
        htf_sma_len = self.input(defval=50, title="HTF SMA Length")

        # Indicators (pd.Series — recomputed each run, NOT in snapshot)
        self.atr = ta.atr(self.high_series, self.low_series, self.close_series, atr_len)
        self.rsi = ta.rsi(self.close_series, rsi_len)

        # Multi-TF: precompute 4h trend filter
        if "240" in self._tf_data:
            self._tf_data["240"]["sma50"] = ta.sma(self._tf_data["240"]["close"], htf_sma_len)

        # Strategy state (simple types — captured in snapshot for live trading)
        self.tp_mult = tp_mult
        self.sl_mult = sl_mult
        self.trail_mult = trail_mult
        self.last_signal_bar = -1     # int: tracks last entry bar
        self.trade_count = 0          # int: running counter

        # ─── WARMUP (must be at end of init) ──────────────────────
        # LTF indicator warmup
        indicator_warmup = max(atr_len, rsi_len)

        # HTF indicator convergence → convert to LTF bars
        def _tf_to_minutes(tf_str):
          if tf_str == "W": return 7 * 1440
          if tf_str and tf_str.endswith("D"):
            d = int(tf_str[:-1]) if len(tf_str) > 1 else 1
            return d * 1440
          try: return int(tf_str)
          except (TypeError, ValueError): return 0

        primary_mins = 30
        if len(self._df) > 1:
          primary_mins = max(1, (self._df.index[1] - self._df.index[0]).total_seconds() / 60)

        htf_warmup = 0
        # 4h SMA(50) needs 50 4h bars before first non-NaN
        if "240" in self._tf_data:
          htf_mins = _tf_to_minutes("240")
          htf_warmup = max(htf_warmup, int(htf_sma_len * htf_mins / primary_mins))

        self._warmup_bars = max(indicator_warmup, htf_warmup)

    def next(self):
        bar = self.bar_index
        atr_val = self.atr.iloc[bar]
        rsi_val = self.rsi.iloc[bar]

        if np.isnan(atr_val) or np.isnan(rsi_val):
            return

        # 4h trend filter — returns NaN until HTF has enough data
        sma_4h = self.request_security("240", "sma50")
        if np.isnan(sma_4h):
            return  # HTF indicator not ready yet
        trend_up = self.close > sma_4h

        # Entry: RSI oversold + uptrend
        if rsi_val < 30 and trend_up and self.position_size == 0:
            self.entry(id="L1", direction="long", qty=10)
            self.last_signal_bar = bar
            self.trade_count += 1

            # Partial TP at 50%, trailing stop for rest
            tp_price = self.close + atr_val * self.tp_mult
            sl_price = self.close - atr_val * self.sl_mult
            self.exit(id="TP1", from_entry="L1", limit=tp_price, qty_percent=50)
            self.exit(id="SL1", from_entry="L1", stop=sl_price)
            self.exit(id="TSL1", from_entry="L1",
                      trail_points=atr_val * self.trail_mult,
                      trail_offset=atr_val * 0.5)

        # Equity-based risk check
        if self.equity < self._initial_capital * 0.9:
            self.close_all()  # stop trading if down 10%
\`\`\`

---

## My Strategy (PineScript)

\`\`\`pine
${pineScript || "[PASTE YOUR PINESCRIPT CODE HERE]"}
\`\`\`

---

Please analyze this strategy and ask me any questions you need to fully understand the logic before generating the Python code. Focus on:
1. What exactly triggers entries and exits?
2. Are there any filters or conditions I should know about?
3. How should position sizing work?
4. Any special handling for the first N bars (indicator warmup)?
5. Does it use multiple timeframes? If so, which ones?
6. Does it use partial closes (TP at X%, trail the rest)?
`;
}

/**
 * TradingView smoothing method reference — injected when "Match TV" is on.
 * Tells Claude exactly which smoothing each indicator uses in TV so
 * manual implementations match bar-for-bar.
 */
const TV_SMOOTHING_REFERENCE = `
## TradingView Indicator Smoothing Reference (MUST MATCH)

When implementing any indicator manually (not using the built-in \`ta.*\` library), you MUST use the exact smoothing method TradingView uses. Mismatched smoothing is the #1 cause of indicator divergence.

### SMA Group (Simple Moving Average)
| Indicator | Component | Smoothing |
|-----------|-----------|-----------|
| Bollinger Bands | Basis + Bands | SMA basis + **population** std dev (ddof=0) |
| SMA | — | Arithmetic mean |
| Stochastic | %K and %D | SMA (smoothing applied to raw %K to get %D) |
| CCI | — | SMA of typical price + mean absolute deviation |

### EMA Group (Exponential Moving Average, alpha = 2/(n+1))
| Indicator | Component | Smoothing |
|-----------|-----------|-----------|
| MACD | Fast/Slow/Signal | EMA (all three lines) |
| EMA | — | Standard EMA |
| TEMA | — | Triple-layered EMA |
| Keltner Channels | Basis (middle line) | EMA |
| VWMA | — | EMA applied to volume-weighted price |

### RMA Group (Wilder's Smoothing, alpha = 1/n) — MOST COMMON SOURCE OF ERRORS
| Indicator | Component | Smoothing |
|-----------|-----------|-----------|
| RSI | Avg Gain/Loss | **RMA** (NOT EMA, NOT SMA) |
| ATR | True Range avg | **RMA** (standard TV default) |
| ADX / DMI | +DI, -DI, ADX | **RMA** for all components |
| SuperTrend | Bands | Uses ATR which uses **RMA** |
| MFI | Money Flow | **RMA** on money flow |
| Stochastic RSI | Base RSI | Inherits **RMA** from RSI |
| Keltner Channels | Outer bands | ATR (**RMA**), but basis is EMA |

### No Moving Average
| Indicator | Component | Method |
|-----------|-----------|--------|
| Donchian Channels | Bands | Highest high / lowest low (no MA) |
| Ichimoku Cloud | All lines | (High + Low) / 2 midpoint (no MA) |
| VWAP | — | Cumulative sum (price * vol) / total vol |
| Parabolic SAR | — | Recursive acceleration factor (no MA) |

### Key Rule
- RMA alpha = \`1/n\` — use \`ta.rma()\` or \`pd.Series.ewm(alpha=1/length, adjust=False).mean()\`
- EMA alpha = \`2/(n+1)\` — use \`ta.ema()\` or \`pd.Series.ewm(span=length, adjust=False).mean()\`
- **Never substitute EMA where RMA is required** — they produce different results

---

`;
