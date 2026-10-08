"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { SynergyAuditResult } from "@/lib/api";

interface Props {
  portfolioId: number;
}

export default function SynergyAuditPanel({ portfolioId }: Props) {
  const [data, setData] = useState<SynergyAuditResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [frictionPct, setFrictionPct] = useState(0.20);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.portfolioSynergyAudit(portfolioId, frictionPct));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Audit failed");
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, [portfolioId]);

  if (loading) return <p className="text-xs text-ag-text-muted py-2">Running synergy audit...</p>;
  if (error) return <p className="text-xs text-ag-red py-2">{error}</p>;
  if (!data || data.trade_count === 0) {
    return <p className="text-xs text-ag-text-muted py-2">No trade data available for audit.</p>;
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h3 className="text-xs font-medium">Selection Engine Audit</h3>
        <span className="text-[10px] text-ag-text-muted">
          {data.trade_count} trades {data.oos_only ? "(OOS only)" : "(all)"}
        </span>
        <div className="flex items-center gap-1 ml-auto">
          <label className="text-[10px] text-ag-text-muted">Friction %:</label>
          <input type="number" value={frictionPct} min={0} max={5} step={0.01}
            onChange={(e) => setFrictionPct(Number(e.target.value))}
            className="w-16 bg-ag-dark border border-ag-border rounded px-1 py-0.5 text-[10px]" />
          <button onClick={load}
            className="px-2 py-0.5 bg-ag-accent/20 text-ag-accent rounded text-[10px] hover:bg-ag-accent/30">
            Refresh
          </button>
        </div>
      </div>

      {/* Marginal Contribution */}
      <MarginalContribution rows={data.marginal} />

      {/* Diversification Multiplier */}
      <DiversificationMultiplier data={data.diversification} />

      {/* Breakeven Friction */}
      <BreakevenFriction data={data.breakeven} currentFriction={frictionPct} />

      {/* Conflicts */}
      <ConflictDetector data={data.conflicts} />
    </div>
  );
}

function MarginalContribution({ rows }: { rows: SynergyAuditResult["marginal"] }) {
  if (rows.length === 0) return null;

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        Marginal Contribution (with friction)
      </h4>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-ag-border text-ag-text-muted">
              <th className="text-left py-1.5 px-2">Strategy</th>
              <th className="text-right py-1.5 px-2">Net Profit</th>
              <th className="text-right py-1.5 px-2">PF</th>
              <th className="text-right py-1.5 px-2">Sharpe</th>
              <th className="text-right py-1.5 px-2">Max DD</th>
              <th className="text-right py-1.5 px-2">SQN</th>
              <th className="text-right py-1.5 px-2">Trades</th>
              <th className="text-right py-1.5 px-2">Friction</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isCombined = r.Strategy === "COMBINED";
              return (
                <tr key={r.Strategy}
                  className={`border-b border-ag-border/20 ${isCombined ? "font-medium bg-ag-accent/5" : "hover:bg-ag-card-hover"}`}>
                  <td className="py-1.5 px-2">{r.Strategy}</td>
                  <td className={`text-right py-1.5 px-2 font-mono ${r["Net Profit"] >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                    ${r["Net Profit"].toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </td>
                  <td className="text-right py-1.5 px-2 font-mono">{r["Profit Factor"].toFixed(2)}</td>
                  <td className="text-right py-1.5 px-2 font-mono">{r["Sharpe Ratio"].toFixed(2)}</td>
                  <td className="text-right py-1.5 px-2 font-mono text-ag-red">
                    ${r["Max Drawdown ($)"].toFixed(0)}
                  </td>
                  <td className="text-right py-1.5 px-2 font-mono">{r.SQN.toFixed(2)}</td>
                  <td className="text-right py-1.5 px-2">{r.Trades}</td>
                  <td className="text-right py-1.5 px-2 font-mono text-ag-text-muted">
                    ${r["Friction Cost"].toFixed(0)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DiversificationMultiplier({ data }: { data: SynergyAuditResult["diversification"] }) {
  const dmColor = data.DM > 0.15 ? "text-ag-green" : data.DM > 0 ? "text-yellow-400" : "text-ag-red";

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        Diversification Multiplier
      </h4>
      <div className="bg-ag-dark/50 rounded p-3">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div>
            <p className="text-[10px] text-ag-text-muted">DM</p>
            <p className={`text-sm font-mono font-bold ${dmColor}`}>
              {(data.DM * 100).toFixed(1)}%
            </p>
          </div>
          <div>
            <p className="text-[10px] text-ag-text-muted">Synergistic</p>
            <p className={`text-sm font-medium ${data.synergistic ? "text-ag-green" : "text-ag-red"}`}>
              {data.synergistic ? "Yes" : "No"}
            </p>
          </div>
          <div>
            <p className="text-[10px] text-ag-text-muted">Combined Std</p>
            <p className="text-sm font-mono">${data.combined_std.toFixed(2)}</p>
          </div>
          <div>
            <p className="text-[10px] text-ag-text-muted">Avg Individual Std</p>
            <p className="text-sm font-mono">${data.avg_individual_std.toFixed(2)}</p>
          </div>
        </div>
        {data.reason && (
          <p className="text-[10px] text-ag-text-muted mt-2">{data.reason}</p>
        )}
        {Object.keys(data.per_strategy_std).length > 1 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(data.per_strategy_std).map(([strat, std]) => (
              <span key={strat} className="text-[10px] text-ag-text-muted">
                {strat}: ${std.toFixed(2)}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function BreakevenFriction({ data, currentFriction }: {
  data: SynergyAuditResult["breakeven"]; currentFriction: number;
}) {
  const marginColor = data.margin_of_safety > 0.5 ? "text-ag-green"
    : data.margin_of_safety > 0 ? "text-yellow-400" : "text-ag-red";

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        Breakeven Friction Analysis
      </h4>
      <div className="bg-ag-dark/50 rounded p-3">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div>
            <p className="text-[10px] text-ag-text-muted">Breakeven Friction</p>
            <p className="text-sm font-mono font-bold">{data.breakeven_pct.toFixed(2)}%</p>
          </div>
          <div>
            <p className="text-[10px] text-ag-text-muted">Margin of Safety</p>
            <p className={`text-sm font-mono font-bold ${marginColor}`}>
              {data.margin_of_safety.toFixed(2)}%
            </p>
          </div>
          <div>
            <p className="text-[10px] text-ag-text-muted">PF at 0% Friction</p>
            <p className="text-sm font-mono">{data.pf_at_zero.toFixed(2)}</p>
          </div>
          <div>
            <p className="text-[10px] text-ag-text-muted">PF at {currentFriction}% Friction</p>
            <p className={`text-sm font-mono ${data.pf_at_current >= 1 ? "text-ag-green" : "text-ag-red"}`}>
              {data.pf_at_current.toFixed(2)}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConflictDetector({ data }: { data: SynergyAuditResult["conflicts"] }) {
  if (data.conflict_count === 0) {
    return (
      <div>
        <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
          Conflict Detection
        </h4>
        <p className="text-xs text-ag-green">No parasitic L/S conflicts detected.</p>
      </div>
    );
  }

  return (
    <div>
      <h4 className="text-[10px] text-ag-text-muted uppercase tracking-wider mb-1.5">
        Conflict Detection
      </h4>
      <div className="bg-ag-dark/50 rounded p-3 space-y-2">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          <div>
            <p className="text-[10px] text-ag-text-muted">Conflicts</p>
            <p className="text-sm font-mono text-ag-red">{data.conflict_count}</p>
          </div>
          <div>
            <p className="text-[10px] text-ag-text-muted">Trades Affected</p>
            <p className="text-sm font-mono">{data.conflict_trade_count}</p>
          </div>
          <div>
            <p className="text-[10px] text-ag-text-muted">Wasted Commission</p>
            <p className="text-sm font-mono text-ag-red">
              ${data.wasted_commission_estimate.toFixed(2)}
            </p>
          </div>
          <div>
            <p className="text-[10px] text-ag-text-muted">Net Offset PnL</p>
            <p className={`text-sm font-mono ${data.net_offset_pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
              ${data.net_offset_pnl.toFixed(2)}
            </p>
          </div>
        </div>

        {data.conflicts.length > 0 && (
          <details className="text-xs">
            <summary className="cursor-pointer text-ag-text-muted hover:text-ag-text">
              Conflict details ({data.conflicts.length})
            </summary>
            <div className="mt-1 overflow-x-auto">
              <table className="w-full text-[10px]">
                <thead>
                  <tr className="border-b border-ag-border text-ag-text-muted">
                    <th className="text-left py-1 px-1">Symbol</th>
                    <th className="text-left py-1 px-1">Long Strat</th>
                    <th className="text-left py-1 px-1">Short Strat</th>
                    <th className="text-right py-1 px-1">Hours</th>
                    <th className="text-right py-1 px-1">Net PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {data.conflicts.slice(0, 20).map((c, i) => (
                    <tr key={i} className="border-b border-ag-border/20">
                      <td className="py-1 px-1">{c["Base Symbol"]}</td>
                      <td className="py-1 px-1">{c["Long Strategy"]}</td>
                      <td className="py-1 px-1">{c["Short Strategy"]}</td>
                      <td className="text-right py-1 px-1">{c["Overlap Hours"]}h</td>
                      <td className={`text-right py-1 px-1 font-mono ${c["Net Offset PnL"] >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                        ${c["Net Offset PnL"].toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.conflicts.length > 20 && (
                <p className="text-[10px] text-ag-text-muted mt-1">
                  ... and {data.conflicts.length - 20} more
                </p>
              )}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}
