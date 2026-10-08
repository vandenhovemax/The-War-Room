"use client";
import { useState, useCallback } from "react";
import {
  DollarSign, TrendingUp, Percent, RefreshCw,
  ArrowUpRight, ArrowDownLeft, Landmark,
} from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import type {
  TaxWithholdingMonth,
  TaxWithholdingQuarter,
} from "@/lib/api";

type View = "monthly" | "quarterly";

export default function TaxWithholdingPanel() {
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(currentYear);
  const [manualRate, setManualRate] = useState<string>("");
  const [view, setView] = useState<View>("monthly");
  const [expenses, setExpenses] = useState<string>("");
  const [expenseCurrency, setExpenseCurrency] = useState<"CAD" | "USD">("CAD");

  const rateNum = manualRate ? parseFloat(manualRate) : 0;
  const expensesNum = expenses ? parseFloat(expenses) : 0;

  // We need a stable FX rate for expense conversion — use manual rate or fallback
  const fxForExpenses = rateNum > 0 ? rateNum : 1.36;
  const expensesCad = expenseCurrency === "USD"
    ? expensesNum * fxForExpenses
    : expensesNum;

  const { data, loading, refetch } = useApi(
    useCallback(
      () => api.taxWithholding(
        year,
        rateNum > 0 ? rateNum : undefined,
        expensesCad > 0 ? expensesCad : undefined,
      ),
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [year, rateNum, expensesCad]
    )
  );

  const handleFetchRate = async () => {
    // Fetch with rate=0 to get auto-fetched rate, then set it as manual
    const res = await api.taxWithholding(year, 0);
    if (res?.usd_cad_rate) {
      setManualRate(String(res.usd_cad_rate));
    }
  };

  const yearOpts: number[] = [];
  for (let y = currentYear; y >= 2020; y--) yearOpts.push(y);

  const fmtCad = (v: number) =>
    `$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const fmtUsd = (v: number) =>
    `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const fmtPct = (v: number) => `${v.toFixed(1)}%`;

  // Only show months that have data or are <= current month for current year
  const activeMonths = (data?.months ?? []).filter((m: TaxWithholdingMonth) => {
    if (year < currentYear) return m.pnl_usd !== 0;
    // Current year: show up to current month (even if 0)
    const now = new Date();
    return m.month <= now.getMonth() + 1 || m.pnl_usd !== 0;
  });

  return (
    <div className="bg-ag-card border border-ag-border rounded-xl p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Landmark size={18} className="text-ag-accent" />
          <h3 className="text-sm font-semibold">
            Tax Withholding Planner
          </h3>
          <span className="text-[10px] text-ag-text-muted bg-ag-dark px-2 py-0.5 rounded">
            Quebec / Canada &bull; Business Income
          </span>
        </div>
      </div>

      {/* Controls row */}
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-[10px] text-ag-text-muted mb-0.5">
            Tax Year
          </label>
          <select
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm"
          >
            {yearOpts.map((y) => (
              <option key={y} value={y}>{y}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-[10px] text-ag-text-muted mb-0.5">
            USD/CAD Rate
          </label>
          <div className="flex items-center gap-1">
            <input
              type="number"
              step="0.01"
              placeholder={data?.usd_cad_rate ? String(data.usd_cad_rate) : "auto"}
              value={manualRate}
              onChange={(e) => setManualRate(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm w-24"
            />
            <button
              onClick={handleFetchRate}
              title="Fetch live rate from Bank of Canada"
              className="flex items-center gap-1 px-2 py-1.5 bg-ag-accent/20 text-ag-accent border border-ag-accent/30 rounded-lg text-xs hover:bg-ag-accent/30"
            >
              <RefreshCw size={12} /> Fetch
            </button>
          </div>
        </div>

        <div>
          <label className="block text-[10px] text-ag-text-muted mb-0.5">
            Business Expenses
          </label>
          <div className="flex items-center gap-1">
            <input
              type="number"
              step="100"
              placeholder="0"
              value={expenses}
              onChange={(e) => setExpenses(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded-lg px-3 py-1.5 text-sm w-28"
            />
            <div className="flex border border-ag-border rounded-lg overflow-hidden">
              <button
                onClick={() => setExpenseCurrency("CAD")}
                className={`px-2 py-1.5 text-[10px] font-medium ${
                  expenseCurrency === "CAD"
                    ? "bg-ag-accent text-white"
                    : "bg-ag-dark text-ag-text-muted hover:text-ag-text"
                }`}
              >
                CAD
              </button>
              <button
                onClick={() => setExpenseCurrency("USD")}
                className={`px-2 py-1.5 text-[10px] font-medium ${
                  expenseCurrency === "USD"
                    ? "bg-ag-accent text-white"
                    : "bg-ag-dark text-ag-text-muted hover:text-ag-text"
                }`}
              >
                USD
              </button>
            </div>
          </div>
        </div>

        <div>
          <label className="block text-[10px] text-ag-text-muted mb-0.5">
            View
          </label>
          <div className="flex border border-ag-border rounded-lg overflow-hidden">
            <button
              onClick={() => setView("monthly")}
              className={`px-3 py-1.5 text-xs ${
                view === "monthly"
                  ? "bg-ag-accent text-white"
                  : "bg-ag-dark text-ag-text-muted hover:text-ag-text"
              }`}
            >
              Monthly
            </button>
            <button
              onClick={() => setView("quarterly")}
              className={`px-3 py-1.5 text-xs ${
                view === "quarterly"
                  ? "bg-ag-accent text-white"
                  : "bg-ag-dark text-ag-text-muted hover:text-ag-text"
              }`}
            >
              Quarterly
            </button>
          </div>
        </div>

        {data && (
          <span className="text-[10px] text-ag-text-muted ml-auto">
            Rate: 1 USD = {data.usd_cad_rate} CAD
          </span>
        )}
      </div>

      {/* Summary cards */}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <WCard
            icon={<DollarSign size={14} />}
            label="YTD Income (CAD)"
            value={fmtCad(data.annual.total_pnl_cad)}
            color={data.annual.total_pnl_cad >= 0 ? "green" : "red"}
          />
          <WCard
            icon={<Landmark size={14} />}
            label="YTD Tax Owed"
            value={fmtCad(data.annual.total_tax)}
            sub={`Fed ${fmtCad(data.annual.federal_tax)} + QC ${fmtCad(data.annual.quebec_tax)}`}
          />
          <WCard
            icon={<Percent size={14} />}
            label="Effective Rate"
            value={fmtPct(data.annual.effective_rate)}
            sub={`Marginal: ${fmtPct(data.annual.marginal_rate)}`}
          />
          <WCard
            icon={<TrendingUp size={14} />}
            label="Side Account Balance"
            value={fmtCad(data.annual.side_account_balance)}
            color={data.annual.side_account_balance >= 0 ? "green" : "amber"}
            sub="Total set aside to date"
          />
        </div>
      )}

      {/* After-expenses summary (only when expenses entered) */}
      {data && data.after_expenses.business_expenses > 0 && (
        <div className="bg-ag-dark/50 border border-ag-border/50 rounded-xl p-4 space-y-2">
          <div className="flex items-center gap-2 mb-2">
            <DollarSign size={14} className="text-amber-400" />
            <span className="text-xs font-semibold">Year-End After Expenses</span>
            <span className="text-[10px] text-ag-text-muted">
              (Expenses: {fmtCad(data.after_expenses.business_expenses)} CAD)
            </span>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <WCard
              icon={<DollarSign size={14} />}
              label="Taxable After Expenses"
              value={fmtCad(data.after_expenses.taxable_income)}
              color={data.after_expenses.taxable_income > 0 ? "green" : "red"}
            />
            <WCard
              icon={<Landmark size={14} />}
              label="Tax After Expenses"
              value={fmtCad(data.after_expenses.total_tax)}
              sub={`Fed ${fmtCad(data.after_expenses.federal_tax)} + QC ${fmtCad(data.after_expenses.quebec_tax)}`}
            />
            <WCard
              icon={<Percent size={14} />}
              label="Effective Rate"
              value={fmtPct(data.after_expenses.effective_rate)}
            />
            <WCard
              icon={<TrendingUp size={14} />}
              label="Tax Savings"
              value={fmtCad(data.after_expenses.tax_savings)}
              color="green"
              sub="From deducting expenses"
            />
          </div>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <p className="text-ag-text-muted text-sm">Calculating withholding...</p>
      ) : !data || activeMonths.length === 0 ? (
        <div className="text-center py-6">
          <Landmark size={28} className="mx-auto text-ag-text-muted mb-2" />
          <p className="text-ag-text-muted text-sm">
            No trade data for {year}. Import your live trades first.
          </p>
        </div>
      ) : view === "monthly" ? (
        <MonthlyTable months={activeMonths} fmtCad={fmtCad} fmtUsd={fmtUsd} fmtPct={fmtPct} />
      ) : (
        <QuarterlyTable quarters={data.quarters} fmtCad={fmtCad} fmtUsd={fmtUsd} />
      )}

      <p className="text-[10px] text-ag-text-muted">
        Based on 2026 Quebec + Federal tax brackets for business income (100% taxable).
        Federal rates include the 16.5% Quebec abatement. Amounts are estimates &mdash;
        consult your accountant for official filings.
      </p>
    </div>
  );
}


/* ── Monthly table ──────────────────────────────────────────── */

function MonthlyTable({ months, fmtCad, fmtUsd, fmtPct }: {
  months: TaxWithholdingMonth[];
  fmtCad: (v: number) => string;
  fmtUsd: (v: number) => string;
  fmtPct: (v: number) => string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-ag-dark/50 text-ag-text-muted border-b border-ag-border">
            <th className="px-3 py-2 text-left">Month</th>
            <th className="px-3 py-2 text-right">PnL (USD)</th>
            <th className="px-3 py-2 text-right">PnL (CAD)</th>
            <th className="px-3 py-2 text-right">Cumulative (CAD)</th>
            <th className="px-3 py-2 text-right">Marginal Rate</th>
            <th className="px-3 py-2 text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => {
            const isSetAside = m.action === "set_aside";
            const isZero = m.action_amount === 0;
            return (
              <tr
                key={m.month}
                className="border-b border-ag-border/50 hover:bg-ag-card-hover transition-colors"
              >
                <td className="px-3 py-2 font-medium">{m.month_name}</td>
                <td className={`px-3 py-2 text-right font-mono ${
                  m.pnl_usd > 0 ? "text-ag-green" : m.pnl_usd < 0 ? "text-red-400" : "text-ag-text-muted"
                }`}>
                  {fmtUsd(m.pnl_usd)}
                </td>
                <td className={`px-3 py-2 text-right font-mono ${
                  m.pnl_cad > 0 ? "text-ag-green" : m.pnl_cad < 0 ? "text-red-400" : "text-ag-text-muted"
                }`}>
                  {fmtCad(m.pnl_cad)}
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  {fmtCad(m.cumulative_cad)}
                </td>
                <td className="px-3 py-2 text-right text-ag-text-muted">
                  {m.cumulative_cad > 0 ? fmtPct(m.marginal_rate) : "—"}
                </td>
                <td className="px-3 py-2 text-right">
                  {isZero ? (
                    <span className="text-ag-text-muted">—</span>
                  ) : (
                    <span className={`inline-flex items-center gap-1 font-mono font-semibold ${
                      isSetAside ? "text-ag-green" : "text-amber-400"
                    }`}>
                      {isSetAside ? (
                        <ArrowUpRight size={12} />
                      ) : (
                        <ArrowDownLeft size={12} />
                      )}
                      {isSetAside ? "Set aside" : "Move back"} {fmtCad(m.action_amount)}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


/* ── Quarterly table ────────────────────────────────────────── */

function QuarterlyTable({ quarters, fmtCad, fmtUsd }: {
  quarters: TaxWithholdingQuarter[];
  fmtCad: (v: number) => string;
  fmtUsd: (v: number) => string;
}) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {quarters.map((q) => {
        const isSetAside = q.action === "set_aside";
        const isZero = q.action_amount === 0;
        const hasData = q.pnl_usd !== 0;
        return (
          <div
            key={q.quarter}
            className={`bg-ag-dark border rounded-xl p-4 space-y-2 ${
              hasData ? "border-ag-border" : "border-ag-border/50 opacity-60"
            }`}
          >
            <div className="text-xs font-semibold text-ag-text-muted">
              {q.quarter}
            </div>
            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-ag-text-muted">PnL (USD)</span>
                <span className={`font-mono ${
                  q.pnl_usd > 0 ? "text-ag-green" : q.pnl_usd < 0 ? "text-red-400" : ""
                }`}>
                  {fmtUsd(q.pnl_usd)}
                </span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-ag-text-muted">PnL (CAD)</span>
                <span className={`font-mono ${
                  q.pnl_cad > 0 ? "text-ag-green" : q.pnl_cad < 0 ? "text-red-400" : ""
                }`}>
                  {fmtCad(q.pnl_cad)}
                </span>
              </div>
            </div>
            <div className="border-t border-ag-border/50 pt-2">
              {isZero ? (
                <span className="text-ag-text-muted text-xs">No action</span>
              ) : (
                <div className={`flex items-center gap-1 text-xs font-semibold ${
                  isSetAside ? "text-ag-green" : "text-amber-400"
                }`}>
                  {isSetAside ? <ArrowUpRight size={12} /> : <ArrowDownLeft size={12} />}
                  {isSetAside ? "Set aside" : "Move back"} {fmtCad(q.action_amount)}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}


/* ── Summary card helper ────────────────────────────────────── */

function WCard({ icon, label, value, color, sub }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  color?: string;
  sub?: string;
}) {
  const colorCls =
    color === "green" ? "text-ag-green"
    : color === "red" ? "text-red-400"
    : color === "amber" ? "text-amber-400"
    : "text-ag-text";
  return (
    <div className="bg-ag-dark border border-ag-border rounded-lg p-3">
      <div className="flex items-center gap-1.5 text-ag-text-muted mb-1">
        {icon}
        <span className="text-[10px]">{label}</span>
      </div>
      <p className={`text-sm font-semibold ${colorCls}`}>{value}</p>
      {sub && (
        <p className="text-[10px] text-ag-text-muted mt-0.5">{sub}</p>
      )}
    </div>
  );
}
