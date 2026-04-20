'use client';

import Link from 'next/link';
import type { ApiKey } from '@/types/api-key';

interface Props {
  items: ApiKey[];
  onRevoke: (id: string) => void;
  onRotate: (id: string) => void;
}

export function ApiKeyTable({ items, onRevoke, onRotate }: Props) {
  return (
    <div className="overflow-x-auto rounded-[var(--radius-md)] border border-[var(--color-neutral-200)]">
      <table className="w-full text-[var(--font-size-sm)]">
        <thead className="bg-[var(--color-neutral-50)] text-left text-[var(--color-neutral-600)]">
          <tr>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">이름</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">Preview</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">허용 Profile</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">Rate/분</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)]">상태</th>
            <th className="px-[var(--spacing-3)] py-[var(--spacing-2)] text-right">액션</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr>
              <td
                colSpan={6}
                className="px-[var(--spacing-3)] py-[var(--spacing-6)] text-center text-[var(--color-neutral-400)]"
              >
                등록된 API Key 가 없습니다.
              </td>
            </tr>
          )}
          {items.map((k) => (
            <tr
              key={k.id}
              className="border-t border-[var(--color-neutral-200)] hover:bg-[var(--color-neutral-50)]"
            >
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)]">
                <Link
                  href={`/admin/api-keys/${k.id}`}
                  className="text-[var(--color-primary-700)] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
                  aria-label={`API Key ${k.name} 상세로 이동`}
                >
                  {k.name}
                </Link>
              </td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)] font-mono text-[var(--color-neutral-500)]">
                {k.preview}
              </td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)]">
                {k.allowed_profiles.length === 0 ? '(전체)' : k.allowed_profiles.join(', ')}
              </td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)]">
                {k.rate_limit_per_min}
              </td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)]">
                <span
                  className={
                    k.is_active
                      ? 'text-[var(--color-success)]'
                      : 'text-[var(--color-neutral-400)]'
                  }
                >
                  {k.is_active ? '활성' : '폐기'}
                </span>
              </td>
              <td className="px-[var(--spacing-3)] py-[var(--spacing-2)] text-right">
                <button
                  type="button"
                  onClick={() => onRotate(k.id)}
                  disabled={!k.is_active}
                  aria-label={`${k.name} 회전`}
                  className="mr-[var(--spacing-2)] text-[var(--color-primary-700)] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)] disabled:text-[var(--color-neutral-300)]"
                >
                  회전
                </button>
                <button
                  type="button"
                  onClick={() => onRevoke(k.id)}
                  disabled={!k.is_active}
                  aria-label={`${k.name} 폐기`}
                  className="text-[var(--color-danger)] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-danger)] disabled:text-[var(--color-neutral-300)]"
                >
                  폐기
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
