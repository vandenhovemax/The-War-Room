"use client";
import type { BatchBacktestRunItem } from "@/lib/api";

interface BatchCatalogProps {
  batches: BatchBacktestRunItem[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}

export default function BatchCatalog({ batches, selectedId, onSelect }: BatchCatalogProps) {
  // Group by TF → Direction
  const groups = new Map<string, Map<string, BatchBacktestRunItem[]>>();
  for (const b of batches) {
    const tf = b.timeframe || "?";
    const dir = b.direction_filter || "both";
    if (!groups.has(tf)) groups.set(tf, new Map());
    const dirMap = groups.get(tf)!;
    if (!dirMap.has(dir)) dirMap.set(dir, []);
    dirMap.get(dir)!.push(b);
  }

  if (batches.length === 0) {
    return (
      <div className="text-center py-12 text-ag-text-muted text-sm">
        No completed batch backtests.
      </div>
    );
  }

  const dirColor = (dir: string) => {
    if (dir === "long") return "text-green-400 bg-green-500/10 border-green-500/30";
    if (dir === "short") return "text-red-400 bg-red-500/10 border-red-500/30";
    return "text-blue-400 bg-blue-500/10 border-blue-500/30";
  };

  return (
    <div className="space-y-6">
      {Array.from(groups.entries()).map(([tf, dirMap]) => (
        <div key={tf}>
          <h3 className="text-sm font-semibold text-ag-text mb-3 flex items-center gap-2">
            <span className="px-2 py-0.5 rounded bg-ag-accent/15 text-ag-accent text-xs font-bold">
              {tf}m
            </span>
            Timeframe
          </h3>
          {Array.from(dirMap.entries()).map(([dir, items]) => (
            <div key={dir} className="mb-4">
              <p className="text-xs text-ag-text-muted uppercase tracking-wider mb-2 ml-1">
                {dir.charAt(0).toUpperCase() + dir.slice(1)}
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {items.map((b) => {
                  const isActive = b.id === selectedId;
                  return (
                    <button
                      key={b.id}
                      onClick={() => onSelect(b.id)}
                      className={`text-left p-3 rounded-lg border transition-all ${
                        isActive
                          ? "border-ag-accent bg-ag-accent/10 ring-1 ring-ag-accent/30"
                          : "border-ag-border bg-ag-card hover:border-ag-accent/50 hover:bg-ag-card-hover"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2 mb-2">
                        <h4 className="text-sm font-medium text-ag-text leading-tight">
                          {b.name || `Batch #${b.id}`}
                        </h4>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded border shrink-0 ${dirColor(dir)}`}>
                          {dir}
                        </span>
                      </div>
                      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                        <div>
                          <span className="text-ag-text-muted">Symbols: </span>
                          <span className="text-ag-text font-medium">{b.completed_symbols}</span>
                        </div>
                        <div>
                          <span className="text-ag-text-muted">TF: </span>
                          <span className="text-ag-text font-medium">{tf}m</span>
                        </div>
                        {b.config_group && (
                          <div className="col-span-2">
                            <span className="text-ag-text-muted">Config: </span>
                            <span className="text-ag-text">{b.config_group}</span>
                          </div>
                        )}
                        {b.created_at && (
                          <div className="col-span-2">
                            <span className="text-ag-text-muted">Created: </span>
                            <span className="text-ag-text">
                              {new Date(b.created_at).toLocaleDateString()}
                            </span>
                          </div>
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
