"use client";
import { useState, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import type { PortfolioDetail, PortfolioTestResult } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  portfolio: PortfolioDetail;
}

export default function PortfolioTestPanel({ portfolio }: Props) {
  // Pre-populate from original batch settings so results are comparable
  const bs = portfolio.batch_settings;
  const [capital, setCapital] = useState(bs?.initial_capital ?? 10000);
  const [commission, setCommission] = useState(bs?.commission_pct ?? 0.055);
  const [leverage, setLeverage] = useState(bs?.leverage ?? 1);
  const [riskMode, setRiskMode] = useState(bs?.risk_mode ?? "pct");
  const [riskValue, setRiskValue] = useState(bs?.risk_value ?? 0);
  const [slippage, setSlippage] = useState(bs?.slippage_value ?? 0);
  const [oosOnly, setOosOnly] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0, group: "" });
  const [result, setResult] = useState<PortfolioTestResult | null>(null);
  const [error, setError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastProgressRef = useRef<{ done: number; time: number }>({ done: 0, time: 0 });

  const cleanup = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  const runTest = async () => {
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const { task_id, total_symbols } = await api.testPortfolio(portfolio.id, {
        initial_capital: capital,
        commission_pct: commission,
        leverage,
        risk_mode: riskMode,
        risk_value: riskValue,
        slippage_pct: slippage,
        oos_only: oosOnly,
      });
      setProgress({ done: 0, total: total_symbols, group: "" });
      lastProgressRef.current = { done: 0, time: Date.now() };

      pollRef.current = setInterval(async () => {
        try {
          const prog = await api.testPortfolioProgress(task_id);
          setProgress({
            done: prog.completed_symbols,
            total: prog.total_symbols,
            group: prog.current_group,
          });

          // Track progress changes for timeout detection
          if (prog.completed_symbols > lastProgressRef.current.done) {
            lastProgressRef.current = { done: prog.completed_symbols, time: Date.now() };
          }

          if (prog.status === "completed" && prog.result) {
            cleanup();
            setResult(prog.result);
            setRunning(false);
          } else if (prog.status === "error") {
            cleanup();
            setError(prog.error || "Test failed");
            setRunning(false);
          } else {
            // Timeout: 5 minutes with no progress change
            const staleMs = Date.now() - lastProgressRef.current.time;
            if (staleMs > 5 * 60 * 1000) {
              cleanup();
              setError("Test appears stuck (no progress for 5 minutes). Try again or check backend logs.");
              setRunning(false);
            }
          }
        } catch {
          cleanup();
          setError("Lost connection to test");
          setRunning(false);
        }
      }, 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start test");
      setRunning(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="text-xs text-ag-text-muted space-y-1">
        <p>
          Portfolio Mode — all {portfolio.assets.filter(a => a.is_active).length} symbols share one equity pool.
          {bs && Object.keys(bs).length > 0 && (
            <span className="text-ag-accent"> Settings pre-filled from original batch.</span>
          )}
        </p>
        <p className="text-ag-text-muted/70">
          Note: Results will differ from per-symbol OOS analysis because all symbols share capital here.
          For comparable results, multiply capital by number of symbols or increase leverage.
        </p>
      </div>

      {/* Config */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <NumField label="Initial Capital" value={capital} min={100}
          onChange={setCapital} hint="Starting equity for the portfolio test. All symbols share this pool." />
        <NumField label="Commission %" value={commission} min={0} step={0.01}
          onChange={setCommission} hint="Trading fee per trade as % of notional. Bybit taker = 0.055%." />
        <NumField label="Leverage" value={leverage} min={1} max={125}
          onChange={setLeverage} hint="Buying power multiplier. 1 = no leverage." />
        <div>
          <label className="block text-[10px] text-ag-text-muted mb-0.5">Risk Mode<HintTip text="% of Equity: risk a percentage of current portfolio value per trade. Fixed $: risk a fixed dollar amount per trade." /></label>
          <select value={riskMode} onChange={(e) => setRiskMode(e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
            <option value="pct">% of Equity</option>
            <option value="fixed">Fixed $</option>
          </select>
        </div>
        <NumField label={riskMode === "pct" ? "Risk %" : "Risk $"} value={riskValue}
          min={0} step={riskMode === "pct" ? 0.5 : 10} onChange={setRiskValue}
          hint={riskMode === "pct" ? "Percentage of current equity risked per trade. 0 = no risk sizing (use full capital)." : "Fixed dollar amount risked per trade. 0 = no risk sizing (use full capital)."} />
        <NumField label="Slippage %" value={slippage} min={0} step={0.01}
          onChange={setSlippage} hint="Simulated price slippage per fill as % of price. Applied to both entries and exits. 0 = no slippage. Crypto guidance: 0.01-0.03% for large-cap limit orders, 0.05% for market orders on liquid alts." />
      </div>

      {/* OOS-only toggle — only show when portfolio has an optimization end date */}
      {portfolio.optimization_end_date && (
        <label className="flex items-center gap-2 text-xs cursor-pointer">
          <input type="checkbox" checked={oosOnly} onChange={(e) => setOosOnly(e.target.checked)}
            className="rounded border-ag-border bg-ag-dark" />
          <span className="text-ag-text-muted">OOS Only</span>
          <HintTip text="Test only on Out-of-Sample data (candles after the optimization end date). The strategy starts fresh — equity begins at Initial Capital, indicators warm up from pre-cutoff bars but no trades fire until OOS begins." />
        </label>
      )}

      <button onClick={runTest} disabled={running}
        className="px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50 transition-colors">
        {running ? "Running..." : "Run Portfolio Test"}
      </button>

      {/* Progress */}
      {running && (
        <div className="space-y-1.5">
          <div className="w-full bg-ag-dark rounded-full h-2">
            <div className="bg-ag-accent h-2 rounded-full transition-all"
              style={{ width: `${progress.total > 0 ? (progress.done / progress.total) * 100 : 0}%` }} />
          </div>
          <p className="text-xs text-ag-text-muted">
            {progress.done}/{progress.total} symbols
            {progress.group && <span> — {progress.group}</span>}
          </p>
        </div>
      )}

      {error && <p className="text-xs text-ag-red">{error}</p>}

      {/* Results */}
      {result && (
        <>
          {result.oos_only && (
            <p className="text-xs text-ag-accent font-medium">Results below are OOS only (unseen data after optimization end date)</p>
          )}
          <TestResults result={result} capital={capital} />
        </>
      )}
    </div>
  );
}

function TestResults({ result, capital }: { result: PortfolioTestResult; capital: number }) {
  const m = result.combined_metrics;
  const net = m.net_profit ?? 0;
  const pf = m.profit_factor;
  const wr = m.win_rate;
  const dd = m.max_drawdown_pct;
  const trades = m.total_trades ?? 0;
  const sharpe = m.sharpe_ratio;

  // Sort per-symbol by net profit descending
  const symbols = Object.entries(result.per_symbol_metrics)
    .map(([sym, sm]) => ({ sym, ...sm } as { sym: string } & Record<string, number | null>))
    .sort((a, b) => (Number(b.net_profit) || 0) - (Number(a.net_profit) || 0));

  return (
    <div className="space-y-4">
      {/* Combined metrics */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        <MetricCard label="Net Profit" value={`$${Number(net).toFixed(0)}`}
          color={Number(net) >= 0 ? "text-ag-green" : "text-ag-red"} />
        <MetricCard label="Profit Factor" value={pf != null ? Number(pf).toFixed(2) : "—"} />
        <MetricCard label="Win Rate" value={wr != null ? `${Number(wr).toFixed(1)}%` : "—"} />
        <MetricCard label="Max DD %" value={dd != null ? `${Number(dd).toFixed(1)}%` : "—"}
          color="text-ag-red" />
        <MetricCard label="Sharpe" value={sharpe != null ? Number(sharpe).toFixed(2) : "—"} />
        <MetricCard label="Positions" value={String(trades)} />
      </div>

      {/* ROI */}
      {capital > 0 && Number(net) !== 0 && (
        <p className="text-xs text-ag-text-muted">
          ROI: <span className={Number(net) >= 0 ? "text-ag-green" : "text-ag-red"}>
            {((Number(net) / capital) * 100).toFixed(1)}%
          </span>
          {" "}on ${capital.toLocaleString()} capital
          {" "}· {result.bars_processed.toLocaleString()} bars processed
        </p>
      )}

      {/* Per-symbol table */}
      {symbols.length > 0 && (
        <div>
          <h4 className="text-xs text-ag-text-muted mb-1.5">Per-Symbol Breakdown</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-ag-border text-ag-text-muted">
                  <th className="text-left py-1.5 px-2">Symbol</th>
                  <th className="text-right py-1.5 px-2">Net Profit</th>
                  <th className="text-right py-1.5 px-2">PF</th>
                  <th className="text-right py-1.5 px-2">Win Rate</th>
                  <th className="text-right py-1.5 px-2">Positions</th>
                </tr>
              </thead>
              <tbody>
                {symbols.map((s) => {
                  const np = Number(s.net_profit) || 0;
                  return (
                    <tr key={s.sym} className="border-b border-ag-border/30 hover:bg-ag-card-hover">
                      <td className="py-1.5 px-2 font-medium">{s.sym}</td>
                      <td className={`text-right py-1.5 px-2 ${np >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                        ${np.toFixed(0)}
                      </td>
                      <td className="text-right py-1.5 px-2">
                        {s.profit_factor != null ? Number(s.profit_factor).toFixed(2) : "—"}
                      </td>
                      <td className="text-right py-1.5 px-2">
                        {s.win_rate != null ? `${Number(s.win_rate).toFixed(1)}%` : "—"}
                      </td>
                      <td className="text-right py-1.5 px-2">{s.total_trades ?? 0}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-ag-dark border border-ag-border rounded-lg p-2 text-center">
      <p className="text-[10px] text-ag-text-muted">{label}</p>
      <p className={`text-sm font-mono font-medium ${color || ""}`}>{value}</p>
    </div>
  );
}

function NumField({ label, value, min, max, step, onChange, hint }: {
  label: string; value: number; min?: number; max?: number; step?: number;
  onChange: (v: number) => void; hint?: string;
}) {
  return (
    <div>
      <label className="block text-[10px] text-ag-text-muted mb-0.5">{label}{hint && <HintTip text={hint} />}</label>
      <input type="number" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Math.max(min ?? 0, Number(e.target.value)))}
        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
    </div>
  );
}
