'use client';

import type { TimelineBucket } from '@/types/key-dashboard';

interface Props {
  data: TimelineBucket[] | null;
}

export function KeyTimelineChart({ data }: Props) {
  if (!data) return <p className="text-[var(--color-neutral-500)]">로딩 중…</p>;
  if (data.length === 0) {
    return (
      <p className="text-[var(--color-neutral-500)]">
        기록된 요청이 없습니다.
      </p>
    );
  }

  const max = Math.max(...data.map((d) => d.request_count));
  const scale = (n: number) => (max > 0 ? (n / max) * 100 : 0);

  return (
    <div
      aria-label="시간별 요청 수"
      className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] p-[var(--spacing-3)]"
    >
      <div className="flex h-[160px] items-end gap-[var(--spacing-1)]">
        {data.map((b) => (
          <div
            key={b.bucket_start}
            title={`${b.bucket_start}: ${b.request_count} (에러 ${b.error_count})`}
            className="flex-1 bg-[var(--color-primary-400)] rounded-t-[var(--radius-sm)]"
            style={{ height: `${scale(b.request_count)}%` }}
          />
        ))}
      </div>
      <div className="mt-[var(--spacing-2)] text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
        {data[0]?.bucket_start.slice(0, 10)} ~ {data[data.length - 1]?.bucket_start.slice(0, 10)}
      </div>
    </div>
  );
}
