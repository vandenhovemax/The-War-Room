import { api } from "@/lib/api";
import type {
  BatchProgress,
  BatchResultSummary,
  DownloadProgress,
  BatchBacktestRunItem,
} from "@/lib/api";

/**
 * Poll batch backtest progress for all given batch IDs every 2 seconds.
 * Returns a cleanup function that clears the interval.
 */
export function pollBatchProgress(
  batchIds: number[],
  onProgress: (map: Record<number, BatchProgress>) => void,
  onComplete: (results: BatchResultSummary[]) => void,
  onError: (msg: string) => void,
  onCancelled?: () => void,
): () => void {
  const id = setInterval(async () => {
    try {
      const updates: Record<number, BatchProgress> = {};
      let allDone = true;
      let anyError = false;
      let anyCancelled = false;
      for (const bid of batchIds) {
        const prog = await api.batchProgress(bid);
        updates[bid] = prog;
        if (prog.status === "cancelled") anyCancelled = true;
        if (prog.status !== "completed" && prog.status !== "error" && prog.status !== "cancelled") allDone = false;
        if (prog.status === "error") anyError = true;
      }
      onProgress(updates);
      if (allDone || anyCancelled) {
        clearInterval(id);
        if (anyCancelled) {
          onCancelled?.();
        } else if (anyError) {
          onError("One or more batches failed");
        } else {
          const details = await Promise.all(
            batchIds.map((bid) => api.batchRunDetail(bid)),
          );
          onComplete(details);
        }
      }
    } catch {
      // Network error — retry on next tick
    }
  }, 2000);
  return () => clearInterval(id);
}

/**
 * Poll download progress every 1.5 seconds.
 * Calls onProgress with the full progress map.
 * Calls onComplete when all downloads are finished (no "queued" or "downloading").
 * Returns a cleanup function.
 */
export function pollDownloadProgress(
  onProgress: (progress: Record<string, DownloadProgress>) => void,
  onComplete: () => void,
): () => void {
  const id = setInterval(async () => {
    try {
      const p = await api.downloadProgress();
      onProgress(p);
      const hasActive = Object.values(p).some(
        (v) => v.status === "downloading" || v.status === "queued",
      );
      if (!hasActive) {
        clearInterval(id);
        onComplete();
      }
    } catch {
      // Network error — retry on next tick
    }
  }, 1500);
  return () => clearInterval(id);
}

/**
 * Check for any currently-running batch backtests on the backend.
 * Used for auto-reconnect on page load / browser refresh.
 */
export async function checkRunningBatches(): Promise<BatchBacktestRunItem[]> {
  const all = await api.batchRuns();
  return all.filter((b) => b.status === "running" || b.status === "queued");
}
