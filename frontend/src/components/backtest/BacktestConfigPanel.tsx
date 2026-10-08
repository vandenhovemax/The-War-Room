"use client";
import type { StrategyItem } from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import DirectionFilter from "./DirectionFilter";
import type { DirectionValue } from "./DirectionFilter";

export interface ConfigPanelProps {
  strategyId: number;
  setStrategyId: (v: number) => void;
  strategies: StrategyItem[];
  direction: DirectionValue;
  setDirection: (v: DirectionValue) => void;
  timeframe: string;
  setTimeframe: (v: string) => void;
  capital: string;
  setCapital: (v: string) => void;
  commission: string;
  setCommission: (v: string) => void;
  includeCosts: boolean;
  setIncludeCosts: (v: boolean) => void;
  slippageMode: string;
  setSlippageMode: (v: "pct" | "pips" | "risk_pct") => void;
  slippageValue: string;
  setSlippageValue: (v: string) => void;
  leverage: string;
  setLeverage: (v: string) => void;
  pyramiding: string;
  setPyramiding: (v: string) => void;
  minAdv: string;
  setMinAdv: (v: string) => void;
  maxAdv: string;
  setMaxAdv: (v: string) => void;
  minPositions: string;
  setMinPositions: (v: string) => void;
  startDate: string;
  setStartDate: (v: string) => void;
  endDate: string;
  setEndDate: (v: string) => void;
  minDataDate: string;
  setMinDataDate: (v: string) => void;
  fundingRate: string;
  setFundingRate: (v: string) => void;
  spread: string;
  setSpread: (v: string) => void;
  prefilterEnabled: boolean;
  setPrefilterEnabled: (v: boolean) => void;
  prefilterMode: "trend" | "meanrev";
  setPrefilterMode: (v: "trend" | "meanrev") => void;
  prefilterHurstMin: string;
  setPrefilterHurstMin: (v: string) => void;
  prefilterTerPctl: string;
  setPrefilterTerPctl: (v: string) => void;
  prefilterHurstMax: string;
  setPrefilterHurstMax: (v: string) => void;
  prefilterVrMax: string;
  setPrefilterVrMax: (v: string) => void;
  prefilterMinCr: string;
  setPrefilterMinCr: (v: string) => void;
  processOrdersOnClose: boolean;
  setProcessOrdersOnClose: (v: boolean) => void;
}

const TFS = ["1", "3", "5", "15", "30", "60", "120", "240", "D"];
const tfLabel = (v: string) => (v === "D" ? "1D" : `${v}m`);

export default function BacktestConfigPanel(p: ConfigPanelProps) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
      {/* Row 1: Strategy, Direction, Timeframe */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Field label="Strategy" hint="Select a strategy with Python source code.">
          <select value={p.strategyId} onChange={(e) => p.setStrategyId(Number(e.target.value))}
            className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none">
            <option value={0}>Select...</option>
            {p.strategies.filter((s) => s.has_python).map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Direction" hint="Long Only = only longs. Short Only = only shorts. Both = longs and shorts independently. Long+Short = can hold both directions simultaneously.">
          <DirectionFilter value={p.direction} onChange={p.setDirection} />
        </Field>
        <Field label="Timeframe" hint="Candle timeframe for the backtest. Must match available downloaded data.">
          <select value={p.timeframe} onChange={(e) => p.setTimeframe(e.target.value)}
            className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none">
            {TFS.map((tf) => <option key={tf} value={tf}>{tfLabel(tf)}</option>)}
          </select>
        </Field>
      </div>

      {/* Trading Costs toggle */}
      <div className="flex items-center gap-3 py-1">
        <button
          type="button"
          onClick={() => p.setIncludeCosts(!p.includeCosts)}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            p.includeCosts ? "bg-ag-accent" : "bg-ag-border"
          }`}
        >
          <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
            p.includeCosts ? "translate-x-[18px]" : "translate-x-[3px]"
          }`} />
        </button>
        <span className="text-xs text-ag-text-muted">
          Trading Costs {p.includeCosts ? <span className="text-ag-accent font-medium">ON</span> : <span className="text-ag-text-muted">OFF</span>}
        </span>
        {!p.includeCosts && (
          <span className="text-[10px] text-ag-text-muted/60">All trading costs disabled — pure strategy validation</span>
        )}
      </div>

      {/* Process Orders on Close toggle */}
      <div className="flex items-center gap-3 py-1">
        <button
          type="button"
          onClick={() => p.setProcessOrdersOnClose(!p.processOrdersOnClose)}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            p.processOrdersOnClose ? "bg-ag-accent" : "bg-ag-border"
          }`}
        >
          <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
            p.processOrdersOnClose ? "translate-x-[18px]" : "translate-x-[3px]"
          }`} />
        </button>
        <span className="text-xs text-ag-text-muted">
          Fill on Close{" "}
          {p.processOrdersOnClose ? <span className="text-ag-accent font-medium">ON</span> : <span className="text-ag-text-muted">OFF</span>}
        </span>
        <HintTip text="When enabled, entry orders fill at the current bar's close instead of the next bar's open. Matches TradingView's process_orders_on_close=true behavior. Required for strategies that detect and act on same-bar structure." />
      </div>

      {/* Row 2: Capital, Commission, Slippage, Leverage, Pyramiding, Min ADV, Max ADV */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4">
        <Field label="Capital ($)" hint="Starting equity. In Individual mode each symbol gets its own pool. In Portfolio mode all symbols share this amount.">
          <Input value={p.capital} onChange={p.setCapital} />
        </Field>
        <div className={!p.includeCosts ? "opacity-40 pointer-events-none" : ""}>
          <Field label="Commission %" hint="Trading fee per trade as a percentage of notional value. Bybit taker fee is 0.055%. Maker fee is 0.02%.">
            <Input value={p.commission} onChange={p.setCommission} />
          </Field>
        </div>
        <div className={!p.includeCosts ? "opacity-40 pointer-events-none" : ""}>
          <Field label="Slippage" hint="Simulated price slippage on each order. % Price = percentage of entry price. Pips = fixed price offset. % Risk = percentage of risk amount.">
            <div className="flex gap-1">
              <select value={p.slippageMode} onChange={(e) => p.setSlippageMode(e.target.value as "pct")}
                className="w-20 px-1.5 py-2 bg-ag-dark border border-ag-border rounded-l text-xs focus:border-ag-accent outline-none">
                <option value="pct">% Price</option>
                <option value="pips">Pips</option>
                <option value="risk_pct">% Risk</option>
              </select>
              <input value={p.slippageValue} onChange={(e) => p.setSlippageValue(e.target.value)} placeholder="0"
                className="flex-1 min-w-0 px-2 py-2 bg-ag-dark border border-ag-border rounded-r text-sm focus:border-ag-accent outline-none" />
            </div>
          </Field>
        </div>
        <div className={!p.includeCosts ? "opacity-40 pointer-events-none" : ""}>
          <Field label="Funding %/day" hint="Daily funding rate for perpetual contracts. Applied based on trade hold duration. Typical: 0.01-0.03%. 0 = disabled.">
            <Input value={p.fundingRate} onChange={p.setFundingRate} placeholder="0" />
          </Field>
        </div>
        <div className={!p.includeCosts ? "opacity-40 pointer-events-none" : ""}>
          <Field label="Spread %" hint="Bid-ask spread %. Half-spread applied on each side (entry + exit). Typical: 0.01-0.05%. 0 = disabled.">
            <Input value={p.spread} onChange={p.setSpread} placeholder="0" />
          </Field>
        </div>
        <Field label="Leverage" hint="Buying power multiplier. 1 = no leverage. Higher values increase position size and risk proportionally.">
          <Input value={p.leverage} onChange={p.setLeverage} />
        </Field>
        <Field label="Pyramiding" hint="Maximum number of same-direction positions open at the same time. 1 = only one position at a time (no pyramiding).">
          <Input value={p.pyramiding} onChange={p.setPyramiding} placeholder="1" />
          <p className="text-[10px] text-ag-text-muted mt-0.5">Max same-dir positions</p>
        </Field>
        <Field label="Min ADV (USDT)" hint="Minimum Average Daily Volume in USDT. Symbols below this threshold are skipped before running. 0 = no filter.">
          <Input value={p.minAdv} onChange={p.setMinAdv} placeholder="0 = no filter" />
        </Field>
        <Field label="Max ADV (USDT)" hint="Maximum Average Daily Volume in USDT. Symbols above this threshold are skipped. 0 = no filter.">
          <Input value={p.maxAdv} onChange={p.setMaxAdv} placeholder="0 = no filter" />
        </Field>
      </div>

      {/* Row 3: Filters + Dates */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-4">
        <Field label="Min Positions" hint="Minimum number of trades a symbol must produce to be included in batch results. Symbols with fewer trades are filtered out after backtesting. 0 = no filter.">
          <Input value={p.minPositions} onChange={p.setMinPositions} placeholder="0 = no filter" />
        </Field>
        <Field label="Start Date" hint="Only backtest data from this date onwards. Leave empty to use all available data.">
          <input type="date" value={p.startDate} onChange={(e) => p.setStartDate(e.target.value)}
            className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
        </Field>
        <Field label="End Date" hint="Only backtest data up to this date. Leave empty to use all available data.">
          <input type="date" value={p.endDate} onChange={(e) => p.setEndDate(e.target.value)}
            className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
        </Field>
        <Field label="Data Must Start Before" hint="Skip assets whose earliest candle is after this date. Use this to exclude coins that are too new and lack enough history. Leave empty to include all.">
          <input type="date" value={p.minDataDate} onChange={(e) => p.setMinDataDate(e.target.value)}
            className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
        </Field>
      </div>

      {/* L0 Pre-Filter */}
      <div className="border border-ag-border rounded-lg p-3 space-y-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => p.setPrefilterEnabled(!p.prefilterEnabled)}
            className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
              p.prefilterEnabled ? "bg-ag-accent" : "bg-ag-border"
            }`}
          >
            <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform ${
              p.prefilterEnabled ? "translate-x-[18px]" : "translate-x-[3px]"
            }`} />
          </button>
          <span className="text-xs font-medium">
            L0 Pre-Filter {p.prefilterEnabled ? <span className="text-ag-accent">ON</span> : <span className="text-ag-text-muted">OFF</span>}
          </span>
          <span className="text-[10px] text-ag-text-muted">
            Skip assets that fail structural quality checks before backtesting
          </span>
        </div>

        {p.prefilterEnabled && (
          <div className="flex items-center gap-4 flex-wrap pl-12">
            <div className="flex items-center gap-2">
              <label className="text-[10px] text-ag-text-muted">Mode:</label>
              <select value={p.prefilterMode} onChange={(e) => p.setPrefilterMode(e.target.value as "trend" | "meanrev")}
                className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs focus:border-ag-accent outline-none">
                <option value="trend">Trend</option>
                <option value="meanrev">Mean-Rev</option>
              </select>
            </div>
            {p.prefilterMode === "trend" ? (
              <>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Min Hurst:</label>
                  <input type="number" min={0.3} max={0.8} step={0.01} value={p.prefilterHurstMin}
                    onChange={(e) => p.setPrefilterHurstMin(e.target.value)}
                    className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center focus:border-ag-accent outline-none" />
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">TER Pctl:</label>
                  <input type="number" min={0} max={75} step={5} value={p.prefilterTerPctl}
                    onChange={(e) => p.setPrefilterTerPctl(e.target.value)}
                    className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center focus:border-ag-accent outline-none" />
                </div>
              </>
            ) : (
              <>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Max Hurst:</label>
                  <input type="number" min={0.3} max={0.7} step={0.01} value={p.prefilterHurstMax}
                    onChange={(e) => p.setPrefilterHurstMax(e.target.value)}
                    className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center focus:border-ag-accent outline-none" />
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Max VR:</label>
                  <input type="number" min={0.5} max={1.5} step={0.05} value={p.prefilterVrMax}
                    onChange={(e) => p.setPrefilterVrMax(e.target.value)}
                    className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center focus:border-ag-accent outline-none" />
                </div>
                <div className="flex items-center gap-1.5">
                  <label className="text-[10px] text-ag-text-muted">Min Cross Rate:</label>
                  <input type="number" min={0} max={0.5} step={0.01} value={p.prefilterMinCr}
                    onChange={(e) => p.setPrefilterMinCr(e.target.value)}
                    className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-center focus:border-ag-accent outline-none" />
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-ag-text-muted mb-1">
        {label}{hint && <HintTip text={hint} />}
      </label>
      {children}
    </div>
  );
}

function Input({ value, onChange, placeholder, disabled }: {
  value: string; onChange: (v: string) => void; placeholder?: string; disabled?: boolean;
}) {
  return (
    <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
      disabled={disabled}
      className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none disabled:opacity-40" />
  );
}
