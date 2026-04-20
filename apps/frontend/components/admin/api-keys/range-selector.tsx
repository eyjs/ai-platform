'use client';

import type { DashboardRange } from '@/types/key-dashboard';

interface Props {
  value: DashboardRange;
  onChange: (r: DashboardRange) => void;
}

const OPTIONS: DashboardRange[] = ['24h', '7d', '30d'];

export function RangeSelector({ value, onChange }: Props) {
  return (
    <div
      role="radiogroup"
      aria-label="기간 선택"
      className="inline-flex gap-[var(--spacing-1)] rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] p-[var(--spacing-1)]"
    >
      {OPTIONS.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          aria-pressed={value === opt}
          aria-label={`최근 ${opt}`}
          className={
            (value === opt
              ? 'bg-[var(--color-primary-600)] text-[var(--color-neutral-50)]'
              : 'text-[var(--color-neutral-700)] hover:bg-[var(--color-neutral-100)]') +
            ' rounded-[var(--radius-sm)] px-[var(--spacing-3)] py-[var(--spacing-1)] text-[var(--font-size-sm)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]'
          }
        >
          {opt}
        </button>
      ))}
    </div>
  );
}
