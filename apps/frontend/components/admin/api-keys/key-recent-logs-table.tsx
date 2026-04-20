'use client';

import type { RecentLogItem } from '@/types/key-dashboard';

interface Props {
  data: RecentLogItem[] | null;
}

export function KeyRecentLogsTable({ data }: Props) {
  if (!data) return <p className="text-[var(--color-neutral-500)]">로딩 중…</p>;
  if (data.length === 0)
    return <p className="text-[var(--color-neutral-500)]">요청 로그가 없습니다.</p>;

  return (
    <div className="overflow-x-auto rounded-[var(--radius-md)] border border-[var(--color-neutral-200)]">
      <table className="w-full text-[var(--font-size-sm)]">
        <thead className="bg-[var(--color-neutral-50)] text-left text-[var(--color-neutral-600)]">
          <tr>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">시각</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">Profile</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">Provider</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">Status</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">지연</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">Cache</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">요청</th>
          </tr>
        </thead>
        <tbody>
          {data.map((l) => (
            <tr
              key={l.id}
              className={
                'border-t border-[var(--color-neutral-200)] ' +
                (l.cache_hit ? 'bg-[var(--color-success)]/5' : '')
              }
            >
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-neutral-500)]">
                {l.ts.slice(0, 19).replace('T', ' ')}
              </td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)]">
                {l.profile_id ?? '-'}
              </td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)]">
                {l.provider_id ?? '-'}
              </td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)]">
                <span
                  className={
                    l.status_code >= 400
                      ? 'text-[var(--color-danger)]'
                      : 'text-[var(--color-success)]'
                  }
                >
                  {l.status_code}
                </span>
              </td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)]">{l.latency_ms}ms</td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)]">
                {l.cache_hit ? '✓' : ''}
              </td>
              <td
                className="max-w-[320px] truncate px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-neutral-600)]"
                title={l.request_preview ?? ''}
              >
                {l.request_preview ?? ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
