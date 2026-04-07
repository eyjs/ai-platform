'use client';

import { useState, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { usePolling } from '@/hooks/use-polling';
import { SummaryCards } from '@/components/admin/dashboard/summary-cards';
import { UsageChart } from '@/components/admin/dashboard/usage-chart';
import { LatencyChart } from '@/components/admin/dashboard/latency-chart';
import { ConversationLogTable } from '@/components/admin/dashboard/conversation-log-table';
import {
  fetchSummary,
  fetchUsage,
  fetchLatency,
  fetchLogs,
} from '@/lib/api/bff-dashboard';

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

  const { data: usage, isLoading: usageLoading } = usePolling({
    fetchFn: useCallback(() => fetchUsage(usagePeriod), [usagePeriod]),
    interval: 300000,
  });

  const { data: latency, isLoading: latencyLoading } = usePolling({
    fetchFn: useCallback(() => fetchLatency(latencyPeriod), [latencyPeriod]),
    interval: 300000,
  });

  const { data: logs, isLoading: logsLoading } = usePolling({
    fetchFn: useCallback(() => fetchLogs(1, 10), []),
    interval: 60000,
  });

  return (
    <div className="flex flex-col gap-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
          대시보드
        </h1>
        <Button variant="ghost" size="sm" onClick={refreshSummary}>
          새로고침
        </Button>
      </div>

      {/* 현황 카드 */}
      <SummaryCards data={summary} isLoading={summaryLoading} />

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

      {/* 대화 로그 */}
      <ConversationLogTable initialData={logs} isLoading={logsLoading} />
    </div>
  );
}
