"use client";

export default function OptimizationError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[40vh] gap-4 p-8">
      <div className="text-center space-y-2">
        <h2 className="text-lg font-semibold text-ag-red">Optimization Error</h2>
        <p className="text-sm text-ag-text-muted max-w-md">
          Something went wrong while rendering the optimization results.
          Your optimization data is still safe — click retry to reload.
        </p>
        <details className="text-xs text-ag-text-muted mt-2">
          <summary className="cursor-pointer hover:text-ag-text">Error details</summary>
          <pre className="mt-1 p-2 bg-ag-dark rounded text-left overflow-x-auto max-w-lg">
            {error.message}
          </pre>
        </details>
      </div>
      <button
        onClick={reset}
        className="px-4 py-2 bg-ag-accent text-white rounded-lg text-sm hover:bg-ag-accent/80"
      >
        Try Again
      </button>
    </div>
  );
}
