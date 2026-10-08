"use client";
import { useState, useEffect } from "react";
import { X, ChevronRight, ChevronDown } from "lucide-react";
import { api } from "@/lib/api";
import type { MonthPosition, MonthTradeExit, MonthTradesResponse } from "@/lib/api";
import TradeChartButton from "@/components/common/TradeChartButton";
import TradeChartPopup from "@/components/common/TradeChartPopup";
import type { TradeChartData } from "@/components/common/TradeChartPopup";

const MONTH_NAMES = [
  "", "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

interface Props {
  wfoRunId: number;
  year: number;
  month: number;
  windowNum?: number;
  onClose: () => void;
  /** Phase 2 selection changes — show asset switches that happened inside this month */
  selectionChanges?: { date: string; added: string[]; removed: string[]; window_num?: number }[];
  /** Phase 2 selected trade records — when provided, the modal shows Phase 2 trades
   *  only (filtered to this month) instead of fetching the Phase 1 trades list. */
  phase2Records?: { dt: string; pnl: number; asset: string; symbol: string; side: string; timeframe: string; batch_id?: number; batch_name?: string; batch_group?: string; pnl_multiplier?: number }[];
}

const numColor = (v: number) =>
  v > 0 ? "text-ag-green" : v < 0 ? "text-ag-red" : "";

const fmtDollar = (v: number | null) =>
  v == null ? "\u2014" : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const fmtTime = (v: string | null) => {
  if (!v) return "\u2014";
  return new Date(v).toLocaleString("en-US", {
    timeZone: "America/New_York",
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
};

export default function WFASMonthTradesModal({
  wfoRunId, year, month, windowNum, onClose, selectionChanges, phase2Records,
}: Props) {
  // Filter selection changes to those that fell inside THIS month
  const monthPrefix = `${year}-${String(month).padStart(2, "0")}`;
  const monthChanges = (selectionChanges ?? [])
    .filter(c => (c.date || "").startsWith(monthPrefix))
    .sort((a, b) => a.date.localeCompare(b.date));

  // Phase 2 mode: filter the Phase 2 selected trade records to this month
  const isPhase2 = Array.isArray(phase2Records);
  const p2Month = isPhase2
    ? (phase2Records ?? [])
        .filter(r => (r.dt || "").startsWith(monthPrefix))
        .sort((a, b) => a.dt.localeCompare(b.dt))
    : [];
  const p2Pnl = p2Month.reduce((s, r) => s + r.pnl, 0);
  const p2Wins = p2Month.filter(r => r.pnl > 0).length;
  const p2Wr = p2Month.length > 0 ? ((p2Wins / p2Month.length) * 100).toFixed(1) : "0";
  const [positions, setPositions] = useState<MonthPosition[]>([]);
  const [posCount, setPosCount] = useState(0);
  const [tradeCount, setTradeCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [chartTrade, setChartTrade] = useState<TradeChartData | null>(null);
  const [expandedPos, setExpandedPos] = useState<Set<number>>(new Set());
  const [sortKey, setSortKey] = useState<"entry_time" | "net_profit" | "asset">("entry_time");
  const [sortAsc, setSortAsc] = useState(true);

  useEffect(() => {
    // Phase 2 mode — no API call; data is already in props.
    if (isPhase2) {
      setLoading(false);
      setError("");
      setPositions([]);
      setPosCount(p2Month.length);
      setTradeCount(p2Month.length);
      return;
    }
    setLoading(true);
    setError("");
    api.wfoMonthTrades(wfoRunId, year, month, windowNum)
      .then((r: MonthTradesResponse) => {
        setPositions(r.positions ?? []);
        setPosCount(r.position_count ?? r.positions?.length ?? 0);
        setTradeCount(r.trade_count ?? 0);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load trades"))
      .finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wfoRunId, year, month, windowNum, isPhase2]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const totalPnl = positions.reduce((s, p) => s + p.net_profit, 0);
  const wins = positions.filter(p => p.net_profit > 0).length;
  const wr = positions.length > 0 ? (wins / positions.length * 100).toFixed(1) : "0";

  // Concentration diagnostic for this month:
  //   monthMaxPct = |largest single trade| / |month total PnL|
  //   A value above ~0.80 means one trade is effectively the entire month —
  //   the config isn't really winning, it rode a single fill.
  // Also detect cross-batch duplicate fills (same symbol+side at same entry bar)
  // which inflate the headline with non-independent trades.
  const absPnls = positions.map(p => Math.abs(p.net_profit)).sort((a, b) => b - a);
  const monthMaxPct = absPnls.length && Math.abs(totalPnl) > 1e-9
    ? absPnls[0] / Math.abs(totalPnl)
    : 0;
  const monthTop2Pct = absPnls.length >= 2 && Math.abs(totalPnl) > 1e-9
    ? (absPnls[0] + absPnls[1]) / Math.abs(totalPnl)
    : 0;
  // Duplicate detection: same (symbol, side, entry_time rounded to bar)
  const dupKeys = new Set<string>();
  const dupHits: string[] = [];
  for (const p of positions) {
    const sym = (p.asset || "").replace(/_(long|short)$/i, "");
    const side = (p.side || "").toLowerCase();
    // Round to 30-minute bar
    const t = p.entry_time ? new Date(p.entry_time).getTime() : 0;
    const barKey = Math.floor(t / (30 * 60 * 1000));
    const key = `${sym}|${side}|${barKey}`;
    if (dupKeys.has(key)) dupHits.push(`${sym} ${side}`);
    else dupKeys.add(key);
  }
  const hasDuplicates = dupHits.length > 0;
  // Severity: 🔴 >80% (whole month is 1 trade), ⚠️ >50% (dominant trade),
  // plus a separate duplicate flag.
  const concSeverity: "ok" | "warn" | "danger" | null =
    positions.length === 0 ? null
    : monthMaxPct > 0.80 ? "danger"
    : monthMaxPct > 0.50 ? "warn"
    : null;

  // Sort positions
  const sorted = [...positions].sort((a, b) => {
    let cmp = 0;
    if (sortKey === "net_profit") cmp = a.net_profit - b.net_profit;
    else if (sortKey === "asset") cmp = (a.asset || "").localeCompare(b.asset || "");
    else cmp = (a.entry_time || "").localeCompare(b.entry_time || "");
    return sortAsc ? cmp : -cmp;
  });

  const handleSort = (key: typeof sortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(true); }
  };

  const toggleExpand = (num: number) => {
    setExpandedPos(prev => {
      const next = new Set(prev);
      if (next.has(num)) next.delete(num);
      else next.add(num);
      return next;
    });
  };

  const openChart = (p: MonthPosition, exit?: MonthTradeExit) => {
    // Backend stores side as lowercase ("long" / "short"), but some legacy rows
    // may be capital — strip both cases so the popup sees the real Bybit symbol.
    const sym = p.asset.replace(/_(long|short)$/i, "");
    const chosenExit = exit ?? p.exits[p.exits.length - 1];
    setChartTrade({
      symbol: sym,
      timeframe: p.timeframe || "30",
      side: p.side || "",
      entryTime: p.entry_time || "",
      exitTime: chosenExit?.exit_time ?? null,
      entryPrice: p.entry_price ?? 0,
      exitPrice: chosenExit?.exit_price ?? null,
      slPrice: chosenExit?.sl_price ?? null,
      tpPrice: chosenExit?.tp_price ?? null,
      netProfit: p.net_profit,
      strategyId: p.strategy_id,
      runId: p.run_id ?? null,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />

      {/* Modal */}
      <div className="relative bg-ag-card border border-ag-border rounded-xl shadow-2xl w-full max-w-5xl max-h-[85vh] flex flex-col mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-ag-border">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-semibold text-ag-text">
              {MONTH_NAMES[month]} {year}
            </h3>
            <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-ag-accent/15 text-ag-accent font-medium">
              {isPhase2 ? "Phase 2 — Selected Trades" : (windowNum != null ? `Window ${windowNum} OOS` : "All Windows OOS")}
            </span>
            {isPhase2 ? (
              <>
                <span className="text-xs text-ag-text-muted">
                  {p2Month.length} trade{p2Month.length !== 1 ? "s" : ""}
                </span>
                {p2Month.length > 0 && (
                  <>
                    <span className={`text-xs font-mono font-medium ${numColor(p2Pnl)}`}>
                      {fmtDollar(p2Pnl)}
                    </span>
                    <span className="text-[10px] text-ag-text-muted">WR: {p2Wr}%</span>
                  </>
                )}
              </>
            ) : (
              <>
                <span className="text-xs text-ag-text-muted">
                  {posCount} position{posCount !== 1 ? "s" : ""}
                  <span className="text-ag-text-muted/50 ml-1">({tradeCount} exits)</span>
                </span>
                {!loading && positions.length > 0 && (
                  <>
                    <span className={`text-xs font-mono font-medium ${numColor(totalPnl)}`}>
                      {fmtDollar(totalPnl)}
                    </span>
                    <span className="text-[10px] text-ag-text-muted">
                      WR: {wr}%
                    </span>
                  </>
                )}
              </>
            )}
          </div>
          <button onClick={onClose} className="p-1 hover:bg-ag-dark/50 rounded transition-colors">
            <X size={16} className="text-ag-text-muted" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-3">
          {/* Concentration warnings — single-trade dominance + cross-batch duplicates */}
          {(concSeverity || hasDuplicates) && !isPhase2 && (
            <div
              className={`mb-3 rounded-lg border px-3 py-2 text-[11px] ${
                concSeverity === "danger"
                  ? "bg-ag-red/10 border-ag-red/40 text-ag-red"
                  : "bg-amber-400/10 border-amber-400/40 text-amber-400"
              }`}
            >
              <div className="font-semibold mb-1">
                {concSeverity === "danger" ? "🔴 Concentration: month rides a single trade"
                  : concSeverity === "warn" ? "⚠️ Concentration: one trade dominates"
                  : "⚠️ Duplicate fills detected"}
              </div>
              <ul className="space-y-0.5 list-disc list-inside">
                {concSeverity && (
                  <li>
                    Largest trade is {(monthMaxPct * 100).toFixed(0)}% of this month&apos;s PnL
                    {absPnls.length >= 2 && ` · top 2 = ${(monthTop2Pct * 100).toFixed(0)}%`}
                  </li>
                )}
                {hasDuplicates && (
                  <li>
                    {dupHits.length} duplicate fill{dupHits.length !== 1 ? "s" : ""} (same symbol+side on same bar):
                    {" "}{Array.from(new Set(dupHits)).slice(0, 4).join(", ")}
                    {dupHits.length > 4 && ` and ${dupHits.length - 4} more`}
                  </li>
                )}
              </ul>
            </div>
          )}

          {/* Phase 2 asset selection changes that happened in this month */}
          {monthChanges.length > 0 && (
            <div className="mb-4 border border-ag-accent/30 rounded-lg bg-ag-accent/5 p-3">
              <div className="text-[10px] uppercase text-ag-accent font-semibold mb-2">
                Asset Selection Changes This Month ({monthChanges.length})
              </div>
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-ag-text-muted border-b border-ag-border/30">
                    <th className="text-left py-1 px-2">Date</th>
                    <th className="text-left py-1 px-2">In</th>
                    <th className="text-left py-1 px-2">Out</th>
                  </tr>
                </thead>
                <tbody>
                  {monthChanges.map((c, i) => (
                    <tr key={`${c.date}-${i}`} className="border-b border-ag-border/10">
                      <td className="py-1 px-2 font-mono text-ag-text-muted">{c.date}</td>
                      <td className="py-1 px-2">
                        {c.added.length > 0 ? (
                          <span className="text-ag-green font-mono">
                            {c.added.map(a => a.replace("USDT_Long", "↑").replace("USDT_Short", "↓").replace("USDT", "")).join(", ")}
                          </span>
                        ) : <span className="text-ag-text-muted/40">—</span>}
                      </td>
                      <td className="py-1 px-2">
                        {c.removed.length > 0 ? (
                          <span className="text-ag-red font-mono">
                            {c.removed.map(a => a.replace("USDT_Long", "↑").replace("USDT_Short", "↓").replace("USDT", "")).join(", ")}
                          </span>
                        ) : <span className="text-ag-text-muted/40">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {isPhase2 && p2Month.length === 0 && (
            <p className="text-xs text-ag-text-muted py-4">No Phase 2 trades for this month.</p>
          )}
          {isPhase2 && p2Month.length > 0 && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ag-text-muted border-b border-ag-border/30">
                  <th className="text-left py-1.5 px-2 font-medium">#</th>
                  <th className="text-left py-1.5 px-2 font-medium">Date</th>
                  <th className="text-left py-1.5 px-2 font-medium">Group</th>
                  <th className="text-left py-1.5 px-2 font-medium">Batch</th>
                  <th className="text-left py-1.5 px-2 font-medium">Asset</th>
                  <th className="text-left py-1.5 px-2 font-medium">Side</th>
                  <th className="text-left py-1.5 px-2 font-medium">TF</th>
                  <th className="text-right py-1.5 px-2 font-medium">P&L</th>
                </tr>
              </thead>
              <tbody>
                {p2Month.map((r, i) => {
                  const sym = (r.symbol || r.asset).replace(/_Long$/, "").replace(/_Short$/, "").replace("USDT", "");
                  const side = r.side || (r.asset.endsWith("_Long") ? "Long" : r.asset.endsWith("_Short") ? "Short" : "");
                  const sideColor = side === "Long" ? "text-ag-green" : side === "Short" ? "text-ag-red" : "";
                  return (
                    <tr key={i} className="border-b border-ag-border/10 hover:bg-ag-dark/20">
                      <td className="py-1.5 px-2 text-ag-text-muted">{i + 1}</td>
                      <td className="py-1.5 px-2 font-mono text-[11px]">{fmtTime(r.dt)}</td>
                      <td className="py-1.5 px-2 text-ag-accent text-[10px]">{r.batch_group || "\u2014"}</td>
                      <td className="py-1.5 px-2 text-ag-text-muted text-[10px]" title={r.batch_name || ""}>
                        {r.batch_id ?? "\u2014"}
                        {r.pnl_multiplier != null && r.pnl_multiplier !== 1 && (
                          <span className="ml-1 text-purple-400">{r.pnl_multiplier}x</span>
                        )}
                      </td>
                      <td className="py-1.5 px-2 font-medium">{sym}</td>
                      <td className={`py-1.5 px-2 ${sideColor}`}>{side ? side.charAt(0) : ""}</td>
                      <td className="py-1.5 px-2 text-ag-text-muted text-[10px]">{r.timeframe || "—"}</td>
                      <td className={`py-1.5 px-2 text-right font-mono font-medium ${numColor(r.pnl)}`}>
                        {fmtDollar(r.pnl)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-ag-border font-semibold">
                  <td colSpan={7} className="py-1.5 px-2 text-right text-ag-text-muted">Total:</td>
                  <td className={`py-1.5 px-2 text-right font-mono ${numColor(p2Pnl)}`}>
                    {fmtDollar(p2Pnl)}
                  </td>
                </tr>
              </tfoot>
            </table>
          )}
          {!isPhase2 && loading && (
            <p className="text-xs text-ag-text-muted py-4">Loading positions...</p>
          )}
          {!isPhase2 && error && (
            <p className="text-xs text-ag-red py-4">{error}</p>
          )}
          {!isPhase2 && !loading && !error && positions.length === 0 && (
            <p className="text-xs text-ag-text-muted py-4">No positions for this month.</p>
          )}
          {!isPhase2 && !loading && !error && positions.length > 0 && (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-ag-text-muted border-b border-ag-border/30">
                  <th className="text-left py-1.5 px-1 w-5"></th>
                  <th className="text-left py-1.5 px-2 font-medium">#</th>
                  <th className="text-left py-1.5 px-2 font-medium">Group</th>
                  <th className="text-left py-1.5 px-2 font-medium">Batch</th>
                  <th
                    className="text-left py-1.5 px-2 font-medium cursor-pointer hover:text-ag-text"
                    onClick={() => handleSort("asset")}
                  >
                    Asset {sortKey === "asset" ? (sortAsc ? "↑" : "↓") : ""}
                  </th>
                  <th className="text-left py-1.5 px-2 font-medium">Side</th>
                  <th
                    className="text-left py-1.5 px-2 font-medium cursor-pointer hover:text-ag-text"
                    onClick={() => handleSort("entry_time")}
                  >
                    Entry {sortKey === "entry_time" ? (sortAsc ? "↑" : "↓") : ""}
                  </th>
                  <th className="text-right py-1.5 px-2 font-medium">Entry $</th>
                  <th className="text-left py-1.5 px-2 font-medium">Exit</th>
                  <th
                    className="text-right py-1.5 px-2 font-medium cursor-pointer hover:text-ag-text"
                    onClick={() => handleSort("net_profit")}
                  >
                    P&L {sortKey === "net_profit" ? (sortAsc ? "↑" : "↓") : ""}
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((p) => {
                  const isOpen = expandedPos.has(p.num);
                  const hasMultiExit = p.exits.length > 1;
                  const sideColor = p.side === "Long"
                    ? "text-ag-green" : p.side === "Short"
                    ? "text-ag-red" : "";

                  return (
                    <PositionRow
                      key={p.num}
                      pos={p}
                      isOpen={isOpen}
                      hasMultiExit={hasMultiExit}
                      sideColor={sideColor}
                      onToggle={() => toggleExpand(p.num)}
                      onChart={(exit) => openChart(p, exit)}
                    />
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-ag-border font-semibold">
                  <td colSpan={9} className="py-1.5 px-2 text-right text-ag-text-muted">Total:</td>
                  <td className={`py-1.5 px-2 text-right font-mono ${numColor(totalPnl)}`}>
                    {fmtDollar(totalPnl)}
                  </td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      </div>

      {chartTrade && (
        <TradeChartPopup
          trade={chartTrade}
          onClose={() => setChartTrade(null)}
        />
      )}
    </div>
  );
}


/* Position row + expandable exits */
function PositionRow({
  pos, isOpen, hasMultiExit, sideColor, onToggle, onChart,
}: {
  pos: MonthPosition;
  isOpen: boolean;
  hasMultiExit: boolean;
  sideColor: string;
  onToggle: () => void;
  onChart: (exit?: MonthTradeExit) => void;
}) {
  const lastExit = pos.exits[pos.exits.length - 1];

  return (
    <>
      {/* Position summary row */}
      <tr
        className={`border-b border-ag-border/10 hover:bg-ag-dark/20 ${hasMultiExit ? "cursor-pointer" : ""}`}
        onClick={hasMultiExit ? onToggle : undefined}
      >
        <td className="py-1.5 px-1 text-ag-text-muted">
          {hasMultiExit ? (
            isOpen
              ? <ChevronDown size={12} className="text-ag-accent" />
              : <ChevronRight size={12} />
          ) : null}
        </td>
        <td className="py-1.5 px-2 text-ag-text-muted">{pos.num}</td>
        <td className="py-1.5 px-2 text-ag-accent text-[10px]">{pos.batch_group || "\u2014"}</td>
        <td className="py-1.5 px-2 text-ag-text-muted text-[10px]" title={pos.batch_name || ""}>
          {pos.batch_id ?? "\u2014"}
          {pos.pnl_multiplier != null && pos.pnl_multiplier !== 1 && (
            <span className="ml-1 text-purple-400">{pos.pnl_multiplier}x</span>
          )}
        </td>
        <td className="py-1.5 px-2 font-medium">{pos.asset_display}</td>
        <td className={`py-1.5 px-2 ${sideColor}`}>
          {pos.side ? pos.side.charAt(0) : ""}
        </td>
        <td className="py-1.5 px-2">{fmtTime(pos.entry_time)}</td>
        <td className="py-1.5 px-2 text-right font-mono">
          {pos.entry_price != null ? pos.entry_price.toFixed(4) : "\u2014"}
        </td>
        <td className="py-1.5 px-2">
          {hasMultiExit ? (
            <span className="text-ag-text-muted">
              {isOpen ? "" : `▸ ${pos.exits.length} exits`}
            </span>
          ) : (
            fmtTime(lastExit?.exit_time ?? null)
          )}
        </td>
        <td className={`py-1.5 px-2 text-right font-mono font-medium ${numColor(pos.net_profit)}`}>
          {fmtDollar(pos.net_profit)}
          <TradeChartButton onClick={(e) => { e.stopPropagation(); onChart(); }} />
        </td>
      </tr>

      {/* Expanded exit rows */}
      {isOpen && pos.exits.map((ex, i) => (
        <tr key={i} className="bg-ag-dark/30 border-b border-ag-border/5">
          <td></td>
          <td></td>
          <td></td>
          <td></td>
          <td></td>
          <td></td>
          <td className="py-1 px-2 text-[10px] text-ag-text-muted">
            {fmtTime(ex.exit_time)}
          </td>
          <td className="py-1 px-2 text-right font-mono text-[10px]">
            {ex.exit_price != null ? ex.exit_price.toFixed(4) : "\u2014"}
          </td>
          <td className="py-1 px-2 text-[10px] text-ag-text-muted">
            {ex.reason || ""}
            {ex.quantity != null && (
              <span className="ml-1 text-ag-text-muted/50">qty: {ex.quantity}</span>
            )}
          </td>
          <td className={`py-1 px-2 text-right font-mono text-[10px] ${numColor(ex.net_profit)}`}>
            {fmtDollar(ex.net_profit)}
          </td>
        </tr>
      ))}
    </>
  );
}
