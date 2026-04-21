'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { FeedbackFilters } from '@/components/admin/feedback/feedback-filters';
import type { FeedbackFilterValue } from '@/components/admin/feedback/feedback-filters';
import { FeedbackList } from '@/components/admin/feedback/feedback-list';
import { fetchAdminFeedback } from '@/lib/api/bff-feedback';
import type { AdminFeedbackItem } from '@/types/feedback';

const PAGE_SIZE = 50;

export default function AdminFeedbackPage() {
  const [filter, setFilter] = useState<FeedbackFilterValue>({
    only_negative: false,
  });
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<AdminFeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const page = await fetchAdminFeedback({
        limit: PAGE_SIZE,
        offset,
        only_negative: filter.only_negative,
        date_from: filter.date_from,
        date_to: filter.date_to,
      });
      setItems(page.items);
      setTotal(page.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : '피드백 조회 실패');
    } finally {
      setIsLoading(false);
    }
  }, [filter, offset]);

  useEffect(() => {
    load();
  }, [load]);

  const handleFilterChange = (next: FeedbackFilterValue) => {
    setFilter(next);
    setOffset(0);
  };

  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
          응답 피드백
        </h1>
        <Button variant="ghost" size="sm" onClick={load}>
          새로고침
        </Button>
      </header>

      <FeedbackFilters value={filter} onChange={handleFilterChange} />

      {error && (
        <div
          role="alert"
          className="rounded-[var(--radius-md)] border border-[var(--color-error)] bg-[var(--color-error-light)] px-3 py-2 text-[var(--font-size-sm)] text-[var(--color-error)]"
        >
          {error}
        </div>
      )}

      <FeedbackList items={items} isLoading={isLoading} total={total} />

      {(hasPrev || hasNext) && (
        <nav
          aria-label="페이지 네비게이션"
          className="flex items-center justify-center gap-2"
        >
          <Button
            size="sm"
            variant="ghost"
            disabled={!hasPrev}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            이전
          </Button>
          <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
            {offset + 1} – {Math.min(offset + PAGE_SIZE, total)} / {total}
          </span>
          <Button
            size="sm"
            variant="ghost"
            disabled={!hasNext}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            다음
          </Button>
        </nav>
      )}
    </div>
  );
}
