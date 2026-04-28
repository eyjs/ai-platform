'use client';

import { useEffect, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { getAccessToken } from '@/lib/auth/token-storage';

interface ProfileStats {
  request_count_7d: number;
  request_count_30d: number;
  avg_latency_ms: number;
  error_rate: number;
  connected_api_keys: number;
  last_request_at: string | null;
  daily_counts: number[];
}

interface ProfileStatsPanelProps {
  profileId: string;
}

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001';

export function ProfileStatsPanel({ profileId }: ProfileStatsPanelProps) {
  const [stats, setStats] = useState<ProfileStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const token = getAccessToken();
    const headers: Record<string, string> = token
      ? { Authorization: `Bearer ${token}` }
      : {};

    fetch(`${BFF_URL}/bff/dashboard/usage?profile_id=${encodeURIComponent(profileId)}`, { headers })
      .then((res) => {
        if (!res.ok) throw new Error('stats fetch failed');
        return res.json() as Promise<ProfileStats>;
      })
      .then(setStats)
      .catch(() => setStats(null))
      .finally(() => setIsLoading(false));
  }, [profileId]);

  if (isLoading) {
    return <Skeleton height="120px" width="100%" />;
  }

  if (!stats) {
    return (
      <div className="rounded-[var(--radius-md)] border border-dashed border-[var(--color-neutral-300)] bg-[var(--surface-card)] px-4 py-3 text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
        사용 통계를 불러올 수 없습니다.
      </div>
    );
  }

  const maxCount = Math.max(...stats.daily_counts, 1);

  return (
    <section
      className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-4"
      aria-label="Profile 사용 통계"
    >
      <h3 className="mb-3 text-[var(--font-size-sm)] font-semibold text-[var(--color-neutral-900)]">
        사용 통계
      </h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatItem label="7일 요청" value={stats.request_count_7d.toLocaleString()} />
        <StatItem label="30일 요청" value={stats.request_count_30d.toLocaleString()} />
        <StatItem label="평균 응답" value={`${stats.avg_latency_ms}ms`} />
        <StatItem
          label="에러율"
          value={`${(stats.error_rate * 100).toFixed(1)}%`}
          variant={stats.error_rate > 0.05 ? 'error' : 'success'}
        />
      </div>
      <div className="mt-3 flex items-center gap-2 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
        <Badge variant="neutral" size="sm">
          API Key {stats.connected_api_keys}개
        </Badge>
        {stats.last_request_at && (
          <span>마지막 요청: {new Date(stats.last_request_at).toLocaleString('ko-KR')}</span>
        )}
      </div>
      {stats.daily_counts.length > 0 && (
        <div className="mt-3 flex items-end gap-px" style={{ height: '40px' }} aria-label="일별 요청 차트">
          {stats.daily_counts.map((count, i) => (
            <div
              key={i}
              className="flex-1 rounded-t-[1px] bg-[var(--color-primary-600)]"
              style={{ height: `${(count / maxCount) * 100}%`, minHeight: count > 0 ? '2px' : '0' }}
              title={`${count}건`}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function StatItem({
  label,
  value,
  variant,
}: {
  label: string;
  value: string;
  variant?: 'success' | 'error';
}) {
  const colorClass = variant === 'error'
    ? 'text-[var(--color-error)]'
    : variant === 'success'
      ? 'text-[var(--color-success)]'
      : 'text-[var(--color-neutral-900)]';

  return (
    <div>
      <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">{label}</p>
      <p className={`text-[var(--font-size-base)] font-bold ${colorClass}`}>{value}</p>
    </div>
  );
}
