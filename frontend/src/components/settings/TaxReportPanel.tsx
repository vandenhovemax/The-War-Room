"use client";
import { useState, useCallback } from "react";
import {
  Download, Upload, Plus, Trash2, Edit3, X, Check,
  DollarSign, TrendingUp, FileText,
  AlertTriangle, BarChart3,
} from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import type { TaxRecord, TaxReportResponse } from "@/lib/api";

export default function TaxReportPanel() {
  const [yearFilter, setYearFilter] = useState(new Date().getFullYear());
  const [usdCadRate, setUsdCadRate] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [report, setReport] = useState<TaxReportResponse | null>(null);
  const [exportingReport, setExportingReport] = useState(false);

  // Year options
  const yearOpts: number[] = [];
  for (let y = new Date().getFullYear(); y >= 2020; y--) yearOpts.push(y);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const r = await api.taxReport(yearFilter, usdCadRate);
      setReport(r);
    } catch { /* ignore */ }
    finally { setGenerating(false); }
  };

  const handleExportReport = async () => {
    setExportingReport(true);
    try {
      const blob = await api.exportTaxReport(yearFilter, usdCadRate);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Tax_Report_${yearFilter}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch { /* ignore */ }
    finally { setExportingReport(false); }
  };

  const fmtCad = (v: number) =>
    `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} CAD`;
  const fmtUsd = (v: number) =>
    `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const fmtPct = (v: number) => `${v.toFixed(1)}%`;

  return (
    <div className="space-y-6">
      {/* ── Controls ── */}
      <div className="bg-ag-card border border-ag-border rounded-xl p-4">
        <h3 className="text-sm font-semibold mb-3">Year-End Tax Report</h3>
        <p className="text-xs text-ag-text-muted mb-4">
          Generate a comprehensive report for CRA T2125 + Quebec TP-21.4.39-V filing.
          All amounts converted to CAD. Uses daily Bank of Canada rates per trade, or a fixed rate if specified.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-[10px] text-ag-text-muted mb-0.5">Tax Year</label>
            <select
              value={yearFilter}
              onChange={(e) => setYearFilter(Number(e.target.value))}
              className="bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm"
            >
              {yearOpts.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-[10px] text-ag-text-muted mb-0.5">
              USD/CAD Rate (0 = daily Bank of Canada)
            </label>
            <input
              type="number"
              step="0.01"
              value={usdCadRate || ""}
              onChange={(e) => setUsdCadRate(parseFloat(e.target.value) || 0)}
              placeholder="Auto"
              className="bg-ag-dark border border-ag-border rounded-lg px-3 py-2 text-sm w-28"
            />
          </div>
          <button
            onClick={handleGenerate}
            disabled={generating}
            className="flex items-center gap-2 px-4 py-2 bg-ag-accent text-white rounded-lg text-sm font-medium hover:bg-ag-accent/80 disabled:opacity-50"
          >
            <BarChart3 size={14} />
            {generating ? "Generating..." : "Generate Report"}
          </button>
          {report && (
            <button
              onClick={handleExportReport}
              disabled={exportingReport}
              className="flex items-center gap-2 px-4 py-2 bg-ag-green/20 text-ag-green border border-ag-green/30 rounded-lg text-sm font-medium hover:bg-ag-green/30 disabled:opacity-50"
            >
              <Download size={14} />
              {exportingReport ? "Exporting..." : "Export for Accountant (.zip)"}
            </button>
          )}
        </div>
        {report && (
          <p className="text-[10px] text-ag-text-muted mt-2">
            {report.rate_info?.mode === "daily"
              ? `Daily Bank of Canada rates — Avg: ${report.rate_info.avg.toFixed(4)} | Range: ${report.rate_info.min.toFixed(4)}–${report.rate_info.max.toFixed(4)}`
              : `Fixed rate: 1 USD = ${report.usd_cad_rate} CAD`}
            {" | "}Generated: {new Date(report.generated_at).toLocaleString()}
          </p>
        )}
      </div>

      {/* ── Report Content ── */}
      {report && (
        <>
          {/* Warnings */}
          {report.warnings.length > 0 && (
            <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4">
              <div className="flex items-center gap-2 text-yellow-400 mb-2">
                <AlertTriangle size={16} />
                <span className="text-sm font-semibold">Data Quality Warnings</span>
              </div>
              <ul className="space-y-1">
                {report.warnings.map((w, i) => (
                  <li key={i} className="text-xs text-yellow-300/80">- {w}</li>
                ))}
              </ul>
            </div>
          )}

          {/* T2125 Income Summary */}
          <ReportSection title="T2125 — Business Income Summary" icon={<DollarSign size={16} />}>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 mb-4">
              <ReportCard
                label="Total Trades" value={String(report.income_summary.total_trades)}
              />
              <ReportCard
                label="Gross PnL" value={fmtCad(report.income_summary.gross_pnl_cad)}
                sub={fmtUsd(report.income_summary.gross_pnl_usd) + " USD"}
                color={report.income_summary.gross_pnl_cad >= 0 ? "green" : "red"}
              />
              <ReportCard
                label="Trading Expenses" value={fmtCad(report.income_summary.total_trading_expenses_cad)}
                sub={`Commissions: ${fmtCad(report.income_summary.total_commissions_cad)} | Funding: ${fmtCad(report.income_summary.total_funding_fees_cad)}`}
              />
              <ReportCard
                label="Net PnL (after fees)" value={fmtCad(report.income_summary.net_pnl_cad)}
                sub={fmtUsd(report.income_summary.net_pnl_usd) + " USD"}
                color={report.income_summary.net_pnl_cad >= 0 ? "green" : "red"}
              />
            </div>
            <div className="bg-ag-dark/50 rounded-lg p-3 space-y-2">
              <div className="flex justify-between text-xs">
                <span className="text-ag-text-muted">Net PnL (after broker fees)</span>
                <span className="font-mono">{fmtCad(report.income_summary.net_pnl_cad)}</span>
              </div>
              <div className="border-t border-ag-border pt-2 flex justify-between text-sm font-semibold">
                <span>Net Business Income (T2125 Line 8299)</span>
                <span className={report.income_summary.net_business_income_cad >= 0 ? "text-ag-green" : "text-red-400"}>
                  {fmtCad(report.income_summary.net_business_income_cad)}
                </span>
              </div>
            </div>
            {/* Tax estimate */}
            <div className="mt-3 bg-ag-dark/50 rounded-lg p-3">
              <p className="text-xs font-semibold text-ag-text-muted mb-2">Estimated Tax (Federal + Quebec)</p>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                <MiniStat label="Federal" value={fmtCad(report.tax_estimate.federal_tax)} />
                <MiniStat label="Quebec" value={fmtCad(report.tax_estimate.quebec_tax)} />
                <MiniStat label="Total Tax" value={fmtCad(report.tax_estimate.total_tax)} />
                <MiniStat label="Effective Rate" value={fmtPct(report.tax_estimate.effective_rate)} />
                <MiniStat label="Marginal Rate" value={fmtPct(report.tax_estimate.marginal_rate)} />
              </div>
            </div>
          </ReportSection>

          {/* Per-Asset Breakdown (TP-21.4.39-V) */}
          <ReportSection title="TP-21.4.39-V — Per-Asset Breakdown" icon={<FileText size={16} />}>
            <p className="text-[10px] text-ag-text-muted mb-2">
              Required by Revenu Quebec — report income/loss by type of cryptoasset
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-ag-dark/50 text-ag-text-muted border-b border-ag-border">
                    <th className="px-3 py-2 text-left">Symbol</th>
                    <th className="px-3 py-2 text-right">Trades</th>
                    <th className="px-3 py-2 text-right">W/L</th>
                    <th className="px-3 py-2 text-right">Win %</th>
                    <th className="px-3 py-2 text-right">Gross PnL (CAD)</th>
                    <th className="px-3 py-2 text-right">Commissions (CAD)</th>
                    <th className="px-3 py-2 text-right">Funding (CAD)</th>
                    <th className="px-3 py-2 text-right">Net PnL (CAD)</th>
                    <th className="px-3 py-2 text-left">Period</th>
                  </tr>
                </thead>
                <tbody>
                  {report.per_asset.map((a) => (
                    <tr key={a.symbol} className="border-b border-ag-border/50 hover:bg-ag-card-hover">
                      <td className="px-3 py-2 font-medium">{a.symbol}</td>
                      <td className="px-3 py-2 text-right">{a.trade_count}</td>
                      <td className="px-3 py-2 text-right">
                        <span className="text-ag-green">{a.wins}</span>
                        /
                        <span className="text-red-400">{a.losses}</span>
                      </td>
                      <td className="px-3 py-2 text-right">{fmtPct(a.win_rate)}</td>
                      <td className="px-3 py-2 text-right font-mono">
                        <span className={a.gross_pnl_cad >= 0 ? "text-ag-green" : "text-red-400"}>
                          {fmtCad(a.gross_pnl_cad)}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-ag-text-muted">
                        {fmtCad(a.commissions_cad)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-ag-text-muted">
                        {fmtCad(a.funding_cad)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono font-medium">
                        <span className={a.net_pnl_cad >= 0 ? "text-ag-green" : "text-red-400"}>
                          {fmtCad(a.net_pnl_cad)}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-ag-text-muted text-[10px]">
                        {a.first_trade_date?.slice(0, 10)} — {a.last_trade_date?.slice(0, 10)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </ReportSection>

          {/* Monthly Breakdown */}
          <ReportSection title="Monthly Income Breakdown" icon={<BarChart3 size={16} />}>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-ag-dark/50 text-ag-text-muted border-b border-ag-border">
                    <th className="px-3 py-2 text-left">Month</th>
                    <th className="px-3 py-2 text-right">Trades</th>
                    <th className="px-3 py-2 text-right">W/L</th>
                    <th className="px-3 py-2 text-right">Gross PnL (USD)</th>
                    <th className="px-3 py-2 text-right">Gross PnL (CAD)</th>
                    <th className="px-3 py-2 text-right">Commissions (CAD)</th>
                    <th className="px-3 py-2 text-right">Funding (CAD)</th>
                    <th className="px-3 py-2 text-right">Net PnL (USD)</th>
                    <th className="px-3 py-2 text-right">Net PnL (CAD)</th>
                  </tr>
                </thead>
                <tbody>
                  {report.monthly.map((m) => (
                    <tr key={m.month} className={`border-b border-ag-border/50 ${m.trade_count > 0 ? "hover:bg-ag-card-hover" : "opacity-40"}`}>
                      <td className="px-3 py-2 font-medium">{m.month_name}</td>
                      <td className="px-3 py-2 text-right">{m.trade_count}</td>
                      <td className="px-3 py-2 text-right">
                        <span className="text-ag-green">{m.wins}</span>/<span className="text-red-400">{m.losses}</span>
                      </td>
                      <td className="px-3 py-2 text-right font-mono">{fmtUsd(m.gross_pnl_usd)}</td>
                      <td className="px-3 py-2 text-right font-mono">{fmtCad(m.gross_pnl_cad)}</td>
                      <td className="px-3 py-2 text-right font-mono text-ag-text-muted">{fmtCad(m.commissions_cad)}</td>
                      <td className="px-3 py-2 text-right font-mono text-ag-text-muted">{fmtCad(m.funding_cad)}</td>
                      <td className="px-3 py-2 text-right font-mono">
                        <span className={m.net_pnl_usd >= 0 ? "text-ag-green" : "text-red-400"}>{fmtUsd(m.net_pnl_usd)}</span>
                      </td>
                      <td className="px-3 py-2 text-right font-mono font-medium">
                        <span className={m.net_pnl_cad >= 0 ? "text-ag-green" : "text-red-400"}>{fmtCad(m.net_pnl_cad)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </ReportSection>

          {/* Performance Summary */}
          <ReportSection title="Performance Summary" icon={<TrendingUp size={16} />}>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
              <MiniCard label="Win Rate" value={fmtPct(report.performance.win_rate)} color="green" />
              <MiniCard label="Wins" value={String(report.performance.total_wins)} color="green" />
              <MiniCard label="Losses" value={String(report.performance.total_losses)} color="red" />
              <MiniCard label="Avg Win" value={fmtUsd(report.performance.avg_win_usd)} color="green" />
              <MiniCard label="Avg Loss" value={fmtUsd(report.performance.avg_loss_usd)} color="red" />
              <MiniCard label="Avg Hold" value={`${report.performance.avg_hold_time_hours}h`} />
              <MiniCard label="Largest Win" value={fmtUsd(report.performance.largest_win_usd)} color="green" />
              <MiniCard label="Largest Loss" value={fmtUsd(report.performance.largest_loss_usd)} color="red" />
              <MiniCard label="Long Trades" value={String(report.performance.long_trades)} />
              <MiniCard label="Short Trades" value={String(report.performance.short_trades)} />
              <MiniCard label="Long PnL" value={fmtUsd(report.performance.long_pnl_usd)}
                color={report.performance.long_pnl_usd >= 0 ? "green" : "red"} />
              <MiniCard label="Short PnL" value={fmtUsd(report.performance.short_pnl_usd)}
                color={report.performance.short_pnl_usd >= 0 ? "green" : "red"} />
            </div>
          </ReportSection>
        </>
      )}

      {/* ── Trade Records (data source) ── */}
      <RecordsSection yearFilter={yearFilter} />
    </div>
  );
}


/* ── Trade Records Section (import/CRUD) ─────────────────────── */

function RecordsSection({ yearFilter }: { yearFilter: number }) {
  const [symbolFilter, setSymbolFilter] = useState("");

  const { data, loading, refetch } = useApi(
    useCallback(
      () => api.taxRecords(symbolFilter || undefined, yearFilter || undefined),
      [symbolFilter, yearFilter]
    )
  );
  const { data: summary, refetch: refetchSummary } = useApi(
    useCallback(
      () => api.taxSummary(yearFilter || undefined),
      [yearFilter]
    )
  );

  const records = data?.records ?? [];

  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [importing, setImporting] = useState(false);
  const [importingJournal, setImportingJournal] = useState(false);
  const [importMsg, setImportMsg] = useState("");
  const [exporting, setExporting] = useState(false);

  const emptyForm = {
    symbol: "", side: "Long", strategy_name: "",
    quantity: 0, leverage: 1, margin_used: 0,
    entry_price: 0, entry_time: "",
    entry_order_type: "Market",
    exit_price: 0, exit_time: "",
    exit_order_type: "Market", exit_reason: "",
    entry_commission: 0, exit_commission: 0,
    funding_fee: 0, gross_pnl: 0, net_pnl: 0,
    net_pnl_pct: 0, outcome: "",
    portfolio_name: "", notes: "",
  };
  const [form, setForm] = useState(emptyForm);
  const f = (key: string, val: unknown) => setForm((p) => ({ ...p, [key]: val }));

  const handleImport = async () => {
    setImporting(true); setImportMsg("");
    try {
      const res = await api.importLiveTrades();
      setImportMsg(res.imported > 0
        ? `Imported ${res.imported} live trades (${res.already_existed} already existed)`
        : `No new live trades (${res.already_existed} already in records)`);
      refetch(); refetchSummary();
    } catch { setImportMsg("Import failed"); }
    finally { setImporting(false); }
  };

  const handleImportJournal = async () => {
    setImportingJournal(true); setImportMsg("");
    try {
      const res = await api.importJournalTrades();
      setImportMsg(res.imported > 0
        ? `Imported ${res.imported} journal trades (${res.already_existed} already existed)`
        : `No new journal trades (${res.already_existed} already in records)`);
      refetch(); refetchSummary();
    } catch { setImportMsg("Journal import failed"); }
    finally { setImportingJournal(false); }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const blob = await api.exportTaxCsv(yearFilter || undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = yearFilter ? `tax_records_${yearFilter}.csv` : "tax_records_all.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch { /* ignore */ }
    finally { setExporting(false); }
  };

  const handleSave = async () => {
    try {
      if (editingId) {
        await api.updateTaxRecord(editingId, form);
      } else {
        await api.createTaxRecord(form);
      }
      setShowForm(false); setEditingId(null); setForm(emptyForm);
      refetch(); refetchSummary();
    } catch { /* ignore */ }
  };

  const handleEdit = (r: TaxRecord) => {
    setEditingId(r.id);
    setForm({
      symbol: r.symbol, side: r.side, strategy_name: r.strategy_name,
      quantity: r.quantity, leverage: r.leverage, margin_used: r.margin_used,
      entry_price: r.entry_price, entry_time: r.entry_time?.slice(0, 16) ?? "",
      entry_order_type: r.entry_order_type, exit_price: r.exit_price ?? 0,
      exit_time: r.exit_time?.slice(0, 16) ?? "", exit_order_type: r.exit_order_type,
      exit_reason: r.exit_reason, entry_commission: r.entry_commission,
      exit_commission: r.exit_commission, funding_fee: r.funding_fee,
      gross_pnl: r.gross_pnl, net_pnl: r.net_pnl, net_pnl_pct: r.net_pnl_pct,
      outcome: r.outcome, portfolio_name: r.portfolio_name, notes: r.notes,
    });
    setShowForm(true);
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this tax record?")) return;
    await api.deleteTaxRecord(id);
    refetch(); refetchSummary();
  };

  const fmtDate = (d: string | null) => {
    if (!d) return "\u2014";
    return new Date(d).toLocaleDateString("en-GB", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
  };
  const fmtNum = (v: number, dp = 2) =>
    v?.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp }) ?? "0";

  return (
    <ReportSection title="Trade Records" icon={<FileText size={16} />}>
      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
          <MiniCard label="Records" value={String(summary.total_records)} />
          <MiniCard label="Net PnL" value={`$${fmtNum(summary.total_net_pnl)}`}
            color={summary.total_net_pnl >= 0 ? "green" : "red"} />
          <MiniCard label="Wins" value={String(summary.wins)} color="green" />
          <MiniCard label="Losses" value={String(summary.losses)} color="red" />
          <MiniCard label="Fees" value={`$${fmtNum(summary.total_fees)}`} />
          <MiniCard label="Funding" value={`$${fmtNum(summary.total_funding)}`} />
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <input type="text" placeholder="Filter symbol..."
          value={symbolFilter} onChange={(e) => setSymbolFilter(e.target.value.toUpperCase())}
          className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-xs w-32" />
        <div className="flex-1" />
        <button onClick={handleImport} disabled={importing}
          className="flex items-center gap-1 px-2 py-1.5 bg-ag-accent/20 text-ag-accent border border-ag-accent/30 rounded-lg text-xs hover:bg-ag-accent/30 disabled:opacity-50">
          <Upload size={12} /> {importing ? "..." : "Import Live"}
        </button>
        <button onClick={handleImportJournal} disabled={importingJournal}
          className="flex items-center gap-1 px-2 py-1.5 bg-purple-500/20 text-purple-400 border border-purple-500/30 rounded-lg text-xs hover:bg-purple-500/30 disabled:opacity-50">
          <Upload size={12} /> {importingJournal ? "..." : "Import Journal"}
        </button>
        <button onClick={handleExport} disabled={exporting}
          className="flex items-center gap-1 px-2 py-1.5 bg-ag-card-hover border border-ag-border rounded-lg text-xs text-ag-text-muted hover:text-ag-text disabled:opacity-50">
          <Download size={12} /> {exporting ? "..." : "CSV"}
        </button>
        <button onClick={() => { setShowForm(true); setEditingId(null); setForm(emptyForm); }}
          className="flex items-center gap-1 px-2 py-1.5 bg-ag-accent text-white rounded-lg text-xs font-medium hover:bg-ag-accent/80">
          <Plus size={12} /> Add Trade
        </button>
      </div>

      {importMsg && (
        <div className={`rounded-lg px-3 py-1.5 text-xs mb-3 ${
          importMsg.includes("failed") ? "bg-red-500/10 border border-red-500/30 text-red-400"
            : "bg-ag-green/10 border border-ag-green/30 text-ag-green"
        }`}>{importMsg}</div>
      )}

      {/* Add/Edit form */}
      {showForm && (
        <div className="bg-ag-dark/30 border border-ag-border rounded-lg p-4 space-y-3 mb-3">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-semibold">{editingId ? "Edit Record" : "Add Manual Trade"}</h4>
            <button onClick={() => { setShowForm(false); setEditingId(null); }}>
              <X size={14} className="text-ag-text-muted hover:text-ag-text" />
            </button>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
            {[
              ["Symbol", "symbol", "text", "BTCUSDT"],
              ["Entry Price", "entry_price", "number", ""],
              ["Exit Price", "exit_price", "number", ""],
              ["Quantity", "quantity", "number", ""],
              ["Leverage", "leverage", "number", ""],
              ["Margin Used", "margin_used", "number", ""],
              ["Entry Commission", "entry_commission", "number", ""],
              ["Exit Commission", "exit_commission", "number", ""],
              ["Funding Fee", "funding_fee", "number", ""],
              ["Gross PnL", "gross_pnl", "number", ""],
              ["Net PnL", "net_pnl", "number", ""],
              ["Net PnL %", "net_pnl_pct", "number", ""],
            ].map(([label, key, type, ph]) => (
              <div key={key}>
                <label className="block text-[10px] text-ag-text-muted mb-0.5">{label}</label>
                <input type={type} step="any" placeholder={ph as string}
                  value={(form as Record<string, unknown>)[key as string] as string || ""}
                  onChange={(e) => f(key as string, type === "number" ? parseFloat(e.target.value) || 0 : e.target.value.toUpperCase())}
                  className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1 text-xs" />
              </div>
            ))}
            <div>
              <label className="block text-[10px] text-ag-text-muted mb-0.5">Side</label>
              <select value={form.side} onChange={(e) => f("side", e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1 text-xs">
                <option>Long</option><option>Short</option>
              </select>
            </div>
            <div>
              <label className="block text-[10px] text-ag-text-muted mb-0.5">Entry Time</label>
              <input type="datetime-local" value={form.entry_time}
                onChange={(e) => f("entry_time", e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1 text-xs" />
            </div>
            <div>
              <label className="block text-[10px] text-ag-text-muted mb-0.5">Exit Time</label>
              <input type="datetime-local" value={form.exit_time}
                onChange={(e) => f("exit_time", e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1 text-xs" />
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div>
              <label className="block text-[10px] text-ag-text-muted mb-0.5">Strategy</label>
              <input value={form.strategy_name} onChange={(e) => f("strategy_name", e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1 text-xs" />
            </div>
            <div>
              <label className="block text-[10px] text-ag-text-muted mb-0.5">Portfolio</label>
              <input value={form.portfolio_name} onChange={(e) => f("portfolio_name", e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1 text-xs" />
            </div>
            <div>
              <label className="block text-[10px] text-ag-text-muted mb-0.5">Exit Reason</label>
              <select value={form.exit_reason} onChange={(e) => f("exit_reason", e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1 text-xs">
                <option value="">--</option>
                <option value="signal">Signal</option><option value="sl">Stop Loss</option>
                <option value="tp">Take Profit</option><option value="tsl">Trailing Stop</option>
                <option value="manual">Manual</option><option value="liquidation">Liquidation</option>
              </select>
            </div>
            <div>
              <label className="block text-[10px] text-ag-text-muted mb-0.5">Notes</label>
              <input value={form.notes} onChange={(e) => f("notes", e.target.value)}
                className="w-full bg-ag-dark border border-ag-border rounded-lg px-2 py-1 text-xs" />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => { setShowForm(false); setEditingId(null); }}
              className="px-3 py-1.5 text-xs text-ag-text-muted hover:text-ag-text">Cancel</button>
            <button onClick={handleSave}
              className="flex items-center gap-1 px-3 py-1.5 bg-ag-accent text-white rounded-lg text-xs hover:bg-ag-accent/80">
              <Check size={12} /> {editingId ? "Update" : "Save"}
            </button>
          </div>
        </div>
      )}

      {/* Records table */}
      {loading ? (
        <p className="text-ag-text-muted text-xs">Loading...</p>
      ) : records.length === 0 ? (
        <div className="bg-ag-dark/30 rounded-lg p-6 text-center">
          <FileText size={24} className="mx-auto text-ag-text-muted mb-2" />
          <p className="text-ag-text-muted text-xs">No tax records. Import live/journal trades or add manually.</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="bg-ag-dark/50 text-ag-text-muted border-b border-ag-border">
                <th className="px-2 py-1.5 text-left">Src</th>
                <th className="px-2 py-1.5 text-left">Symbol</th>
                <th className="px-2 py-1.5 text-left">Side</th>
                <th className="px-2 py-1.5 text-left">Entry</th>
                <th className="px-2 py-1.5 text-left">Exit</th>
                <th className="px-2 py-1.5 text-right">Entry $</th>
                <th className="px-2 py-1.5 text-right">Exit $</th>
                <th className="px-2 py-1.5 text-right">Qty</th>
                <th className="px-2 py-1.5 text-right">Fees</th>
                <th className="px-2 py-1.5 text-right">Funding</th>
                <th className="px-2 py-1.5 text-right">Net PnL</th>
                <th className="px-2 py-1.5 text-center">W/L</th>
                <th className="px-2 py-1.5 text-center">Act</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.id} className="border-b border-ag-border/50 hover:bg-ag-card-hover">
                  <td className="px-2 py-1.5">
                    <span className={`inline-block px-1 py-0.5 rounded text-[9px] font-medium ${
                      r.source === "live" ? "bg-ag-accent/20 text-ag-accent"
                        : r.source === "journal" ? "bg-purple-500/20 text-purple-400"
                          : "bg-ag-card-hover text-ag-text-muted"
                    }`}>{r.source}</span>
                  </td>
                  <td className="px-2 py-1.5 font-medium">{r.symbol}</td>
                  <td className="px-2 py-1.5">
                    <span className={r.side === "Long" ? "text-ag-green" : "text-red-400"}>{r.side}</span>
                  </td>
                  <td className="px-2 py-1.5 text-ag-text-muted">{fmtDate(r.entry_time)}</td>
                  <td className="px-2 py-1.5 text-ag-text-muted">{fmtDate(r.exit_time)}</td>
                  <td className="px-2 py-1.5 text-right font-mono">{fmtNum(r.entry_price, 4)}</td>
                  <td className="px-2 py-1.5 text-right font-mono">{r.exit_price ? fmtNum(r.exit_price, 4) : "\u2014"}</td>
                  <td className="px-2 py-1.5 text-right font-mono">{fmtNum(r.quantity, 4)}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-ag-text-muted">${fmtNum(r.total_commission)}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-ag-text-muted">${fmtNum(r.funding_fee)}</td>
                  <td className="px-2 py-1.5 text-right font-mono font-medium">
                    <span className={r.net_pnl >= 0 ? "text-ag-green" : "text-red-400"}>${fmtNum(r.net_pnl)}</span>
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <span className={`text-[9px] font-bold ${
                      r.outcome === "Win" ? "text-ag-green" : r.outcome === "Loss" ? "text-red-400" : "text-ag-text-muted"
                    }`}>{r.outcome || "\u2014"}</span>
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <div className="flex items-center justify-center gap-0.5">
                      <button onClick={() => handleEdit(r)} className="p-0.5 text-ag-text-muted hover:text-ag-accent"><Edit3 size={11} /></button>
                      <button onClick={() => handleDelete(r.id)} className="p-0.5 text-ag-text-muted hover:text-red-400"><Trash2 size={11} /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ReportSection>
  );
}


/* ── Shared UI helpers ───────────────────────────────────────── */

function ReportSection({ title, icon, children }: {
  title: string; icon: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-ag-accent">{icon}</span>
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function ReportCard({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  const cls = color === "green" ? "text-ag-green" : color === "red" ? "text-red-400" : "text-ag-text";
  return (
    <div className="bg-ag-dark/50 rounded-lg p-3">
      <p className="text-[10px] text-ag-text-muted mb-1">{label}</p>
      <p className={`text-sm font-semibold ${cls}`}>{value}</p>
      {sub && <p className="text-[10px] text-ag-text-muted mt-0.5">{sub}</p>}
    </div>
  );
}

function MiniCard({ label, value, color }: {
  label: string; value: string; color?: string;
}) {
  const cls = color === "green" ? "text-ag-green" : color === "red" ? "text-red-400" : "text-ag-text";
  return (
    <div className="bg-ag-dark/50 rounded-lg p-2">
      <p className="text-[10px] text-ag-text-muted">{label}</p>
      <p className={`text-xs font-semibold ${cls}`}>{value}</p>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] text-ag-text-muted">{label}</p>
      <p className="text-xs font-mono font-medium">{value}</p>
    </div>
  );
}
