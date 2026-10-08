"use client";

import { useState, useEffect, useMemo } from "react";
import { api } from "@/lib/api";
import type {
  SessionIntelResult,
  SessionIntelCellMetrics,
  SessionIntelHourMetrics,
  SessionIntelDayMetrics,
  SessionTimeMaskCell,
} from "@/lib/api";
import HintTip from "@/components/common/HintTip";
import SavedMasksList from "@/components/common/SavedMasksList";

interface Props {
  batchId: number;
}

const SUB_TABS = [
  { id: "heatmap", label: "Hour × Day Heatmap" },
  { id: "hour", label: "Hour Profile" },
  { id: "day", label: "Day Profile" },
  { id: "winrate", label: "Win Rate Matrix" },
] as const;

type SubTabId = (typeof SUB_TABS)[number]["id"];

// ---------------------------------------------------------------------------
// Color helpers
// ---------------------------------------------------------------------------

function pnlColor(val: number, maxAbs: number): string {
  if (maxAbs === 0) return "rgba(100,100,100,0.15)";
  const ratio = Math.min(Math.abs(val) / maxAbs, 1);
  if (val > 0) return `rgba(34,197,94,${0.15 + ratio * 0.6})`;
  if (val < 0) return `rgba(239,68,68,${0.15 + ratio * 0.6})`;
  return "rgba(100,100,100,0.15)";
}

function winRateColor(wr: number): string {
  if (wr >= 60) return `rgba(34,197,94,${0.3 + ((wr - 60) / 40) * 0.55})`;
  if (wr > 50) return `rgba(34,197,94,${0.1 + ((wr - 50) / 10) * 0.2})`;
  if (wr === 50) return "rgba(100,100,100,0.15)";
  if (wr >= 40) return `rgba(239,68,68,${0.1 + ((50 - wr) / 10) * 0.2})`;
  return `rgba(239,68,68,${0.3 + ((40 - wr) / 40) * 0.55})`;
}

function fmt(n: number, decimals = 2): string {
  return n.toFixed(decimals);
}

function fmtDollar(n: number): string {
  return n < 0 ? `-$${Math.abs(n).toFixed(2)}` : `$${n.toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// Tooltip for heatmap cells
// ---------------------------------------------------------------------------

function CellTooltip({ m }: { m: SessionIntelCellMetrics }) {
  if (m.trade_count === 0) return <span className="text-ag-text-muted text-[10px]">No trades</span>;
  return (
    <div className="text-[10px] leading-relaxed space-y-0.5">
      <div><b>Trades:</b> {m.trade_count} {!m.is_confident && <span className="text-yellow-400">(low confidence)</span>}</div>
      <div><b>Total PnL:</b> {fmtDollar(m.total_pnl)}</div>
      <div><b>Avg PnL:</b> {fmtDollar(m.avg_pnl)} · <b>Median:</b> {fmtDollar(m.median_pnl)}</div>
      <div><b>Win Rate:</b> {fmt(m.win_rate, 1)}%</div>
      <div><b>PF:</b> {fmt(m.profit_factor)} · <b>Expectancy:</b> {fmtDollar(m.expectancy)}</div>
      <div><b>Avg Win:</b> {fmtDollar(m.avg_win)} · <b>Avg Loss:</b> {fmtDollar(m.avg_loss)}</div>
      <div><b>Payoff:</b> {fmt(m.payoff_ratio)} · <b>Best:</b> {fmtDollar(m.best_trade)} · <b>Worst:</b> {fmtDollar(m.worst_trade)}</div>
      <div><b>Avg MAE:</b> {fmtDollar(m.avg_mae)} · <b>Avg MFE:</b> {fmtDollar(m.avg_mfe)} · <b>MFE/MAE:</b> {fmt(m.mfe_mae_ratio)}</div>
      <div><b>Symbols:</b> {m.symbol_count} · <b>Profitable:</b> {fmt(m.profitable_symbol_pct, 1)}%</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function SessionIntelPanel({ batchId }: Props) {
  const [subTab, setSubTab] = useState<SubTabId>("heatmap");
  const [data, setData] = useState<SessionIntelResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Controls
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [granularity, setGranularity] = useState<"hour" | "half_hour">("hour");
  const [groupBy, setGroupBy] = useState<"entry" | "exit">("entry");
  const [sideFilter, setSideFilter] = useState<"both" | "long" | "short">("both");
  const [minTrades, setMinTrades] = useState(20);

  // Track whether dates were auto-populated (full range = show OOS warning)
  const [batchDateRange, setBatchDateRange] = useState<{ start: string; end: string } | null>(null);
  const isFullRange = batchDateRange && startDate === batchDateRange.start && endDate === batchDateRange.end;

  // Mask creation modal state
  const [showMaskModal, setShowMaskModal] = useState(false);
  const [maskName, setMaskName] = useState("");
  const [maskDesc, setMaskDesc] = useState("");
  const [maskGrid, setMaskGrid] = useState<boolean[][]>(() =>
    Array.from({ length: 24 }, () => Array(7).fill(true))
  );
  const [maskSaving, setMaskSaving] = useState(false);
  const [maskError, setMaskError] = useState("");
  const [maskSuccess, setMaskSuccess] = useState("");
  // Auto-populate thresholds
  const [autoMinPF, setAutoMinPF] = useState(1.0);
  const [autoMinWR, setAutoMinWR] = useState(50);
  const [autoMinSymPct, setAutoMinSymPct] = useState(40);
  const [autoMinTrades, setAutoMinTrades] = useState(10);
  const [maskRefreshKey, setMaskRefreshKey] = useState(0);

  // Auto-populate date range from batch trades on mount
  useEffect(() => {
    api.batchRunDetail(batchId).then((detail) => {
      // Use the batch's date range
      const runs = (detail as unknown as Record<string, unknown>)?.runs as Array<{ start_date?: string; end_date?: string }> | undefined;
      let earliest = "";
      let latest = "";
      if (runs && runs.length > 0) {
        for (const r of runs) {
          const s = r.start_date?.split("T")[0] || "";
          const e = r.end_date?.split("T")[0] || "";
          if (s && (!earliest || s < earliest)) earliest = s;
          if (e && (!latest || e > latest)) latest = e;
        }
      }
      if (earliest && latest) {
        setStartDate(earliest);
        setEndDate(latest);
        setBatchDateRange({ start: earliest, end: latest });
      }
    }).catch(() => {});
  }, [batchId]);

  // Fetch data
  const fetchData = () => {
    if (!startDate || !endDate) return;
    setLoading(true);
    setError("");
    api.sessionIntel(batchId, { start_date: startDate, end_date: endDate, granularity, group_by: groupBy, side_filter: sideFilter, min_trades: minTrades })
      .then((result) => setData(result))
      .catch((err) => setError(err.message || "Failed to load session intel"))
      .finally(() => setLoading(false));
  };

  // Auto-fetch when controls change (debounced by dependency)
  useEffect(() => {
    if (startDate && endDate) fetchData();
  }, [batchId, startDate, endDate, granularity, groupBy, sideFilter, minTrades]);

  // Max absolute PnL for heatmap color scaling
  const maxAbsPnl = useMemo(() => {
    if (!data) return 0;
    let m = 0;
    for (const row of data.heatmap) {
      for (const cell of row) {
        if (cell.trade_count > 0) m = Math.max(m, Math.abs(cell.total_pnl));
      }
    }
    return m;
  }, [data]);

  return (
    <div className="space-y-4">
      {/* Controls bar */}
      <div className="bg-ag-card border border-ag-border rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-4 flex-wrap">
          <label className="text-xs text-ag-text-muted flex items-center gap-1">
            Analysis Start
            <HintTip text="Set to your in-sample period to avoid contaminating OOS data. Viewing time patterns across OOS data leaks information that can bias future decisions." />
          </label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
          />
          <label className="text-xs text-ag-text-muted flex items-center gap-1">
            Analysis End
            <HintTip text="Set to your in-sample period to avoid contaminating OOS data. Viewing time patterns across OOS data leaks information that can bias future decisions." />
          </label>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
          />

          <span className="w-px h-5 bg-ag-border" />

          <label className="text-xs text-ag-text-muted flex items-center gap-1">
            Granularity
            <HintTip text="Hour gives cleaner signals with more trades per cell. Half-hour gives finer resolution but thinner data." />
          </label>
          <select
            value={granularity}
            onChange={(e) => setGranularity(e.target.value as "hour" | "half_hour")}
            className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
          >
            <option value="hour">Hour</option>
            <option value="half_hour">Half-Hour</option>
          </select>

          <span className="w-px h-5 bg-ag-border" />

          <label className="text-xs text-ag-text-muted flex items-center gap-1">
            Group By
            <HintTip text="Entry time shows when your strategy opens positions. Exit time shows when trades close — useful for seeing if exits cluster at specific times." />
          </label>
          <select
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as "entry" | "exit")}
            className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
          >
            <option value="entry">Entry Time</option>
            <option value="exit">Exit Time</option>
          </select>

          <span className="w-px h-5 bg-ag-border" />

          <label className="text-xs text-ag-text-muted flex items-center gap-1">
            Side
            <HintTip text="Long and short may have different time profiles. Impulse buys and structural shorts can peak at different hours." />
          </label>
          <select
            value={sideFilter}
            onChange={(e) => setSideFilter(e.target.value as "both" | "long" | "short")}
            className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
          >
            <option value="both">Both</option>
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>

          <span className="w-px h-5 bg-ag-border" />

          <label className="text-xs text-ag-text-muted flex items-center gap-1">
            Min Trades
            <HintTip text="Cells below this threshold are marked low-confidence. Higher = stricter but may hide thin-but-real patterns. 20 is a reasonable default." />
          </label>
          <input
            type="number"
            min={1}
            value={minTrades}
            onChange={(e) => setMinTrades(Math.max(1, parseInt(e.target.value) || 20))}
            className="w-16 bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm text-center"
          />
        </div>

        {/* OOS Warning Banner */}
        {isFullRange && (
          <div className="bg-amber-500/15 border border-amber-500/40 rounded-lg px-4 py-2.5 text-sm text-amber-300">
            <b>Warning:</b> You are viewing the full backtest range. If this batch contains OOS data, restrict the date range to your in-sample period to preserve OOS integrity.
          </div>
        )}
      </div>

      {/* Summary strip + Create Mask button */}
      {data && (
        <div className="flex items-center gap-4 text-xs text-ag-text-muted">
          <span><b className="text-ag-text">{data.summary.total_trades_in_range.toLocaleString()}</b> trades</span>
          <span><b className={data.summary.total_pnl_in_range >= 0 ? "text-green-400" : "text-red-400"}>{fmtDollar(data.summary.total_pnl_in_range)}</b> total PnL</span>
          <span><b className="text-ag-text">{data.summary.symbols_in_batch}</b> symbols</span>
          <span><b className="text-ag-text">{data.summary.date_range_days}</b> days</span>
          <span className="flex-1" />
          <button
            onClick={() => {
              setMaskName("");
              setMaskDesc("");
              setMaskGrid(Array.from({ length: 24 }, () => Array(7).fill(true)));
              setMaskError("");
              setMaskSuccess("");
              setShowMaskModal(true);
            }}
            className="px-3 py-1.5 rounded-lg bg-ag-accent/20 text-ag-accent hover:bg-ag-accent/30 text-xs font-medium transition-colors flex items-center gap-1.5"
          >
            Create Time Mask
            <HintTip text="Create a saved hour×day filter based on this session intelligence data. Use it in the optimizer to pre-filter trades by time structure. The mask should be based on in-sample data only." />
          </button>
        </div>
      )}

      {/* Saved Masks */}
      <SavedMasksList refreshKey={maskRefreshKey} defaultOpen={false} />

      {/* Sub-tabs */}
      <div className="flex gap-1 border-b border-ag-border/50">
        {SUB_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setSubTab(t.id)}
            className={`px-3 py-1.5 text-xs transition-colors border-b-2 -mb-px ${
              subTab === t.id
                ? "border-ag-accent text-ag-accent"
                : "border-transparent text-ag-text-muted hover:text-ag-text"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      {loading && (
        <div className="text-center py-12 text-ag-text-muted text-sm">Loading session intelligence...</div>
      )}
      {error && (
        <div className="text-center py-12 text-red-400 text-sm">{error}</div>
      )}
      {!loading && !error && !data && (
        <div className="text-center py-12 text-ag-text-muted text-sm">Set date range to begin analysis.</div>
      )}

      {!loading && data && (
        <>
          {subTab === "heatmap" && (
            <HeatmapView data={data} maxAbsPnl={maxAbsPnl} />
          )}
          {subTab === "hour" && (
            <HourProfileView data={data} />
          )}
          {subTab === "day" && (
            <DayProfileView data={data} />
          )}
          {subTab === "winrate" && (
            <WinRateMatrixView data={data} maxAbsPnl={maxAbsPnl} />
          )}
        </>
      )}

      {/* Create Time Mask Modal */}
      {showMaskModal && data && (
        <CreateMaskModal
          data={data}
          maskName={maskName}
          setMaskName={setMaskName}
          maskDesc={maskDesc}
          setMaskDesc={setMaskDesc}
          maskGrid={maskGrid}
          setMaskGrid={setMaskGrid}
          maskSaving={maskSaving}
          maskError={maskError}
          maskSuccess={maskSuccess}
          autoMinPF={autoMinPF}
          setAutoMinPF={setAutoMinPF}
          autoMinWR={autoMinWR}
          setAutoMinWR={setAutoMinWR}
          autoMinSymPct={autoMinSymPct}
          setAutoMinSymPct={setAutoMinSymPct}
          autoMinTrades={autoMinTrades}
          setAutoMinTrades={setAutoMinTrades}
          onAutoPopulate={() => {
            // Auto-populate mask grid from heatmap data using thresholds
            const newGrid = Array.from({ length: 24 }, () => Array(7).fill(false));
            for (let ri = 0; ri < data.heatmap.length; ri++) {
              // Map heatmap row to hour (handles half_hour granularity)
              const hour = data.granularity === "half_hour" ? Math.floor(ri / 2) : ri;
              if (hour >= 24) continue;
              for (let ci = 0; ci < data.heatmap[ri].length && ci < 7; ci++) {
                const cell = data.heatmap[ri][ci];
                if (
                  cell.trade_count >= autoMinTrades &&
                  cell.profit_factor >= autoMinPF &&
                  cell.win_rate >= autoMinWR &&
                  cell.profitable_symbol_pct >= autoMinSymPct
                ) {
                  newGrid[hour][ci] = true;
                }
              }
            }
            setMaskGrid(newGrid);
          }}
          onSave={async () => {
            if (!maskName.trim()) {
              setMaskError("Name is required");
              return;
            }
            setMaskSaving(true);
            setMaskError("");
            setMaskSuccess("");
            try {
              // Build cells array from grid (include mode — only active cells)
              const cells: SessionTimeMaskCell[] = [];
              for (let hour = 0; hour < 24; hour++) {
                const days: number[] = [];
                for (let day = 0; day < 7; day++) {
                  if (maskGrid[hour][day]) days.push(day);
                }
                if (days.length > 0) cells.push({ hour, days });
              }
              await api.sessionMaskCreate({
                name: maskName.trim(),
                description: maskDesc.trim() || undefined,
                mode: "include",
                cells,
              });
              setMaskSuccess(`Mask "${maskName.trim()}" saved! You can now select it in the Optimizer.`);
              setMaskRefreshKey((k) => k + 1);
              setTimeout(() => setShowMaskModal(false), 1500);
            } catch (err: unknown) {
              const msg = err instanceof Error ? err.message : "Failed to save mask";
              setMaskError(msg);
            } finally {
              setMaskSaving(false);
            }
          }}
          onClose={() => setShowMaskModal(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create Time Mask Modal
// ---------------------------------------------------------------------------

const DAY_LABELS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

interface MaskModalProps {
  data: SessionIntelResult;
  maskName: string;
  setMaskName: (v: string) => void;
  maskDesc: string;
  setMaskDesc: (v: string) => void;
  maskGrid: boolean[][];
  setMaskGrid: (v: boolean[][]) => void;
  maskSaving: boolean;
  maskError: string;
  maskSuccess: string;
  autoMinPF: number;
  setAutoMinPF: (v: number) => void;
  autoMinWR: number;
  setAutoMinWR: (v: number) => void;
  autoMinSymPct: number;
  setAutoMinSymPct: (v: number) => void;
  autoMinTrades: number;
  setAutoMinTrades: (v: number) => void;
  onAutoPopulate: () => void;
  onSave: () => void;
  onClose: () => void;
}

function CreateMaskModal({
  data, maskName, setMaskName, maskDesc, setMaskDesc,
  maskGrid, setMaskGrid, maskSaving, maskError, maskSuccess,
  autoMinPF, setAutoMinPF, autoMinWR, setAutoMinWR,
  autoMinSymPct, setAutoMinSymPct, autoMinTrades, setAutoMinTrades,
  onAutoPopulate, onSave, onClose,
}: MaskModalProps) {
  const activeCells = maskGrid.flat().filter(Boolean).length;
  const totalCells = 24 * 7;
  const [isDragging, setIsDragging] = useState(false);
  const [dragValue, setDragValue] = useState(true);

  const toggleCell = (hour: number, day: number) => {
    const next = maskGrid.map((row) => [...row]);
    next[hour][day] = !next[hour][day];
    setMaskGrid(next);
  };

  const setCell = (hour: number, day: number, value: boolean) => {
    if (maskGrid[hour][day] !== value) {
      const next = maskGrid.map((row) => [...row]);
      next[hour][day] = value;
      setMaskGrid(next);
    }
  };

  const selectAll = () => setMaskGrid(Array.from({ length: 24 }, () => Array(7).fill(true)));
  const clearAll = () => setMaskGrid(Array.from({ length: 24 }, () => Array(7).fill(false)));

  const toggleRow = (hour: number) => {
    const allOn = maskGrid[hour].every(Boolean);
    const next = maskGrid.map((row) => [...row]);
    next[hour] = Array(7).fill(!allOn);
    setMaskGrid(next);
  };

  const toggleCol = (day: number) => {
    const allOn = maskGrid.every((row) => row[day]);
    const next = maskGrid.map((row) => [...row]);
    for (let h = 0; h < 24; h++) next[h][day] = !allOn;
    setMaskGrid(next);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-ag-card border border-ag-border rounded-xl shadow-2xl w-[700px] max-h-[90vh] overflow-y-auto p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-ag-text">Create Time Mask</h2>
          <button onClick={onClose} className="text-ag-text-muted hover:text-ag-text text-lg leading-none">&times;</button>
        </div>

        {/* OOS reminder */}
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2 text-xs text-amber-300">
          This mask should be based on <b>in-sample data only</b>. Using OOS data to create a mask leaks future information into the optimizer.
        </div>

        {/* Name / Description */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Mask Name *</label>
            <input
              value={maskName}
              onChange={(e) => setMaskName(e.target.value)}
              placeholder="e.g. US Session Mon-Fri"
              className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="text-xs text-ag-text-muted block mb-1">Description</label>
            <input
              value={maskDesc}
              onChange={(e) => setMaskDesc(e.target.value)}
              placeholder="Optional rationale / notes"
              className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
            />
          </div>
        </div>

        {/* Auto-populate from heatmap */}
        <div className="bg-ag-dark/50 border border-ag-border/50 rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-ag-text">Auto-populate from heatmap</span>
            <HintTip text="Set thresholds to auto-select cells that meet ALL criteria. Cells that don't meet the thresholds will be deselected. You can still manually adjust after." />
          </div>
          <div className="flex items-center gap-3 flex-wrap text-xs">
            <label className="text-ag-text-muted flex items-center gap-1">
              Min PF
              <input
                type="number" step={0.1} min={0} value={autoMinPF}
                onChange={(e) => setAutoMinPF(parseFloat(e.target.value) || 0)}
                className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-center"
              />
            </label>
            <label className="text-ag-text-muted flex items-center gap-1">
              Min WR %
              <input
                type="number" step={1} min={0} max={100} value={autoMinWR}
                onChange={(e) => setAutoMinWR(parseInt(e.target.value) || 0)}
                className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-center"
              />
            </label>
            <label className="text-ag-text-muted flex items-center gap-1">
              Min Sym %
              <input
                type="number" step={1} min={0} max={100} value={autoMinSymPct}
                onChange={(e) => setAutoMinSymPct(parseInt(e.target.value) || 0)}
                className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-center"
              />
            </label>
            <label className="text-ag-text-muted flex items-center gap-1">
              Min Trades
              <input
                type="number" step={1} min={0} value={autoMinTrades}
                onChange={(e) => setAutoMinTrades(parseInt(e.target.value) || 0)}
                className="w-14 bg-ag-dark border border-ag-border rounded px-1.5 py-1 text-center"
              />
            </label>
            <button
              onClick={onAutoPopulate}
              className="ml-auto px-3 py-1 rounded bg-ag-accent/20 text-ag-accent hover:bg-ag-accent/30 text-xs font-medium transition-colors"
            >
              Apply Thresholds
            </button>
          </div>
        </div>

        {/* Interactive 24×7 grid */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-ag-text-muted">
              <b className="text-ag-text">{activeCells}</b> / {totalCells} cells active ({Math.round(activeCells / totalCells * 100)}%)
            </span>
            <div className="flex gap-2">
              <button onClick={selectAll} className="text-[10px] px-2 py-0.5 rounded bg-green-500/15 text-green-400 hover:bg-green-500/25">Select All</button>
              <button onClick={clearAll} className="text-[10px] px-2 py-0.5 rounded bg-red-500/15 text-red-400 hover:bg-red-500/25">Clear All</button>
            </div>
          </div>

          <div
            className="overflow-auto select-none"
            onMouseUp={() => setIsDragging(false)}
            onMouseLeave={() => setIsDragging(false)}
          >
            <table className="text-[10px] border-collapse">
              <thead>
                <tr>
                  <th className="p-1 text-ag-text-muted font-normal text-left w-[60px]">Hour</th>
                  {DAY_LABELS_SHORT.map((d, i) => (
                    <th key={i} className="p-1 min-w-[60px] text-center">
                      <button
                        onClick={() => toggleCol(i)}
                        className={`px-2 py-0.5 rounded text-[10px] hover:bg-ag-border/30 ${i >= 5 ? "text-yellow-400/70" : "text-ag-text-muted"}`}
                      >
                        {d}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 24 }, (_, hour) => (
                  <tr key={hour}>
                    <td className="p-0.5 text-ag-text-muted font-mono pr-2">
                      <button
                        onClick={() => toggleRow(hour)}
                        className="hover:text-ag-text text-[10px]"
                      >
                        {String(hour).padStart(2, "0")}:00
                      </button>
                    </td>
                    {Array.from({ length: 7 }, (_, day) => (
                      <td
                        key={day}
                        className="p-0.5 text-center"
                        onMouseDown={() => {
                          setIsDragging(true);
                          setDragValue(!maskGrid[hour][day]);
                          toggleCell(hour, day);
                        }}
                        onMouseEnter={() => {
                          if (isDragging) setCell(hour, day, dragValue);
                        }}
                      >
                        <div
                          className={`w-full h-5 rounded-sm cursor-pointer transition-colors ${
                            maskGrid[hour][day]
                              ? "bg-ag-accent/40 border border-ag-accent/60"
                              : "bg-ag-dark/60 border border-ag-border/30 hover:border-ag-border/60"
                          }`}
                        />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] text-ag-text-muted mt-1">Click cells to toggle. Click hour/day headers to toggle entire row/column. Drag to paint.</p>
        </div>

        {/* Error / Success */}
        {maskError && (
          <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">{maskError}</div>
        )}
        {maskSuccess && (
          <div className="text-sm text-green-400 bg-green-500/10 border border-green-500/30 rounded-lg px-3 py-2">{maskSuccess}</div>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-3 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg border border-ag-border text-ag-text-muted hover:text-ag-text hover:border-ag-border/80"
          >
            Cancel
          </button>
          <button
            onClick={onSave}
            disabled={maskSaving || !maskName.trim() || activeCells === 0}
            className="px-4 py-2 text-sm rounded-lg bg-ag-accent text-white font-medium hover:bg-ag-accent/80 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {maskSaving ? "Saving..." : "Save Mask"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-tab: Heatmap (Hour × Day)
// ---------------------------------------------------------------------------

function HeatmapView({ data, maxAbsPnl }: { data: SessionIntelResult; maxAbsPnl: number }) {
  const [hoveredCell, setHoveredCell] = useState<{ row: number; col: number } | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-ag-text">
        PnL Heatmap — {data.granularity === "half_hour" ? "Half-Hour" : "Hour"} × Day of Week
      </h3>
      <div className="overflow-auto">
        <table className="text-[10px] border-collapse relative">
          <thead>
            <tr>
              <th className="p-1 text-ag-text-muted font-normal text-left min-w-[100px]">Time (NYT)</th>
              {data.day_labels.map((d, i) => (
                <th key={i} className={`p-1 font-normal min-w-[70px] text-center ${i >= 5 ? "text-yellow-400/70" : "text-ag-text-muted"}`}>
                  {d}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.heatmap.map((row, ri) => {
              const label = data.time_labels[ri];
              const sessionLabel = data.session_labels[String(data.granularity === "half_hour" ? Math.floor(ri / 2) : ri)] || "";
              return (
                <tr key={ri}>
                  <td className="p-1 text-ag-text-muted whitespace-nowrap pr-2">
                    <span className="font-mono">{label}</span>
                    {sessionLabel && <span className="ml-1 text-[9px] text-ag-text-muted/60">({sessionLabel})</span>}
                  </td>
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      className="p-0.5 text-center relative"
                      onMouseEnter={(e) => {
                        setHoveredCell({ row: ri, col: ci });
                        const rect = e.currentTarget.getBoundingClientRect();
                        setTooltipPos({ x: rect.right + 8, y: rect.top });
                      }}
                      onMouseLeave={() => setHoveredCell(null)}
                    >
                      <div
                        className={`rounded-sm px-1 py-1 font-mono text-[10px] ${
                          !cell.is_confident && cell.trade_count > 0 ? "opacity-50" : ""
                        } ${cell.trade_count === 0 ? "opacity-20" : ""}`}
                        style={{ backgroundColor: cell.trade_count > 0 ? pnlColor(cell.total_pnl, maxAbsPnl) : "rgba(100,100,100,0.08)" }}
                      >
                        {cell.trade_count > 0 ? fmtDollar(cell.total_pnl) : "—"}
                      </div>
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>

        {/* Floating tooltip */}
        {hoveredCell && data.heatmap[hoveredCell.row] && (
          <div
            className="fixed z-50 bg-ag-dark border border-ag-border rounded-lg shadow-xl px-3 py-2 max-w-xs"
            style={{ left: Math.min(tooltipPos.x, window.innerWidth - 320), top: Math.min(tooltipPos.y, window.innerHeight - 240) }}
          >
            <div className="text-[10px] text-ag-text-muted mb-1">
              {data.time_labels[hoveredCell.row]} · {data.day_labels[hoveredCell.col]}
            </div>
            <CellTooltip m={data.heatmap[hoveredCell.row][hoveredCell.col]} />
          </div>
        )}
      </div>
      <div className="flex items-center gap-3 text-[10px] text-ag-text-muted">
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(239,68,68,0.6)" }} /> Negative PnL
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(100,100,100,0.15)" }} /> Neutral
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(34,197,94,0.6)" }} /> Positive PnL
        </span>
        <span className="ml-2 opacity-50">Faded cells = low confidence (&lt; min trades)</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-tab: Hour Profile
// ---------------------------------------------------------------------------

function HourProfileView({ data }: { data: SessionIntelResult }) {
  const hours = data.hour_profile;
  const maxBar = Math.max(...hours.map((h) => Math.abs(h.total_pnl)), 1);

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-ag-text">Hour Profile — PnL Contribution by Time of Day</h3>

      {/* Bar chart */}
      <div className="space-y-0.5">
        {hours.map((h, i) => {
          const barWidth = Math.abs(h.total_pnl) / maxBar * 100;
          const isPositive = h.total_pnl >= 0;
          return (
            <div key={i} className="flex items-center gap-2 text-[10px]">
              <span className="w-[110px] shrink-0 text-ag-text-muted font-mono text-right">
                {h.label} <span className="text-[9px] opacity-60">{h.session}</span>
              </span>
              <div className="flex-1 flex items-center h-4">
                <div className="w-1/2 flex justify-end">
                  {!isPositive && (
                    <div
                      className="h-3.5 rounded-l-sm bg-red-500/70"
                      style={{ width: `${barWidth}%` }}
                    />
                  )}
                </div>
                <div className="w-px h-4 bg-ag-border" />
                <div className="w-1/2">
                  {isPositive && (
                    <div
                      className="h-3.5 rounded-r-sm bg-green-500/70"
                      style={{ width: `${barWidth}%` }}
                    />
                  )}
                </div>
              </div>
              <span className={`w-[70px] text-right font-mono ${isPositive ? "text-green-400" : "text-red-400"}`}>
                {fmtDollar(h.total_pnl)}
              </span>
            </div>
          );
        })}
      </div>

      {/* Table */}
      <div className="overflow-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="border-b border-ag-border text-ag-text-muted">
              <th className="text-left py-1.5 px-2">Hour</th>
              <th className="text-right py-1.5 px-2">Trades</th>
              <th className="text-right py-1.5 px-2">Total PnL</th>
              <th className="text-right py-1.5 px-2">PnL %</th>
              <th className="text-right py-1.5 px-2">Win Rate</th>
              <th className="text-right py-1.5 px-2">PF</th>
              <th className="text-right py-1.5 px-2">Expectancy</th>
              <th className="text-right py-1.5 px-2">Payoff</th>
              <th className="text-right py-1.5 px-2">
                Sym Consistency
                <HintTip text="What % of symbols are individually profitable in this hour. High % = structural edge. Low % = one or two symbols carrying the cell." />
              </th>
              <th className="text-right py-1.5 px-2">Edge Density</th>
            </tr>
          </thead>
          <tbody>
            {hours.map((h, i) => (
              <tr key={i} className={`border-b border-ag-border/30 ${!h.is_confident ? "opacity-50" : ""}`}>
                <td className="py-1 px-2 font-mono">{h.label} <span className="text-[9px] text-ag-text-muted">({h.session})</span></td>
                <td className="text-right py-1 px-2">{h.trade_count}</td>
                <td className={`text-right py-1 px-2 font-mono ${h.total_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>{fmtDollar(h.total_pnl)}</td>
                <td className="text-right py-1 px-2">{fmt(h.pnl_contribution_pct, 1)}%</td>
                <td className="text-right py-1 px-2">{fmt(h.win_rate, 1)}%</td>
                <td className="text-right py-1 px-2">{fmt(h.profit_factor)}</td>
                <td className={`text-right py-1 px-2 font-mono ${h.expectancy >= 0 ? "text-green-400" : "text-red-400"}`}>{fmtDollar(h.expectancy)}</td>
                <td className="text-right py-1 px-2">{fmt(h.payoff_ratio)}</td>
                <td className="text-right py-1 px-2">{fmt(h.profitable_symbol_pct, 1)}%</td>
                <td className={`text-right py-1 px-2 font-mono ${h.edge_density >= 0 ? "text-green-400" : "text-red-400"}`}>{fmtDollar(h.edge_density)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-tab: Day-of-Week Profile
// ---------------------------------------------------------------------------

function DayProfileView({ data }: { data: SessionIntelResult }) {
  const days = data.day_profile;
  const maxBar = Math.max(...days.map((d) => Math.abs(d.total_pnl)), 1);

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-ag-text">Day-of-Week Profile — PnL Contribution by Day</h3>

      {/* Bar chart */}
      <div className="flex items-end gap-2 h-40 px-4">
        {days.map((d, i) => {
          const barH = Math.abs(d.total_pnl) / maxBar * 100;
          const isPositive = d.total_pnl >= 0;
          return (
            <div key={i} className="flex-1 flex flex-col items-center gap-1">
              <span className={`text-[10px] font-mono ${isPositive ? "text-green-400" : "text-red-400"}`}>
                {fmtDollar(d.total_pnl)}
              </span>
              <div className="w-full flex justify-center" style={{ height: "100px" }}>
                <div className="w-8 flex flex-col justify-end">
                  <div
                    className={`w-full rounded-t-sm ${isPositive ? "bg-green-500/70" : "bg-red-500/70"}`}
                    style={{ height: `${barH}%` }}
                  />
                </div>
              </div>
              <span className={`text-[11px] font-medium ${d.is_weekend ? "text-yellow-400/80" : "text-ag-text-muted"}`}>
                {d.label}
              </span>
            </div>
          );
        })}
      </div>

      {/* Table */}
      <div className="overflow-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="border-b border-ag-border text-ag-text-muted">
              <th className="text-left py-1.5 px-2">Day</th>
              <th className="text-right py-1.5 px-2">Trades</th>
              <th className="text-right py-1.5 px-2">Total PnL</th>
              <th className="text-right py-1.5 px-2">PnL %</th>
              <th className="text-right py-1.5 px-2">Win Rate</th>
              <th className="text-right py-1.5 px-2">PF</th>
              <th className="text-right py-1.5 px-2">Expectancy</th>
              <th className="text-right py-1.5 px-2">Avg/Session</th>
              <th className="text-right py-1.5 px-2">Consistency</th>
              <th className="text-right py-1.5 px-2">Best Day</th>
              <th className="text-right py-1.5 px-2">Worst Day</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d, i) => (
              <tr key={i} className={`border-b border-ag-border/30 ${d.is_weekend ? "bg-yellow-500/5" : ""} ${!d.is_confident ? "opacity-50" : ""}`}>
                <td className={`py-1 px-2 font-medium ${d.is_weekend ? "text-yellow-400/80" : ""}`}>{d.label}</td>
                <td className="text-right py-1 px-2">{d.trade_count}</td>
                <td className={`text-right py-1 px-2 font-mono ${d.total_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>{fmtDollar(d.total_pnl)}</td>
                <td className="text-right py-1 px-2">{fmt(d.pnl_contribution_pct, 1)}%</td>
                <td className="text-right py-1 px-2">{fmt(d.win_rate, 1)}%</td>
                <td className="text-right py-1 px-2">{fmt(d.profit_factor)}</td>
                <td className={`text-right py-1 px-2 font-mono ${d.expectancy >= 0 ? "text-green-400" : "text-red-400"}`}>{fmtDollar(d.expectancy)}</td>
                <td className="text-right py-1 px-2">{fmt(d.avg_trades_per_session, 1)}</td>
                <td className="text-right py-1 px-2">{fmt(d.consistency, 1)}%</td>
                <td className="text-right py-1 px-2 font-mono text-green-400">{fmtDollar(d.best_day_pnl)}</td>
                <td className="text-right py-1 px-2 font-mono text-red-400">{fmtDollar(d.worst_day_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-tab: Win Rate Matrix
// ---------------------------------------------------------------------------

function WinRateMatrixView({ data, maxAbsPnl }: { data: SessionIntelResult; maxAbsPnl: number }) {
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-ag-text">Win Rate Matrix — Probability of Profit per Cell</h3>
      <div className="overflow-auto">
        <table className="text-[10px] border-collapse">
          <thead>
            <tr>
              <th className="p-1 text-ag-text-muted font-normal text-left min-w-[100px]">Time (NYT)</th>
              {data.day_labels.map((d, i) => (
                <th key={i} className={`p-1 font-normal min-w-[60px] text-center ${i >= 5 ? "text-yellow-400/70" : "text-ag-text-muted"}`}>
                  {d}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.heatmap.map((row, ri) => {
              const label = data.time_labels[ri];
              return (
                <tr key={ri}>
                  <td className="p-1 text-ag-text-muted whitespace-nowrap pr-2 font-mono">{label}</td>
                  {row.map((cell, ci) => (
                    <td key={ci} className="p-0.5 text-center">
                      <div
                        className={`rounded-sm px-1 py-1 font-mono text-[10px] ${
                          !cell.is_confident && cell.trade_count > 0 ? "opacity-50" : ""
                        } ${cell.trade_count === 0 ? "opacity-20" : ""}`}
                        style={{ backgroundColor: cell.trade_count > 0 ? pnlColor(cell.total_pnl, maxAbsPnl) : "rgba(100,100,100,0.08)" }}
                        title={`${data.time_labels[ri]} ${data.day_labels[ci]}: ${fmt(cell.win_rate, 1)}% WR (${cell.trade_count} trades)`}
                      >
                        {cell.trade_count > 0 ? `${fmt(cell.win_rate, 0)}%` : "—"}
                      </div>
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-3 text-[10px] text-ag-text-muted">
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(239,68,68,0.6)" }} /> Negative PnL
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(100,100,100,0.15)" }} /> Neutral
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "rgba(34,197,94,0.6)" }} /> Positive PnL
        </span>
        <span className="ml-2 opacity-50">Faded cells = low confidence (&lt; min trades)</span>
      </div>
    </div>
  );
}
