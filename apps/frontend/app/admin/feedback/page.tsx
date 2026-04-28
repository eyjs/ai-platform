'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { FeedbackFilters } from '@/components/admin/feedback/feedback-filters';
import type { FeedbackFilterValue } from '@/components/admin/feedback/feedback-filters';
import { FeedbackList } from '@/components/admin/feedback/feedback-list';
import { fetchAdminFeedback } from '@/lib/api/bff-feedback';
import { fetchProfiles } from '@/lib/api/bff-profiles';
import type { AdminFeedbackItem } from '@/types/feedback';
import type { ProfileListItem } from '@/types/profile';

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
  const [profiles, setProfiles] = useState<ProfileListItem[]>([]);

  useEffect(() => {
    fetchProfiles().then(setProfiles).catch(() => {});
  }, []);

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
        profile_id: filter.profile_id,
        keyword: filter.keyword,
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

  const stats = useMemo(() => {
    const positive = items.filter((i) => i.score === 1).length;
    const negative = items.filter((i) => i.score === -1).length;
    const ratio = positive + negative > 0
      ? Math.round((positive / (positive + negative)) * 100)
      : 0;
    return { positive, negative, ratio };
  }, [items]);

  const profileOptions = useMemo(
    () => profiles.map((p) => ({ id: p.id, name: p.name })),
    [profiles],
  );

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

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" aria-label="피드백 요약">
        <div className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-4 py-3">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">좋아요</p>
          <p className="text-[var(--font-size-xl)] font-bold text-[var(--color-success)]">
            {stats.positive}
          </p>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-4 py-3">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">별로예요</p>
          <p className="text-[var(--font-size-xl)] font-bold text-[var(--color-error)]">
            {stats.negative}
          </p>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-4 py-3">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">긍정 비율</p>
          <p className="text-[var(--font-size-xl)] font-bold text-[var(--color-primary-600)]">
            {stats.ratio}%
          </p>
        </div>
      </div>

      <FeedbackFilters value={filter} onChange={handleFilterChange} profiles={profileOptions} />

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
