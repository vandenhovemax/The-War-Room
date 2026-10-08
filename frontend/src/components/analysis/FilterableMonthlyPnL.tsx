"use client";
import { useState, useEffect, useCallback } from "react";
import { ChevronDown } from "lucide-react";
import { api } from "@/lib/api";
import { MonthlyMatrix } from "./metrics";

type MatrixType = Record<string, Record<string, number | { pnl: number; trades: number; win_rate: number }>>;

interface BatchOption {
  id: number;
  name: string;
  strategy: string;
}

interface Props {
  portfolioId: number;
  cutoffDate?: string;
  /** "oos" = only show OOS matrix, "both" = show IS + OOS */
  mode: "oos" | "both";
  onCellClick?: (year: number, month: number, period: "is" | "oos") => void;
}

const DIR_TABS = [
  { id: "all", label: "All" },
  { id: "long", label: "Long" },
  { id: "short", label: "Short" },
] as const;

type DirTab = (typeof DIR_TABS)[number]["id"];

export default function FilterableMonthlyPnL({ portfolioId, cutoffDate, mode, onCellClick }: Props) {
  const [isMatrix, setIsMatrix] = useState<MatrixType>({});
  const [oosMatrix, setOosMatrix] = useState<MatrixType>({});
  const [batches, setBatches] = useState<BatchOption[]>([]);
  const [selectedBatches, setSelectedBatches] = useState<number[]>([]);
  const [direction, setDirection] = useState<DirTab>("all");
  const [loading, setLoading] = useState(true);
  const [batchDropdownOpen, setBatchDropdownOpen] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.portfolioMonthlyFiltered(portfolioId, {
        cutoff_date: cutoffDate,
        batch_ids: selectedBatches.length > 0 ? selectedBatches : undefined,
        direction,
      });
      setIsMatrix(res.is_matrix);
      setOosMatrix(res.oos_matrix);
      if (batches.length === 0 && res.batches.length > 0) {
        setBatches(res.batches);
      }
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, [portfolioId, cutoffDate, selectedBatches, direction, batches.length]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const toggleBatch = (id: number) => {
    setSelectedBatches((prev) =>
      prev.includes(id) ? prev.filter((b) => b !== id) : [...prev, id]
    );
  };

  const batchLabel = selectedBatches.length === 0
    ? "All Batches"
    : selectedBatches.length === 1
      ? batches.find((b) => b.id === selectedBatches[0])?.name ?? "1 Batch"
      : `${selectedBatches.length} Batches`;

  return (
    <div className="space-y-2">
      {/* Filter controls */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Batch selector dropdown */}
        {batches.length > 1 && (
          <div className="relative">
            <button
              onClick={() => setBatchDropdownOpen(!batchDropdownOpen)}
              className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] rounded bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text transition-colors"
              title="Filter monthly PnL by specific batch(es)"
            >
              {batchLabel}
              <ChevronDown size={12} className={`transition-transform ${batchDropdownOpen ? "rotate-180" : ""}`} />
            </button>
            {batchDropdownOpen && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setBatchDropdownOpen(false)} />
                <div className="absolute left-0 top-full mt-1 z-20 bg-ag-card border border-ag-border rounded-lg shadow-xl py-1 min-w-[200px] max-h-48 overflow-y-auto">
                  <button
                    onClick={() => { setSelectedBatches([]); setBatchDropdownOpen(false); }}
                    className={`w-full text-left px-3 py-1.5 text-[11px] hover:bg-ag-accent/10 ${
                      selectedBatches.length === 0 ? "text-ag-accent font-medium" : "text-ag-text-muted"
                    }`}
                  >
                    All Batches
                  </button>
                  {batches.map((b) => (
                    <button
                      key={b.id}
                      onClick={() => toggleBatch(b.id)}
                      className={`w-full text-left px-3 py-1.5 text-[11px] hover:bg-ag-accent/10 flex items-center gap-2 ${
                        selectedBatches.includes(b.id) ? "text-ag-accent font-medium" : "text-ag-text-muted"
                      }`}
                    >
                      <span className={`w-3 h-3 rounded border flex-shrink-0 flex items-center justify-center ${
                        selectedBatches.includes(b.id) ? "bg-ag-accent border-ag-accent" : "border-ag-border"
                      }`}>
                        {selectedBatches.includes(b.id) && (
                          <span className="text-white text-[8px]">&#10003;</span>
                        )}
                      </span>
                      {b.name}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* Direction tabs */}
        <div className="flex rounded bg-ag-dark border border-ag-border overflow-hidden">
          {DIR_TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setDirection(t.id)}
              className={`px-2.5 py-1 text-[11px] transition-colors ${
                direction === t.id
                  ? "bg-ag-accent/15 text-ag-accent font-medium"
                  : "text-ag-text-muted hover:text-ag-text"
              }`}
              title={`Show ${t.label.toLowerCase()} positions only`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {loading && (
          <span className="text-[10px] text-ag-text-muted animate-pulse">Loading...</span>
        )}
      </div>

      {/* Monthly matrices */}
      {!loading && (
        <>
          {mode === "both" && Object.keys(isMatrix).length > 0 && (
            <MonthlyMatrix
              matrix={isMatrix}
              title="IS Monthly PnL"
              onCellClick={onCellClick ? (y, m) => onCellClick(y, m, "is") : undefined}
            />
          )}
          {Object.keys(oosMatrix).length > 0 && (
            <MonthlyMatrix
              matrix={oosMatrix}
              title="OOS Monthly PnL"
              onCellClick={onCellClick ? (y, m) => onCellClick(y, m, "oos") : undefined}
            />
          )}
          {Object.keys(oosMatrix).length === 0 && Object.keys(isMatrix).length === 0 && (
            <p className="text-[10px] text-ag-text-muted">No monthly data for current filters.</p>
          )}
        </>
      )}
    </div>
  );
}
