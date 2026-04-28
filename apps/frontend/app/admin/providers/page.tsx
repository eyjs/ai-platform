'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { ProviderCard } from '@/components/admin/provider-card';
import { fetchProviderStatus, type ProviderStatus } from '@/lib/api/bff-providers';

const REFRESH_INTERVAL_MS = 30_000;

const typeOrder: Record<string, number> = { llm: 0, embedding: 1, reranker: 2 };

export default function ProviderStatusPage() {
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadProviders = useCallback(async () => {
    try {
      const data = await fetchProviderStatus();
      const sorted = [...data].sort(
        (a, b) => (typeOrder[a.type] ?? 99) - (typeOrder[b.type] ?? 99),
      );
      setProviders(sorted);
      setError(null);
      setLastRefreshed(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Provider 상태 로딩 실패');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProviders();
    intervalRef.current = setInterval(loadProviders, REFRESH_INTERVAL_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [loadProviders]);

  const grouped = {
    llm: providers.filter((p) => p.type === 'llm'),
    embedding: providers.filter((p) => p.type === 'embedding'),
    reranker: providers.filter((p) => p.type === 'reranker'),
  };

  const groupLabels: Record<string, string> = {
    llm: 'LLM Providers',
    embedding: 'Embedding Providers',
    reranker: 'Reranker Providers',
  };

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
          Provider Status
        </h1>
        <div className="flex items-center gap-3">
          {lastRefreshed && (
            <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
              마지막 갱신: {lastRefreshed.toLocaleTimeString('ko-KR')}
            </span>
          )}
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--color-success)]" />
            <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
              30초 자동 갱신
            </span>
          </div>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-6">
          {[1, 2, 3].map((i) => (
            <div key={i}>
              <Skeleton height="20px" className="mb-3 w-40" />
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                {[1, 2].map((j) => (
                  <Skeleton key={j} height="160px" />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="flex flex-col items-center gap-3 py-12">
          <p className="text-[var(--color-error)]">{error}</p>
          <Button variant="secondary" onClick={loadProviders}>
            재시도
          </Button>
        </div>
      ) : providers.length === 0 ? (
        <div className="py-12 text-center text-[var(--color-neutral-500)]">
          등록된 Provider가 없습니다
        </div>
      ) : (
        <div className="space-y-8">
          {(Object.keys(grouped) as Array<keyof typeof grouped>).map((type) => {
            const items = grouped[type];
            if (items.length === 0) return null;
            return (
              <section key={type}>
                <h2 className="mb-3 text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
                  {groupLabels[type]}
                </h2>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
                  {items.map((provider) => (
                    <ProviderCard
                      key={provider.name}
                      name={provider.name}
                      type={provider.type}
                      status={provider.status}
                      avgLatencyMs={provider.avgLatencyMs}
                      errorRate={provider.errorRate}
                      lastError={provider.lastError}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
