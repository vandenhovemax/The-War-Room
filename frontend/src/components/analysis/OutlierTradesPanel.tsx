"use client";
import { useState, useEffect, useRef } from "react";
import { ChevronDown, ChevronRight, AlertTriangle, Trophy } from "lucide-react";
import { api } from "@/lib/api";
import type { DashboardAnalysisRequest, OutlierResult, OutlierAssetGroup } from "@/lib/api";
import TradeChartButton from "@/components/common/TradeChartButton";
import TradeChartPopup from "@/components/common/TradeChartPopup";
import type { TradeChartData } from "@/components/common/TradeChartPopup";

interface Props {
  req: DashboardAnalysisRequest;
  timeframe?: string;
  strategyId?: number;
}

export default function OutlierTradesPanel({ req, timeframe, strategyId }: Props) {
  const [lossThreshold, setLossThreshold] = useState("");
  const [winThreshold, setWinThreshold] = useState("");
  const [lossMult, setLossMult] = useState(2.0);
  const [winMult, setWinMult] = useState(2.0);
  const [data, setData] = useState<OutlierResult | null>(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!req.batch_id && !req.portfolio_id) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setLoading(true);
      api.dashboardHealthOutliers({
        ...req,
        loss_threshold: lossThreshold ? parseFloat(lossThreshold) : undefined,
        win_threshold: winThreshold ? parseFloat(winThreshold) : undefined,
        loss_multiplier: lossMult,
        win_multiplier: winMult,
      })
        .then(setData)
        .catch(() => setData(null))
        .finally(() => setLoading(false));
    }, 500);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [req.batch_id, req.portfolio_id, req.date_to, req.min_trades, req.exclude_top_n, req.pf_min, req.pf_max, req.wr_min, lossThreshold, winThreshold, lossMult, winMult]);

  const lossCutoff = data ? (lossThreshold ? parseFloat(lossThreshold) : data.avg_loss * lossMult) : 0;
  const winCutoff = data ? (winThreshold ? parseFloat(winThreshold) : data.avg_win * winMult) : 0;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4 space-y-4">
      <h3 className="text-sm font-semibold text-ag-text">Outlier Trade Detection</h3>

      {/* Config row */}
      <div className="flex items-center gap-3 flex-wrap text-xs">
        <label className="text-ag-text-muted">Loss $:</label>
        <input type="number" value={lossThreshold} onChange={(e) => setLossThreshold(e.target.value)}
          placeholder="auto" className="w-20 bg-ag-dark border border-ag-border rounded px-2 py-1 text-sm text-center" />
        <label className="text-ag-text-muted">Loss Mult:</label>
        <input type="number" step="0.5" min={1} value={lossMult} onChange={(e) => setLossMult(parseFloat(e.target.value) || 2)}
          className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-sm text-center" />
        <span className="text-ag-text-muted">×avg</span>
        <span className="w-px h-4 bg-ag-border" />
        <label className="text-ag-text-muted">Win $:</label>
        <input type="number" value={winThreshold} onChange={(e) => setWinThreshold(e.target.value)}
          placeholder="auto" className="w-20 bg-ag-dark border border-ag-border rounded px-2 py-1 text-sm text-center" />
        <label className="text-ag-text-muted">Win Mult:</label>
        <input type="number" step="0.5" min={1} value={winMult} onChange={(e) => setWinMult(parseFloat(e.target.value) || 2)}
          className="w-16 bg-ag-dark border border-ag-border rounded px-2 py-1 text-sm text-center" />
        <span className="text-ag-text-muted">×avg</span>
      </div>

      {loading && <p className="text-xs text-ag-text-muted">Scanning...</p>}

      {data && (
        <div className="flex gap-3 text-xs text-ag-text-muted flex-wrap">
          <span>Avg Loss: <span className="text-ag-red font-mono">${data.avg_loss.toFixed(2)}</span></span>
          <span>Avg Win: <span className="text-ag-green font-mono">${data.avg_win.toFixed(2)}</span></span>
          <span>Loss cutoff: <span className="text-ag-red font-mono">${lossCutoff.toFixed(2)}</span></span>
          <span>Win cutoff: <span className="text-ag-green font-mono">${winCutoff.toFixed(2)}</span></span>
        </div>
      )}

      {data && data.flagged_losses.length > 0 && (
        <FlaggedSection
          title={`Disproportionate Losses (${data.flagged_losses.reduce((s, g) => s + g.trade_count, 0)} trades, ${data.flagged_losses.length} assets)`}
          groups={data.flagged_losses}
          icon={<AlertTriangle size={14} className="text-ag-red" />}
          color="text-ag-red"
          timeframe={timeframe}
          strategyId={strategyId}
        />
      )}

      {data && data.flagged_wins.length > 0 && (
        <FlaggedSection
          title={`Disproportionate Wins (${data.flagged_wins.reduce((s, g) => s + g.trade_count, 0)} trades, ${data.flagged_wins.length} assets)`}
          groups={data.flagged_wins}
          icon={<Trophy size={14} className="text-ag-green" />}
          color="text-ag-green"
          timeframe={timeframe}
          strategyId={strategyId}
        />
      )}

      {data && data.flagged_losses.length === 0 && data.flagged_wins.length === 0 && (
        <p className="text-xs text-ag-text-muted">No outlier trades detected at current thresholds.</p>
      )}
    </div>
  );
}

function FlaggedSection({ title, groups, icon, color, timeframe, strategyId }: {
  title: string; groups: OutlierAssetGroup[]; icon: React.ReactNode; color: string; timeframe?: string; strategyId?: number;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs font-medium">
        {icon}
        <span>{title}</span>
      </div>
      {groups.map((g) => (
        <AssetGroup key={g.asset} group={g} color={color} timeframe={timeframe} strategyId={strategyId} />
      ))}
    </div>
  );
}

function AssetGroup({ group, color, timeframe, strategyId }: { group: OutlierAssetGroup; color: string; timeframe?: string; strategyId?: number }) {
  const [open, setOpen] = useState(false);
  const [chartTrade, setChartTrade] = useState<TradeChartData | null>(null);

  const openChart = (t: OutlierAssetGroup["trades"][number]) => {
    setChartTrade({
      symbol: group.asset,
      timeframe: timeframe || "30",
      side: t.side,
      entryTime: t.entry_time || "",
      exitTime: t.exit_time || null,
      entryPrice: t.entry_price,
      exitPrice: t.exit_price,
      slPrice: null,
      strategyId,
      tpPrice: null,
      netProfit: t.net_profit,
    });
  };

  return (
    <div className="border border-ag-border/50 rounded-lg overflow-hidden">
      <button onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-ag-dark/50">
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <span className="font-medium">{group.asset}</span>
        <span className="text-ag-text-muted">{group.trade_count} trades</span>
        <span className={`ml-auto font-mono ${color}`}>${group.total_pnl.toFixed(2)}</span>
      </button>
      {open && (
        <div className="border-t border-ag-border/30 overflow-auto max-h-48">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-ag-darker text-ag-text-muted">
                <th className="p-1.5 text-left">Exit Time</th>
                <th className="p-1.5 text-left">Side</th>
                <th className="p-1.5 text-right">Entry</th>
                <th className="p-1.5 text-right">Exit</th>
                <th className="p-1.5 text-right">PnL</th>
              </tr>
            </thead>
            <tbody>
              {group.trades.map((t, i) => (
                <tr key={i} className="border-t border-ag-border/20">
                  <td className="p-1.5 font-mono">{t.exit_time.split(".")[0]}</td>
                  <td className="p-1.5">{t.side}</td>
                  <td className="p-1.5 text-right font-mono">{t.entry_price}</td>
                  <td className="p-1.5 text-right font-mono">{t.exit_price}</td>
                  <td className={`p-1.5 text-right font-mono font-bold ${t.net_profit >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                    ${t.net_profit.toFixed(2)}
                    {timeframe && (
                      <TradeChartButton onClick={() => openChart(t)} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {chartTrade && (
        <TradeChartPopup
          trade={chartTrade}
          onClose={() => setChartTrade(null)}
        />
      )}
    </div>
  );
}
