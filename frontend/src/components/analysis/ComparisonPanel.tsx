"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { BatchBacktestRunItem, PortfolioItem } from "@/lib/api";
import BatchCompareSection from "./BatchCompareSection";
import MetricsTable from "./MetricsTable";

type Mode = "batches" | "portfolios";

export default function ComparisonPanel() {
  const [mode, setMode] = useState<Mode>("batches");
  const [batches, setBatches] = useState<BatchBacktestRunItem[]>([]);
  const [portfolios, setPortfolios] = useState<PortfolioItem[]>([]);
  const [selectedBatches, setSelectedBatches] = useState<number[]>([]);
  const [selectedPortfolios, setSelectedPortfolios] = useState<number[]>([]);

  useEffect(() => {
    api.batchRuns().then(setBatches).catch(() => {});
    api.portfolios().then(setPortfolios).catch(() => {});
  }, []);

  const toggleBatch = (id: number) => {
    setSelectedBatches((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const togglePortfolio = (id: number) => {
    setSelectedPortfolios((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  // Portfolio compare is client-side — just read metrics_snapshot
  const portfolioResults = selectedPortfolios.length >= 2
    ? selectedPortfolios.map((id) => portfolios.find((p) => p.id === id)).filter(Boolean) as PortfolioItem[]
    : [];

  return (
    <div className="space-y-6">
      {/* Mode toggle */}
      <div className="flex gap-2">
        <ModeBtn active={mode === "batches"} onClick={() => setMode("batches")}>Compare Batches</ModeBtn>
        <ModeBtn active={mode === "portfolios"} onClick={() => setMode("portfolios")}>Compare Portfolios</ModeBtn>
      </div>

      {mode === "batches" && (
        <BatchCompareSection
          batches={batches}
          selected={selectedBatches}
          toggle={toggleBatch}
        />
      )}

      {mode === "portfolios" && (
        <PortfolioCompareSection
          portfolios={portfolios}
          selected={selectedPortfolios}
          toggle={togglePortfolio}
          results={portfolioResults}
        />
      )}
    </div>
  );
}

function ModeBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
        active ? "bg-ag-accent text-ag-darker" : "bg-ag-card border border-ag-border text-ag-text-muted hover:text-ag-text"
      }`}
    >
      {children}
    </button>
  );
}

function PortfolioCompareSection({ portfolios, selected, toggle, results }: {
  portfolios: PortfolioItem[]; selected: number[]; toggle: (id: number) => void;
  results: PortfolioItem[];
}) {
  const metricKeys = results.length > 0
    ? Object.keys(results[0].metrics_snapshot).filter((k) => typeof results[0].metrics_snapshot[k] === "number")
    : [];

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium">Select Portfolios to Compare</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {portfolios.map((p) => (
          <label key={p.id} className={`flex items-center gap-2 p-3 rounded-lg border cursor-pointer transition-colors ${
            selected.includes(p.id) ? "border-ag-accent bg-ag-accent/10" : "border-ag-border bg-ag-card hover:border-ag-accent/50"
          }`}>
            <input type="checkbox" checked={selected.includes(p.id)} onChange={() => toggle(p.id)} className="accent-ag-accent w-4 h-4" />
            <div className="min-w-0">
              <p className="text-sm truncate">{p.name}</p>
              <p className="text-[10px] text-ag-text-muted">
                {p.asset_count ?? "?"} assets &middot; {p.portfolio_source ?? "optimizer"}
                {p.is_cross_strat ? " &middot; cross-strat" : ""}
              </p>
            </div>
          </label>
        ))}
      </div>
      {portfolios.length === 0 && <p className="text-xs text-ag-text-muted">No saved portfolios yet.</p>}
      {selected.length < 2 && <p className="text-xs text-ag-text-muted">Select at least 2 portfolios to compare.</p>}
      {results.length >= 2 && (
        <MetricsTable
          columns={results.map((r) => ({
            id: r.id,
            label: r.optimization_end_date ? `${r.name} (IS)` : r.name,
          }))}
          metricKeys={metricKeys}
          getValue={(colIdx, key) => results[colIdx].metrics_snapshot[key]}
        />
      )}
    </div>
  );
}
