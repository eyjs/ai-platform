'use client';

import { useState, useEffect, useCallback, useRef } from 'react';

interface UsePollingOptions<T> {
  fetchFn: () => Promise<T>;
  interval: number;
  enabled?: boolean;
}

export function usePolling<T>({ fetchFn, interval, enabled = true }: UsePollingOptions<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const fetchFnRef = useRef(fetchFn);
  fetchFnRef.current = fetchFn;

  const refresh = useCallback(async () => {
    try {
      const result = await fetchFnRef.current();
      setData(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Polling 실패'));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;

    refresh();
    const timer = setInterval(refresh, interval);
    return () => clearInterval(timer);
  }, [enabled, interval, refresh]);

  return { data, error, isLoading, refresh };
}
