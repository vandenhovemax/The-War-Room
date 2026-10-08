"use client";
import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { FlaskConical, Trash2, Eye, EyeOff } from "lucide-react";

interface EngineTrade {
  id: number; symbol: string; side: string; entry_price: number;
  exit_price: number; realized_pnl: number; close_reason: string;
  closed_at: string | null; is_test: boolean;
}

interface Props {
  onChanged?: () => void;
}

export default function EngineTradeManager({ onChanged }: Props) {
  const [show, setShow] = useState(false);
  const [showTest, setShowTest] = useState(false);
  const [trades, setTrades] = useState<EngineTrade[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [dateBefore, setDateBefore] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    if (!show) return;
    setLoading(true);
    api.tradesList(500, showTest)
      .then(setTrades)
      .catch(() => setTrades([]))
      .finally(() => setLoading(false));
  }, [show, showTest]);

  useEffect(() => { load(); }, [load]);

  const refresh = () => { load(); setSelected(new Set()); onChanged?.(); };

  const handleMarkTest = async (isTest: boolean) => {
    if (selected.size === 0) return;
    setBusy(true);
    await api.tradesBulkMarkTest({ trade_ids: Array.from(selected), is_test: isTest });
    setBusy(false);
    refresh();
  };

  const handleDelete = async () => {
    if (selected.size === 0) return;
    if (!confirm(`Delete ${selected.size} engine trades permanently?`)) return;
    setBusy(true);
    await api.tradesBulkDelete(Array.from(selected));
    setBusy(false);
    refresh();
  };

  const handleDateMark = async () => {
    if (!dateBefore) return;
    if (!confirm(`Mark ALL engine trades before ${dateBefore} as Testing?`)) return;
    setBusy(true);
    await api.tradesBulkMarkTest({ date_before: dateBefore, is_test: true });
    setDateBefore("");
    setBusy(false);
    refresh();
  };

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === trades.length) setSelected(new Set());
    else setSelected(new Set(trades.map((t) => t.id)));
  };

  const testCount = trades.filter((t) => t.is_test).length;

  if (!show) {
    return (
      <button onClick={() => setShow(true)}
        className="px-3 py-2 rounded-lg text-sm font-medium flex items-center gap-1.5 bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text transition-colors">
        <FlaskConical size={14} /> Manage Engine Trades
      </button>
    );
  }

  return (
    <div className="bg-ag-card border border-orange-500/30 rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <FlaskConical size={16} className="text-orange-400" />
          Engine Trade Management
          {testCount > 0 && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-orange-500/15 text-orange-400">
              {testCount} test
            </span>
          )}
        </h3>
        <div className="flex items-center gap-3">
          <button onClick={() => setShowTest(!showTest)}
            className="flex items-center gap-1.5 text-xs text-ag-text-muted hover:text-ag-text">
            {showTest ? <Eye size={14} /> : <EyeOff size={14} />}
            {showTest ? "Showing test" : "Test hidden"}
          </button>
          <button onClick={() => setShow(false)} className="text-xs text-ag-text-muted hover:text-ag-text">Close</button>
        </div>
      </div>

      {/* Date range marker */}
      <div className="flex items-center gap-3 p-3 bg-ag-dark/50 rounded-lg border border-ag-border/30">
        <span className="text-xs text-ag-text-muted whitespace-nowrap">Mark all before:</span>
        <input type="date" value={dateBefore} onChange={(e) => setDateBefore(e.target.value)}
          className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs flex-1 max-w-[180px]" />
        <button onClick={handleDateMark} disabled={!dateBefore || busy}
          className="px-3 py-1 bg-orange-500/15 text-orange-400 rounded text-xs font-medium hover:bg-orange-500/25 disabled:opacity-50">
          Mark as Testing
        </button>
      </div>

      {/* Bulk actions */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 p-2 bg-ag-accent/5 rounded-lg border border-ag-accent/20">
          <span className="text-xs font-medium text-ag-accent">{selected.size} selected</span>
          <button onClick={() => handleMarkTest(true)} disabled={busy}
            className="px-2 py-1 bg-orange-500/15 text-orange-400 rounded text-xs hover:bg-orange-500/25 disabled:opacity-50">Mark Testing</button>
          <button onClick={() => handleMarkTest(false)} disabled={busy}
            className="px-2 py-1 bg-ag-green/15 text-ag-green rounded text-xs hover:bg-ag-green/25 disabled:opacity-50">Unmark</button>
          <button onClick={handleDelete} disabled={busy}
            className="px-2 py-1 bg-ag-red/15 text-ag-red rounded text-xs hover:bg-ag-red/25 disabled:opacity-50 flex items-center gap-1">
            <Trash2 size={12} /> Delete
          </button>
          <button onClick={() => setSelected(new Set())}
            className="px-2 py-1 text-ag-text-muted text-xs hover:text-ag-text ml-auto">Clear</button>
        </div>
      )}

      {/* Trade list */}
      <div className="max-h-[400px] overflow-y-auto">
        {loading ? (
          <p className="text-xs text-ag-text-muted text-center py-4">Loading...</p>
        ) : trades.length === 0 ? (
          <p className="text-xs text-ag-text-muted text-center py-4">No engine trades found</p>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-ag-card z-10">
              <tr className="text-[10px] text-ag-text-muted uppercase border-b border-ag-border/30">
                <th className="py-1.5 px-2 text-left">
                  <input type="checkbox" checked={selected.size === trades.length && trades.length > 0}
                    onChange={toggleAll} className="rounded border-ag-border" />
                </th>
                <th className="py-1.5 px-2 text-left">Symbol</th>
                <th className="py-1.5 px-2 text-left">Side</th>
                <th className="py-1.5 px-2 text-right">P&L</th>
                <th className="py-1.5 px-2 text-left">Reason</th>
                <th className="py-1.5 px-2 text-left">Closed</th>
                <th className="py-1.5 px-2 text-left">Type</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id} className={`border-b border-ag-border/10 hover:bg-ag-dark/30 ${t.is_test ? "opacity-60" : ""}`}>
                  <td className="py-1.5 px-2">
                    <input type="checkbox" checked={selected.has(t.id)} onChange={() => toggle(t.id)}
                      className="rounded border-ag-border" />
                  </td>
                  <td className="py-1.5 px-2 font-medium">{t.symbol.replace("USDT", "")}</td>
                  <td className="py-1.5 px-2">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${t.side === "Long" ? "bg-ag-green/15 text-ag-green" : "bg-ag-red/15 text-ag-red"}`}>
                      {t.side}
                    </span>
                  </td>
                  <td className={`py-1.5 px-2 text-right font-mono ${t.realized_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                    ${t.realized_pnl.toFixed(2)}
                  </td>
                  <td className="py-1.5 px-2 text-ag-text-muted">{t.close_reason || "—"}</td>
                  <td className="py-1.5 px-2 text-ag-text-muted">
                    {t.closed_at ? new Date(t.closed_at).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—"}
                  </td>
                  <td className="py-1.5 px-2">
                    {t.is_test
                      ? <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500/15 text-orange-400">Test</span>
                      : <span className="text-[10px] text-ag-text-muted">Real</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
