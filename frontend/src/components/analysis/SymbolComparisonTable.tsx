"use client";
import { useState, useMemo } from "react";
import { ArrowUpDown } from "lucide-react";
import type { SymbolComparisonData, CompareStrategy } from "@/lib/api";

type FilterMode = "all" | "profitable_all" | "only_a" | "only_b" | "mixed";
type SortKey = "symbol" | "delta" | "pnl_0" | "pnl_1" | "wr_0" | "wr_1";

interface Props {
  data: SymbolComparisonData;
  strategies: CompareStrategy[];
  onSymbolClick: (symbolKey: string) => void;
}

export default function SymbolComparisonTable({ data, strategies, onSymbolClick }: Props) {
  const [filter, setFilter] = useState<FilterMode>("all");
  const [sortKey, setSortKey] = useState<SortKey>("delta");
  const [sortAsc, setSortAsc] = useState(false);

  const batchLabels = data.batches.map((b) => {
    const s = strategies.find((st) => st.batch_id === b.batch_id);
    return s ? `${s.strategy_name} #${s.batch_id}` : `Batch #${b.batch_id}`;
  });

  const rows = useMemo(() => {
    return data.all_symbols.map((sym) => {
      const vals = data.batches.map((b) => b.symbols[sym] ?? null);
      const pnls = vals.map((v) => v?.net_profit ?? 0);
      const profitable = vals.map((v) => (v?.net_profit ?? 0) > 0);
      const delta = pnls.length >= 2 ? pnls[0] - pnls[1] : 0;
      return { symbol: sym, vals, pnls, profitable, delta };
    });
  }, [data]);

  const counts = useMemo(() => ({
    all: rows.length,
    profitableAll: rows.filter((r) => r.profitable.every(Boolean)).length,
    onlyA: rows.filter((r) => r.profitable[0] && !r.profitable[1]).length,
    onlyB: rows.filter((r) => !r.profitable[0] && r.profitable[1]).length,
    mixed: rows.filter((r) => r.profitable[0] !== r.profitable[1]).length,
  }), [rows]);

  const filtered = useMemo(() => {
    if (filter === "all") return rows;
    if (filter === "profitable_all") return rows.filter((r) => r.profitable.every(Boolean));
    if (filter === "only_a") return rows.filter((r) => r.profitable[0] && !r.profitable[1]);
    if (filter === "only_b") return rows.filter((r) => !r.profitable[0] && r.profitable[1]);
    if (filter === "mixed") return rows.filter((r) => r.profitable[0] !== r.profitable[1]);
    return rows;
  }, [rows, filter]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      let av = 0, bv = 0;
      if (sortKey === "symbol") return sortAsc ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol);
      if (sortKey === "delta") { av = a.delta; bv = b.delta; }
      if (sortKey === "pnl_0") { av = a.pnls[0]; bv = b.pnls[0]; }
      if (sortKey === "pnl_1") { av = a.pnls[1]; bv = b.pnls[1]; }
      if (sortKey === "wr_0") { av = a.vals[0]?.win_rate ?? 0; bv = b.vals[0]?.win_rate ?? 0; }
      if (sortKey === "wr_1") { av = a.vals[1]?.win_rate ?? 0; bv = b.vals[1]?.win_rate ?? 0; }
      return sortAsc ? av - bv : bv - av;
    });
    return arr;
  }, [filtered, sortKey, sortAsc]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const fmtPnl = (v: number | null) => v == null ? "\u2014" : `$${v.toFixed(2)}`;
  const fmtWr = (v: number | null) => v == null ? "\u2014" : `${v.toFixed(1)}%`;
  const fmtPf = (v: number | null) => v == null ? "\u2014" : v.toFixed(2);
  const pnlColor = (v: number | null) => v == null ? "" : v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";

  return (
    <div className="space-y-3">
      <h4 className="text-sm font-semibold">Symbol Comparison</h4>

      {/* Filter chips */}
      <div className="flex flex-wrap gap-2">
        <FilterChip active={filter === "all"} onClick={() => setFilter("all")} label={`All (${counts.all})`} />
        <FilterChip active={filter === "profitable_all"} onClick={() => setFilter("profitable_all")} label={`Profitable Both (${counts.profitableAll})`} color="text-ag-green" />
        <FilterChip active={filter === "only_a"} onClick={() => setFilter("only_a")} label={`Only ${batchLabels[0]?.split(" ")[0] ?? "A"} (${counts.onlyA})`} color="text-blue-400" />
        <FilterChip active={filter === "only_b"} onClick={() => setFilter("only_b")} label={`Only ${batchLabels[1]?.split(" ")[0] ?? "B"} (${counts.onlyB})`} color="text-purple-400" />
        <FilterChip active={filter === "mixed"} onClick={() => setFilter("mixed")} label={`Differs (${counts.mixed})`} color="text-ag-yellow" />
      </div>

      {/* Table */}
      <div className="overflow-auto max-h-[500px]">
        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 bg-ag-darker z-10">
            <tr className="border-b border-ag-border">
              <Th label="Symbol" sortKey="symbol" currentSort={sortKey} asc={sortAsc} onSort={toggleSort} />
              <Th label={`PnL ${batchLabels[0] ?? ""}`} sortKey="pnl_0" currentSort={sortKey} asc={sortAsc} onSort={toggleSort} align="right" />
              <Th label={`WR ${batchLabels[0] ?? ""}`} sortKey="wr_0" currentSort={sortKey} asc={sortAsc} onSort={toggleSort} align="right" />
              <th className="text-right p-2 text-ag-text-muted font-normal text-xs">PF</th>
              <Th label={`PnL ${batchLabels[1] ?? ""}`} sortKey="pnl_1" currentSort={sortKey} asc={sortAsc} onSort={toggleSort} align="right" />
              <Th label={`WR ${batchLabels[1] ?? ""}`} sortKey="wr_1" currentSort={sortKey} asc={sortAsc} onSort={toggleSort} align="right" />
              <th className="text-right p-2 text-ag-text-muted font-normal text-xs">PF</th>
              <Th label="Delta" sortKey="delta" currentSort={sortKey} asc={sortAsc} onSort={toggleSort} align="right" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.symbol} onClick={() => onSymbolClick(r.symbol)}
                className="border-b border-ag-border/50 hover:bg-ag-dark/50 cursor-pointer">
                <td className="p-2 text-xs font-medium">{r.symbol}</td>
                <td className={`p-2 text-right font-mono text-xs ${pnlColor(r.vals[0]?.net_profit ?? null)}`}>{fmtPnl(r.vals[0]?.net_profit ?? null)}</td>
                <td className="p-2 text-right font-mono text-xs text-ag-text-muted">{fmtWr(r.vals[0]?.win_rate ?? null)}</td>
                <td className="p-2 text-right font-mono text-xs text-ag-text-muted">{fmtPf(r.vals[0]?.profit_factor ?? null)}</td>
                <td className={`p-2 text-right font-mono text-xs ${pnlColor(r.vals[1]?.net_profit ?? null)}`}>{fmtPnl(r.vals[1]?.net_profit ?? null)}</td>
                <td className="p-2 text-right font-mono text-xs text-ag-text-muted">{fmtWr(r.vals[1]?.win_rate ?? null)}</td>
                <td className="p-2 text-right font-mono text-xs text-ag-text-muted">{fmtPf(r.vals[1]?.profit_factor ?? null)}</td>
                <td className={`p-2 text-right font-mono text-xs font-bold ${pnlColor(r.delta)}`}>{fmtPnl(r.delta)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {sorted.length === 0 && <p className="text-xs text-ag-text-muted py-4 text-center">No symbols match this filter.</p>}
      </div>
    </div>
  );
}

function FilterChip({ active, onClick, label, color }: { active: boolean; onClick: () => void; label: string; color?: string }) {
  return (
    <button onClick={onClick} className={`px-3 py-1 rounded-full text-xs font-medium transition-colors border ${
      active ? "bg-ag-accent/20 border-ag-accent text-ag-accent" : `border-ag-border bg-ag-card hover:border-ag-accent/50 ${color ?? "text-ag-text-muted"}`
    }`}>
      {label}
    </button>
  );
}

function Th({ label, sortKey, currentSort, asc, onSort, align }: {
  label: string; sortKey: SortKey; currentSort: SortKey; asc: boolean; onSort: (k: SortKey) => void; align?: string;
}) {
  const isActive = currentSort === sortKey;
  return (
    <th className={`p-2 text-ag-text-muted font-normal text-xs cursor-pointer hover:text-ag-text select-none ${align === "right" ? "text-right" : "text-left"}`}
      onClick={() => onSort(sortKey)}>
      <span className="inline-flex items-center gap-1">
        {label}
        <ArrowUpDown size={10} className={isActive ? "text-ag-accent" : "opacity-30"} />
      </span>
    </th>
  );
}
