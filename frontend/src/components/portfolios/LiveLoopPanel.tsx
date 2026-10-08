"use client";
import { useEffect, useRef, useState } from "react";
import { AlertCircle, Clock, Download, Hammer, PlayCircle, RefreshCw, ShieldCheck } from "lucide-react";
import { api, type RebuildProgress } from "@/lib/api";
import HintTip from "@/components/common/HintTip";

type Status = Awaited<ReturnType<typeof api.getLiveLoopStatus>>;
type StaleInfo = Awaited<ReturnType<typeof api.getStaleBatches>>;

export default function LiveLoopPanel() {
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<null | "catchup" | "canary" | "status" | "full" | "rebuild">(null);
  const [error, setError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<string | null>(null);

  const [stale, setStale] = useState<StaleInfo | null>(null);
  const [rebuild, setRebuild] = useState<RebuildProgress | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStatus = async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, st] = await Promise.all([
        api.getLiveLoopStatus(),
        api.getStaleBatches(),
      ]);
      setStatus(s);
      setStale(st);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const pollRebuild = async () => {
    try {
      const p = await api.getRebuildStatus();
      setRebuild(p);
      if (p.status !== "running" && pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
        // Refresh stale list once rebuild completes.
        try {
          const st = await api.getStaleBatches();
          setStale(st);
        } catch { /* non-fatal */ }
      }
    } catch { /* non-fatal — keep polling */ }
  };

  const startRebuild = async () => {
    if (!stale || stale.batch_count === 0) return;
    const msg =
      `Rebuild ${stale.run_count} stale runs across ${stale.batch_count} batches?\n\n` +
      `This replays each backtest end-to-end to capture strategy_state, so the ` +
      `nightly Live Loop can touch these batches forever after. ` +
      `Your batch_ids and portfolio bindings are preserved. ` +
      `Can take 10-30+ minutes depending on total symbols.`;
    if (!confirm(msg)) return;
    setBusy("rebuild");
    setError(null);
    try {
      const resp = await api.startRebuildStaleBatches();
      if (!resp.started && resp.reason === "already_running") {
        // A previous rebuild is already running — just pick up polling.
      }
      setRebuild(resp.progress);
      if (!pollRef.current) {
        pollRef.current = setInterval(pollRebuild, 3000);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    loadStatus();
    // If a rebuild is already running server-side (e.g. page reload), pick it up.
    api.getRebuildStatus().then((p) => {
      setRebuild(p);
      if (p.status === "running" && !pollRef.current) {
        pollRef.current = setInterval(pollRebuild, 3000);
      }
    }).catch(() => { /* non-fatal */ });
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runCatchUp = async (
    mode: "catchup" | "canary" | "full",
  ) => {
    const includeCanary = mode === "canary";
    const downloadCandles = mode === "full";
    const msg =
      mode === "full"
        ? "Download all missing candles from Bybit AND run catch-up (L0 → L1 → L2 → L3)? This downloads deltas (append-only, no re-download), then extends every batch to the latest candle. Can take several minutes for a multi-day gap."
        : includeCanary
          ? "Run Live Loop catch-up + parity canary now? This can take several minutes."
          : "Run Live Loop catch-up now (L1 → L2 → L3)? This extends every batch's trade store up to the latest candle, refreshes hybrid baskets, and checks OOS boundaries.";
    if (!confirm(msg)) return;
    setBusy(mode);
    setError(null);
    try {
      const resp = await api.runLiveLoopCatchUp(includeCanary, downloadCandles);
      const failed = resp.layers.filter((l) => !l.ok);
      if (failed.length === 0) {
        setLastRun(`✓ ${resp.layers.length} layers OK — ${new Date().toLocaleTimeString()}`);
      } else {
        setLastRun(`⚠ ${failed.length} layer(s) failed: ${failed.map((l) => l.layer).join(", ")}`);
      }
      await loadStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="bg-ag-dark border border-ag-border rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Clock size={16} className="text-ag-accent" />
          <h3 className="text-sm font-semibold text-ag-text">Live Loop</h3>
          <HintTip text="The nightly pipeline that keeps batches + portfolios in sync with live trades. L1 extends batches (00:15 NYT), L2 refreshes hybrid baskets (00:30 NYT), L3 checks OOS boundaries and files rebalance proposals (01:00 NYT), L4 is the monthly parity audit (1st 02:00 NYT). Run catch-up if the backend was offline for a while." />
        </div>
        <button
          onClick={loadStatus}
          disabled={loading}
          className="flex items-center gap-1 bg-ag-dark border border-ag-border rounded px-2 py-1 text-xs text-ag-text hover:bg-ag-border"
          title="Refresh status"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-ag-red/10 border border-ag-red/30 rounded p-2 text-xs text-ag-red">
          <AlertCircle size={14} />
          {error}
        </div>
      )}

      {status && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          <Stat label="Active batches" value={status.active_batches} />
          <Stat label="Live portfolios" value={status.active_live_portfolios} />
          <Stat label="Hybrid portfolios" value={status.hybrid_portfolios} />
          <Stat
            label="Pending proposals"
            value={status.pending_proposals}
            highlight={status.pending_proposals > 0}
          />
        </div>
      )}

      {status?.last_canary && (
        <div className="text-xs text-ag-text-muted">
          Last parity canary: {status.last_canary.run_date} —
          {" "}{status.last_canary.total_combos} combos,
          {" "}
          <span className={status.last_canary.combos_with_diff > 0 ? "text-ag-red" : "text-ag-green"}>
            {status.last_canary.combos_with_diff} divergences
          </span>
        </div>
      )}

      {(stale && stale.batch_count > 0) || (rebuild && rebuild.status !== "idle") ? (
        <div className="bg-ag-yellow/5 border border-ag-yellow/30 rounded p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Hammer size={14} className="text-ag-yellow" />
              <span className="text-xs font-semibold text-ag-yellow">
                Stale batches (pre-Live-Loop)
              </span>
              <HintTip text="Batches created before the Live Loop existed don't have strategy_state captured on their runs, so the nightly L1 skips them. Clicking Rebuild re-runs each backtest end-to-end (same batch_id, same run_ids — portfolio bindings are preserved) to capture that state. After a one-off rebuild, these batches are picked up by L1 every night forever." />
            </div>
            {stale && (
              <span className="text-xs text-ag-text-muted">
                {stale.run_count} runs across {stale.batch_count} batches
              </span>
            )}
          </div>

          {rebuild && rebuild.status !== "idle" && (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-xs text-ag-text-muted">
                <span>
                  {rebuild.status === "running" && (
                    rebuild.phase === "downloading"
                      ? "⬇ Downloading new candles from Bybit…"
                      : rebuild.phase === "loading_candles"
                        ? `⚡ Preloading candles (${rebuild.candles_cached} cached)…`
                        : `Rebuilding: ${rebuild.current || "…"}`
                  )}
                  {rebuild.status === "completed" && "✓ Rebuild complete"}
                  {rebuild.status === "error" && `⚠ Rebuild error: ${rebuild.current}`}
                </span>
                <span>
                  {rebuild.completed_runs + rebuild.failed_runs}
                  {" / "}
                  {rebuild.total_runs}
                  {rebuild.failed_runs > 0 && (
                    <span className="text-ag-red"> ({rebuild.failed_runs} failed)</span>
                  )}
                </span>
              </div>
              <div className="w-full h-1.5 bg-ag-dark border border-ag-border rounded overflow-hidden">
                <div
                  className={
                    rebuild.status === "error"
                      ? "h-full bg-ag-red"
                      : rebuild.status === "completed"
                        ? "h-full bg-ag-green"
                        : "h-full bg-ag-yellow"
                  }
                  style={{
                    width: rebuild.total_runs > 0
                      ? `${Math.min(100, Math.round(((rebuild.completed_runs + rebuild.failed_runs) / rebuild.total_runs) * 100))}%`
                      : "0%",
                  }}
                />
              </div>
            </div>
          )}

          {stale && stale.batch_count > 0 && (!rebuild || rebuild.status !== "running") && (
            <button
              onClick={startRebuild}
              disabled={busy !== null}
              className="flex items-center gap-1 bg-ag-yellow/15 border border-ag-yellow/40 text-ag-yellow px-3 py-1.5 rounded text-xs hover:bg-ag-yellow/25 disabled:opacity-50"
            >
              <Hammer size={13} />
              Rebuild All Stale Batches
            </button>
          )}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2 pt-1">
        <button
          onClick={() => runCatchUp("full")}
          disabled={busy !== null}
          title="Downloads missing candles from Bybit (append-only), then replays every batch to the latest candle. Use after a multi-day gap."
          className="flex items-center gap-1 bg-ag-green/15 border border-ag-green/40 text-ag-green px-3 py-1.5 rounded text-xs hover:bg-ag-green/25 disabled:opacity-50"
        >
          <Download size={13} />
          {busy === "full" ? "Downloading + catching up…" : "Sync Candles + Catch-Up (L0 → L3)"}
        </button>
        <button
          onClick={() => runCatchUp("catchup")}
          disabled={busy !== null}
          title="Replays batches over candles already in the DB. Use when candle_sync.py is already running."
          className="flex items-center gap-1 bg-ag-accent/15 border border-ag-accent/40 text-ag-accent px-3 py-1.5 rounded text-xs hover:bg-ag-accent/25 disabled:opacity-50"
        >
          <PlayCircle size={13} />
          {busy === "catchup" ? "Running catch-up…" : "Catch-Up Only (L1 → L2 → L3)"}
        </button>
        <button
          onClick={() => runCatchUp("canary")}
          disabled={busy !== null}
          className="flex items-center gap-1 bg-ag-dark border border-ag-border text-ag-text-muted px-3 py-1.5 rounded text-xs hover:bg-ag-border disabled:opacity-50"
        >
          <ShieldCheck size={13} />
          {busy === "canary" ? "Running catch-up + canary…" : "+ Parity Canary (L4)"}
        </button>
        {lastRun && (
          <span className="text-xs text-ag-text-muted self-center">{lastRun}</span>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, highlight }: { label: string; value: number; highlight?: boolean }) {
  return (
    <div className="bg-ag-dark border border-ag-border rounded p-2">
      <div className="text-ag-text-muted">{label}</div>
      <div className={`text-lg font-semibold ${highlight ? "text-ag-yellow" : "text-ag-text"}`}>
        {value}
      </div>
    </div>
  );
}
