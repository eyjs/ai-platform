'use client';

import type { KeySummary } from '@/types/key-dashboard';

interface Props {
  data: KeySummary | null;
}

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-[var(--spacing-3)]">
      <div className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">{label}</div>
      <div className="mt-[var(--spacing-1)] text-[var(--font-size-xl)] font-semibold text-[var(--color-neutral-900)]">
        {value}
      </div>
    </div>
  );
}

export function KeySummaryCards({ data }: Props) {
  if (!data) {
    return (
      <div
        aria-label="요약 로딩 중"
        className="text-[var(--color-neutral-500)]"
      >
        로딩 중…
      </div>
    );
  }
  const pct = (n: number) => `${(n * 100).toFixed(1)}%`;
  return (
    <div
      className="grid grid-cols-2 gap-[var(--spacing-3)] md:grid-cols-5"
      aria-label="요약 카드"
    >
      <Card label="요청 수" value={data.request_count.toLocaleString()} />
      <Card label="에러율" value={pct(data.error_rate)} />
      <Card label="p50 지연" value={`${data.p50_latency_ms} ms`} />
      <Card label="p95 지연" value={`${data.p95_latency_ms} ms`} />
      <Card label="캐시 적중률" value={pct(data.cache_hit_rate)} />
    </div>
  );
}
