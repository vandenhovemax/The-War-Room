"use client";
import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import type { CalendarDaySummary, JournalTradeItem, StrategyItem } from "@/lib/api";
import CalendarGrid from "./CalendarGrid";
import TradingDayDetail from "./TradingDayDetail";
import TradeForm from "./TradeForm";
import MonthlyRecap from "./MonthlyRecap";
import {
  FlaskConical, Trash2, ChevronDown, ChevronRight, Eye, EyeOff,
} from "lucide-react";

export default function TradingJournalTab() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth());
  const [summary, setSummary] = useState<CalendarDaySummary[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [dayTrades, setDayTrades] = useState<JournalTradeItem[]>([]);
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [formDate, setFormDate] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Trade management state
  const [showManage, setShowManage] = useState(false);
  const [showTestTrades, setShowTestTrades] = useState(false);
  const [allTrades, setAllTrades] = useState<JournalTradeItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkDateBefore, setBulkDateBefore] = useState("");
  const [bulkLoading, setBulkLoading] = useState(false);
  const [manageLoading, setManageLoading] = useState(false);

  const summaryMap = Object.fromEntries(summary.map((s) => [s.date, s]));

  const loadSummary = useCallback(() => {
    api.journalCalendarSummary(year, month + 1).then(setSummary).catch(() => {});
  }, [year, month]);

  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => { api.strategies().then(setStrategies).catch(() => {}); }, []);

  // Load all trades when manage panel is open
  const loadAllTrades = useCallback(() => {
    if (!showManage) return;
    setManageLoading(true);
    api.journalList({ limit: 500, include_test: showTestTrades })
      .then(setAllTrades)
      .catch(() => setAllTrades([]))
      .finally(() => setManageLoading(false));
  }, [showManage, showTestTrades]);

  useEffect(() => { loadAllTrades(); }, [loadAllTrades]);

  const handleMonthChange = (y: number, m: number) => {
    setYear(y); setMonth(m); setSelectedDate(null); setDayTrades([]);
  };

  const handleDayClick = (date: string) => {
    setSelectedDate(date);
    setLoading(true);
    api.journalList({ trade_date: date, limit: 100, include_test: showTestTrades })
      .then(setDayTrades)
      .catch(() => setDayTrades([]))
      .finally(() => setLoading(false));
  };

  const handleCreated = () => { setShowForm(false); setFormDate(null); loadSummary(); if (selectedDate) handleDayClick(selectedDate); };

  const handleAddTradeOnDay = (date: string) => {
    setFormDate(date);
    setShowForm(true);
  };

  const refreshAll = () => {
    loadSummary();
    loadAllTrades();
    if (selectedDate) handleDayClick(selectedDate);
  };

  // Bulk operations
  const handleBulkMarkTest = async (isTest: boolean) => {
    if (selectedIds.size === 0) return;
    setBulkLoading(true);
    await api.journalBulkMarkTest({ trade_ids: Array.from(selectedIds), is_test: isTest });
    setSelectedIds(new Set());
    setBulkLoading(false);
    refreshAll();
  };

  const handleBulkDelete = async () => {
    if (selectedIds.size === 0) return;
    if (!confirm(`Delete ${selectedIds.size} trades permanently?`)) return;
    setBulkLoading(true);
    await api.journalBulkDelete(Array.from(selectedIds));
    setSelectedIds(new Set());
    setBulkLoading(false);
    refreshAll();
  };

  const handleDateRangeMark = async () => {
    if (!bulkDateBefore) return;
    if (!confirm(`Mark ALL trades before ${bulkDateBefore} as Testing?`)) return;
    setBulkLoading(true);
    await api.journalBulkMarkTest({ date_before: bulkDateBefore, is_test: true });
    setBulkDateBefore("");
    setBulkLoading(false);
    refreshAll();
  };

  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === allTrades.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(allTrades.map((t) => t.id)));
    }
  };

  const renderCell = (date: string) => {
    const day = summaryMap[date];
    if (!day) return null;
    return (
      <div className="space-y-0.5">
        <span className="text-[10px] text-ag-text-muted">{day.trade_count} trade{day.trade_count !== 1 ? "s" : ""}</span>
        <span className={`block text-xs font-semibold ${day.pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
          {day.pnl >= 0 ? "+" : ""}${day.pnl.toFixed(2)}
        </span>
      </div>
    );
  };

  const testCount = allTrades.filter((t) => t.is_test).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Trading Journal</h2>
          <p className="text-ag-text-muted text-sm mt-0.5">Calendar view of your trades</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowManage(!showManage)}
            className={`px-3 py-2 rounded-lg text-sm font-medium flex items-center gap-1.5 transition-colors ${showManage ? "bg-orange-500/15 text-orange-400" : "bg-ag-dark border border-ag-border text-ag-text-muted hover:text-ag-text"}`}>
            <FlaskConical size={14} />
            Manage Trades
          </button>
          <button onClick={() => { setShowForm(!showForm); if (showForm) setFormDate(null); }}
            className="px-4 py-2 bg-ag-accent text-white rounded-lg text-sm font-medium hover:brightness-110">
            {showForm ? "Cancel" : "+ Add Trade"}
          </button>
        </div>
      </div>

      {showForm && <TradeForm onCreated={handleCreated} onCancel={() => { setShowForm(false); setFormDate(null); }} initialDate={formDate ?? undefined} />}

      {/* Trade Management Panel */}
      {showManage && (
        <div className="bg-ag-card border border-orange-500/30 rounded-xl p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <FlaskConical size={16} className="text-orange-400" />
              Trade Management
              {testCount > 0 && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-orange-500/15 text-orange-400">
                  {testCount} test trade{testCount !== 1 ? "s" : ""}
                </span>
              )}
            </h3>
            <button
              onClick={() => setShowTestTrades(!showTestTrades)}
              className="flex items-center gap-1.5 text-xs text-ag-text-muted hover:text-ag-text transition-colors"
            >
              {showTestTrades ? <Eye size={14} /> : <EyeOff size={14} />}
              {showTestTrades ? "Showing test trades" : "Test trades hidden"}
            </button>
          </div>

          {/* Date range bulk marker */}
          <div className="flex items-center gap-3 p-3 bg-ag-dark/50 rounded-lg border border-ag-border/30">
            <span className="text-xs text-ag-text-muted whitespace-nowrap">Mark all trades before:</span>
            <input
              type="date"
              value={bulkDateBefore}
              onChange={(e) => setBulkDateBefore(e.target.value)}
              className="bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs flex-1 max-w-[180px]"
            />
            <button
              onClick={handleDateRangeMark}
              disabled={!bulkDateBefore || bulkLoading}
              className="px-3 py-1 bg-orange-500/15 text-orange-400 rounded text-xs font-medium hover:bg-orange-500/25 disabled:opacity-50"
            >
              Mark as Testing
            </button>
          </div>

          {/* Bulk actions bar */}
          {selectedIds.size > 0 && (
            <div className="flex items-center gap-3 p-2 bg-ag-accent/5 rounded-lg border border-ag-accent/20">
              <span className="text-xs font-medium text-ag-accent">
                {selectedIds.size} selected
              </span>
              <button onClick={() => handleBulkMarkTest(true)} disabled={bulkLoading}
                className="px-2 py-1 bg-orange-500/15 text-orange-400 rounded text-xs hover:bg-orange-500/25 disabled:opacity-50">
                Mark Testing
              </button>
              <button onClick={() => handleBulkMarkTest(false)} disabled={bulkLoading}
                className="px-2 py-1 bg-ag-green/15 text-ag-green rounded text-xs hover:bg-ag-green/25 disabled:opacity-50">
                Unmark Testing
              </button>
              <button onClick={handleBulkDelete} disabled={bulkLoading}
                className="px-2 py-1 bg-ag-red/15 text-ag-red rounded text-xs hover:bg-ag-red/25 disabled:opacity-50 flex items-center gap-1">
                <Trash2 size={12} /> Delete
              </button>
              <button onClick={() => setSelectedIds(new Set())}
                className="px-2 py-1 text-ag-text-muted text-xs hover:text-ag-text ml-auto">
                Clear
              </button>
            </div>
          )}

          {/* Trade list with checkboxes */}
          <div className="max-h-[400px] overflow-y-auto">
            {manageLoading ? (
              <p className="text-xs text-ag-text-muted text-center py-4">Loading trades...</p>
            ) : allTrades.length === 0 ? (
              <p className="text-xs text-ag-text-muted text-center py-4">No trades found</p>
            ) : (
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-ag-card z-10">
                  <tr className="text-[10px] text-ag-text-muted uppercase border-b border-ag-border/30">
                    <th className="py-1.5 px-2 text-left">
                      <input type="checkbox"
                        checked={selectedIds.size === allTrades.length && allTrades.length > 0}
                        onChange={toggleSelectAll}
                        className="rounded border-ag-border" />
                    </th>
                    <th className="py-1.5 px-2 text-left">Symbol</th>
                    <th className="py-1.5 px-2 text-left">Side</th>
                    <th className="py-1.5 px-2 text-left">Status</th>
                    <th className="py-1.5 px-2 text-right">P&L</th>
                    <th className="py-1.5 px-2 text-left">Source</th>
                    <th className="py-1.5 px-2 text-left">Date</th>
                    <th className="py-1.5 px-2 text-left">Type</th>
                  </tr>
                </thead>
                <tbody>
                  {allTrades.map((t) => (
                    <tr key={t.id}
                      className={`border-b border-ag-border/10 hover:bg-ag-dark/30 ${t.is_test ? "opacity-60" : ""}`}>
                      <td className="py-1.5 px-2">
                        <input type="checkbox"
                          checked={selectedIds.has(t.id)}
                          onChange={() => toggleSelect(t.id)}
                          className="rounded border-ag-border" />
                      </td>
                      <td className="py-1.5 px-2 font-medium">{t.symbol.replace("USDT", "")}</td>
                      <td className="py-1.5 px-2">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${t.side === "Long" ? "bg-ag-green/15 text-ag-green" : "bg-ag-red/15 text-ag-red"}`}>
                          {t.side}
                        </span>
                      </td>
                      <td className="py-1.5 px-2">
                        <span className={`text-[10px] ${t.status === "open" ? "text-yellow-400" : "text-ag-text-muted"}`}>
                          {t.status}
                        </span>
                      </td>
                      <td className={`py-1.5 px-2 text-right font-mono ${t.net_profit != null ? (t.net_profit >= 0 ? "text-ag-green" : "text-ag-red") : "text-ag-text-muted"}`}>
                        {t.net_profit != null ? `$${t.net_profit.toFixed(2)}` : "---"}
                      </td>
                      <td className="py-1.5 px-2 text-ag-text-muted capitalize">{t.source}</td>
                      <td className="py-1.5 px-2 text-ag-text-muted">
                        {t.entry_time ? new Date(t.entry_time).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—"}
                      </td>
                      <td className="py-1.5 px-2">
                        {t.is_test ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-500/15 text-orange-400">Test</span>
                        ) : (
                          <span className="text-[10px] text-ag-text-muted">Real</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Calendar */}
      <CalendarGrid year={year} month={month}
        onMonthChange={handleMonthChange} onDayClick={handleDayClick}
        onAddTrade={handleAddTradeOnDay}
        renderCell={renderCell} selectedDate={selectedDate ?? undefined} />

      {/* Monthly recap */}
      <MonthlyRecap year={year} month={month} />

      {/* Day detail */}
      {selectedDate && (
        <TradingDayDetail date={selectedDate} trades={dayTrades}
          strategies={strategies} loading={loading} onUpdated={() => {
            loadSummary(); handleDayClick(selectedDate);
          }} />
      )}
    </div>
  );
}
