'use client';

import { cn } from '@/lib/cn';
import type { LlmLinkStatus } from '@/types/llm-engines';
import {
  LINK_STATE_LABEL,
  LINK_STATE_SYMBOL,
  formatLatency,
  formatRelativeCheckedAt,
  toLatencyLevel,
  toLinkState,
} from './llm-engine-format';

const stateTextStyles = {
  up: 'text-[var(--color-success)]',
  down: 'text-[var(--color-error)]',
  unknown: 'text-[var(--color-neutral-500)]',
} as const;

// 부하 등급별 색. 응답시간이 정상 색과 별개다 — 연결은 됐는데(up) 느린 상태를 드러낸다.
const latencyStyles = {
  fast: 'text-[var(--color-success)]',
  warn: 'text-[var(--color-warning)]',
  slow: 'text-[var(--color-error)]',
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
  const latencyLevel = toLatencyLevel(link.latencyMs);
  const latencyText = formatLatency(link.latencyMs);

  return (
    <span className={cn('inline-flex flex-col gap-0.5', className)}>
      <span className="inline-flex items-center gap-2">
        <span className={cn('inline-flex items-center gap-1.5', stateTextStyles[state])}>
          <span aria-hidden="true" className="text-[var(--font-size-xs)] leading-none">
            {LINK_STATE_SYMBOL[state]}
          </span>
          <span className="text-[var(--font-size-sm)] font-medium">
            {LINK_STATE_LABEL[state]}
          </span>
        </span>
        {/* 응답시간 = 부하. up이어도 느리면 경고색으로 드러난다. */}
        {latencyText && latencyLevel && (
          <span
            className={cn('text-[var(--font-size-xs)] font-medium tabular-nums', latencyStyles[latencyLevel])}
            title="프로브 응답시간 (서버 부하 지표)"
          >
            {latencyText}
          </span>
        )}
      </span>
      {showMeta && (relative || link.detail) && (
        <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
          {[relative, link.detail].filter(Boolean).join(' · ')}
        </span>
      )}
    </span>
  );
}
