"use client";
import HintTip from "@/components/common/HintTip";

interface Props {
  riskMode: string;
  setRiskMode: (v: string) => void;
  riskValue: string;
  setRiskValue: (v: string) => void;
  martingaleEnabled: boolean;
  setMartingaleEnabled: (v: boolean) => void;
  martingaleType: string;
  setMartingaleType: (v: string) => void;
  martingaleMultiplier: string;
  setMartingaleMultiplier: (v: string) => void;
  martingaleMaxStreak: string;
  setMartingaleMaxStreak: (v: string) => void;
  martingaleScope?: string;
  setMartingaleScope?: (v: string) => void;
  volFilterEnabled: boolean;
  setVolFilterEnabled: (v: boolean) => void;
  volFilterAtrCeiling: string;
  setVolFilterAtrCeiling: (v: string) => void;
  volFilterAtrLength: string;
  setVolFilterAtrLength: (v: string) => void;
  normalized?: boolean;
  setNormalized?: (v: boolean) => void;
  enforceBybitLimits?: boolean;
  setEnforceBybitLimits?: (v: boolean) => void;
  expectedLiveSize?: string;
  setExpectedLiveSize?: (v: string) => void;
  maxMarginPerTrade?: string;
  setMaxMarginPerTrade?: (v: string) => void;
  marginFilterTolerance?: string;
  setMarginFilterTolerance?: (v: string) => void;
  bybitRejectPct?: string;
  setBybitRejectPct?: (v: string) => void;
}

export default function PositionSizingSection(p: Props) {
  const riskActive = parseFloat(p.riskValue) > 0;
  const isNormalized = p.normalized ?? false;

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ag-text">Position Sizing</h3>
        {p.setNormalized && (
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <span className="text-xs text-ag-text-muted">Normalized Mode</span>
            <HintTip text="Normalizes all trade sizes to a fixed $ risk. This makes IS/OOS periods fully independent, which is essential for reliable portfolio optimization. Position sizing (%, martingale, Kelly) is configured at the portfolio level instead." />
            <input type="checkbox" checked={isNormalized}
              onChange={(e) => p.setNormalized!(e.target.checked)}
              className="accent-ag-accent w-4 h-4" />
          </label>
        )}
      </div>

      {/* Normalized info banner */}
      {isNormalized && (
        <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg px-4 py-2.5 text-xs text-blue-300">
          All trades use a fixed $ risk — IS and OOS are fully independent.
          Position sizing, martingale, and Kelly criterion are configured at the portfolio level after optimization.
        </div>
      )}
      {!isNormalized && p.setNormalized && (
        <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-4 py-2.5 text-xs text-yellow-300">
          Non-normalized mode: IS/OOS trade PnL will be correlated due to equity compounding.
        </div>
      )}

      {/* Risk mode + value */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Risk Mode<HintTip text="How position size is calculated. '% of Equity' risks a percentage of current equity per trade. 'Fixed $' risks a fixed dollar amount per trade." /></label>
          <select value={isNormalized ? "fixed" : p.riskMode}
            onChange={(e) => p.setRiskMode(e.target.value)}
            disabled={isNormalized}
            className={`w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none ${isNormalized ? "opacity-60 cursor-not-allowed" : ""}`}>
            <option value="pct">% of Equity</option>
            <option value="fixed">Fixed $</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">
            Risk Per Trade ($){isNormalized ? "" : p.riskMode === "pct" ? " (%)" : " ($)"}<HintTip text={isNormalized ? "Fixed dollar risk per trade. All trades will risk this exact amount, keeping IS/OOS independent." : "If > 0, overrides the strategy's built-in position sizing. Set to 0 to let the strategy control its own position size."} />
          </label>
          <input value={p.riskValue} onChange={(e) => p.setRiskValue(e.target.value)}
            placeholder={isNormalized ? "e.g. 100" : "0 = disabled"}
            className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
          {!riskActive && !isNormalized && <p className="text-[10px] text-ag-text-muted mt-0.5">0 = strategy controls sizing</p>}
        </div>
      </div>

      {/* Martingale — hidden in normalized mode */}
      {!isNormalized && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">Martingale<HintTip text="Adjusts position size based on win/loss streaks. Anti-Martingale: size UP after wins. Martingale: size UP after losses. Inverse Martingale: size DOWN after losses (protect during drawdowns). Inverse Anti-Martingale: size DOWN after wins (conservative profit taking)." /></label>
            <label className="flex items-center gap-2 cursor-pointer select-none mt-1">
              <input type="checkbox" checked={p.martingaleEnabled}
                onChange={(e) => p.setMartingaleEnabled(e.target.checked)}
                className="accent-ag-accent w-4 h-4" />
              <span className="text-sm">{p.martingaleEnabled ? "On" : "Off"}</span>
            </label>
          </div>
          {p.martingaleEnabled && (
            <>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Type<HintTip text="Anti-Martingale: increase size after wins (ride streaks). Martingale: increase size after losses (mean-reversion). Inverse Martingale: decrease size after losses (protect capital). Inverse Anti-Martingale: decrease size after wins (conservative)." /></label>
                <select value={p.martingaleType} onChange={(e) => p.setMartingaleType(e.target.value)}
                  className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none">
                  <option value="anti">Anti-Martingale</option>
                  <option value="martingale">Martingale</option>
                  <option value="inv_martingale">Inverse Martingale</option>
                  <option value="inv_anti">Inverse Anti-Martingale</option>
                </select>
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Multiplier<HintTip text="Position size multiplier per streak. E.g. 2 = double after each win/loss (standard modes) or halve after each win/loss (inverse modes)." /></label>
                <input value={p.martingaleMultiplier} onChange={(e) => p.setMartingaleMultiplier(e.target.value)}
                  className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
              </div>
              <div>
                <label className="block text-xs text-ag-text-muted mb-1">Max Streak<HintTip text="Stop increasing position size after this many consecutive wins/losses. Caps the risk from the martingale system." /></label>
                <input value={p.martingaleMaxStreak} onChange={(e) => p.setMartingaleMaxStreak(e.target.value)}
                  className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
              </div>
              {p.setMartingaleScope && (
                <div>
                  <label className="block text-xs text-ag-text-muted mb-1">Scope<HintTip text="Per Asset: streaks tracked per symbol across all batches. Portfolio-wide: one shared streak across ALL assets. Per Asset + Batch: per symbol within each batch." /></label>
                  <select
                    value={p.martingaleScope ?? "asset"}
                    onChange={(e) => p.setMartingaleScope!(e.target.value)}
                    className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none"
                  >
                    <option value="asset">Per Asset</option>
                    <option value="portfolio">Portfolio-wide</option>
                    <option value="asset_batch">Per Asset + Batch</option>
                  </select>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Volatility filter */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Volatility Filter<HintTip text="Blocks new entries when market volatility (ATR) is too high. Helps avoid entering during extreme moves." /></label>
          <label className="flex items-center gap-2 cursor-pointer select-none mt-1">
            <input type="checkbox" checked={p.volFilterEnabled}
              onChange={(e) => p.setVolFilterEnabled(e.target.checked)}
              className="accent-ag-accent w-4 h-4" />
            <span className="text-sm">{p.volFilterEnabled ? "On" : "Off"}</span>
          </label>
        </div>
        {p.volFilterEnabled && (
          <>
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">ATR Ceiling<HintTip text="Maximum ATR value to allow entries. If the current ATR is above this value, entries are blocked." /></label>
              <input value={p.volFilterAtrCeiling} onChange={(e) => p.setVolFilterAtrCeiling(e.target.value)}
                placeholder="Max ATR to allow entry"
                className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
            </div>
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">ATR Length<HintTip text="Number of bars used to calculate ATR (Average True Range). Default 14. Higher = smoother, lower = more reactive." /></label>
              <input value={p.volFilterAtrLength} onChange={(e) => p.setVolFilterAtrLength(e.target.value)}
                className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
            </div>
          </>
        )}
      </div>

      {/* Bybit instrument limits */}
      {p.setEnforceBybitLimits && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">Bybit Limits<HintTip text="Fetches real Bybit per-instrument limits (min qty, max qty, lot size step). Rounds position sizes to lot_size, rejects trades below min_qty, and caps at max_qty. Shows rejected trade count in results." /></label>
            <label className="flex items-center gap-2 cursor-pointer select-none mt-1">
              <input type="checkbox" checked={p.enforceBybitLimits ?? false}
                onChange={(e) => p.setEnforceBybitLimits!(e.target.checked)}
                className="accent-ag-accent w-4 h-4" />
              <span className="text-sm">{p.enforceBybitLimits ? "On" : "Off"}</span>
            </label>
          </div>
          {p.enforceBybitLimits && p.setExpectedLiveSize && (
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Expected Live Size ($)<HintTip text="Your actual $ risk per trade in live trading. After backtesting, assets where this risk produces positions below Bybit's minimum order size are excluded from results. Leave at 0 to skip this filter. Example: backtest at $100, live at $250 — assets are checked at $250." /></label>
              <input value={p.expectedLiveSize ?? "0"} onChange={(e) => p.setExpectedLiveSize!(e.target.value)}
                placeholder="0 = disabled"
                className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
            </div>
          )}
          {p.enforceBybitLimits && p.setMaxMarginPerTrade && parseFloat(p.expectedLiveSize ?? "0") > 0 && (
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Max Margin Per Trade ($)<HintTip text="Maximum equity tied up per trade. If too many entries at your expected live risk require more margin than this, the asset is excluded. Depends on SL distance and Bybit's max leverage for each asset. Leave at 0 to disable." /></label>
              <input value={p.maxMarginPerTrade ?? "0"} onChange={(e) => p.setMaxMarginPerTrade!(e.target.value)}
                placeholder="0 = disabled"
                className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
            </div>
          )}
          {p.enforceBybitLimits && p.setMarginFilterTolerance && parseFloat(p.maxMarginPerTrade ?? "0") > 0 && (
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Margin Tolerance (%)<HintTip text="What percentage of a symbol's entry trades are allowed to exceed the margin cap before the asset is dropped. Default 10% — if more than 10% of entries exceed the limit, the asset is excluded." /></label>
              <input value={p.marginFilterTolerance ?? "10"} onChange={(e) => p.setMarginFilterTolerance!(e.target.value)}
                placeholder="10"
                className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
            </div>
          )}
          {p.enforceBybitLimits && p.setBybitRejectPct && (
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Reject Threshold (%)<HintTip text="Drop a symbol if this percentage (or more) of its trade attempts were rejected because the position was below Bybit's minimum order size. Set to 1 to drop any symbol with even 1 rejected trade. Set to 10 to allow up to 10% rejected. 0 = disabled." /></label>
              <input value={p.bybitRejectPct ?? "0"} onChange={(e) => p.setBybitRejectPct!(e.target.value)}
                placeholder="0 = disabled"
                className="w-full px-3 py-2 bg-ag-dark border border-ag-border rounded-lg text-sm focus:border-ag-accent outline-none" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
