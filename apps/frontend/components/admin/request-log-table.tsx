'use client';

import { useRouter } from 'next/navigation';
import { cn } from '@/lib/cn';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { RequestLogSummary } from '@/lib/api/admin';

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

export interface RequestLogTableProps {
  data: RequestLogSummary[];
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onSort?: (key: string, direction: 'asc' | 'desc') => void;
  isLoading?: boolean;
}

export function RequestLogTable({
  data,
  total,
  page,
  pageSize,
  onPageChange,
  onSort,
  isLoading,
}: RequestLogTableProps) {
  const router = useRouter();
  const totalPages = Math.ceil(total / pageSize);

  const handleSort = (key: string) => {
    onSort?.(key, 'desc');
  };

  const handleRowClick = (id: string) => {
    router.push(`/admin/request-logs/${id}`);
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[var(--font-size-sm)]">
        <thead>
          <tr className="border-b border-[var(--color-neutral-200)]">
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">
              ID
            </th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">
              Profile
            </th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">
              질문
            </th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">
              상태
            </th>
            <th
              className="cursor-pointer px-4 py-3 text-left font-medium text-[var(--color-neutral-600)] hover:text-[var(--color-neutral-900)]"
              onClick={() => handleSort('latencyMs')}
              role="button"
              tabIndex={0}
              aria-label="응답 시간 정렬"
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') handleSort('latencyMs');
              }}
            >
              응답 시간
            </th>
            <th
              className="cursor-pointer px-4 py-3 text-left font-medium text-[var(--color-neutral-600)] hover:text-[var(--color-neutral-900)]"
              onClick={() => handleSort('timestamp')}
              role="button"
              tabIndex={0}
              aria-label="시각 정렬"
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') handleSort('timestamp');
              }}
            >
              시각
            </th>
          </tr>
        </thead>
        <tbody>
          {isLoading ? (
            <tr>
              <td
                colSpan={6}
                className="px-4 py-12 text-center text-[var(--color-neutral-400)]"
              >
                로딩 중...
              </td>
            </tr>
          ) : data.length === 0 ? (
            <tr>
              <td
                colSpan={6}
                className="px-4 py-12 text-center text-[var(--color-neutral-400)]"
              >
                요청 로그가 없습니다
              </td>
            </tr>
          ) : (
            data.map((row) => (
              <tr
                key={row.id}
                onClick={() => handleRowClick(row.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleRowClick(row.id);
                }}
                role="button"
                tabIndex={0}
                className={cn(
                  'cursor-pointer border-b border-[var(--color-neutral-100)]',
                  'transition-colors hover:bg-[var(--color-neutral-50)]',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-primary-500)]',
                )}
                aria-label={`요청 로그 ${row.id} 상세 보기`}
              >
                <td className="px-4 py-3">
                  <span className="font-mono text-[var(--font-size-xs)]">
                    {row.id.slice(0, 8)}...
                  </span>
                </td>
                <td className="px-4 py-3 text-[var(--color-neutral-800)]">
                  {row.profileName}
                </td>
                <td className="px-4 py-3 text-[var(--color-neutral-700)]">
                  <span className="block max-w-[200px] truncate">
                    {row.questionPreview || '-'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <Badge variant={statusVariant[row.status] ?? 'neutral'}>
                    {statusLabel[row.status] ?? row.status}
                  </Badge>
                </td>
                <td className="px-4 py-3">
                  <span
                    className="font-mono text-[var(--font-size-xs)] font-medium"
                    style={{ color: latencyColor(row.latencyMs) }}
                  >
                    {row.latencyMs}ms
                  </span>
                </td>
                <td className="px-4 py-3 text-[var(--color-neutral-500)]">
                  {new Date(row.timestamp).toLocaleString('ko-KR')}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-[var(--color-neutral-200)] px-4 py-3">
          <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
            {total}개 중 {(page - 1) * pageSize + 1}-
            {Math.min(page * pageSize, total)}
          </span>
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
            >
              이전
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => onPageChange(page + 1)}
            >
              다음
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
