"use client";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import type { LiveEngineStatus } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

interface Props {
  status: LiveEngineStatus | null;
  onRefresh: () => void;
}

export default function EngineControls({ status, onRefresh }: Props) {
  const [acting, setActing] = useState(false);

  // Paper engine (port 8056) — independent state from the live engine
  const [paperStatus, setPaperStatus] = useState<LiveEngineStatus | null>(null);
  const [paperActing, setPaperActing] = useState(false);
  const [paperReachable, setPaperReachable] = useState<boolean | null>(null);

  const refreshPaperStatus = async () => {
    try {
      const s = await api.paperStatus();
      setPaperStatus(s);
      setPaperReachable(true);
    } catch {
      setPaperStatus(null);
      setPaperReachable(false);
    }
  };

  useEffect(() => {
    refreshPaperStatus();
    const id = setInterval(refreshPaperStatus, 5000);
    return () => clearInterval(id);
  }, []);

  const handleStart = async () => {
    setActing(true);
    try {
      await api.liveStart();
      onRefresh();
    } catch (e) {
      alert("Failed to start engine. Check API keys and settings.");
    } finally {
      setActing(false);
    }
  };

  const handleStop = async () => {
    if (!confirm("Stop the engine? Pending entries will be cancelled. Exit orders (SL/TP) remain active.")) return;
    setActing(true);
    try {
      await api.liveStop();
      onRefresh();
    } finally {
      setActing(false);
    }
  };

  const handleEmergency = async () => {
    if (!confirm("EMERGENCY STOP: This will close ALL positions at market and shut down the engine. Continue?")) return;
    setActing(true);
    try {
      await api.liveEmergencyStop();
      onRefresh();
    } finally {
      setActing(false);
    }
  };

  const handlePaperStart = async () => {
    setPaperActing(true);
    try {
      await api.paperStart();
      await refreshPaperStatus();
    } catch (e) {
      alert("Failed to start paper engine. Make sure the [WAR ROOM - PAPER BACKEND] window is open.");
    } finally {
      setPaperActing(false);
    }
  };

  const handlePaperStop = async () => {
    if (!confirm("Stop the paper engine? Paper positions remain — they'll just stop receiving updates.")) return;
    setPaperActing(true);
    try {
      await api.paperStop();
      await refreshPaperStatus();
    } finally {
      setPaperActing(false);
    }
  };

  const running = status?.running ?? false;
  const wsPublic = status?.websocket?.public ?? false;
  const wsPrivate = status?.websocket?.private ?? false;

  return (
    <div className="bg-ag-card border border-ag-border rounded-lg p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          {/* Status indicator */}
          <div className="flex items-center gap-2">
            <div className={`w-3 h-3 rounded-full ${running ? "bg-ag-green animate-pulse" : "bg-ag-red"}`} />
            <span className="text-sm font-medium">{running ? "Running" : "Stopped"}</span>
            <HintTip text="Engine status. When running, the engine monitors candle feeds and executes strategy signals automatically." />
          </div>

          {/* Connection badges */}
          {running && (
            <div className="flex gap-2">
              <Badge label="WS Public" ok={wsPublic} />
              <Badge label="WS Private" ok={wsPrivate} />
              <Badge label={status?.testnet ? "Testnet" : "Mainnet"} ok={true}
                color={status?.testnet ? "bg-yellow-500/15 text-yellow-400" : "bg-ag-green/15 text-ag-green"} />
              {status?.paper_trading && (
                <Badge label="PAPER" ok={true}
                  color="bg-purple-500/15 text-purple-400" />
              )}
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          {!running ? (
            <>
              <button
                onClick={handleStart}
                disabled={acting || !status?.enabled}
                className="px-4 py-1.5 bg-ag-green/20 text-ag-green border border-ag-green/30 rounded-lg text-sm font-medium hover:bg-ag-green/30 disabled:opacity-50"
              >
                {acting ? "Starting..." : "Start Engine"}
              </button>
              <HintTip text="Start the live engine. Connects to Bybit via WebSocket, subscribes to candle feeds, and begins executing strategy signals." />
            </>
          ) : (
            <>
              <button
                onClick={handleStop}
                disabled={acting}
                className="px-4 py-1.5 bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 rounded-lg text-sm font-medium hover:bg-yellow-500/30 disabled:opacity-50"
              >
                Stop
              </button>
              <HintTip text="Graceful stop. Cancels pending entry orders but keeps exit orders (SL/TP/TSL) active on the exchange. Open positions are preserved." />
              <button
                onClick={handleEmergency}
                disabled={acting}
                className="px-4 py-1.5 bg-ag-red/20 text-ag-red border border-ag-red/30 rounded-lg text-sm font-medium hover:bg-ag-red/30 disabled:opacity-50"
              >
                Emergency Stop
              </button>
              <HintTip text="EMERGENCY: Immediately closes ALL open positions at market price, cancels all orders, and shuts down the engine. Use only in emergencies." />
            </>
          )}
        </div>
      </div>

      {!status?.enabled && (
        <p className="text-xs text-ag-red mt-2">
          Engine is disabled. Set LIVE_ENGINE_ENABLED=true in .env to enable.
        </p>
      )}

      {/* ── Paper Engine controls (dual-backend, port 8056) ───────── */}
      <div className="mt-3 pt-3 border-t border-ag-border/60">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div className={`w-3 h-3 rounded-full ${
                paperReachable === false
                  ? "bg-ag-text-muted/40"
                  : paperStatus?.running ? "bg-purple-400 animate-pulse" : "bg-ag-red"
              }`} />
              <span className="text-sm font-medium text-purple-300">Paper Engine</span>
              <HintTip text="The paper engine runs in a separate backend on port 8056. It simulates fills for paper-type portfolios while the live engine above trades real portfolios in parallel. Both share the same DB; the is_paper migration keeps state isolated." />
              {paperReachable === false && (
                <span className="text-[10px] text-ag-text-muted">
                  paper backend not reachable
                </span>
              )}
            </div>
            {paperReachable && paperStatus?.running && (
              <Badge label="PAPER" ok={true} color="bg-purple-500/15 text-purple-400" />
            )}
          </div>
          <div className="flex items-center gap-2">
            {paperReachable === false ? (
              <span className="text-[11px] text-ag-text-muted">
                Open [WAR ROOM - PAPER BACKEND] window to use this
              </span>
            ) : !paperStatus?.running ? (
              <button
                onClick={handlePaperStart}
                disabled={paperActing}
                className="px-3 py-1 bg-purple-500/20 text-purple-300 border border-purple-500/30 rounded-lg text-xs font-medium hover:bg-purple-500/30 disabled:opacity-50"
              >
                {paperActing ? "Starting..." : "Start Paper Engine"}
              </button>
            ) : (
              <button
                onClick={handlePaperStop}
                disabled={paperActing}
                className="px-3 py-1 bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 rounded-lg text-xs font-medium hover:bg-yellow-500/30 disabled:opacity-50"
              >
                {paperActing ? "Stopping..." : "Stop Paper Engine"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Badge({ label, ok, color }: { label: string; ok: boolean; color?: string }) {
  const cls = color ?? (ok ? "bg-ag-green/15 text-ag-green" : "bg-ag-red/15 text-ag-red");
  return (
    <span className={`text-[10px] px-2 py-0.5 rounded ${cls}`}>
      {label}
    </span>
  );
}
