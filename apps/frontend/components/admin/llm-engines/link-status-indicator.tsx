'use client';

import { cn } from '@/lib/cn';
import type { LlmLinkStatus } from '@/types/llm-engines';
import {
  LINK_STATE_LABEL,
  LINK_STATE_SYMBOL,
  formatRelativeCheckedAt,
  toLinkState,
} from './llm-engine-format';

const stateTextStyles = {
  up: 'text-[var(--color-success)]',
  down: 'text-[var(--color-error)]',
  unknown: 'text-[var(--color-neutral-500)]',
} as const;

export interface LinkStatusIndicatorProps {
  link: LlmLinkStatus;
  /** 확인 시각/상세를 함께 보여줄지. 표 안처럼 좁은 곳에서는 끈다. */
  showMeta?: boolean;
  className?: string;
}

/**
 * 3상태(up/down/unknown) 링크 표시.
 * 색에만 의존하지 않도록 기호 + 한글 라벨을 항상 함께 렌더한다.
 */
export function LinkStatusIndicator({
  link,
  showMeta = true,
  className,
}: LinkStatusIndicatorProps) {
  const state = toLinkState(link.up);
  const relative = formatRelativeCheckedAt(link.checkedAt);

  return (
    <span className={cn('inline-flex flex-col gap-0.5', className)}>
      <span className={cn('inline-flex items-center gap-1.5', stateTextStyles[state])}>
        <span aria-hidden="true" className="text-[var(--font-size-xs)] leading-none">
          {LINK_STATE_SYMBOL[state]}
        </span>
        <span className="text-[var(--font-size-sm)] font-medium">
          {LINK_STATE_LABEL[state]}
        </span>
      </span>
      {showMeta && (relative || link.detail) && (
        <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
          {[relative, link.detail].filter(Boolean).join(' · ')}
        </span>
      )}
    </span>
  );
}
