"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { JournalTradeItem, StrategyItem, StrategyConfigItem } from "@/lib/api";

interface TradeCardProps {
  trade: JournalTradeItem;
  strategies: StrategyItem[];
  onUpdated: () => void;
}

const EXIT_REASONS = ["TP Hit", "SL Hit", "Trailing Stop", "Manual Close", "Signal", "Other"];

export default function TradeCard({ trade, strategies, onUpdated }: TradeCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [closing, setClosing] = useState(false);
  const [editing, setEditing] = useState(false);

  // Close trade fields
  const [exitPrice, setExitPrice] = useState("");
  const [exitReason, setExitReason] = useState("Manual Close");
  const [actualComm, setActualComm] = useState("");
  const [actualPnl, setActualPnl] = useState("");

  // Edit fields — all editable trade properties
  const [editSymbol, setEditSymbol] = useState(trade.symbol);
  const [editSide, setEditSide] = useState(trade.side);
  const [editEntry, setEditEntry] = useState(String(trade.entry_price));
  const [editQty, setEditQty] = useState(String(trade.quantity));
  const [editRisk, setEditRisk] = useState(trade.risk_amount != null ? String(trade.risk_amount) : "");
  const [editSl, setEditSl] = useState(trade.sl_price != null ? String(trade.sl_price) : "");
  const [editTp, setEditTp] = useState(trade.tp_price != null ? String(trade.tp_price) : "");
  const [editTp2, setEditTp2] = useState(trade.tp2_price != null ? String(trade.tp2_price) : "");
  const [editTime, setEditTime] = useState(trade.entry_time?.split(".")[0] || "");
  const [editNotes, setEditNotes] = useState(trade.notes);
  const [editRating, setEditRating] = useState(trade.rating);
  const [editActualComm, setEditActualComm] = useState(
    trade.actual_commission != null ? String(trade.actual_commission) : "",
  );
  const [editNetProfit, setEditNetProfit] = useState(
    trade.net_profit != null ? String(trade.net_profit) : "",
  );
  const [editStrategyId, setEditStrategyId] = useState<number | null>(trade.strategy_id ?? null);
  const [editConfigId, setEditConfigId] = useState<number | null>(trade.config_id ?? null);
  const [editConfigs, setEditConfigs] = useState<StrategyConfigItem[]>([]);

  // Load configs when editing strategy changes
  useEffect(() => {
    if (editStrategyId && editing) {
      api.strategyConfigs(editStrategyId).then(setEditConfigs).catch(() => setEditConfigs([]));
    } else {
      setEditConfigs([]);
    }
  }, [editStrategyId, editing]);

  const isOpen = trade.status === "open";
  const sideColor = trade.side === "Long" ? "text-ag-green" : "text-ag-red";
  const sideBg = trade.side === "Long" ? "bg-ag-green/15" : "bg-ag-red/15";

  const handleClose = async () => {
    if (!exitPrice) return;
    setClosing(true);
    try {
      await api.journalUpdate(trade.id, {
        status: "closed",
        exit_price: parseFloat(exitPrice),
        exit_reason: exitReason,
        actual_commission: actualComm ? parseFloat(actualComm) : undefined,
        net_profit: actualPnl ? parseFloat(actualPnl) : undefined,
      });
      onUpdated();
    } catch { /* handled */ }
    setClosing(false);
  };

  const handleSaveEdit = async () => {
    try {
      await api.journalUpdate(trade.id, {
        symbol: editSymbol,
        side: editSide,
        entry_price: parseFloat(editEntry),
        quantity: parseFloat(editQty),
        risk_amount: editRisk ? parseFloat(editRisk) : undefined,
        sl_price: editSl ? parseFloat(editSl) : undefined,
        tp_price: editTp ? parseFloat(editTp) : undefined,
        tp2_price: editTp2 ? parseFloat(editTp2) : undefined,
        entry_time: editTime || undefined,
        notes: editNotes,
        rating: editRating,
        actual_commission: editActualComm ? parseFloat(editActualComm) : undefined,
        net_profit: editNetProfit ? parseFloat(editNetProfit) : undefined,
        strategy_id: editStrategyId ?? undefined,
        config_id: editConfigId ?? undefined,
      });
      setEditing(false);
      onUpdated();
    } catch { /* handled */ }
  };

  const handleDelete = async () => {
    if (!confirm("Delete this trade?")) return;
    await api.journalDelete(trade.id);
    onUpdated();
  };

  const handleToggleTest = async () => {
    await api.journalBulkMarkTest({ trade_ids: [trade.id], is_test: !trade.is_test });
    onUpdated();
  };

  // Commission comparison
  const feeExpected = trade.expected_commission || 0;
  const feeActual = trade.actual_commission;
  const feeDelta = feeActual != null ? feeActual - feeExpected : null;
  const feeDeltaPct = feeExpected > 0 && feeDelta != null ? (feeDelta / feeExpected) * 100 : null;
  const feeColor = feeDeltaPct == null ? "" : feeDeltaPct < 10 ? "text-ag-green" : feeDeltaPct < 25 ? "text-yellow-400" : "text-ag-red";

  // P&L comparison (expected vs actual)
  const pnlExpected = trade.expected_pnl;
  const pnlActual = trade.net_profit;
  const hasPnlComparison = pnlExpected != null && pnlActual != null && pnlExpected !== pnlActual;
  const pnlDelta = hasPnlComparison ? pnlActual! - pnlExpected! : null;
  const pnlDeltaPct = hasPnlComparison && pnlExpected !== 0 ? ((pnlDelta!) / Math.abs(pnlExpected!)) * 100 : null;
  const pnlDeltaColor = pnlDelta == null ? "" : pnlDelta >= 0 ? "text-ag-green" : "text-ag-red";


  return (
    <div className="bg-ag-card border border-ag-border rounded-lg">
      {/* Header row */}
      <button onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-ag-card-hover transition-colors rounded-lg">
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${sideBg} ${sideColor}`}>
          {trade.side}
        </span>
        <span className="font-medium text-sm">
          {trade.symbol.replace("USDT", "")}
        </span>
        <span className="text-xs text-ag-text-muted">@ {trade.entry_price}</span>
        {trade.strategy_name && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-ag-accent/15 text-ag-accent">
            {trade.strategy_name}{trade.config_name ? ` / ${trade.config_name}` : ""}
          </span>
        )}
        <span className={`text-xs px-1.5 py-0.5 rounded ${isOpen ? "bg-yellow-500/15 text-yellow-400" : "bg-ag-border text-ag-text-muted"}`}>
          {isOpen ? "Open" : "Closed"}
        </span>
        {trade.is_test && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500/15 text-orange-400 font-medium">
            Testing
          </span>
        )}
        {!isOpen && trade.net_profit != null && (
          <span className={`ml-auto text-sm font-medium ${trade.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
            ${trade.net_profit.toFixed(2)}
          </span>
        )}
        {isOpen && trade.risk_amount != null && (
          <span className="ml-auto text-xs text-ag-text-muted">
            Risk: ${trade.risk_amount}
          </span>
        )}
        <span className="text-ag-text-muted text-xs">{expanded ? "▲" : "▼"}</span>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-ag-border pt-3">
          {!editing ? (
            <>
              {/* Trade details grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                <div>
                  <span className="text-ag-text-muted">Entry Time</span>
                  <p>{trade.entry_time ? new Date(trade.entry_time).toLocaleString("en-US", {
                    timeZone: "America/New_York",
                    month: "short", day: "numeric", year: "2-digit",
                    hour: "2-digit", minute: "2-digit",
                  }) : "—"}</p>
                </div>
                <div>
                  <span className="text-ag-text-muted">Quantity</span>
                  <p>{trade.quantity}</p>
                </div>
                {trade.risk_amount != null && (
                  <div>
                    <span className="text-ag-text-muted">$ Risk</span>
                    <p>${trade.risk_amount}</p>
                  </div>
                )}
                {trade.sl_price != null && (
                  <div>
                    <span className="text-ag-text-muted">Stop Loss</span>
                    <p className="text-ag-red">{trade.sl_price}</p>
                  </div>
                )}
                {trade.tp_price != null && (
                  <div>
                    <span className="text-ag-text-muted">Take Profit 1</span>
                    <p className="text-ag-green">{trade.tp_price}</p>
                  </div>
                )}
                {trade.tp2_price != null && (
                  <div>
                    <span className="text-ag-text-muted">Take Profit 2</span>
                    <p className="text-ag-green">{trade.tp2_price}</p>
                  </div>
                )}
                {!isOpen && (
                  <>
                    <div>
                      <span className="text-ag-text-muted">Exit Price</span>
                      <p>{trade.exit_price ?? "—"}</p>
                    </div>
                    <div>
                      <span className="text-ag-text-muted">Exit Reason</span>
                      <p>{trade.exit_reason ?? "—"}</p>
                    </div>
                    <div>
                      <span className="text-ag-text-muted">P&L %</span>
                      <p className={trade.net_profit_pct != null && trade.net_profit_pct >= 0 ? "text-ag-green" : "text-ag-red"}>
                        {trade.net_profit_pct != null ? `${trade.net_profit_pct.toFixed(2)}%` : "—"}
                      </p>
                    </div>
                  </>
                )}
                <div>
                  <span className="text-ag-text-muted">Source</span>
                  <p className="capitalize">{trade.source}</p>
                </div>
              </div>


              {/* Expected vs Actual comparison (from strategy) */}
              {(trade.expected_entry != null || trade.expected_sl != null || trade.expected_tp1 != null) && (
                <div className="text-xs p-2 bg-ag-dark/50 rounded border border-ag-border/50 space-y-1">
                  <p className="text-ag-text-muted font-medium">Expected vs Actual</p>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {trade.expected_entry != null && (
                      <div>
                        <span className="text-ag-text-muted">Entry: </span>
                        <span className="text-ag-text-muted">{trade.expected_entry.toFixed(4)}</span>
                        <span className="mx-1">→</span>
                        <span>{trade.entry_price.toFixed(4)}</span>
                      </div>
                    )}
                    {trade.expected_sl != null && (
                      <div>
                        <span className="text-ag-text-muted">SL: </span>
                        <span className="text-ag-red">{trade.expected_sl.toFixed(4)}</span>
                        {trade.sl_price != null && (
                          <>
                            <span className="mx-1">→</span>
                            <span className="text-ag-red">{trade.sl_price.toFixed(4)}</span>
                          </>
                        )}
                      </div>
                    )}
                    {trade.expected_tp1 != null && (
                      <div>
                        <span className="text-ag-text-muted">TP1: </span>
                        <span className="text-ag-green">{trade.expected_tp1.toFixed(4)}</span>
                        {trade.tp_price != null && (
                          <>
                            <span className="mx-1">→</span>
                            <span className="text-ag-green">{trade.tp_price.toFixed(4)}</span>
                          </>
                        )}
                      </div>
                    )}
                    {trade.expected_exit != null && (
                      <div>
                        <span className="text-ag-text-muted">Exit: </span>
                        <span>{trade.expected_exit.toFixed(4)}</span>
                        {trade.exit_price != null && (
                          <>
                            <span className="mx-1">→</span>
                            <span>{trade.exit_price.toFixed(4)}</span>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Commission comparison */}
              <div className="flex items-center gap-4 text-xs p-2 bg-ag-dark/50 rounded">
                <div>
                  <span className="text-ag-text-muted">Expected Fee:</span>
                  <span className="ml-1">${feeExpected.toFixed(4)}</span>
                </div>
                <div>
                  <span className="text-ag-text-muted">Actual Fee:</span>
                  <span className="ml-1">{feeActual != null ? `$${feeActual.toFixed(4)}` : "—"}</span>
                </div>
                {feeDelta != null && (
                  <div>
                    <span className="text-ag-text-muted">Delta:</span>
                    <span className={`ml-1 ${feeColor}`}>
                      {feeDelta >= 0 ? "+" : ""}${feeDelta.toFixed(4)}
                      {feeDeltaPct != null && ` (${feeDeltaPct >= 0 ? "+" : ""}${feeDeltaPct.toFixed(0)}%)`}
                    </span>
                  </div>
                )}
              </div>

              {/* P&L comparison (expected vs actual) */}
              {hasPnlComparison && (
                <div className="flex items-center gap-4 text-xs p-2 bg-ag-dark/50 rounded border border-ag-border/30">
                  <div>
                    <span className="text-ag-text-muted">Expected P&L:</span>
                    <span className={`ml-1 ${pnlExpected! >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                      ${pnlExpected!.toFixed(2)}
                    </span>
                  </div>
                  <div>
                    <span className="text-ag-text-muted">Actual P&L:</span>
                    <span className={`ml-1 font-medium ${pnlActual! >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                      ${pnlActual!.toFixed(2)}
                    </span>
                  </div>
                  {pnlDelta != null && (
                    <div>
                      <span className="text-ag-text-muted">Delta:</span>
                      <span className={`ml-1 font-medium ${pnlDeltaColor}`}>
                        {pnlDelta >= 0 ? "+" : ""}${pnlDelta.toFixed(2)}
                        {pnlDeltaPct != null && ` (${pnlDeltaPct >= 0 ? "+" : ""}${pnlDeltaPct.toFixed(1)}%)`}
                      </span>
                    </div>
                  )}
                </div>
              )}

              {/* Notes + Rating */}
              {trade.notes && (
                <div className="text-xs p-2 bg-ag-dark/50 rounded">
                  <span className="text-ag-text-muted">Notes: </span>{trade.notes}
                </div>
              )}
              {trade.rating > 0 && (
                <div className="text-xs">
                  {"★".repeat(trade.rating)}{"☆".repeat(5 - trade.rating)}
                </div>
              )}

              {/* Close trade form */}
              {isOpen && (
                <div className="space-y-2 p-3 bg-ag-dark/50 rounded border border-ag-border/50">
                  <p className="text-xs font-medium">Close Trade</p>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div>
                      <label className="block text-[10px] text-ag-text-muted mb-0.5">Exit Price</label>
                      <input type="number" step="any" value={exitPrice} onChange={(e) => setExitPrice(e.target.value)}
                        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                    </div>
                    <div>
                      <label className="block text-[10px] text-ag-text-muted mb-0.5">Reason</label>
                      <select value={exitReason} onChange={(e) => setExitReason(e.target.value)}
                        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
                        {EXIT_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[10px] text-ag-text-muted mb-0.5">Actual Fee</label>
                      <input type="number" step="any" value={actualComm} onChange={(e) => setActualComm(e.target.value)}
                        placeholder="Optional"
                        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                    </div>
                    <div>
                      <label className="block text-[10px] text-ag-text-muted mb-0.5">Actual P&L ($)</label>
                      <input type="number" step="any" value={actualPnl} onChange={(e) => setActualPnl(e.target.value)}
                        placeholder="Auto if empty"
                        className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                    </div>
                  </div>
                  <button onClick={handleClose} disabled={closing || !exitPrice}
                    className="px-3 py-1 bg-ag-accent text-white rounded text-xs disabled:opacity-50">
                    {closing ? "Closing..." : "Close Trade"}
                  </button>
                </div>
              )}
            </>
          ) : (
            /* Full edit mode — all fields editable */
            <div className="space-y-3 p-3 bg-ag-dark/50 rounded border border-ag-border/50">
              <p className="text-xs font-medium">Edit Trade</p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">Symbol</label>
                  <input value={editSymbol} onChange={(e) => setEditSymbol(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">Side</label>
                  <select value={editSide} onChange={(e) => setEditSide(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
                    <option value="Long">Long</option>
                    <option value="Short">Short</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">Entry Price</label>
                  <input type="number" step="any" value={editEntry} onChange={(e) => setEditEntry(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">Quantity</label>
                  <input type="number" step="any" value={editQty} onChange={(e) => setEditQty(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">$ Risk</label>
                  <input type="number" step="any" value={editRisk} onChange={(e) => setEditRisk(e.target.value)}
                    placeholder="Optional"
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">Entry Time</label>
                  <input type="datetime-local" value={editTime} onChange={(e) => setEditTime(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">Stop Loss</label>
                  <input type="number" step="any" value={editSl} onChange={(e) => setEditSl(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">TP 1</label>
                  <input type="number" step="any" value={editTp} onChange={(e) => setEditTp(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">TP 2</label>
                  <input type="number" step="any" value={editTp2} onChange={(e) => setEditTp2(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">Strategy</label>
                  <select value={editStrategyId ?? ""} onChange={(e) => { setEditStrategyId(Number(e.target.value) || null); setEditConfigId(null); }}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
                    <option value="">None</option>
                    {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </div>
                {editStrategyId && editConfigs.length > 0 && (
                  <div>
                    <label className="block text-[10px] text-ag-text-muted mb-0.5">Config</label>
                    <select value={editConfigId ?? ""} onChange={(e) => setEditConfigId(Number(e.target.value) || null)}
                      className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs">
                      <option value="">Default</option>
                      {editConfigs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                    </select>
                  </div>
                )}
                <div>
                  <label className="block text-[10px] text-ag-text-muted mb-0.5">Actual Commission</label>
                  <input type="number" step="any" value={editActualComm}
                    onChange={(e) => setEditActualComm(e.target.value)}
                    className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                </div>
                {!isOpen && (
                  <div>
                    <label className="block text-[10px] text-ag-text-muted mb-0.5">Actual P&L ($)</label>
                    <input type="number" step="any" value={editNetProfit}
                      onChange={(e) => setEditNetProfit(e.target.value)}
                      className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs" />
                  </div>
                )}
              </div>
              <div>
                <label className="block text-[10px] text-ag-text-muted mb-0.5">Notes</label>
                <textarea value={editNotes} onChange={(e) => setEditNotes(e.target.value)} rows={2}
                  className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs resize-none" />
              </div>
              <div>
                <label className="block text-[10px] text-ag-text-muted mb-0.5">Rating</label>
                <div className="flex gap-0.5">
                  {[1, 2, 3, 4, 5].map((n) => (
                    <button key={n} onClick={() => setEditRating(n)}
                      className={`text-sm ${n <= editRating ? "text-yellow-400" : "text-ag-text-muted"}`}>
                      ★
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex gap-2">
                <button onClick={handleSaveEdit}
                  className="px-3 py-1 bg-ag-green/20 text-ag-green rounded text-xs">Save</button>
                <button onClick={() => setEditing(false)}
                  className="px-3 py-1 bg-ag-border text-ag-text-muted rounded text-xs">Cancel</button>
              </div>
            </div>
          )}

          {/* Actions */}
          {!editing && (
            <div className="flex gap-2">
              <button onClick={() => setEditing(true)}
                className="px-3 py-1 bg-ag-border text-ag-text-muted rounded text-xs hover:text-ag-text">
                Edit
              </button>
              <button onClick={handleToggleTest}
                className={`px-3 py-1 rounded text-xs ${trade.is_test ? "bg-ag-green/10 text-ag-green hover:bg-ag-green/20" : "bg-orange-500/10 text-orange-400 hover:bg-orange-500/20"}`}>
                {trade.is_test ? "Unmark Testing" : "Mark as Testing"}
              </button>
              <button onClick={handleDelete}
                className="px-3 py-1 bg-ag-red/10 text-ag-red rounded text-xs hover:bg-ag-red/20">
                Delete
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
