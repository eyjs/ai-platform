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
import { fetchUsage, fetchLatency } from '@/lib/api/bff-dashboard';
import { fetchPlatformOverview, type PlatformOverview } from '@/lib/api/admin';

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
        const label = new Date(item.hour).getHours();
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
              aria-label={`${label}시: ${item.count}건`}
            />
            <span className="mt-1 text-[9px] text-[var(--color-neutral-400)]">{label}</span>
            <div className="pointer-events-none absolute -top-8 left-1/2 z-10 hidden -translate-x-1/2 rounded-[var(--radius-sm)] bg-[var(--color-neutral-900)] px-2 py-1 text-[10px] text-white group-hover:block">
              {item.count}건
            </div>
          </div>
        );
      })}
    </div>
  );
}

const cardIcon = (
  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
  </svg>
);

export default function DashboardPage() {
  const [usagePeriod, setUsagePeriod] = useState('today');
  const [latencyPeriod, setLatencyPeriod] = useState('today');

  const {
    data: overview,
    isLoading: overviewLoading,
    refresh: refreshOverview,
  } = usePolling<PlatformOverview>({
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

  return (
    <div className="flex flex-col gap-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
          모니터링 대시보드
        </h1>
        <Button variant="ghost" size="sm" onClick={refreshOverview}>
          새로고침
        </Button>
      </div>

      {/* KPI 카드 — 6개 */}
      {overviewLoading || !overview ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <Skeleton key={i} height="120px" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
          <StatsCard title="발행 프로필" value={overview.totalProfiles.toLocaleString()} icon={cardIcon} />
          <StatsCard title="활성 프로필" value={overview.activeProfiles.toLocaleString()} icon={cardIcon} />
          <StatsCard title="활성 API 키" value={overview.apiKeys.active.toLocaleString()} icon={cardIcon} />
          <StatsCard title="오늘 요청" value={overview.todayRequests.toLocaleString()} icon={cardIcon} />
          <StatsCard
            title="오류율"
            value={`${(overview.errorRate * 100).toFixed(1)}%`}
            variant={overview.errorRate >= 0.05 ? 'error' : 'default'}
            icon={cardIcon}
          />
          <StatsCard
            title="평균 레이턴시"
            value={
              overview.avgLatencyMs > 0
                ? `${overview.avgLatencyMs}ms · p95 ${overview.p95LatencyMs}ms`
                : '-'
            }
            variant={overview.avgLatencyMs > 2000 ? 'warning' : 'default'}
            icon={cardIcon}
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
          ) : overview.requests24h.length === 0 ? (
            <p className="py-8 text-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
              최근 24시간 요청이 없습니다
            </p>
          ) : (
            <HourlyTrendChart data={overview.requests24h} />
          )}
        </CardContent>
      </Card>

      {/* 차트 영역 */}
      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <Tabs defaultValue="today" variant="pill" onValueChange={setUsagePeriod} className="mb-3">
            <TabsList>
              <TabsTrigger value="today">오늘</TabsTrigger>
              <TabsTrigger value="7d">7일</TabsTrigger>
              <TabsTrigger value="30d">30일</TabsTrigger>
            </TabsList>
          </Tabs>
          <UsageChart data={usage} isLoading={usageLoading} />
        </div>
        <div>
          <Tabs defaultValue="today" variant="pill" onValueChange={setLatencyPeriod} className="mb-3">
            <TabsList>
              <TabsTrigger value="today">오늘</TabsTrigger>
              <TabsTrigger value="7d">7일</TabsTrigger>
              <TabsTrigger value="30d">30일</TabsTrigger>
            </TabsList>
          </Tabs>
          <LatencyChart data={latency} isLoading={latencyLoading} />
        </div>
      </div>

      {/* 최근 요청 로그 (api_request_logs) */}
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
          {overviewLoading || !overview ? (
            <Skeleton height="200px" />
          ) : overview.recentLogs.length === 0 ? (
            <p className="py-8 text-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
              요청 로그가 없습니다
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[var(--font-size-sm)]">
                <thead>
                  <tr className="border-b border-[var(--color-neutral-200)]">
                    <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">Profile</th>
                    <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">상태</th>
                    <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">요청</th>
                    <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">레이턴시</th>
                    <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">시각</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.recentLogs.slice(0, 8).map((log, i) => (
                    <tr
                      key={`${log.ts}-${i}`}
                      className="border-b border-[var(--color-neutral-100)] transition-colors hover:bg-[var(--color-neutral-50)]"
                    >
                      <td className="px-3 py-2 text-[var(--color-neutral-800)]">
                        {log.profileId || '-'}
                      </td>
                      <td className="px-3 py-2">
                        <Badge variant={log.statusCode >= 400 ? 'error' : 'success'}>
                          {log.statusCode}
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-[var(--color-neutral-700)]">
                        <span className="block max-w-[220px] truncate">
                          {log.requestPreview || '-'}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className="font-mono text-[var(--font-size-xs)] font-medium"
                          style={{ color: latencyColor(log.latencyMs) }}
                        >
                          {log.latencyMs}ms
                        </span>
                      </td>
                      <td className="px-3 py-2 text-[var(--color-neutral-500)]">
                        {new Date(log.ts).toLocaleString('ko-KR')}
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
