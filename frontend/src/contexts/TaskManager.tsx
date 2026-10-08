"use client";
import { createContext, useContext, useState, useCallback, useRef, useEffect } from "react";
import type {
  AnyTask, BatchTask, DownloadTask, OptimizationTask,
  QueuedBatchItem, ToastItem, TaskManagerContextType,
} from "./taskTypes";
import type { DownloadProgress, CorrelationResult, PoolStats } from "@/lib/api";
import { api } from "@/lib/api";
import { pollBatchProgress, pollDownloadProgress, checkRunningBatches } from "./taskPolling";

function uid(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

const TaskManagerContext = createContext<TaskManagerContextType>({
  tasks: [], toasts: [],
  startBatch: () => "", startDownload: () => "",
  updateDownloadProgress: () => {},
  startOptimization: () => "", updateOptimizationProgress: () => {},
  dismissToast: () => {},
  addToast: () => {}, activeTasks: [],
  latestBatchTask: undefined, latestDownloadTask: undefined,
  latestOptimizationTask: () => undefined, clearTask: () => {},
  cancelTask: async () => {}, cancelAllTasks: async () => {},
  queueBatch: () => "", batchQueue: [], removeFromQueue: () => {},
});

export function useTaskManager() {
  return useContext(TaskManagerContext);
}

export default function TaskManagerProvider({ children }: { children: React.ReactNode }) {
  const [tasks, setTasks] = useState<AnyTask[]>([]);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [batchQueue, setBatchQueue] = useState<QueuedBatchItem[]>([]);
  const cleanupRefs = useRef<Map<string, () => void>>(new Map());
  const reconnectedRef = useRef(false);
  const processingQueueRef = useRef(false);
  const submittingRef = useRef(false);

  // Auto-dismiss toasts after 5 seconds
  useEffect(() => {
    if (toasts.length === 0) return;
    const id = setInterval(() => {
      setToasts((prev) => prev.filter((t) => Date.now() - t.createdAt < 5000));
    }, 1000);
    return () => clearInterval(id);
  }, [toasts.length]);

  const addToast = useCallback((message: string, variant: ToastItem["variant"]) => {
    setToasts((prev) => [...prev, { id: uid(), message, variant, createdAt: Date.now() }]);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const updateTask = useCallback((taskId: string, patch: Partial<AnyTask>) => {
    setTasks((prev) => prev.map((t) => (t.id === taskId ? { ...t, ...patch } as AnyTask : t)));
  }, []);

  const clearTask = useCallback((id: string) => {
    const cleanup = cleanupRefs.current.get(id);
    if (cleanup) { cleanup(); cleanupRefs.current.delete(id); }
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // -- Batch --
  const startBatch = useCallback((batchIds: number[], label: string, skippedCount = 0, skippedDataDateCount = 0, prefilteredCount = 0): string => {
    const taskId = uid();
    const task: BatchTask = {
      id: taskId, type: "batch", batchIds, label,
      status: "running", startedAt: Date.now(),
      progressMap: {}, results: null, error: null, skippedCount, skippedDataDateCount, prefilteredCount,
    };
    setTasks((prev) => [...prev, task]);

    const cleanup = pollBatchProgress(
      batchIds,
      (progressMap) => updateTask(taskId, { progressMap }),
      (results) => {
        updateTask(taskId, { status: "completed", results });
        addToast(`${label} completed`, "success");
      },
      (err) => {
        updateTask(taskId, { status: "error", error: err });
        addToast(`${label} failed`, "error");
      },
      () => {
        updateTask(taskId, { status: "cancelled" });
        addToast(`${label} cancelled`, "info");
      },
    );
    cleanupRefs.current.set(taskId, cleanup);
    return taskId;
  }, [addToast, updateTask]);

  // -- Queue processor: starts next queued batch when no batch is running --
  const processQueue = useCallback(() => {
    // Guard MUST be set immediately (not inside a state updater) to prevent
    // double-firing when React triggers the effect multiple times rapidly.
    if (processingQueueRef.current) return;
    processingQueueRef.current = true;

    setBatchQueue((prev) => {
      if (prev.length === 0) {
        processingQueueRef.current = false;
        return prev;
      }
      // Check if any batch is currently running (read from latest tasks)
      setTasks((currentTasks) => {
        const hasRunning = currentTasks.some((t) => t.type === "batch" && t.status === "running");
        if (hasRunning) {
          processingQueueRef.current = false;
          return currentTasks; // Don't start next yet
        }
        const [next, ...rest] = prev;
        if (!next) {
          processingQueueRef.current = false;
          return currentTasks;
        }
        // Start the queued batch
        next.startFn()
          .then(({ batchIds, label, skippedCount, skippedDataDateCount, prefilteredCount }) => {
            startBatch(batchIds, label, skippedCount, skippedDataDateCount ?? 0, prefilteredCount ?? 0);
            addToast(`Queue: started "${label}"`, "info");
          })
          .catch((err) => {
            const msg = err instanceof Error ? err.message : "Failed to start queued batch";
            addToast(`Queue error: ${msg}`, "error");
          })
          .finally(() => { processingQueueRef.current = false; });
        setBatchQueue(rest);
        return currentTasks;
      });
      return prev; // The inner setBatchQueue handles the actual update
    });
  }, [startBatch, addToast]);

  // Watch for batch task completion → process queue
  const prevTasksRef = useRef<AnyTask[]>([]);
  useEffect(() => {
    const prev = prevTasksRef.current;
    prevTasksRef.current = tasks;
    // Check if a batch task just finished (was running, now terminal)
    const justFinished = tasks.some((t) => {
      if (t.type !== "batch") return false;
      if (t.status === "running") return false;
      const old = prev.find((p) => p.id === t.id);
      return old?.status === "running";
    });
    if (justFinished && batchQueue.length > 0) {
      // Small delay to let state settle
      setTimeout(processQueue, 500);
    }
  }, [tasks, batchQueue.length, processQueue]);

  // -- Queue a batch (starts immediately if nothing running, otherwise queues) --
  const queueBatch = useCallback((item: Omit<QueuedBatchItem, "id">): string => {
    const id = uid();
    const hasRunning = tasks.some((t) => t.type === "batch" && t.status === "running");
    if (!hasRunning && batchQueue.length === 0 && !submittingRef.current) {
      // Nothing running and not already submitting — start immediately
      submittingRef.current = true;
      item.startFn()
        .then(({ batchIds, label, skippedCount, skippedDataDateCount, prefilteredCount }) => {
          startBatch(batchIds, label, skippedCount, skippedDataDateCount ?? 0, prefilteredCount ?? 0);
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : "Failed to start batch";
          addToast(msg, "error");
        })
        .finally(() => { submittingRef.current = false; });
    } else if (submittingRef.current) {
      // Another submission is in-flight — queue it (supports multi-config runs)
      setBatchQueue((prev) => {
        const newQueue = [...prev, { id, ...item }];
        addToast(`"${item.label}" queued (position ${newQueue.length})`, "info");
        return newQueue;
      });
    } else {
      // Queue it (intentional: user clicked Run while a batch is already running)
      setBatchQueue((prev) => [...prev, { id, ...item }]);
      const pos = batchQueue.length + 1;
      addToast(`"${item.label}" added to queue (position ${pos})`, "info");
    }
    return id;
  }, [tasks, batchQueue, startBatch, addToast]);

  const removeFromQueue = useCallback((id: string) => {
    setBatchQueue((prev) => {
      const item = prev.find((q) => q.id === id);
      if (item) addToast(`"${item.label}" removed from queue`, "info");
      return prev.filter((q) => q.id !== id);
    });
  }, [addToast]);

  // -- Download --
  const startDownload = useCallback((label: string): string => {
    const taskId = uid();
    const task: DownloadTask = {
      id: taskId, type: "download", label,
      status: "running", startedAt: Date.now(), progress: {}, error: null,
    };
    setTasks((prev) => [...prev, task]);

    const cleanup = pollDownloadProgress(
      (progress) => updateTask(taskId, { progress }),
      () => {
        updateTask(taskId, { status: "completed" });
        addToast(`${label} completed`, "success");
      },
    );
    cleanupRefs.current.set(taskId, cleanup);
    return taskId;
  }, [addToast, updateTask]);

  const updateDownloadProgress = useCallback((progress: Record<string, DownloadProgress>) => {
    setTasks((prev) => {
      const dl = [...prev].reverse().find((t): t is DownloadTask => t.type === "download" && t.status === "running");
      if (!dl) return prev;
      return prev.map((t) => (t.id === dl.id ? { ...t, progress } as DownloadTask : t));
    });
  }, []);

  // -- Optimization --
  const updateOptimizationProgress = useCallback((taskId: string, pct: number, progressLabel: string) => {
    setTasks((prev) => prev.map((t) =>
      t.id === taskId && t.type === "optimization"
        ? { ...t, progressPct: pct, progressLabel } as OptimizationTask
        : t
    ));
  }, []);

  const startOptimization = useCallback((
    fetchFn: (
      onProgress?: (pct: number, label: string) => void,
      onBackendTaskId?: (backendId: string) => void,
    ) => Promise<{
      candidates: unknown[];
      poolStats: PoolStats | null;
      corr: CorrelationResult | null;
      extra?: Record<string, unknown>;
    }>,
    label: string,
  ): string => {
    const taskId = uid();
    const task: OptimizationTask = {
      id: taskId, type: "optimization", label,
      status: "running", startedAt: Date.now(),
      progressPct: 0, progressLabel: "Starting...",
      result: null, error: null,
    };
    setTasks((prev) => [...prev, task]);

    // Progress callback — updates TaskManager state
    const onProgress = (pct: number, progressLabel: string) => {
      updateOptimizationProgress(taskId, pct, progressLabel);
    };

    // Backend task ID callback — stores for cancellation
    const onBackendTaskId = (backendId: string) => {
      updateTask(taskId, { backendTaskId: backendId } as Partial<OptimizationTask>);
    };

    fetchFn(onProgress, onBackendTaskId)
      .then((result) => {
        updateTask(taskId, { status: "completed", result, progressPct: 100, progressLabel: "Complete" } as Partial<OptimizationTask>);
        addToast(`${label} completed`, "success");
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          updateTask(taskId, { status: "cancelled" });
          addToast(`${label} cancelled`, "info");
          return;
        }
        const msg = err instanceof Error ? err.message : "Operation failed";
        updateTask(taskId, { status: "error", error: msg });
        addToast(`${label} failed`, "error");
      });

    return taskId;
  }, [addToast, updateTask, updateOptimizationProgress]);

  // -- Cancel single task --
  const cancelTask = useCallback(async (taskId: string) => {
    const task = tasks.find((t) => t.id === taskId);
    if (!task || task.status !== "running") return;

    try {
      if (task.type === "batch") {
        await Promise.all(task.batchIds.map((bid) => api.cancelBatch(bid)));
      } else if (task.type === "download") {
        await api.cancelDownloads();
      } else if (task.type === "optimization") {
        const ot = task as OptimizationTask;
        if (ot.backendTaskId) {
          await api.cancelOptimizeTask(ot.backendTaskId);
        }
      }
      // Stop polling
      const cleanup = cleanupRefs.current.get(taskId);
      if (cleanup) { cleanup(); cleanupRefs.current.delete(taskId); }
      updateTask(taskId, { status: "cancelled" });
      addToast(`${task.label} cancelled`, "info");
    } catch {
      addToast("Failed to cancel task", "error");
    }
  }, [tasks, updateTask, addToast]);

  // -- Cancel ALL running tasks + clear queue --
  const cancelAllTasks = useCallback(async () => {
    // Cancel all running batches via backend bulk endpoint
    const runningBatches = tasks.filter((t): t is BatchTask => t.type === "batch" && t.status === "running");
    if (runningBatches.length > 0) {
      try {
        await api.cancelAllBatches();
      } catch {
        // Fall back to individual cancellation
        for (const bt of runningBatches) {
          await Promise.all(bt.batchIds.map((bid) => api.cancelBatch(bid).catch(() => {})));
        }
      }
    }

    // Cancel downloads
    const runningDownloads = tasks.filter((t) => t.type === "download" && t.status === "running");
    if (runningDownloads.length > 0) {
      try { await api.cancelDownloads(); } catch { /* ignore */ }
    }

    // Cancel running optimization tasks via backend
    const runningOpts = tasks.filter((t): t is OptimizationTask => t.type === "optimization" && t.status === "running");
    for (const ot of runningOpts) {
      if (ot.backendTaskId) {
        try { await api.cancelOptimizeTask(ot.backendTaskId); } catch { /* ignore */ }
      }
    }

    // Stop all polling
    cleanupRefs.current.forEach((fn) => fn());
    cleanupRefs.current.clear();

    // Mark all running tasks as cancelled
    setTasks((prev) => prev.map((t) =>
      t.status === "running" ? { ...t, status: "cancelled" } as AnyTask : t
    ));

    // Clear the queue
    const queueCount = batchQueue.length;
    setBatchQueue([]);

    const totalStopped = runningBatches.length + runningDownloads.length + runningOpts.length;
    const parts: string[] = [];
    if (totalStopped > 0) parts.push(`${totalStopped} task(s) stopped`);
    if (queueCount > 0) parts.push(`${queueCount} queued item(s) cleared`);
    if (parts.length > 0) addToast(parts.join(", "), "info");
  }, [tasks, batchQueue.length, addToast]);

  // Auto-reconnect to running batches on mount
  useEffect(() => {
    if (reconnectedRef.current) return;
    reconnectedRef.current = true;
    checkRunningBatches().then((running) => {
      running.forEach((batch) => {
        const ids = batch.source_batch_ids ?? [batch.id];
        startBatch(ids, batch.name || `Batch #${batch.id}`);
      });
    }).catch(() => {});
  }, [startBatch]);

  // Cleanup all polling on unmount
  useEffect(() => {
    return () => { cleanupRefs.current.forEach((fn) => fn()); };
  }, []);

  const activeTasks = tasks.filter((t) => t.status === "running");
  const latestBatchTask = [...tasks].reverse().find((t): t is BatchTask => t.type === "batch");
  const latestDownloadTask = [...tasks].reverse().find((t): t is DownloadTask => t.type === "download");
  const latestOptimizationTask = useCallback((label?: string): OptimizationTask | undefined => {
    if (label) {
      return [...tasks].reverse().find((t): t is OptimizationTask => t.type === "optimization" && t.label === label);
    }
    return [...tasks].reverse().find((t): t is OptimizationTask => t.type === "optimization");
  }, [tasks]);

  return (
    <TaskManagerContext.Provider value={{
      tasks, toasts, startBatch, startDownload, updateDownloadProgress,
      startOptimization, updateOptimizationProgress,
      cancelTask, cancelAllTasks, dismissToast, addToast,
      activeTasks, latestBatchTask, latestDownloadTask, latestOptimizationTask,
      clearTask, queueBatch, batchQueue, removeFromQueue,
    }}>
      {children}
    </TaskManagerContext.Provider>
  );
}
