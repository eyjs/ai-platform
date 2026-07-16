'use client';

import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/cn';

interface RepeatableListProps {
  /** 항목 개수만큼 렌더된 행들. */
  children: ReactNode;
  isEmpty: boolean;
  emptyText: string;
  addLabel: string;
  onAdd: () => void;
}

/** "행 목록 + 추가 버튼" 패턴. tools / hybrid_triggers / intent_hints 가 공유한다. */
export function RepeatableList({
  children,
  isEmpty,
  emptyText,
  addLabel,
  onAdd,
}: RepeatableListProps) {
  return (
    <div className="flex flex-col gap-2">
      {isEmpty ? (
        <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">{emptyText}</p>
      ) : (
        children
      )}
      <div>
        <Button type="button" variant="secondary" size="sm" onClick={onAdd}>
          {addLabel}
        </Button>
      </div>
    </div>
  );
}

interface RepeatableRowProps {
  title: string;
  onRemove: () => void;
  removeLabel: string;
  children: ReactNode;
}

export function RepeatableRow({ title, onRemove, removeLabel, children }: RepeatableRowProps) {
  return (
    <div className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-page)] p-3">
      <div className="flex items-center justify-between">
        <span className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)]">
          {title}
        </span>
        <button
          type="button"
          aria-label={removeLabel}
          onClick={onRemove}
          className={cn(
            'rounded-[var(--radius-sm)] px-2 py-0.5 text-[var(--font-size-xs)]',
            'text-[var(--color-neutral-500)] hover:text-[var(--color-error)]',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary-200)]',
          )}
        >
          삭제
        </button>
      </div>
      {children}
    </div>
  );
}
