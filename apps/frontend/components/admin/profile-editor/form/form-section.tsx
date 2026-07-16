'use client';

import { useId, useState, type ReactNode } from 'react';
import { cn } from '@/lib/cn';
import { Badge } from '@/components/ui/badge';

interface FormSectionProps {
  title: string;
  description?: string;
  /** 이 섹션에 포함된 오류 개수. 0 이면 배지를 그리지 않는다. */
  errorCount?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}

/** 접이식 폼 섹션. 오류가 있는 섹션은 닫혀 있어도 배지로 드러난다. */
export function FormSection({
  title,
  description,
  errorCount = 0,
  defaultOpen = true,
  children,
}: FormSectionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const panelId = useId();

  return (
    <section className="rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)]">
      <button
        type="button"
        aria-expanded={isOpen}
        aria-controls={panelId}
        onClick={() => setIsOpen((prev) => !prev)}
        className={cn(
          'flex w-full items-center justify-between gap-2 rounded-[var(--radius-lg)] px-4 py-3 text-left',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary-200)]',
          'hover:bg-[var(--color-neutral-50)] transition-colors',
        )}
      >
        <span className="flex items-center gap-2">
          <span className="text-[var(--font-size-sm)] font-semibold text-[var(--color-neutral-900)]">
            {title}
          </span>
          {errorCount > 0 && (
            <Badge variant="error" size="sm">
              오류 {errorCount}
            </Badge>
          )}
        </span>
        <svg
          className={cn(
            'h-4 w-4 shrink-0 text-[var(--color-neutral-400)] transition-transform',
            isOpen && 'rotate-180',
          )}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <div id={panelId} className="flex flex-col gap-4 border-t border-[var(--color-neutral-200)] px-4 py-4">
          {description && (
            <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
              {description}
            </p>
          )}
          {children}
        </div>
      )}
    </section>
  );
}
