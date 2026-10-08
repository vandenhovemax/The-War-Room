"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { StrategyItem, StrategyConfigItem, LookupTradeEntry } from "@/lib/api";

interface TradeFormProps {
  onCreated: () => void;
  onCancel: () => void;
  initialDate?: string; // ISO date string (YYYY-MM-DD) to pre-fill entry time
}

const RISK_PRESETS = [25, 50, 75, 100, 150, 200, 250, 500];

export default function TradeForm({ onCreated, onCancel, initialDate }: TradeFormProps) {
  const [mode, setMode] = useState<"manual" | "strategy">("manual");

  // Trade fields
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState("Long");
  const [entryPrice, setEntryPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [riskAmount, setRiskAmount] = useState("");
  const [slPrice, setSlPrice] = useState("");
  const [tpPrice, setTpPrice] = useState("");
  const [tp2Price, setTp2Price] = useState("");
  // Pre-fill entry time with the selected date (noon) if provided
  const [entryTime, setEntryTime] = useState(
    initialDate ? `${initialDate}T12:00` : ""
  );
  const [commRate, setCommRate] = useState("0.055");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Strategy lookup fields
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [configs, setConfigs] = useState<StrategyConfigItem[]>([]);
  const [selStrategy, setSelStrategy] = useState<number | null>(null);
  const [selConfig, setSelConfig] = useState<number | null>(null);
  const [lookupSymbol, setLookupSymbol] = useState("");
  const [lookupTf, setLookupTf] = useState("30");
  const [lookupTrades, setLookupTrades] = useState<LookupTradeEntry[]>([]);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [selectedTrade, setSelectedTrade] = useState<LookupTradeEntry | null>(null);

  useEffect(() => {
    api.strategies().then(setStrategies).catch(() => {});
  }, []);

  useEffect(() => {
    if (selStrategy) {
      api.strategyConfigs(selStrategy).then(setConfigs).catch(() => {});
    } else {
      setConfigs([]);
    }
  }, [selStrategy]);

  const handleLookup = async () => {
    if (!selStrategy || !lookupSymbol.trim()) return;
    setLookupLoading(true);
    setError("");
    try {
      const result = await api.journalLookupTrades({
        strategy_id: selStrategy,
        config_id: selConfig ?? undefined,
        symbol: lookupSymbol.trim().toUpperCase(),
        timeframe: lookupTf,
        auto_download: true,
      });
      setLookupTrades(result.trades);
      if (result.trades.length === 0) {
        setError(`No trades found for ${lookupSymbol} with this strategy`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lookup failed");
    }
    setLookupLoading(false);
  };

  const handleSelectLookupTrade = (t: LookupTradeEntry) => {
    setSelectedTrade(t);
    setSymbol(lookupSymbol.trim().toUpperCase());
    setSide(t.side);
    setEntryPrice(String(t.entry_price));
    setQuantity(String(t.quantity));
    setEntryTime(t.entry_time?.split(".")[0] || "");
    if (t.sl_price) setSlPrice(String(t.sl_price));
    if (t.tp_price) setTpPrice(String(t.tp_price));
  };

  const handleSubmit = async () => {
    if (!symbol.trim() || !entryPrice || !quantity) {
      setError("Symbol, entry price, and quantity are required");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const ep = parseFloat(entryPrice);
      const qty = parseFloat(quantity);
      const rate = parseFloat(commRate) || 0.055;
      const autoExpectedComm = Math.abs(ep * qty) * (rate / 100) * 2;

      await api.journalCreate({
        symbol: symbol.trim().toUpperCase(),
        side,
        entry_price: ep,
        quantity: qty,
        risk_amount: riskAmount ? parseFloat(riskAmount) : undefined,
        source: mode === "strategy" ? "backtest" : "manual",
        strategy_id: selStrategy ?? undefined,
        config_id: selConfig ?? undefined,
        sl_price: slPrice ? parseFloat(slPrice) : undefined,
        tp_price: tpPrice ? parseFloat(tpPrice) : undefined,
        tp2_price: tp2Price ? parseFloat(tp2Price) : undefined,
        entry_time: entryTime || undefined,
        commission_rate: rate,
        // Save strategy values as expected (for comparison)
        expected_entry: selectedTrade ? selectedTrade.entry_price : undefined,
        expected_sl: selectedTrade?.sl_price ?? undefined,
        expected_tp1: selectedTrade?.tp_price ?? undefined,
        expected_exit: selectedTrade ? selectedTrade.exit_price : undefined,
        expected_commission: selectedTrade ? selectedTrade.commission : autoExpectedComm,
        notes,
      });
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create trade");
    }
    setSaving(false);
  };

  const expectedComm = entryPrice && quantity && commRate
    ? (Math.abs(parseFloat(entryPrice) * parseFloat(quantity)) * (parseFloat(commRate) / 100) * 2).toFixed(4)
    : "—";

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">New Trade</h3>
        <button onClick={onCancel} className="text-xs text-ag-text-muted hover:text-ag-text">Cancel</button>
      </div>

      {/* Mode toggle */}
      <div className="flex rounded-lg border border-ag-border overflow-hidden">
        <button onClick={() => setMode("manual")}
          className={`flex-1 px-3 py-1.5 text-xs font-medium transition-colors ${mode === "manual" ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"}`}>
          Manual Entry
        </button>
        <button onClick={() => setMode("strategy")}
          className={`flex-1 px-3 py-1.5 text-xs font-medium transition-colors ${mode === "strategy" ? "bg-ag-accent text-white" : "bg-ag-dark text-ag-text-muted hover:text-ag-text"}`}>
          From Strategy
        </button>
      </div>

      {/* Strategy lookup */}
      {mode === "strategy" && (
        <div className="space-y-3 p-3 bg-ag-dark/50 rounded-lg border border-ag-border/50">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Strategy</label>
              <select value={selStrategy ?? ""} onChange={(e) => setSelStrategy(Number(e.target.value) || null)}
                className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1.5 text-xs">
                <option value="">Select...</option>
                {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Config</label>
              <select value={selConfig ?? ""} onChange={(e) => setSelConfig(Number(e.target.value) || null)}
                className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1.5 text-xs">
                <option value="">Default</option>
                {configs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-ag-text-muted mb-1">Symbol</label>
              <input value={lookupSymbol} onChange={(e) => setLookupSymbol(e.target.value)}
                placeholder="BTCUSDT"
                className="w-full bg-ag-dark border border-ag-border rounded px-2 py-1.5 text-xs" />
            </div>
            <div className="flex items-end">
              <button onClick={handleLookup} disabled={lookupLoading || !selStrategy || !lookupSymbol.trim()}
                className="w-full px-3 py-1.5 bg-ag-accent text-white rounded text-xs font-medium disabled:opacity-50">
                {lookupLoading ? "Finding..." : "Find Trades"}
              </button>
            </div>
          </div>

          {/* Lookup results */}
          {lookupTrades.length > 0 && (
            <div className="max-h-48 overflow-y-auto space-y-1">
              <p className="text-xs text-ag-text-muted">{lookupTrades.length} recent trades — click to select:</p>
              {lookupTrades.map((t, i) => (
                <button key={i} onClick={() => handleSelectLookupTrade(t)}
                  className={`w-full flex items-center justify-between px-3 py-1.5 rounded text-xs transition-colors ${
                    selectedTrade === t ? "bg-ag-accent/20 border border-ag-accent" : "bg-ag-dark hover:bg-ag-card-hover border border-transparent"
                  }`}>
                  <span className={t.side === "Long" ? "text-ag-green" : "text-ag-red"}>{t.side}</span>
                  <span>{t.entry_time?.split("T")[0]}</span>
                  <span>@ {t.entry_price.toFixed(4)}</span>
                  <span className={t.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}>
                    ${t.net_profit.toFixed(2)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Trade fields — Row 1 */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Symbol</label>
          <input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="BTCUSDT"
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Side</label>
          <div className="flex rounded-lg border border-ag-border overflow-hidden">
            <button onClick={() => setSide("Long")}
              className={`flex-1 px-3 py-1.5 text-xs font-medium ${side === "Long" ? "bg-ag-green/20 text-ag-green" : "bg-ag-dark text-ag-text-muted"}`}>
              Long
            </button>
            <button onClick={() => setSide("Short")}
              className={`flex-1 px-3 py-1.5 text-xs font-medium ${side === "Short" ? "bg-ag-red/20 text-ag-red" : "bg-ag-dark text-ag-text-muted"}`}>
              Short
            </button>
          </div>
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Entry Price</label>
          <input type="number" step="any" value={entryPrice} onChange={(e) => setEntryPrice(e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
        </div>
      </div>

      {/* Trade fields — Row 2: Risk + Qty + Outcome */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">$ Risk Amount</label>
          <select value={riskAmount} onChange={(e) => setRiskAmount(e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
            <option value="">Custom...</option>
            {RISK_PRESETS.map((r) => <option key={r} value={String(r)}>${r}</option>)}
          </select>
          {riskAmount === "" && (
            <input type="number" step="any" value={riskAmount} onChange={(e) => setRiskAmount(e.target.value)}
              placeholder="Enter amount"
              className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1 text-xs mt-1" />
          )}
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Quantity</label>
          <input type="number" step="any" value={quantity} onChange={(e) => setQuantity(e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
        </div>
        {mode === "manual" && (
          <div>
            <label className="block text-xs text-ag-text-muted mb-1">Strategy</label>
            <select value={selStrategy ?? ""} onChange={(e) => { setSelStrategy(Number(e.target.value) || null); setSelConfig(null); }}
              className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
              <option value="">None</option>
              {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
        )}
      </div>

      {/* Config dropdown — visible in manual mode when strategy selected */}
      {mode === "manual" && selStrategy && configs.length > 0 && (
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Config (Daughter)</label>
          <select value={selConfig ?? ""} onChange={(e) => setSelConfig(Number(e.target.value) || null)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm">
            <option value="">Default</option>
            {configs.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
      )}

      {/* Trade fields — Row 3: SL + TP1 + TP2 */}
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Stop Loss</label>
          <input type="number" step="any" value={slPrice} onChange={(e) => setSlPrice(e.target.value)}
            placeholder="Optional"
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Take Profit 1</label>
          <input type="number" step="any" value={tpPrice} onChange={(e) => setTpPrice(e.target.value)}
            placeholder="Optional"
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Take Profit 2</label>
          <input type="number" step="any" value={tp2Price} onChange={(e) => setTp2Price(e.target.value)}
            placeholder="Optional"
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
        </div>
      </div>

      {/* Trade fields — Row 4: Time + Commission */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Entry Time</label>
          <input type="datetime-local" value={entryTime} onChange={(e) => setEntryTime(e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Commission Rate %</label>
          <input type="number" step="0.001" value={commRate} onChange={(e) => setCommRate(e.target.value)}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-ag-text-muted mb-1">Expected Fee</label>
          <p className="text-sm text-ag-text-muted py-1.5">${expectedComm}</p>
        </div>
      </div>

      <div>
        <label className="block text-xs text-ag-text-muted mb-1">Notes</label>
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2}
          placeholder="Trade rationale, observations..."
          className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1.5 text-sm resize-none" />
      </div>

      {/* Strategy expected values preview */}
      {selectedTrade && (
        <div className="text-xs p-2 bg-ag-dark/50 rounded border border-ag-border/50 space-y-1">
          <p className="text-ag-text-muted font-medium">Strategy Expected Values (saved for comparison):</p>
          <div className="flex gap-4 flex-wrap">
            <span>Entry: {selectedTrade.entry_price.toFixed(4)}</span>
            {selectedTrade.sl_price && <span className="text-ag-red">SL: {selectedTrade.sl_price.toFixed(4)}</span>}
            {selectedTrade.tp_price && <span className="text-ag-green">TP: {selectedTrade.tp_price.toFixed(4)}</span>}
            <span>Exit: {selectedTrade.exit_price.toFixed(4)}</span>
            <span>Fee: ${selectedTrade.commission.toFixed(4)}</span>
            <span className={selectedTrade.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}>
              P&L: ${selectedTrade.net_profit.toFixed(2)}
            </span>
          </div>
        </div>
      )}

      {error && <p className="text-xs text-ag-red">{error}</p>}

      <button onClick={handleSubmit} disabled={saving}
        className="w-full px-4 py-2 bg-ag-accent text-white rounded-lg text-sm font-medium hover:brightness-110 disabled:opacity-50">
        {saving ? "Creating..." : "Add Trade"}
      </button>
    </div>
  );
}
