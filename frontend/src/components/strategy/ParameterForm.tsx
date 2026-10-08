"use client";
import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { ParameterDefinition } from "@/lib/api";
import { groupParameters } from "./parameterGroups";

type ParamValues = Record<string, number | boolean | string>;

// Visual-only params that only affect TradingView chart display, not backtesting
const HIDDEN_PATTERNS = [
  /^Fib Level \d/i,
  /color/i,
  /^plot/i,
  /^Volume Confluence Highlight$/,
];

// Backtester-level params — hidden from strategy UI because they're controlled
// by the engine's Position Sizing section (or will be). Config values still
// pass through to the strategy; only direct UI inputs get stripped by backend.
const BACKTESTER_LEVEL_PATTERNS = [
  /^Trade Direction$/i,
  /^Risk Mode$/i,
  /^Risk Amount/i,
  /^Risk %/i,
  /^Risk Per Trade$/i,
  /^Max Concurrent Trades$/i,
  /^Martingale/i,
  /^Anti.?Martingale/i,
  /^ATR Volatility/i,
  /^Volatility ATR/i,
  /^Volatility Filter$/i,
  /^Volatility Multiplier$/i,
  /^Vol Filter$/i,
];

function isVisualParam(name: string): boolean {
  return HIDDEN_PATTERNS.some((p) => p.test(name));
}

function isBacktesterParam(name: string): boolean {
  return BACKTESTER_LEVEL_PATTERNS.some((p) => p.test(name));
}

interface ParameterFormProps {
  definitions: ParameterDefinition[];
  values: ParamValues;
  onChange: (values: ParamValues) => void;
  disabled?: boolean;
}

export default function ParameterForm({ definitions, values, onChange, disabled }: ParameterFormProps) {
  const [showHidden, setShowHidden] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const update = (name: string, val: number | boolean | string) => {
    onChange({ ...values, [name]: val });
  };

  const toggleGroup = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const visible = definitions.filter((d) => !isVisualParam(d.name) && !isBacktesterParam(d.name));
  const hidden = definitions.filter((d) => isVisualParam(d.name));
  const backtesterHidden = definitions.filter((d) => isBacktesterParam(d.name));
  const groups = groupParameters(visible);
  const showHeaders = groups.length > 1;

  if (definitions.length === 0) {
    return <p className="text-xs text-ag-text-muted">No configurable parameters.</p>;
  }

  const renderParam = (def: ParameterDefinition) => {
    const val = values[def.name] ?? def.default;
    return (
      <div key={def.name}>
        <label className="block text-xs text-ag-text-muted mb-1 truncate" title={def.name}>
          {def.name}
        </label>
        {def.type === "bool" ? (
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={val === true || val === "true"}
              onChange={(e) => update(def.name, e.target.checked)}
              disabled={disabled}
              className="accent-ag-accent w-4 h-4"
            />
            <span className="text-sm">{val ? "On" : "Off"}</span>
          </label>
        ) : def.type === "string" && def.options ? (
          <select
            value={String(val)}
            onChange={(e) => update(def.name, e.target.value)}
            disabled={disabled}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent outline-none disabled:opacity-50"
          >
            {def.options.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        ) : def.type === "string" ? (
          <input
            type="text"
            value={String(val)}
            onChange={(e) => update(def.name, e.target.value)}
            disabled={disabled}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent outline-none disabled:opacity-50"
          />
        ) : (
          <input
            type="number"
            value={Number(val)}
            min={def.min}
            max={def.max}
            step={def.step ?? (def.type === "int" ? 1 : 0.1)}
            onChange={(e) => {
              const n = def.type === "int" ? parseInt(e.target.value) : parseFloat(e.target.value);
              if (!isNaN(n)) update(def.name, n);
            }}
            disabled={disabled}
            className="w-full bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm focus:border-ag-accent outline-none disabled:opacity-50"
          />
        )}
        {(def.min !== undefined || def.max !== undefined) && (
          <p className="text-[10px] text-ag-text-muted mt-0.5">
            {def.min !== undefined && `Min: ${def.min}`}
            {def.min !== undefined && def.max !== undefined && " · "}
            {def.max !== undefined && `Max: ${def.max}`}
          </p>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {groups.map(({ id, label, params }) => {
        const isCollapsed = collapsed.has(id);
        return (
          <div key={id}>
            {showHeaders && (
              <button
                type="button"
                onClick={() => toggleGroup(id)}
                className="flex items-center gap-1.5 text-xs font-semibold text-ag-text-muted mb-2 hover:text-ag-text transition-colors"
              >
                <ChevronRight size={12} className={`transition-transform ${isCollapsed ? "" : "rotate-90"}`} />
                {label} ({params.length})
              </button>
            )}
            {!isCollapsed && (
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
                {params.map(renderParam)}
              </div>
            )}
          </div>
        );
      })}

      {backtesterHidden.length > 0 && (
        <p className="text-[10px] text-ag-text-muted italic">
          {backtesterHidden.length} params controlled by engine (direction, risk, martingale, vol filter)
        </p>
      )}

      {hidden.length > 0 && (
        <button
          type="button"
          onClick={() => setShowHidden(!showHidden)}
          className="text-xs text-ag-text-muted hover:text-ag-text transition-colors"
        >
          {showHidden ? "Hide" : "Show"} {hidden.length} visual-only params
        </button>
      )}
      {showHidden && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 opacity-50">
          {hidden.map(renderParam)}
        </div>
      )}
    </div>
  );
}
