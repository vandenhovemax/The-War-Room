"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { TradingDashboardResult, StrategyItem, PortfolioItem, DashboardSymbolStats } from "@/lib/api";

interface Props {
  onAssetSelect?: (symbol: string, stats: DashboardSymbolStats) => void;
}

function KpiCard({ label, value, color, sub }: {
  label: string; value: string; color?: string; sub?: string;
}) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <p className="text-[10px] text-ag-text-muted uppercase tracking-wider">{label}</p>
      <p className={`text-xl font-bold mt-1 ${color || ""}`}>{value}</p>
      {sub && <p className="text-[10px] text-ag-text-muted mt-1">{sub}</p>}
    </div>
  );
}

export default function TradingAnalysis({ onAssetSelect }: Props) {
  const [data, setData] = useState<TradingDashboardResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [filterSymbol, setFilterSymbol] = useState("");
  const [filterSide, setFilterSide] = useState("");
  const [filterStrategy, setFilterStrategy] = useState<number | null>(null);
  const [filterPortfolio, setFilterPortfolio] = useState<number | null>(null);
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);

  useEffect(() => {
    Promise.all([api.strategies(), api.portfolios()])
      .then(([s, p]) => { setStrategies(s); setPortfolios(p); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    const filters: Record<string, string | number> = {};
    if (filterSymbol) filters.symbol = filterSymbol;
    if (filterSide) filters.side = filterSide;
    if (filterStrategy) filters.strategy_id = filterStrategy;
    if (filterPortfolio) filters.portfolio_id = filterPortfolio;
    api.journalDashboard(filters)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [filterSymbol, filterSide, filterStrategy, filterPortfolio]);

  const symbols = data?.by_symbol.map((s) => s.symbol) ?? [];

  return (
    <div className="border-t border-ag-border/30 pt-6 space-y-4">
      <h2 className="text-lg font-bold">Trading Analysis</h2>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <select value={filterSymbol} onChange={(e) => setFilterSymbol(e.target.value)}
          className="bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
          <option value="">All Symbols</option>
          {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={filterSide} onChange={(e) => setFilterSide(e.target.value)}
          className="bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
          <option value="">All Sides</option>
          <option value="Long">Long</option>
          <option value="Short">Short</option>
        </select>
        <select value={filterStrategy ?? ""} onChange={(e) => setFilterStrategy(Number(e.target.value) || null)}
          className="bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
          <option value="">All Strategies</option>
          {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select value={filterPortfolio ?? ""} onChange={(e) => setFilterPortfolio(Number(e.target.value) || null)}
          className="bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
          <option value="">All Portfolios</option>
          {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {(filterSymbol || filterSide || filterStrategy || filterPortfolio) && (
          <button onClick={() => { setFilterSymbol(""); setFilterSide(""); setFilterStrategy(null); setFilterPortfolio(null); }}
            className="text-xs text-ag-text-muted hover:text-ag-text underline">Clear filters</button>
        )}
      </div>

      {loading ? (
        <p className="text-sm text-ag-text-muted py-8 text-center">Loading...</p>
      ) : !data || data.total_trades === 0 ? (
        <p className="text-sm text-ag-text-muted text-center py-8">No journal trades recorded yet.</p>
      ) : (
        <>
          {/* KPI Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <KpiCard label="Total P&L" value={`$${data.total_pnl.toFixed(2)}`}
              color={data.total_pnl >= 0 ? "text-ag-green" : "text-ag-red"} />
            <KpiCard label="Win Rate" value={`${data.win_rate.toFixed(1)}%`}
              color={data.win_rate >= 50 ? "text-ag-green" : "text-ag-red"}
              sub={`${data.closed_trades} closed / ${data.open_trades} open`} />
            <KpiCard label="Cumulative Fees" value={`$${data.total_actual_fees.toFixed(2)}`}
              sub={`Expected: $${data.total_expected_fees.toFixed(2)}`} />
            <KpiCard label="Fee Overcharge"
              value={`$${data.fee_delta >= 0 ? "+" : ""}${data.fee_delta.toFixed(2)}`}
              color={data.fee_delta > 0 ? "text-ag-red" : "text-ag-green"}
              sub={data.total_expected_fees > 0
                ? `${((data.fee_delta / data.total_expected_fees) * 100).toFixed(0)}% over expected` : ""} />
          </div>

          {/* Side breakdown */}
          {Object.keys(data.by_side).length > 0 && (
            <div className="bg-ag-card border border-ag-border rounded-lg p-4">
              <h3 className="text-sm font-medium mb-3">By Direction</h3>
              <div className="grid grid-cols-2 gap-4">
                {Object.entries(data.by_side).map(([side, s]) => (
                  <div key={side} className="flex items-center justify-between px-3 py-2 bg-ag-dark/50 rounded">
                    <span className={`text-sm font-medium ${side === "Long" ? "text-ag-green" : "text-ag-red"}`}>{side}</span>
                    <div className="flex gap-4 text-xs">
                      <span>{s.trades} trades</span>
                      <span className={s.pnl >= 0 ? "text-ag-green" : "text-ag-red"}>${s.pnl.toFixed(2)}</span>
                      <span>WR: {s.win_rate.toFixed(1)}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Per-Symbol table */}
          {data.by_symbol.length > 0 && (
            <SymbolTable data={data.by_symbol} onSelect={onAssetSelect} />
          )}

          {/* Per-Strategy table */}
          {data.by_strategy.length > 0 && (
            <div className="bg-ag-card border border-ag-border rounded-lg p-4">
              <h3 className="text-sm font-medium mb-3">By Strategy</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-ag-border text-ag-text-muted">
                      <th className="text-left py-2 pr-3">Strategy</th>
                      <th className="text-right py-2 px-2">Trades</th>
                      <th className="text-right py-2 px-2">P&L</th>
                      <th className="text-right py-2 px-2">Win Rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_strategy.map((s) => (
                      <tr key={s.strategy_id} className="border-b border-ag-border/50">
                        <td className="py-1.5 pr-3 font-medium">{s.name}</td>
                        <td className="py-1.5 px-2 text-right">{s.trades}</td>
                        <td className={`py-1.5 px-2 text-right ${s.pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>${s.pnl.toFixed(2)}</td>
                        <td className="py-1.5 px-2 text-right">{s.win_rate.toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Monthly P&L */}
          {Object.keys(data.monthly_pnl).length > 0 && (
            <div className="bg-ag-card border border-ag-border rounded-lg p-4">
              <h3 className="text-sm font-medium mb-3">Monthly P&L</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(data.monthly_pnl).map(([month, pnl]) => (
                  <div key={month} className="px-3 py-2 bg-ag-dark/50 rounded text-center min-w-[80px]">
                    <p className="text-[10px] text-ag-text-muted">{month}</p>
                    <p className={`text-sm font-medium ${pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>${pnl.toFixed(0)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SymbolTable({ data, onSelect }: {
  data: DashboardSymbolStats[]; onSelect?: (s: string, d: DashboardSymbolStats) => void;
}) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <h3 className="text-sm font-medium mb-3">By Asset</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-ag-border text-ag-text-muted">
              <th className="text-left py-2 pr-3">Symbol</th>
              <th className="text-right py-2 px-2">Trades</th>
              <th className="text-right py-2 px-2">P&L</th>
              <th className="text-right py-2 px-2">Win Rate</th>
              <th className="text-right py-2 px-2">Expected Fees</th>
              <th className="text-right py-2 px-2">Actual Fees</th>
              <th className="text-right py-2 px-2">Delta</th>
            </tr>
          </thead>
          <tbody>
            {data.map((s) => {
              const delta = s.fees_actual - s.fees_expected;
              return (
                <tr key={s.symbol}
                  onClick={() => onSelect?.(s.symbol, s)}
                  className="border-b border-ag-border/50 cursor-pointer hover:bg-ag-dark/30 transition-colors">
                  <td className="py-1.5 pr-3 font-medium text-ag-accent hover:underline">{s.symbol.replace("USDT", "")}</td>
                  <td className="py-1.5 px-2 text-right">{s.trades}</td>
                  <td className={`py-1.5 px-2 text-right ${s.pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>${s.pnl.toFixed(2)}</td>
                  <td className="py-1.5 px-2 text-right">{s.win_rate.toFixed(1)}%</td>
                  <td className="py-1.5 px-2 text-right">${s.fees_expected.toFixed(2)}</td>
                  <td className="py-1.5 px-2 text-right">${s.fees_actual.toFixed(2)}</td>
                  <td className={`py-1.5 px-2 text-right ${delta > 0 ? "text-ag-red" : "text-ag-green"}`}>
                    {delta >= 0 ? "+" : ""}${delta.toFixed(2)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
