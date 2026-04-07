import { type ReactNode } from 'react';
import { cn } from '@/lib/cn';

export interface StatCardProps {
  label: string;
  value: string | number;
  change?: number;
  icon?: ReactNode;
  highlight?: boolean;
  className?: string;
}

export function StatCard({
  label,
  value,
  change,
  icon,
  highlight = false,
  className,
}: StatCardProps) {
  const isPositive = change != null && change > 0;
  const isNegative = change != null && change < 0;

  return (
    <div
      className={cn(
        'rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-4',
        highlight && 'border-[var(--color-error)] bg-[var(--color-error-light)]',
        className,
      )}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[var(--font-size-sm)] text-[var(--color-neutral-600)]">
            {label}
          </p>
          <p className="mt-1 text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
            {value}
          </p>
        </div>
        {icon && (
          <span className="text-[var(--color-neutral-400)]">{icon}</span>
        )}
      </div>
      {change != null && (
        <p
          className={cn(
            'mt-2 text-[var(--font-size-xs)] font-medium',
            isPositive && 'text-[var(--color-success)]',
            isNegative && 'text-[var(--color-error)]',
            !isPositive && !isNegative && 'text-[var(--color-neutral-500)]',
          )}
        >
          {isPositive && '\u25B2'}
          {isNegative && '\u25BC'}
          {change != null && ` ${Math.abs(change).toFixed(1)}%`}
          <span className="ml-1 text-[var(--color-neutral-400)]">
            전일 대비
          </span>
        </p>
      )}
    </div>
  );
}
