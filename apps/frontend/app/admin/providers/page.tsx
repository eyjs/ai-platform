'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { StatsCard } from '@/components/admin/stats-card';
import { fetchProviderStatus, type ProvidersStatus } from '@/lib/api/bff-providers';

const REFRESH_INTERVAL_MS = 30_000;

export default function ProviderStatusPage() {
  const [status, setStatus] = useState<ProvidersStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadProviders = useCallback(async () => {
    try {
      const data = await fetchProviderStatus();
      setStatus(data);
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
          <Button variant="ghost" size="sm" onClick={loadProviders}>
            새로고침
          </Button>
        </div>
      </div>

      {isLoading || !status ? (
        error ? (
          <div className="flex flex-col items-center gap-3 py-12">
            <p className="text-[var(--color-error)]">{error}</p>
            <Button variant="secondary" onClick={loadProviders}>
              재시도
            </Button>
          </div>
        ) : (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              {[1, 2, 3, 4].map((i) => (
                <Skeleton key={i} height="120px" />
              ))}
            </div>
            <Skeleton height="200px" />
          </div>
        )
      ) : (
        <div className="space-y-6">
          {/* 요약 카드 */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatsCard title="전체 Provider" value={status.totalProviders.toLocaleString()} />
            <StatsCard title="활성 Provider" value={status.activeProviders.toLocaleString()} />
            <StatsCard title="캐시 엔트리" value={status.cacheEntries.toLocaleString()} />
            <StatsCard
              title="만료 엔트리"
              value={status.expiredEntries.toLocaleString()}
              variant={status.expiredEntries > 0 ? 'warning' : 'default'}
            />
          </div>

          {/* 타입별 요약 */}
          {status.providersByType.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>타입별 현황</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                  {status.providersByType.map((t) => (
                    <div
                      key={t.providerType}
                      className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] p-3"
                    >
                      <p className="text-[var(--font-size-sm)] font-semibold text-[var(--color-neutral-900)]">
                        {t.providerType}
                      </p>
                      <p className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                        Provider {t.totalProviders} · 활성 {t.activeEntries} · 만료 {t.expiredEntries}
                      </p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Provider별 상세 */}
          <Card>
            <CardHeader>
              <CardTitle>Provider 상세</CardTitle>
            </CardHeader>
            <CardContent>
              {status.providerMetrics.length === 0 ? (
                <p className="py-8 text-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
                  등록된 Provider가 없습니다
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[var(--font-size-sm)]">
                    <thead>
                      <tr className="border-b border-[var(--color-neutral-200)]">
                        <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">Provider</th>
                        <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">타입</th>
                        <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">상태</th>
                        <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">캐시</th>
                        <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">만료</th>
                        <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">최근 활동</th>
                      </tr>
                    </thead>
                    <tbody>
                      {status.providerMetrics.map((p) => (
                        <tr
                          key={p.providerId}
                          className="border-b border-[var(--color-neutral-100)] transition-colors hover:bg-[var(--color-neutral-50)]"
                        >
                          <td className="px-3 py-2 font-medium text-[var(--color-neutral-800)]">{p.providerId}</td>
                          <td className="px-3 py-2 text-[var(--color-neutral-600)]">{p.providerType}</td>
                          <td className="px-3 py-2">
                            <Badge variant={p.isActive ? 'success' : 'warning'}>
                              {p.isActive ? '활성' : '비활성'}
                            </Badge>
                          </td>
                          <td className="px-3 py-2 text-[var(--color-neutral-700)]">{p.cacheEntries}</td>
                          <td className="px-3 py-2 text-[var(--color-neutral-700)]">{p.expiredEntries}</td>
                          <td className="px-3 py-2 text-[var(--color-neutral-500)]">
                            {p.lastActivity ? new Date(p.lastActivity).toLocaleString('ko-KR') : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
