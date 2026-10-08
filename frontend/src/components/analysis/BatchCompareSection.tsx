"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import type { BatchBacktestRunItem, CompareStrategy, CategoryComparison, SymbolComparisonData } from "@/lib/api";
import CategoryComparisonTable from "./CategoryComparisonTable";
import MetricsTable from "./MetricsTable";
import SymbolComparisonTable from "./SymbolComparisonTable";
import SymbolTradeDrawer from "./SymbolTradeDrawer";

interface Props {
  batches: BatchBacktestRunItem[];
  selected: number[];
  toggle: (id: number) => void;
}

export default function BatchCompareSection({ batches, selected, toggle }: Props) {
  const [results, setResults] = useState<CompareStrategy[]>([]);
  const [catComparison, setCatComparison] = useState<CategoryComparison | null>(null);
  const [symComparison, setSymComparison] = useState<SymbolComparisonData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [drawerSymbol, setDrawerSymbol] = useState<string | null>(null);

  const runCompare = async () => {
    if (selected.length < 2) return;
    setLoading(true);
    setError("");
    try {
      const res = await api.compareStrategies(selected);
      setResults(res.strategies);
      setCatComparison(res.category_comparison ?? null);
      setSymComparison(res.symbol_comparison ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Compare failed");
    } finally {
      setLoading(false);
    }
  };

  const metricKeys = results.length > 0
    ? Object.keys(results[0].metrics).filter((k) => typeof results[0].metrics[k] === "number")
    : [];

  const completed = batches.filter((b) => b.status === "completed");

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium">Select Batch Runs to Compare</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {completed.map((b) => (
          <label key={b.id} className={`flex items-center gap-2 p-3 rounded-lg border cursor-pointer transition-colors ${
            selected.includes(b.id) ? "border-ag-accent bg-ag-accent/10" : "border-ag-border bg-ag-card hover:border-ag-accent/50"
          }`}>
            <input type="checkbox" checked={selected.includes(b.id)} onChange={() => toggle(b.id)} className="accent-ag-accent w-4 h-4" />
            <div className="min-w-0">
              <p className="text-sm truncate">{b.name || `Batch #${b.id}`} &mdash; {b.timeframe}m</p>
              <p className="text-[10px] text-ag-text-muted">{b.completed_symbols}/{b.total_symbols} symbols &middot; {b.direction_filter}</p>
            </div>
          </label>
        ))}
      </div>
      {completed.length === 0 && <p className="text-xs text-ag-text-muted">No completed batch runs yet.</p>}

      <button onClick={runCompare} disabled={loading || selected.length < 2}
        className="px-4 py-2 bg-ag-accent text-ag-darker rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
        {loading ? "Comparing..." : `Compare ${selected.length} Runs`}
      </button>
      {error && <p className="text-sm text-ag-red">{error}</p>}

      {results.length > 0 && (
        <MetricsTable
          columns={results.map((r) => ({ id: r.batch_id, label: `${r.strategy_name} #${r.batch_id}` }))}
          metricKeys={metricKeys}
          getValue={(colIdx, key) => results[colIdx].metrics[key]}
        />
      )}

      {results.length > 0 && catComparison && catComparison.all_categories.length > 0 && (
        <CategoryComparisonTable data={catComparison} strategies={results} />
      )}

      {results.length > 0 && symComparison && symComparison.all_symbols.length > 0 && (
        <SymbolComparisonTable
          data={symComparison}
          strategies={results}
          onSymbolClick={(sym) => setDrawerSymbol(sym)}
        />
      )}

      {drawerSymbol && (
        <SymbolTradeDrawer
          symbolKey={drawerSymbol}
          batchIds={selected}
          strategies={results}
          onClose={() => setDrawerSymbol(null)}
          timeframe={batches.find((b) => selected.includes(b.id))?.timeframe}
        />
      )}
    </div>
  );
}
