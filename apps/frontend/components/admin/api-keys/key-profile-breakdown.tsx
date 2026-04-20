'use client';

import type { ProfileBreakdownItem } from '@/types/key-dashboard';

interface Props {
  data: ProfileBreakdownItem[] | null;
}

export function KeyProfileBreakdown({ data }: Props) {
  if (!data) return <p className="text-[var(--color-neutral-500)]">로딩 중…</p>;
  if (data.length === 0)
    return <p className="text-[var(--color-neutral-500)]">프로파일 사용 기록이 없습니다.</p>;

  const total = data.reduce((sum, d) => sum + d.request_count, 0);

  return (
    <ul className="flex flex-col gap-[var(--spacing-2)]" aria-label="프로파일별 사용 비중">
      {data.map((d) => {
        const pct = total > 0 ? (d.request_count / total) * 100 : 0;
        return (
          <li
            key={d.profile_id}
            className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] p-[var(--spacing-3)]"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium text-[var(--color-neutral-800)]">
                {d.profile_id}
              </span>
              <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
                {d.request_count.toLocaleString()} · 에러 {(d.error_rate * 100).toFixed(1)}%
              </span>
            </div>
            <div className="mt-[var(--spacing-2)] h-[6px] w-full rounded-full bg-[var(--color-neutral-100)]">
              <div
                className="h-full rounded-full bg-[var(--color-primary-500)]"
                style={{ width: `${pct}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
