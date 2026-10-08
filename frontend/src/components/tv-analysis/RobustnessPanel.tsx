"use client";

import { useState, useEffect } from "react";
import { api, DashboardAnalysisRequest, WFEAssetResult, BenchmarkAssetResult, SignificanceResult } from "@/lib/api";

interface Props {
  req: DashboardAnalysisRequest;
}

export default function RobustnessPanel({ req }: Props) {
  const [wfe, setWfe] = useState<WFEAssetResult[]>([]);
  const [benchmark, setBenchmark] = useState<Record<string, BenchmarkAssetResult>>({});
  const [sourcePf, setSourcePf] = useState(0);
  const [sig, setSig] = useState<SignificanceResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!req.strategy_filename && !req.batch_id && !req.portfolio_id) return;
    setLoading(true);
    Promise.all([
      api.dashboardWfe(req).catch(() => ({ assets: [] })),
      api.dashboardBenchmark(req).catch(() => ({ assets: {}, source_pf: 0 })),
      api.dashboardSignificance(req).catch(() => null),
    ]).then(([wRes, bRes, sRes]) => {
      setWfe(wRes.assets);
      setBenchmark(bRes.assets);
      setSourcePf(bRes.source_pf);
      setSig(sRes);
    }).finally(() => setLoading(false));
  }, [req.strategy_filename, req.batch_id, req.portfolio_id, req.side_filter, req.date_to, req.min_trades]);

  if (loading) return <div className="bg-ag-card border border-ag-border rounded-lg p-6 text-ag-text-muted">Loading robustness data...</div>;

  const statusColor: Record<string, string> = {
    Elite: "text-ag-green", Strong: "text-ag-green",
    Stable: "text-ag-yellow", Weak: "text-ag-red", Fragile: "text-ag-red",
  };

  return (
    <div className="space-y-4">
      {/* Significance */}
      {sig && (
        <div className="bg-ag-card border border-ag-border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-ag-text mb-3">Statistical Significance</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
            <div>
              <p className="text-ag-text-muted">T-Statistic</p>
              <p className="text-lg font-semibold text-ag-text">{sig.t_stat?.toFixed(2) ?? "—"}</p>
            </div>
            <div>
              <p className="text-ag-text-muted">P-Value</p>
              <p className="text-lg font-semibold text-ag-text">{sig.p_value?.toFixed(6) ?? "—"}</p>
            </div>
            <div>
              <p className="text-ag-text-muted">Bonferroni P</p>
              <p className="text-lg font-semibold text-ag-text">{sig.bonferroni_p?.toFixed(6) ?? "—"}</p>
            </div>
            <div>
              <p className="text-ag-text-muted">Significant?</p>
              <p className={`text-lg font-semibold ${sig.significant ? "text-ag-green" : "text-ag-red"}`}>
                {sig.significant ? "Yes" : "No"}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* WFE Table */}
      {wfe.length > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-lg p-4 overflow-x-auto">
          <h3 className="text-sm font-semibold text-ag-text mb-3">Walk-Forward Efficiency</h3>
          <table className="w-full text-xs">
            <thead>
              <tr>
                <th className="text-left py-1 px-2 text-ag-text-muted">Asset</th>
                <th className="text-right py-1 px-2 text-ag-text-muted">WFE</th>
                <th className="text-right py-1 px-2 text-ag-text-muted">IS PF</th>
                <th className="text-right py-1 px-2 text-ag-text-muted">OOS PF</th>
                <th className="text-right py-1 px-2 text-ag-text-muted">IS Trades</th>
                <th className="text-right py-1 px-2 text-ag-text-muted">OOS Trades</th>
              </tr>
            </thead>
            <tbody>
              {wfe.map((w) => (
                <tr key={w.asset} className="border-t border-ag-border/50">
                  <td className="py-1.5 px-2 text-ag-text">{w.asset}</td>
                  <td className={`py-1.5 px-2 text-right ${w.WFE >= 0.6 ? "text-ag-green" : w.WFE >= 0.3 ? "text-ag-yellow" : "text-ag-red"}`}>
                    {w.WFE.toFixed(2)}
                  </td>
                  <td className="py-1.5 px-2 text-right text-ag-text">{w.IS_PF.toFixed(2)}</td>
                  <td className={`py-1.5 px-2 text-right ${w.OOS_PF >= 1 ? "text-ag-green" : "text-ag-red"}`}>
                    {w.OOS_PF.toFixed(2)}
                  </td>
                  <td className="py-1.5 px-2 text-right text-ag-text">{w.IS_Trades}</td>
                  <td className="py-1.5 px-2 text-right text-ag-text">{w.OOS_Trades}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Benchmark Table */}
      {Object.keys(benchmark).length > 0 && (
        <div className="bg-ag-card border border-ag-border rounded-lg p-4 overflow-x-auto">
          <h3 className="text-sm font-semibold text-ag-text mb-3">
            Benchmark Robustness
            <span className="ml-2 text-xs text-ag-text-muted font-normal">Source PF: {sourcePf.toFixed(2)}</span>
          </h3>
          <table className="w-full text-xs">
            <thead>
              <tr>
                <th className="text-left py-1 px-2 text-ag-text-muted">Asset</th>
                <th className="text-right py-1 px-2 text-ag-text-muted">H1 PF</th>
                <th className="text-right py-1 px-2 text-ag-text-muted">H2 PF</th>
                <th className="text-right py-1 px-2 text-ag-text-muted">H1 Trades</th>
                <th className="text-right py-1 px-2 text-ag-text-muted">H2 Trades</th>
                <th className="text-center py-1 px-2 text-ag-text-muted">Status</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(benchmark).map(([asset, b]) => (
                <tr key={asset} className="border-t border-ag-border/50">
                  <td className="py-1.5 px-2 text-ag-text">{asset}</td>
                  <td className="py-1.5 px-2 text-right text-ag-text">{b.H1_PF?.toFixed(2) ?? "—"}</td>
                  <td className="py-1.5 px-2 text-right text-ag-text">{b.H2_PF?.toFixed(2) ?? "—"}</td>
                  <td className="py-1.5 px-2 text-right text-ag-text">{b.h1_trades}</td>
                  <td className="py-1.5 px-2 text-right text-ag-text">{b.h2_trades}</td>
                  <td className={`py-1.5 px-2 text-center font-medium ${statusColor[b.status] ?? "text-ag-text-muted"}`}>
                    {b.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
