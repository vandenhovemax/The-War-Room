"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Shield, Zap, Sun, Moon, Bell, ChevronDown, Square, OctagonX, Play } from "lucide-react";
import { useTheme } from "@/components/ThemeProvider";
import { api } from "@/lib/api";
import type { LiveSignal, LiveTrade, LivePosition, LiveEngineStatus } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { useTaskManager } from "@/contexts/TaskManager";
import TaskStatusIndicator from "@/components/ui/TaskStatusIndicator";

const pageTitles: Record<string, string> = {
  "/": "Dashboard",
  "/data": "Data Collection",
  "/strategies": "Strategies",
  "/backtest": "Backtester",
  "/analysis": "Analysis",
  "/optimization": "Optimization",
  "/portfolios": "Portfolios",
  "/journal": "Trade Journal",
  "/trading": "Live Trading",
  "/settings": "Settings",
};

/** Format a UTC date string to New York time — matches LiveSignalsTable format. */
function toNYT(utc: string): string {
  try {
    return new Date(utc).toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return ""; }
}

/** Compute delay between two ISO timestamps as "Xm Ys". */
function delayStr(from: string, to: string): string {
  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (ms < 0) return "";
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function LiveClock() {
  const [time, setTime] = useState("");
  useEffect(() => {
    const fmt = () =>
      new Date().toLocaleTimeString("en-US", {
        timeZone: "America/New_York",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      });
    setTime(fmt());
    const id = setInterval(() => setTime(fmt()), 1000);
    return () => clearInterval(id);
  }, []);
  if (!time) return null;
  return (
    <span className="font-mono text-sm text-ag-text-muted tabular-nums" title="New York Time">
      {time}
    </span>
  );
}

function SignalBanner() {
  const [signals, setSignals] = useState<LiveSignal[]>([]);
  const [positions, setPositions] = useState<LivePosition[]>([]);
  const [trades, setTrades] = useState<LiveTrade[]>([]);
  const [open, setOpen] = useState(false);
  const dropRef = useRef<HTMLDivElement>(null);

  // Poll signals + open positions
  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const [sigs, pos] = await Promise.all([
          api.liveSignals({ limit: 3 }),
          api.livePositions(),
        ]);
        if (mounted) {
          if (sigs.length > 0) setSignals(sigs.slice(0, 3));
          setPositions(pos.filter((p) => p.quantity > 0));
        }
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 10_000);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  // Fetch trades when dropdown opens
  useEffect(() => {
    if (!open) return;
    let mounted = true;
    api.liveTrades(50)
      .then((t) => { if (mounted) setTrades(t); })
      .catch(() => {});
    return () => { mounted = false; };
  }, [open]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  if (signals.length === 0 && positions.length === 0) return null;

  return (
    <div ref={dropRef}>
      <div className="w-full bg-ag-darker border-b border-ag-accent/20 px-6 py-1 flex items-center gap-4 overflow-x-auto">
        {/* Bell + dropdown toggle */}
        <button
          onClick={() => setOpen(!open)}
          className="flex items-center gap-0.5 text-ag-accent shrink-0 hover:text-ag-accent/80 transition-colors"
          title="Show recent trades"
        >
          <Bell size={12} />
          <ChevronDown size={10} className={`transition-transform ${open ? "rotate-180" : ""}`} />
        </button>

        {/* Open positions */}
        {positions.map((p) => {
          const pnl = p.unrealized_pnl;
          return (
            <span key={p.id} className="flex items-center gap-1.5 text-[11px] whitespace-nowrap border-r border-ag-border/40 pr-4">
              <span className="text-[9px] px-1 py-0.5 rounded bg-ag-accent/15 text-ag-accent font-semibold">OPEN</span>
              <span className={`font-semibold ${p.side === "Buy" ? "text-ag-green" : "text-ag-red"}`}>
                {p.side === "Buy" ? "LONG" : "SHORT"}
              </span>
              <span className="text-ag-text">{p.symbol}</span>
              <span className="text-ag-text-muted">@ {p.entry_price.toFixed(2)}</span>
              <span className={`font-semibold tabular-nums ${pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
              </span>
            </span>
          );
        })}

        {/* Signal chips — use bar_timestamp (candle time) for display */}
        {signals.map((s) => {
          const sigTime = s.bar_timestamp || s.created_at;
          return (
            <span key={s.id} className="flex items-center gap-1.5 text-[11px] whitespace-nowrap">
              <span className={`font-semibold ${s.direction === "long" ? "text-ag-green" : "text-ag-red"}`}>
                {s.action.toUpperCase()}
              </span>
              <span className="text-ag-text">{s.symbol}</span>
              <span className="text-ag-text-muted">@ {s.close_price.toFixed(2)}</span>
              {sigTime && (
                <span className="text-ag-text-muted/60 text-[10px]">{toNYT(sigTime)}</span>
              )}
              {s.filled_at && sigTime && (
                <>
                  <span className="text-ag-accent text-[10px]">{toNYT(s.filled_at)}</span>
                  <span className="text-ag-yellow text-[10px] font-mono">+{delayStr(sigTime, s.filled_at)}</span>
                </>
              )}
            </span>
          );
        })}
      </div>

      {/* Dropdown — last 50 trades (outside the overflow container) */}
      {open && (
        <div className="absolute left-0 right-0 z-50 max-h-[420px] overflow-y-auto bg-ag-darker border border-ag-border shadow-xl">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-ag-darker border-b border-ag-border">
              <tr className="text-ag-text-muted text-left">
                <th className="py-1.5 px-3">Time</th>
                <th className="py-1.5 px-2">Symbol</th>
                <th className="py-1.5 px-2">Side</th>
                <th className="py-1.5 px-2 text-right">Entry</th>
                <th className="py-1.5 px-2 text-right">Exit</th>
                <th className="py-1.5 px-2 text-right">Qty</th>
                <th className="py-1.5 px-2 text-right">PnL</th>
                <th className="py-1.5 px-2">Reason</th>
              </tr>
            </thead>
            <tbody>
              {trades.length === 0 ? (
                <tr><td colSpan={8} className="py-4 text-center text-ag-text-muted">No trades yet</td></tr>
              ) : trades.map((t) => {
                const pnl = t.realized_pnl - t.commission;
                return (
                  <tr key={t.id} className="border-b border-ag-border/30 hover:bg-ag-card-hover">
                    <td className="py-1 px-3 text-ag-text-muted whitespace-nowrap">
                      {t.closed_at ? toNYT(t.closed_at) : t.opened_at ? toNYT(t.opened_at) : "—"}
                    </td>
                    <td className="py-1 px-2 text-ag-text font-medium">{t.symbol}</td>
                    <td className="py-1 px-2">
                      <span className={t.side === "Buy" ? "text-ag-green" : "text-ag-red"}>{t.side}</span>
                    </td>
                    <td className="py-1 px-2 text-right tabular-nums">{t.entry_price.toFixed(2)}</td>
                    <td className="py-1 px-2 text-right tabular-nums">{t.exit_price.toFixed(2)}</td>
                    <td className="py-1 px-2 text-right tabular-nums">{t.quantity}</td>
                    <td className={`py-1 px-2 text-right font-semibold tabular-nums ${pnl >= 0 ? "text-ag-green" : "text-ag-red"}`}>
                      {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
                    </td>
                    <td className="py-1 px-2 text-ag-text-muted">{t.close_reason || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function QuickEngineControls() {
  const { addToast } = useTaskManager();
  const [status, setStatus] = useState<LiveEngineStatus | null>(null);
  const [acting, setActing] = useState(false);

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const s = await api.liveStatus();
        if (mounted) setStatus(s);
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 10_000);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  if (!status) return null;

  const running = status.running;

  const handleStart = async () => {
    setActing(true);
    try { await api.liveStart(); } catch { alert("Failed to start engine. Check API keys and settings."); }
    setActing(false);
  };

  const handleStop = async () => {
    if (!confirm("Stop the engine? Pending entries will be cancelled. Exit orders (SL/TP) remain active.")) return;
    setActing(true);
    try { await api.liveStop(); } catch { addToast("Failed to stop engine", "error"); }
    setActing(false);
  };

  const handleEmergency = async () => {
    if (!confirm("EMERGENCY STOP: This will close ALL positions at market and shut down the engine. Continue?")) return;
    setActing(true);
    try { await api.liveEmergencyStop(); } catch { addToast("Emergency stop failed — check backend", "error"); }
    setActing(false);
  };

  if (!running) {
    return (
      <button
        onClick={handleStart}
        disabled={acting || !status.enabled}
        className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded bg-ag-green/20 text-ag-green border border-ag-green/30 hover:bg-ag-green/30 disabled:opacity-50"
        title="Start the live engine"
      >
        <Play size={10} /> Start
      </button>
    );
  }

  return (
    <>
      <button
        onClick={handleStop}
        disabled={acting}
        className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 hover:bg-yellow-500/30 disabled:opacity-50"
        title="Graceful stop — cancels pending entries, keeps SL/TP active"
      >
        <Square size={10} /> Stop
      </button>
      <button
        onClick={handleEmergency}
        disabled={acting}
        className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded bg-ag-red/20 text-ag-red border border-ag-red/30 hover:bg-ag-red/30 disabled:opacity-50"
        title="EMERGENCY — closes ALL positions at market and shuts down engine"
      >
        <OctagonX size={10} /> Emergency
      </button>
    </>
  );
}

export default function Header() {
  const pathname = usePathname();
  const title = pageTitles[pathname] ?? "The War Room";
  const { theme, toggleTheme } = useTheme();
  const { data: settings } = useApi(
    useCallback(() => api.settings(), []),
    "header-mode",
  );
  const isPaper = settings?.paper_trading ?? true;

  return (
    <div className="sticky top-0 z-40">
      <SignalBanner />
      <header className="h-14 bg-ag-darker border-b border-ag-border flex items-center justify-between px-6">
        <h2 className="text-lg font-semibold">{title}</h2>
        <div className="flex items-center gap-3">
          <TaskStatusIndicator />
          <QuickEngineControls />
          <LiveClock />
          <button
            onClick={toggleTheme}
            className="p-2 rounded-lg hover:bg-ag-card-hover text-ag-text-muted hover:text-ag-text transition-colors"
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          {isPaper ? (
            <span className="flex items-center gap-1.5 text-xs bg-ag-yellow/15 text-ag-yellow px-2.5 py-1 rounded-full">
              <Shield size={12} />
              PAPER
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs bg-ag-red/15 text-ag-red px-2.5 py-1 rounded-full">
              <Zap size={12} />
              LIVE
            </span>
          )}
        </div>
      </header>
    </div>
  );
}
