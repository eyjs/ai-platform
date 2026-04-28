'use client';

import { useState, useCallback } from 'react';
import Link from 'next/link';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { usePolling } from '@/hooks/use-polling';
import { DateRangePicker } from '@/components/admin/date-range-picker';
import { RequestLogTable } from '@/components/admin/request-log-table';
import { StatsCard } from '@/components/admin/stats-card';
import {
  fetchRequestLogs,
  fetchRequestLogStats,
  type RequestLogFilters,
  type RequestLogsResponse,
  type RequestLogStats,
} from '@/lib/api/admin';

const STATUS_OPTIONS = [
  { value: '', label: '전체 상태' },
  { value: 'success', label: '성공' },
  { value: 'error', label: '오류' },
  { value: 'timeout', label: '타임아웃' },
];

export default function RequestLogsPage() {
  const [filters, setFilters] = useState<RequestLogFilters>({
    page: 1,
    size: 20,
  });

  const { data: stats, isLoading: statsLoading } = usePolling<RequestLogStats>({
    fetchFn: fetchRequestLogStats,
    interval: 60000,
  });

  const { data: logs, isLoading: logsLoading, refresh } = usePolling<RequestLogsResponse>({
    fetchFn: useCallback(() => fetchRequestLogs(filters), [filters]),
    interval: 30000,
  });

  const updateFilters = (patch: Partial<RequestLogFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch, page: patch.page ?? 1 }));
  };

  const handleDateChange = (startDate: string, endDate: string) => {
    updateFilters({ startDate, endDate });
  };

  const handlePageChange = (page: number) => {
    setFilters((prev) => ({ ...prev, page }));
  };

  return (
    <div className="flex flex-col gap-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
            요청 로그
          </h1>
          <p className="mt-1 text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
            플랫폼 요청/응답 기록을 조회합니다
          </p>
        </div>
        <Link
          href="/admin/dashboard"
          className="text-[var(--font-size-sm)] text-[var(--color-primary-600)] hover:underline"
        >
          대시보드로 돌아가기
        </Link>
      </div>

      {/* 통계 카드 */}
      {statsLoading || !stats ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} height="88px" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatsCard
            title="오늘 총 요청"
            value={stats.totalToday.toLocaleString()}
            icon={
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            }
          />
          <StatsCard
            title="오류 건수"
            value={stats.errorCount.toLocaleString()}
            variant={stats.errorCount > 0 ? 'error' : 'default'}
            icon={
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            }
          />
          <StatsCard
            title="평균 응답 시간"
            value={stats.avgLatencyMs > 0 ? `${stats.avgLatencyMs}ms` : '-'}
            variant={stats.avgLatencyMs > 2000 ? 'warning' : 'default'}
            icon={
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            }
          />
        </div>
      )}

      {/* 필터 */}
      <Card className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={filters.status ?? ''}
            onChange={(e) => updateFilters({ status: e.target.value || undefined })}
            className="h-8 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-input)] px-2 text-[var(--font-size-sm)] text-[var(--color-neutral-700)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-500)]"
            aria-label="상태 필터"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder="Profile ID"
            value={filters.profileId ?? ''}
            onChange={(e) => updateFilters({ profileId: e.target.value || undefined })}
            className="h-8 w-40 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-input)] px-2 text-[var(--font-size-sm)] text-[var(--color-neutral-700)] placeholder:text-[var(--color-neutral-400)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-500)]"
            aria-label="Profile ID 필터"
          />
        </div>
        <DateRangePicker onChange={handleDateChange} />
      </Card>

      {/* 테이블 */}
      <Card className="overflow-hidden p-0">
        <RequestLogTable
          data={logs?.data ?? []}
          total={logs?.total ?? 0}
          page={filters.page ?? 1}
          pageSize={filters.size ?? 20}
          onPageChange={handlePageChange}
          isLoading={logsLoading}
        />
      </Card>
    </div>
  );
}
