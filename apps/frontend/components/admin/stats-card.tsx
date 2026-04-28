'use client';

import { type ReactNode } from 'react';
import { cn } from '@/lib/cn';

const variantStyles = {
  default: 'border-[var(--color-neutral-200)] bg-[var(--surface-card)]',
  success: 'border-[var(--color-success)] bg-[var(--color-success-light)]',
  warning: 'border-[var(--color-warning)] bg-[var(--color-warning-light)]',
  error: 'border-[var(--color-error)] bg-[var(--color-error-light)]',
} as const;

export interface StatsCardProps {
  title: string;
  value: string | number;
  change?: number;
  icon?: ReactNode;
  variant?: keyof typeof variantStyles;
  className?: string;
}

export function StatsCard({
  title,
  value,
  change,
  icon,
  variant = 'default',
  className,
}: StatsCardProps) {
  const isPositive = change != null && change > 0;
  const isNegative = change != null && change < 0;

  return (
    <div
      className={cn(
        'rounded-[var(--radius-lg)] border p-4',
        variantStyles[variant],
        className,
      )}
    >
      <div className="flex items-start justify-between">
        <div className="min-w-0 flex-1">
          <p className="text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
            {title}
          </p>
          <p className="mt-1 text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
            {value}
          </p>
        </div>
        {icon && (
          <span className="shrink-0 text-[var(--color-neutral-400)]">{icon}</span>
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
          {isPositive && '▲'}
          {isNegative && '▼'}
          {` ${Math.abs(change).toFixed(1)}%`}
          <span className="ml-1 text-[var(--color-neutral-400)]">전일 대비</span>
        </p>
      )}
    </div>
  );
}
