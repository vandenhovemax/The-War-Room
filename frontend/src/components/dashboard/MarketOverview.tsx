"use client";

import { useState, useEffect } from "react";
import { Settings2, Plus, X } from "lucide-react";
import TVMiniChart from "./TVMiniChart";

const STORAGE_KEY = "warroom_market_overview";

const DEFAULT_SYMBOLS = [
  "TVC:GOLD",
  "SP:SPX",
  "NASDAQ:AAPL",
  "NASDAQ:NVDA",
  "NASDAQ:MSFT",
  "NASDAQ:AMZN",
  "NASDAQ:GOOGL",
  "NASDAQ:META",
];

const PRESETS: Record<string, string[]> = {
  "MAG7": ["NASDAQ:AAPL", "NASDAQ:MSFT", "NASDAQ:NVDA", "NASDAQ:GOOGL", "NASDAQ:AMZN", "NASDAQ:META", "NASDAQ:TSLA"],
  "Crypto": ["BYBIT:BTCUSDT", "BYBIT:ETHUSDT", "BYBIT:SOLUSDT", "BYBIT:BNBUSDT"],
  "Indices": ["SP:SPX", "TVC:DJI", "NASDAQ:NDX", "TVC:UKX"],
  "Commodities": ["TVC:GOLD", "TVC:SILVER", "NYMEX:CL1!", "NYMEX:NG1!"],
};

function loadSymbols(): string[] {
  if (typeof window === "undefined") return DEFAULT_SYMBOLS;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return DEFAULT_SYMBOLS;
}

function saveSymbols(symbols: string[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(symbols));
}

export default function MarketOverview() {
  const [symbols, setSymbols] = useState<string[]>(DEFAULT_SYMBOLS);
  const [editing, setEditing] = useState(false);
  const [newSymbol, setNewSymbol] = useState("");

  useEffect(() => {
    setSymbols(loadSymbols());
  }, []);

  const update = (next: string[]) => {
    setSymbols(next);
    saveSymbols(next);
  };

  const remove = (sym: string) => update(symbols.filter((s) => s !== sym));

  const add = (sym: string) => {
    const trimmed = sym.trim().toUpperCase();
    if (trimmed && !symbols.includes(trimmed)) {
      update([...symbols, trimmed]);
    }
    setNewSymbol("");
  };

  const applyPreset = (key: string) => {
    const preset = PRESETS[key] ?? [];
    const merged = [...new Set([...symbols, ...preset])];
    update(merged);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ag-text-muted uppercase tracking-wider">
          Market Overview
        </h3>
        <button
          onClick={() => setEditing(!editing)}
          className={`p-1.5 rounded transition-colors ${
            editing ? "bg-ag-accent text-ag-darker" : "text-ag-text-muted hover:text-ag-text"
          }`}
          title="Customize assets"
        >
          <Settings2 size={14} />
        </button>
      </div>

      {editing && (
        <div className="bg-ag-card border border-ag-border rounded-lg p-3 space-y-2">
          {/* Current symbols as removable chips */}
          <div className="flex flex-wrap gap-1.5">
            {symbols.map((s) => (
              <span key={s} className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded bg-ag-dark border border-ag-border text-ag-text">
                {s}
                <button onClick={() => remove(s)} className="hover:text-red-400 transition-colors">
                  <X size={10} />
                </button>
              </span>
            ))}
          </div>

          {/* Add new symbol */}
          <div className="flex gap-2">
            <input
              type="text"
              value={newSymbol}
              onChange={(e) => setNewSymbol(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") add(newSymbol); }}
              placeholder="NASDAQ:TSLA"
              className="flex-1 px-2 py-1 bg-ag-dark border border-ag-border rounded text-xs"
            />
            <button onClick={() => add(newSymbol)} disabled={!newSymbol.trim()}
              className="px-2 py-1 bg-ag-accent text-ag-darker rounded text-xs font-medium disabled:opacity-50">
              <Plus size={12} />
            </button>
          </div>

          {/* Preset groups */}
          <div className="flex gap-1.5 flex-wrap">
            <span className="text-[10px] text-ag-text-muted self-center">Presets:</span>
            {Object.keys(PRESETS).map((k) => (
              <button key={k} onClick={() => applyPreset(k)}
                className="px-2 py-0.5 text-[10px] rounded bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-accent hover:border-ag-accent transition-colors">
                + {k}
              </button>
            ))}
            <button onClick={() => update(DEFAULT_SYMBOLS)}
              className="px-2 py-0.5 text-[10px] rounded bg-ag-dark border border-ag-border text-ag-text-muted hover:text-yellow-400 hover:border-yellow-400 transition-colors">
              Reset
            </button>
          </div>
        </div>
      )}

      {/* Widget grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {symbols.map((sym) => (
          <TVMiniChart key={sym} symbol={sym} />
        ))}
      </div>
    </div>
  );
}
