"use client";
import { useState, useEffect, useCallback, useRef } from "react";

interface UsePollingResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled: boolean = true,
): UsePollingResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const refetch = useCallback(() => {
    fetcherRef.current()
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refetch();
    if (!enabled) return;
    const id = setInterval(refetch, intervalMs);
    return () => clearInterval(id);
  }, [refetch, intervalMs, enabled]);

  return { data, loading, error, refetch };
}
