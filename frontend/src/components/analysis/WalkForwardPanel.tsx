"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { WFOTimelineResponse, PortfolioDetail } from "@/lib/api";

/** Real walk-forward reconstruction for saved WFO/hybrid portfolios.
 *
 * The existing `PortfolioFullMetrics` panel loads trades for the FINAL basket
 * across the entire period — a fixed-basket hypothetical, NOT the real
 * rotation. This panel calls /wfo-timeline which stitches per-window OOS
 * (time-based) or per-rotation OOS (event-based) so the user sees truth.
 */
export default function WalkForwardPanel({ detail }: { detail: PortfolioDetail }) {
  const [data, setData] = useState<WFOTimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const isWfo = detail.portfolio_source === "wfo" || detail.portfolio_source === "wfas_phase2";

  useEffect(() => {
    if (!isWfo) return;
    setLoading(true);
    setError(null);
    api.portfolioWfoTimeline(detail.id)
      .then(setData)
      .catch((e) => { setError(String(e?.message || e)); setData(null); })
      .finally(() => setLoading(false));
  }, [detail.id, isWfo]);

  if (!isWfo) return null;
  if (loading) {
    return (
      <div className="border border-ag-border rounded-lg p-3 bg-ag-dark/30">
        <div className="text-xs text-ag-text-muted">Loading walk-forward timeline…</div>
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="border border-red-500/30 rounded-lg p-3 bg-red-500/10">
        <div className="text-xs font-semibold text-red-300 mb-1">Walk-Forward timeline unavailable</div>
        <div className="text-[11px] text-red-300/80">
          {error ?? "No data returned from /wfo-timeline endpoint."}
        </div>
      </div>
    );
  }

  if (!data.has_walkforward) {
    return (
      <div className="border border-yellow-500/30 rounded-lg p-3 bg-yellow-500/10">
        <div className="text-xs font-semibold text-yellow-300 mb-1">Walk-Forward data not saved</div>
        <div className="text-[11px] text-yellow-300/80">
          This portfolio was saved before walk-forward capture was added. The KPIs
          below show the final basket applied to the whole period — not the real
          rotation. Re-save from the WFO panel to enable real walk-forward view.
        </div>
      </div>
    );
  }

  const m = data.metrics ?? {};
  const fmtMoney = (v: unknown) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    const abs = Math.abs(n);
    const str = abs >= 1000 ? abs.toLocaleString("en-US", { maximumFractionDigits: 0 }) : abs.toFixed(2);
    return `${n < 0 ? "-" : ""}$${str}`;
  };
  // Backend returns win_rate already as a percent (e.g., 45.5), not a fraction.
  const fmtPct = (v: unknown) => {
    const n = Number(v);
    return Number.isFinite(n) ? `${n.toFixed(1)}%` : "—";
  };
  const fmtNum = (v: unknown, digits = 2) => {
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(digits) : "—";
  };

  const researchSegs = data.segments.filter((s) => s.phase !== "live");
  const liveSegs = data.segments.filter((s) => s.phase === "live");
  const researchPnl = researchSegs.reduce((a, s) => a + (s.realized_pnl || 0), 0);
  const livePnl = liveSegs.reduce((a, s) => a + (s.realized_pnl || 0), 0);

  return (
    <div className="border border-ag-accent/30 rounded-lg p-3 bg-ag-accent/5">
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="text-sm font-semibold text-ag-accent">Walk-Forward Truth</div>
          <div className="text-[11px] text-ag-text-muted">
            {data.mode === "time"
              ? "Per-window OOS stitched — each window's actual basket over its own OOS dates."
              : "Per-rotation OOS stitched — basket active between each rebalance event, plus live rotations."}
          </div>
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-[11px] px-2 py-1 rounded bg-ag-accent/15 hover:bg-ag-accent/25 text-ag-accent"
        >
          {expanded ? "Hide" : "Show"} breakdown
        </button>
      </div>

      {/* KPI summary — OOS-only from walk-forward stitching */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs mb-2">
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Net Profit (stitched)</div>
          <div className={`font-mono font-semibold ${Number(m.net_profit) >= 0 ? "text-ag-green" : "text-ag-red"}`}>
            {fmtMoney(m.net_profit)}
          </div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Profit Factor</div>
          <div className="font-mono">{fmtNum(m.profit_factor)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Win Rate</div>
          <div className="font-mono">{fmtPct(m.win_rate)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Trades</div>
          <div className="font-mono">{fmtNum(m.total_trades, 0)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Max DD</div>
          <div className="font-mono text-ag-red">{fmtMoney(m.max_drawdown)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Gross Profit</div>
          <div className="font-mono text-ag-green">{fmtMoney(m.gross_profit)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Gross Loss</div>
          <div className="font-mono text-ag-red">{fmtMoney(m.gross_loss)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Avg Trade</div>
          <div className={`font-mono ${Number(m.avg_trade) >= 0 ? "text-ag-green" : "text-ag-red"}`}>
            {fmtMoney(m.avg_trade)}
          </div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Sortino</div>
          <div className="font-mono">{fmtNum(m.sortino)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Calmar</div>
          <div className="font-mono">{fmtNum(m.calmar_ratio)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Recovery</div>
          <div className="font-mono">{fmtNum(m.recovery_factor)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Stability (R²)</div>
          <div className="font-mono">{fmtNum(m.stability)}</div>
        </div>
        <div className="bg-ag-dark/40 rounded px-2 py-1">
          <div className="text-ag-text-muted text-[10px]">Assets</div>
          <div className="font-mono">{fmtNum(m.n_assets, 0)}</div>
        </div>
      </div>

      {/* Research vs Live split (event-based only) */}
      {data.mode === "event" && (researchSegs.length > 0 || liveSegs.length > 0) && (
        <div className="grid grid-cols-2 gap-2 text-xs mb-2">
          <div className="bg-ag-dark/40 rounded px-2 py-1">
            <div className="text-ag-text-muted text-[10px]">Research OOS PnL</div>
            <div className={`font-mono ${researchPnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
              {fmtMoney(researchPnl)} <span className="text-ag-text-muted">({researchSegs.length} segs)</span>
            </div>
          </div>
          <div className="bg-ag-dark/40 rounded px-2 py-1">
            <div className="text-ag-text-muted text-[10px]">Live PnL (since {data.optimization_end_date ?? "cutoff"})</div>
            <div className={`font-mono ${livePnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
              {fmtMoney(livePnl)} <span className="text-ag-text-muted">({liveSegs.length} segs)</span>
            </div>
          </div>
        </div>
      )}

      {/* Breakdown table */}
      {expanded && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead className="text-ag-text-muted">
              <tr className="border-b border-ag-border">
                {data.mode === "time" ? (
                  <>
                    <th className="text-left px-2 py-1">Window</th>
                    <th className="text-left px-2 py-1">OOS Start</th>
                    <th className="text-left px-2 py-1">OOS End</th>
                    <th className="text-left px-2 py-1">Basket</th>
                    <th className="text-right px-2 py-1">Trades</th>
                    <th className="text-right px-2 py-1">Realized PnL</th>
                    <th className="text-right px-2 py-1">Expected PnL</th>
                  </>
                ) : (
                  <>
                    <th className="text-left px-2 py-1">Phase</th>
                    <th className="text-left px-2 py-1">From</th>
                    <th className="text-left px-2 py-1">To</th>
                    <th className="text-left px-2 py-1">Basket</th>
                    <th className="text-right px-2 py-1">Trades</th>
                    <th className="text-right px-2 py-1">Realized PnL</th>
                  </>
                )}
              </tr>
            </thead>
            <tbody className="font-mono">
              {data.segments.map((s, i) => {
                const basket = (s.assets ?? s.basket ?? []) as string[];
                const basketStr = basket.length > 4
                  ? `${basket.slice(0, 4).join(", ")} +${basket.length - 4}`
                  : basket.join(", ");
                const pnl = s.realized_pnl ?? 0;
                return (
                  <tr key={i} className="border-b border-ag-border/30 hover:bg-ag-dark/30">
                    {data.mode === "time" ? (
                      <>
                        <td className="px-2 py-1">{s.window_num ?? "—"}</td>
                        <td className="px-2 py-1">{s.oos_start ?? "—"}</td>
                        <td className="px-2 py-1">{s.oos_end ?? "—"}</td>
                        <td className="px-2 py-1 text-ag-text-muted">{basketStr}</td>
                        <td className="px-2 py-1 text-right">{s.trades}</td>
                        <td className={`px-2 py-1 text-right ${pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                          {fmtMoney(pnl)}
                        </td>
                        <td className="px-2 py-1 text-right text-ag-text-muted">
                          {s.expected_oos_pnl != null ? fmtMoney(s.expected_oos_pnl) : "—"}
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="px-2 py-1">
                          <span className={
                            s.phase === "live"
                              ? "text-ag-green px-1.5 rounded bg-ag-green/15"
                              : "text-ag-accent px-1.5 rounded bg-ag-accent/15"
                          }>
                            {s.phase}
                          </span>
                        </td>
                        <td className="px-2 py-1">{s.date_from ?? "—"}</td>
                        <td className="px-2 py-1">{s.date_to ?? "—"}</td>
                        <td className="px-2 py-1 text-ag-text-muted">{basketStr}</td>
                        <td className="px-2 py-1 text-right">{s.trades}</td>
                        <td className={`px-2 py-1 text-right ${pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                          {fmtMoney(pnl)}
                        </td>
                      </>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Caveat footer */}
      <div className="mt-2 text-[10px] text-ag-text-muted italic">
        The &ldquo;Full Metrics&rdquo; panel below applies the FINAL basket to the whole period
        (fixed-basket hypothetical). The numbers here are the real rotation.
      </div>
    </div>
  );
}
