'use client';

import { useState, useCallback } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { usePolling } from '@/hooks/use-polling';
import { StatsCard } from '@/components/admin/stats-card';
import { UsageChart } from '@/components/admin/dashboard/usage-chart';
import { LatencyChart } from '@/components/admin/dashboard/latency-chart';
import {
  fetchSummary,
  fetchUsage,
  fetchLatency,
  fetchLogs,
} from '@/lib/api/bff-dashboard';
import {
  fetchPlatformOverview,
  type PlatformOverview,
} from '@/lib/api/admin';

const statusVariant: Record<string, 'success' | 'error' | 'warning'> = {
  success: 'success',
  error: 'error',
  timeout: 'warning',
};

const statusLabel: Record<string, string> = {
  success: '성공',
  error: '오류',
  timeout: '타임아웃',
};

function latencyColor(ms: number): string {
  if (ms < 500) return 'var(--color-success)';
  if (ms <= 2000) return 'var(--color-warning)';
  return 'var(--color-error)';
}

function HourlyTrendChart({ data }: { data: Array<{ hour: string; count: number }> }) {
  const maxCount = Math.max(...data.map((d) => d.count), 1);

  return (
    <div className="flex items-end gap-1" style={{ height: '120px' }}>
      {data.map((item) => {
        const pct = Math.round((item.count / maxCount) * 100);
        return (
          <div
            key={item.hour}
            className="group relative flex flex-1 flex-col items-center justify-end"
            style={{ height: '100%' }}
          >
            <div
              className="w-full rounded-t-[var(--radius-sm)] bg-[var(--color-primary-500)] transition-all duration-[var(--duration-normal)] group-hover:bg-[var(--color-primary-600)]"
              style={{ height: `${Math.max(pct, 2)}%`, minHeight: '2px' }}
              role="img"
              aria-label={`${item.hour}시: ${item.count}건`}
            />
            <span className="mt-1 text-[9px] text-[var(--color-neutral-400)]">
              {item.hour}
            </span>
            <div className="pointer-events-none absolute -top-8 left-1/2 z-10 hidden -translate-x-1/2 rounded-[var(--radius-sm)] bg-[var(--color-neutral-900)] px-2 py-1 text-[10px] text-white group-hover:block">
              {item.count}건
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function DashboardPage() {
  const [usagePeriod, setUsagePeriod] = useState('today');
  const [latencyPeriod, setLatencyPeriod] = useState('today');

  const {
    data: summary,
    isLoading: summaryLoading,
    refresh: refreshSummary,
  } = usePolling({
    fetchFn: fetchSummary,
    interval: 30000,
  });

  const { data: overview, isLoading: overviewLoading } = usePolling<PlatformOverview>({
    fetchFn: fetchPlatformOverview,
    interval: 60000,
  });

  const { data: usage, isLoading: usageLoading } = usePolling({
    fetchFn: useCallback(() => fetchUsage(usagePeriod), [usagePeriod]),
    interval: 300000,
  });

  const { data: latency, isLoading: latencyLoading } = usePolling({
    fetchFn: useCallback(() => fetchLatency(latencyPeriod), [latencyPeriod]),
    interval: 300000,
  });

  const { data: logs, isLoading: logsLoading } = usePolling({
    fetchFn: useCallback(() => fetchLogs(1, 5), []),
    interval: 60000,
  });

  return (
    <div className="flex flex-col gap-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
          플랫폼 개요
        </h1>
        <Button variant="ghost" size="sm" onClick={refreshSummary}>
          새로고침
        </Button>
      </div>

      {/* KPI 카드 — 4개 */}
      {overviewLoading || !overview ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} height="120px" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatsCard
            title="총 요청"
            value={overview.totalRequests.toLocaleString()}
            change={overview.changes.totalRequests}
            icon={
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            }
          />
          <StatsCard
            title="오류율"
            value={`${overview.errorRate}%`}
            change={overview.changes.errorRate}
            variant={overview.errorRate >= 5 ? 'error' : 'default'}
            icon={
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            }
          />
          <StatsCard
            title="평균 응답 시간"
            value={overview.avgLatencyMs > 0 ? `${overview.avgLatencyMs}ms` : '-'}
            change={overview.changes.avgLatency}
            variant={overview.avgLatencyMs > 2000 ? 'warning' : 'default'}
            icon={
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            }
          />
          <StatsCard
            title="활성 Profiles"
            value={overview.activeProfiles.toLocaleString()}
            change={overview.changes.activeProfiles}
            icon={
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
              </svg>
            }
          />
        </div>
      )}

      {/* 24h 요청 트렌드 */}
      <Card>
        <CardHeader>
          <CardTitle>24시간 요청 트렌드</CardTitle>
        </CardHeader>
        <CardContent>
          {overviewLoading || !overview ? (
            <Skeleton height="140px" />
          ) : (
            <HourlyTrendChart data={overview.hourlyTrend} />
          )}
        </CardContent>
      </Card>

      {/* 차트 영역 */}
      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <Tabs
            defaultValue="today"
            variant="pill"
            onValueChange={setUsagePeriod}
            className="mb-3"
          >
            <TabsList>
              <TabsTrigger value="today">오늘</TabsTrigger>
              <TabsTrigger value="7d">7일</TabsTrigger>
              <TabsTrigger value="30d">30일</TabsTrigger>
            </TabsList>
          </Tabs>
          <UsageChart data={usage} isLoading={usageLoading} />
        </div>
        <div>
          <Tabs
            defaultValue="today"
            variant="pill"
            onValueChange={setLatencyPeriod}
            className="mb-3"
          >
            <TabsList>
              <TabsTrigger value="today">오늘</TabsTrigger>
              <TabsTrigger value="7d">7일</TabsTrigger>
              <TabsTrigger value="30d">30일</TabsTrigger>
            </TabsList>
          </Tabs>
          <LatencyChart data={latency} isLoading={latencyLoading} />
        </div>
      </div>

      {/* 최근 요청 로그 프리뷰 */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>최근 요청 로그</CardTitle>
            <Link
              href="/admin/request-logs"
              className="text-[var(--font-size-sm)] text-[var(--color-primary-600)] hover:underline"
            >
              전체 보기 →
            </Link>
          </div>
        </CardHeader>
        <CardContent>
          {logsLoading || !logs ? (
            <Skeleton height="200px" />
          ) : logs.data.length === 0 ? (
            <p className="py-8 text-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
              요청 로그가 없습니다
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[var(--font-size-sm)]">
                <thead>
                  <tr className="border-b border-[var(--color-neutral-200)]">
                    <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">Profile</th>
                    <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">질문</th>
                    <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">응답 시간</th>
                    <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">시각</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.data.slice(0, 5).map((log) => (
                    <tr
                      key={log.sessionId}
                      className="border-b border-[var(--color-neutral-100)] transition-colors hover:bg-[var(--color-neutral-50)]"
                    >
                      <td className="px-3 py-2 text-[var(--color-neutral-800)]">
                        {log.profileName}
                      </td>
                      <td className="px-3 py-2 text-[var(--color-neutral-700)]">
                        <span className="block max-w-[200px] truncate">
                          {log.questionPreview || '-'}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className="font-mono text-[var(--font-size-xs)] font-medium"
                          style={{ color: latencyColor(log.responseTimeMs) }}
                        >
                          {log.responseTimeMs > 0 ? `${log.responseTimeMs}ms` : '-'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-[var(--color-neutral-500)]">
                        {new Date(log.timestamp).toLocaleString('ko-KR')}
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
  );
}
