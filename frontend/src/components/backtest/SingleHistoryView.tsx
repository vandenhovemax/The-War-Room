"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight, Eye, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { BacktestRunItem, BacktestRunDetail, BacktestTradeItem, StrategyItem } from "@/lib/api";
import SingleRunDetail from "./SingleRunDetail";
import BacktestResults from "./BacktestResults";

interface Props {
  runs: BacktestRunItem[];
  strategies: StrategyItem[];
  onRefresh: () => void;
}

interface SymbolGroup {
  symbol: string;
  strategies: StrategySubGroup[];
}
interface StrategySubGroup {
  key: string;
  label: string;
  runs: BacktestRunItem[];
}

function buildGroups(runs: BacktestRunItem[], strategies: StrategyItem[]): SymbolGroup[] {
  const stratMap = new Map(strategies.map((s) => [s.id, s.name]));
  const symbolMap = new Map<string, Map<string, BacktestRunItem[]>>();
  for (const r of runs) {
    if (!symbolMap.has(r.symbol)) symbolMap.set(r.symbol, new Map());
    const sMap = symbolMap.get(r.symbol)!;
    const key = `${r.strategy_id}`;
    if (!sMap.has(key)) sMap.set(key, []);
    sMap.get(key)!.push(r);
  }
  const result: SymbolGroup[] = [];
  for (const [symbol, sMap] of symbolMap) {
    const subs: StrategySubGroup[] = [];
    for (const [key, rList] of sMap) {
      rList.sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime());
      const sid = parseInt(key);
      subs.push({ key, label: stratMap.get(sid) || `Strategy #${sid}`, runs: rList });
    }
    result.push({ symbol, strategies: subs });
  }
  return result.sort((a, b) => a.symbol.localeCompare(b.symbol));
}

export default function SingleHistoryView({ runs, strategies, onRefresh }: Props) {
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);
  const [expandedStrat, setExpandedStrat] = useState<string | null>(null);
  const [viewRun, setViewRun] = useState<BacktestRunDetail | null>(null);
  const [viewTrades, setViewTrades] = useState<BacktestTradeItem[]>([]);
  const groups = buildGroups(runs, strategies);

  const handleViewFull = async (runId: number) => {
    const [detail, t] = await Promise.all([
      api.backtestRunDetail(runId),
      api.backtestRunTrades(runId),
    ]);
    setViewRun(detail);
    setViewTrades(t);
  };

  if (groups.length === 0) {
    return <p className="text-sm text-ag-text-muted py-4">No standalone backtests found.</p>;
  }

  if (viewRun) {
    return (
      <BacktestResults run={viewRun} trades={viewTrades}
        onClose={() => { setViewRun(null); setViewTrades([]); }} />
    );
  }

  return (
    <div className="space-y-2">
      {groups.map((g) => {
        const isExpanded = expandedSymbol === g.symbol;
        const totalRuns = g.strategies.reduce((sum, s) => sum + s.runs.length, 0);
        return (
          <div key={g.symbol} className="bg-ag-card border border-ag-border rounded-lg">
            <button onClick={() => setExpandedSymbol(isExpanded ? null : g.symbol)}
              className="w-full flex items-center justify-between px-4 py-3 hover:bg-ag-card-hover transition-colors rounded-lg">
              <div className="flex items-center gap-2">
                {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span className="text-sm font-medium">{g.symbol}</span>
                <span className="text-xs text-ag-text-muted">
                  {totalRuns} run{totalRuns !== 1 ? "s" : ""} · {g.strategies.length} strateg{g.strategies.length !== 1 ? "ies" : "y"}
                </span>
              </div>
            </button>
            {isExpanded && (
              <div className="border-t border-ag-border">
                {g.strategies.map((s) => {
                  const sKey = `${g.symbol}-${s.key}`;
                  const sExpanded = expandedStrat === sKey;
                  return (
                    <div key={sKey} className="border-b border-ag-border/30 last:border-b-0">
                      <button onClick={() => setExpandedStrat(sExpanded ? null : sKey)}
                        className="w-full flex items-center justify-between px-6 py-2 text-xs hover:bg-ag-card-hover transition-colors">
                        <div className="flex items-center gap-2">
                          {sExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                          <span className="font-medium">{s.label}</span>
                          <span className="text-ag-text-muted">{s.runs.length} run{s.runs.length !== 1 ? "s" : ""}</span>
                        </div>
                      </button>
                      {sExpanded && (
                        <div className="px-8 pb-2 space-y-1">
                          {s.runs.map((r) => (
                            <RunRow key={r.id} run={r} onDeleted={onRefresh} onViewFull={handleViewFull} />
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function RunRow({ run, onDeleted, onViewFull }: {
  run: BacktestRunItem; onDeleted: () => void; onViewFull: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const statusColor = run.status === "completed" ? "bg-ag-green"
    : run.status === "error" ? "bg-ag-red" : "bg-yellow-500 animate-pulse";

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete run for ${run.symbol}? This removes all its trades.`)) return;
    await api.deleteBacktestRun(run.id);
    onDeleted();
  };

  return (
    <div>
      <div className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-ag-dark/50 text-xs cursor-pointer"
        onClick={() => setExpanded(!expanded)}>
        <div className="flex items-center gap-2">
          {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
          <div className={`w-2 h-2 rounded-full ${statusColor}`} />
          <span>{run.timeframe}</span>
          {run.status === "completed" && run.net_profit != null && (
            <>
              <span className={run.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}>
                ${run.net_profit.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </span>
              <span className="text-ag-text-muted">{run.total_trades ?? 0}t</span>
              {run.win_rate != null && <span className="text-ag-text-muted">WR:{run.win_rate.toFixed(1)}%</span>}
              {run.profit_factor != null && <span className="text-ag-text-muted">PF:{run.profit_factor.toFixed(2)}</span>}
            </>
          )}
          {run.status !== "completed" && <span className="text-ag-text-muted">{run.status}</span>}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-ag-text-muted">{run.created_at ? `${new Date(run.created_at).toLocaleDateString()} ${new Date(run.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}` : "—"}</span>
          {run.status === "completed" && (
            <button onClick={(e) => { e.stopPropagation(); onViewFull(run.id); }}
              className="text-ag-accent hover:text-ag-accent/80" title="View full results">
              <Eye size={12} />
            </button>
          )}
          <button onClick={handleDelete} className="text-ag-text-muted hover:text-ag-red">
            <Trash2 size={12} />
          </button>
        </div>
      </div>
      {expanded && <SingleRunDetail runId={run.id} />}
    </div>
  );
}
