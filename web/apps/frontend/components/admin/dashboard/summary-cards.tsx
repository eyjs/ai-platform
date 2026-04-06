'use client';

import { StatCard } from '@/components/ui/stat-card';
import { Skeleton } from '@/components/ui/skeleton';
import type { DashboardSummary } from '@/lib/api/bff-dashboard';

interface SummaryCardsProps {
  data: DashboardSummary | null;
  isLoading: boolean;
}

export function SummaryCards({ data, isLoading }: SummaryCardsProps) {
  if (isLoading || !data) {
    return (
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[1, 2, 3, 4].map((i) => (
          <Skeleton key={i} height="120px" />
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <StatCard
        label="활성 세션"
        value={data.activeSessions.toLocaleString()}
        change={data.changes.activeSessions}
      />
      <StatCard
        label="오늘 대화"
        value={data.todayConversations.toLocaleString()}
        change={data.changes.todayConversations}
      />
      <StatCard
        label="평균 응답 시간"
        value={data.avgResponseTimeMs > 0 ? `${data.avgResponseTimeMs}ms` : '-'}
        change={data.changes.avgResponseTime}
      />
      <StatCard
        label="오류율"
        value={`${data.errorRate}%`}
        change={data.changes.errorRate}
        highlight={data.errorRate >= 5}
      />
    </div>
  );
}
