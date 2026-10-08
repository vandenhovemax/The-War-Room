import type {
  BatchProgress,
  BatchResultSummary,
  DownloadProgress,
  CorrelationResult,
  PoolStats,
} from "@/lib/api";

// -- Task status --
export type TaskStatus = "running" | "completed" | "error" | "cancelled";

// -- Batch backtest task --
export interface BatchTask {
  id: string;
  type: "batch";
  batchIds: number[];
  label: string;
  status: TaskStatus;
  startedAt: number;
  progressMap: Record<number, BatchProgress>;
  results: BatchResultSummary[] | null;
  error: string | null;
  skippedCount: number;
  skippedDataDateCount: number;
  prefilteredCount: number;
}

// -- Download task --
export interface DownloadTask {
  id: string;
  type: "download";
  label: string;
  status: TaskStatus;
  startedAt: number;
  progress: Record<string, DownloadProgress>;
  error: string | null;
}

// -- Optimization / cross-strategy build task --
export interface OptimizationTask {
  id: string;
  type: "optimization";
  label: string;
  status: TaskStatus;
  startedAt: number;
  // Progress tracking (step-based from backend polling)
  progressPct: number;
  progressLabel: string;
  // Generic result — consumers cast to their specific type
  result: {
    candidates: unknown[];
    poolStats: PoolStats | null;
    corr: CorrelationResult | null;
    extra?: Record<string, unknown>;
  } | null;
  error: string | null;
  abortController?: AbortController;
  // Backend task_id for cancellation
  backendTaskId?: string;
}

export type AnyTask = BatchTask | DownloadTask | OptimizationTask;

// -- Queued batch item (waiting to start) --
export interface QueuedBatchItem {
  id: string;
  label: string;
  /** Closure that calls the API to start this batch. Returns batch IDs. */
  startFn: () => Promise<{ batchIds: number[]; label: string; skippedCount: number; skippedDataDateCount?: number; prefilteredCount?: number }>;
}

// -- Toast notification --
export interface ToastItem {
  id: string;
  message: string;
  variant: "success" | "error" | "info";
  createdAt: number;
}

// -- Context shape --
export interface TaskManagerContextType {
  tasks: AnyTask[];
  toasts: ToastItem[];
  // Batch
  startBatch: (batchIds: number[], label: string, skippedCount?: number, skippedDataDateCount?: number, prefilteredCount?: number) => string;
  // Queue
  queueBatch: (item: Omit<QueuedBatchItem, "id">) => string;
  batchQueue: QueuedBatchItem[];
  removeFromQueue: (id: string) => void;
  // Download
  startDownload: (label: string) => string;
  updateDownloadProgress: (progress: Record<string, DownloadProgress>) => void;
  // Optimization
  startOptimization: (
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
  ) => string;
  updateOptimizationProgress: (taskId: string, pct: number, label: string) => void;
  // Cancel
  cancelTask: (taskId: string) => Promise<void>;
  cancelAllTasks: () => Promise<void>;
  // Toast
  dismissToast: (id: string) => void;
  addToast: (message: string, variant: ToastItem["variant"]) => void;
  // Query helpers
  activeTasks: AnyTask[];
  latestBatchTask: BatchTask | undefined;
  latestDownloadTask: DownloadTask | undefined;
  latestOptimizationTask: (label?: string) => OptimizationTask | undefined;
  clearTask: (id: string) => void;
}
