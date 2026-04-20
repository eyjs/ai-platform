'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { RangeSelector } from '@/components/admin/api-keys/range-selector';
import { KeySummaryCards } from '@/components/admin/api-keys/key-summary-cards';
import { KeyTimelineChart } from '@/components/admin/api-keys/key-timeline-chart';
import { KeyProfileBreakdown } from '@/components/admin/api-keys/key-profile-breakdown';
import { KeyRecentLogsTable } from '@/components/admin/api-keys/key-recent-logs-table';
import {
  getProfileBreakdown,
  getRecentLogs,
  getSummary,
  getTimeline,
} from '@/lib/api/bff-key-dashboard';
import { getApiKey } from '@/lib/api/bff-api-keys';
import type { ApiKey } from '@/types/api-key';
import type {
  DashboardBucket,
  DashboardRange,
  KeySummary,
  ProfileBreakdownItem,
  RecentLogItem,
  TimelineBucket,
} from '@/types/key-dashboard';

export default function ApiKeyDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [range, setRange] = useState<DashboardRange>('24h');
  const [apiKey, setApiKey] = useState<ApiKey | null>(null);
  const [summary, setSummary] = useState<KeySummary | null>(null);
  const [breakdown, setBreakdown] = useState<ProfileBreakdownItem[] | null>(null);
  const [timeline, setTimeline] = useState<TimelineBucket[] | null>(null);
  const [recent, setRecent] = useState<RecentLogItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    getApiKey(id)
      .then(setApiKey)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);

  useEffect(() => {
    if (!id) return;
    const bucket: DashboardBucket = range === '30d' ? 'day' : 'hour';
    setSummary(null);
    setBreakdown(null);
    setTimeline(null);
    setRecent(null);
    Promise.all([
      getSummary(id, range),
      getProfileBreakdown(id, range),
      getTimeline(id, range, bucket),
      getRecentLogs(id, 100),
    ])
      .then(([s, b, t, r]) => {
        setSummary(s);
        setBreakdown(b);
        setTimeline(t);
        setRecent(r);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id, range]);

  return (
    <div className="flex flex-col gap-[var(--spacing-5)] p-[var(--spacing-5)]">
      <header className="flex flex-col gap-[var(--spacing-2)] md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-[var(--font-size-2xl)] font-semibold text-[var(--color-neutral-900)]">
            {apiKey ? apiKey.name : 'API Key 상세'}
          </h1>
          {apiKey && (
            <p className="text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
              {apiKey.preview} · {apiKey.is_active ? '활성' : '폐기'}
            </p>
          )}
        </div>
        <RangeSelector value={range} onChange={setRange} />
      </header>

      {error && (
        <div className="rounded-[var(--radius-md)] bg-[var(--color-danger)]/10 p-[var(--spacing-3)] text-[var(--color-danger)]">
          {error}
        </div>
      )}

      <section aria-label="요약">
        <KeySummaryCards data={summary} />
      </section>

      <section aria-label="타임라인">
        <h2 className="mb-[var(--spacing-2)] text-[var(--font-size-lg)] font-medium text-[var(--color-neutral-800)]">
          시간별 요청
        </h2>
        <KeyTimelineChart data={timeline} />
      </section>

      <section aria-label="프로파일 분포">
        <h2 className="mb-[var(--spacing-2)] text-[var(--font-size-lg)] font-medium text-[var(--color-neutral-800)]">
          프로파일별 사용
        </h2>
        <KeyProfileBreakdown data={breakdown} />
      </section>

      <section aria-label="최근 요청">
        <h2 className="mb-[var(--spacing-2)] text-[var(--font-size-lg)] font-medium text-[var(--color-neutral-800)]">
          최근 요청
        </h2>
        <KeyRecentLogsTable data={recent} />
      </section>
    </div>
  );
}
