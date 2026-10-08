"use client";
import { useState, useEffect, useCallback, useRef } from "react";

interface CacheEntry<T> {
  data: T;
  timestamp: number;
}

// Module-level cache shared across all components
const cache = new Map<string, CacheEntry<unknown>>();

const STALE_MS = 30_000; // 30 seconds — return cached, revalidate in background

/** Invalidate a specific cache key (call after mutations). */
export function invalidateCache(key: string): void {
  cache.delete(key);
}

/** Invalidate all cache keys matching a prefix. */
export function invalidateCachePrefix(prefix: string): void {
  for (const k of cache.keys()) {
    if (k.startsWith(prefix)) cache.delete(k);
  }
}

interface UseApiResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

/**
 * Fetch data with optional stale-while-revalidate caching.
 *
 * @param fetcher  Async function that returns data
 * @param cacheKey Optional key — if provided, results are cached in memory.
 *                 On re-mount: returns stale data instantly (no loading spinner),
 *                 then revalidates in background.
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  cacheKey?: string,
): UseApiResult<T> {
  // Initialize state from cache if available
  const cached = cacheKey ? (cache.get(cacheKey) as CacheEntry<T> | undefined) : undefined;
  const [data, setData] = useState<T | null>(cached?.data ?? null);
  const [loading, setLoading] = useState(!cached);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const refetch = useCallback(() => {
    // Only show loading spinner if we have no cached data at all
    if (!cacheKey || !cache.has(cacheKey)) {
      setLoading(true);
    }
    setError(null);

    fetcher()
      .then((result) => {
        if (!mountedRef.current) return;
        setData(result);
        setLoading(false);
        if (cacheKey) {
          cache.set(cacheKey, { data: result, timestamp: Date.now() });
        }
      })
      .catch((e) => {
        if (!mountedRef.current) return;
        setError(e.message);
        setLoading(false);
      });
  }, [fetcher, cacheKey]);

  useEffect(() => {
    mountedRef.current = true;

    if (cacheKey && cached) {
      const age = Date.now() - cached.timestamp;
      if (age < STALE_MS) {
        // Fresh cache — skip fetch entirely
        setData(cached.data);
        setLoading(false);
        return;
      }
      // Stale cache — return cached data, revalidate in background
      setData(cached.data);
      setLoading(false);
    }

    refetch();

    return () => {
      mountedRef.current = false;
    };
  }, [refetch, cacheKey, cached]);

  return { data, loading, error, refetch };
}
