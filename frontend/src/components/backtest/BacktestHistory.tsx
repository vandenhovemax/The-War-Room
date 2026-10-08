"use client";
import { useState } from "react";
import { Trash2, ChevronDown, ChevronRight, FlaskConical } from "lucide-react";
import type { BacktestRunItem, BatchBacktestRunItem, StrategyItem } from "@/lib/api";

type HistoryEntry =
  | { type: "single"; run: BacktestRunItem }
  | { type: "batch"; batch: BatchBacktestRunItem; strategyName: string };

interface Props {
  runs: BacktestRunItem[];
  batchRuns: BatchBacktestRunItem[];
  strategies: StrategyItem[];
  selectedRunId: number | null;
  onViewRun: (runId: number) => void;
  onDeleteRun: (runId: number) => void;
  onViewBatch: (batchId: number) => void;
  onDeleteBatch?: (batchId: number) => void;
}

export default function BacktestHistory({
  runs, batchRuns, strategies, selectedRunId,
  onViewRun, onDeleteRun, onViewBatch, onDeleteBatch,
}: Props) {
  const [expandedBatch, setExpandedBatch] = useState<number | null>(null);

  const strategyName = (id: number) =>
    strategies.find((s) => s.id === id)?.name ?? `Strategy #${id}`;

  // Build merged timeline: standalone single runs + batch groups
  const standaloneRuns = runs.filter((r) => !r.batch_id);
  const batchChildRuns = runs.filter((r) => r.batch_id);

  const entries: HistoryEntry[] = [
    ...standaloneRuns.map((r) => ({ type: "single" as const, run: r })),
    ...batchRuns.map((b, i) => ({
      type: "batch" as const, batch: b,
      strategyName: `${strategyName(b.strategy_id)}...#${batchRuns.length - i}`,
    })),
  ].sort((a, b) => {
    const dateA = a.type === "single" ? a.run.created_at : a.batch.created_at;
    const dateB = b.type === "single" ? b.run.created_at : b.batch.created_at;
    return new Date(dateB ?? 0).getTime() - new Date(dateA ?? 0).getTime();
  });

  if (entries.length === 0) {
    return (
      <div className="text-center py-8">
        <div className="inline-flex p-4 rounded-2xl bg-ag-accent/10 text-ag-accent mb-4">
          <FlaskConical size={32} />
        </div>
        <p className="text-ag-text-muted">No backtests yet. Run your first one above.</p>
      </div>
    );
  }

  const childrenOf = (batchId: number) =>
    batchChildRuns.filter((r) => r.batch_id === batchId);

  return (
    <div className="space-y-1.5">
      {entries.map((entry) => {
        if (entry.type === "single") {
          const r = entry.run;
          return (
            <HistoryRow key={`r-${r.id}`} active={selectedRunId === r.id}
              onClick={() => onViewRun(r.id)} status={r.status}
              label={`${r.symbol} · ${r.timeframe}m`}
              configName={r.config_name}
              date={r.created_at} onDelete={() => onDeleteRun(r.id)} />
          );
        }
        const b = entry.batch;
        const expanded = expandedBatch === b.id;
        return (
          <div key={`b-${b.id}`}>
            <div role="button" tabIndex={0}
              onClick={() => setExpandedBatch(expanded ? null : b.id)}
              onKeyDown={(e) => e.key === "Enter" && setExpandedBatch(expanded ? null : b.id)}
              className="w-full text-left bg-ag-dark border border-ag-border rounded-lg p-3 flex items-center justify-between hover:border-ag-accent/30 cursor-pointer">
              <div className="flex items-center gap-3">
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <StatusDot status={b.status} />
                {b.batch_grade && <GradeBadge grade={b.batch_grade} />}
                <span className="font-medium text-sm">{entry.strategyName}</span>
                <span className="text-xs text-ag-text-muted">
                  {b.total_symbols} symbols · {b.timeframe}m · {b.direction_filter}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-ag-text-muted">
                  {b.created_at && `${new Date(b.created_at).toLocaleDateString()} ${new Date(b.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}`}
                </span>
                {onDeleteBatch && (
                  <button onClick={(e) => {
                    e.stopPropagation();
                    if (confirm(`Delete "${b.name || "Batch #" + b.id}" and all its ${b.total_symbols} runs?`))
                      onDeleteBatch(b.id);
                  }}
                    className="p-1 rounded hover:bg-ag-red/20 hover:text-ag-red text-ag-text-muted transition-colors"
                    title="Delete this batch">
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            </div>
            {expanded && (
              <div className="ml-6 mt-1 space-y-1">
                <button onClick={() => onViewBatch(b.id)}
                  className="text-xs text-ag-accent hover:underline mb-1">
                  View aggregate results
                </button>
                {childrenOf(b.id).map((cr) => (
                  <HistoryRow key={`cr-${cr.id}`} active={selectedRunId === cr.id}
                    onClick={() => onViewRun(cr.id)} status={cr.status}
                    label={cr.symbol} date={null} onDelete={() => onDeleteRun(cr.id)} small />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function HistoryRow({ active, onClick, status, label, configName, date, onDelete, small }: {
  active: boolean; onClick: () => void; status: string;
  label: string; configName?: string | null; date: string | null; onDelete: () => void; small?: boolean;
}) {
  return (
    <div role="button" tabIndex={0} onClick={onClick}
      onKeyDown={(e) => e.key === "Enter" && onClick()}
      className={`w-full text-left bg-ag-dark border rounded-lg ${small ? "p-2" : "p-3"} flex items-center justify-between hover:border-ag-accent/30 cursor-pointer ${
        active ? "border-ag-accent" : "border-ag-border"
      }`}>
      <div className="flex items-center gap-3">
        <StatusDot status={status} />
        <span className={`font-medium ${small ? "text-xs" : "text-sm"}`}>{label}</span>
        {configName && <span className="text-xs text-ag-accent/70">{configName}</span>}
      </div>
      <div className="flex items-center gap-2 text-xs text-ag-text-muted">
        {date && <span>{new Date(date).toLocaleDateString()} {new Date(date).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</span>}
        <button onClick={(e) => { e.stopPropagation(); if (confirm("Delete this run?")) onDelete(); }}
          className="p-1 rounded hover:bg-ag-red/20 hover:text-ag-red"><Trash2 size={12} /></button>
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color = status === "completed" ? "bg-ag-green"
    : status === "error" ? "bg-ag-red"
    : "bg-yellow-500 animate-pulse";
  return <div className={`w-2 h-2 rounded-full ${color}`} />;
}

function GradeBadge({ grade }: { grade: string }) {
  const color = grade.startsWith("A") ? "bg-ag-green/20 text-ag-green border-ag-green/30"
    : grade.startsWith("B") ? "bg-lime-500/20 text-lime-400 border-lime-500/30"
    : grade.startsWith("C") ? "bg-yellow-500/20 text-yellow-400 border-yellow-500/30"
    : grade.startsWith("D") ? "bg-orange-500/20 text-orange-400 border-orange-500/30"
    : "bg-ag-red/20 text-ag-red border-ag-red/30";
  return (
    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${color}`}
      title={`Batch grade: ${grade} — based on PF, win rate, trade count, profitable symbols, and distribution quality`}>
      {grade}
    </span>
  );
}
