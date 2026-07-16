'use client';

import { cn } from '@/lib/cn';
import type { LlmEnginesHealth } from '@/types/llm-engines';
import {
  LINK_STATE_LABEL,
  LINK_STATE_SYMBOL,
  describeFallback,
  formatRelativeCheckedAt,
  toLinkState,
} from './llm-engine-format';

const HEADLINE_TEXT = {
  up: 'DGX Spark 연결됨',
  down: 'DGX Spark 연결 끊김',
  unknown: 'DGX Spark 상태 미확인',
} as const;

const containerStyles = {
  up: 'border-[var(--color-success)] bg-[var(--color-success-light)]',
  down: 'border-[var(--color-error)] bg-[var(--color-error-light)]',
  unknown: 'border-[var(--color-neutral-300)] bg-[var(--color-neutral-50)]',
} as const;

const accentTextStyles = {
  up: 'text-[var(--color-success)]',
  down: 'text-[var(--color-error)]',
  unknown: 'text-[var(--color-neutral-600)]',
} as const;

export interface DgxStatusHeadlineProps {
  health: LlmEnginesHealth;
  className?: string;
}

/**
 * 운영자가 가장 먼저 봐야 하는 한 줄: 지금 DGX가 붙어 있나?
 * 끊겼으면 대신 무엇이 도는지(폴백)까지 같은 화면에서 말해준다.
 */
export function DgxStatusHeadline({ health, className }: DgxStatusHeadlineProps) {
  const { dgx, providerMode, fallbackEnabled } = health;
  const state = dgx.configured ? toLinkState(dgx.link.up) : 'unknown';
  const relative = formatRelativeCheckedAt(dgx.link.checkedAt);
  const fallbackText = describeFallback(providerMode, fallbackEnabled);
  const isFallbackCritical = !fallbackEnabled;

  return (
    <section
      role="status"
      aria-live="polite"
      aria-label="DGX Spark 연결 상태"
      className={cn(
        'rounded-[var(--radius-lg)] border-2 p-5',
        containerStyles[state],
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className={cn('text-[var(--font-size-2xl)] leading-none', accentTextStyles[state])}
          >
            {LINK_STATE_SYMBOL[state]}
          </span>
          <div>
            <p
              className={cn(
                'text-[var(--font-size-xl)] font-bold',
                accentTextStyles[state],
              )}
            >
              {dgx.configured ? HEADLINE_TEXT[state] : 'DGX Spark 미설정'}
            </p>
            <p className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
              {dgx.configured
                ? [
                    `상태: ${LINK_STATE_LABEL[state]}`,
                    relative,
                    dgx.link.detail,
                  ]
                    .filter(Boolean)
                    .join(' · ')
                : 'DGX 연결 정보가 구성되지 않았습니다'}
            </p>
          </div>
        </div>

        <dl className="flex flex-wrap gap-x-6 gap-y-2">
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
              Base URL
            </dt>
            <dd className="mt-0.5 font-[family-name:var(--font-mono)] text-[var(--font-size-xs)] text-[var(--color-neutral-800)]">
              {dgx.baseUrl ?? '-'}
            </dd>
          </div>
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
              기본 모델
            </dt>
            <dd className="mt-0.5 font-[family-name:var(--font-mono)] text-[var(--font-size-xs)] text-[var(--color-neutral-800)]">
              {dgx.defaultModel ?? '-'}
            </dd>
          </div>
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
              Provider 모드
            </dt>
            <dd className="mt-0.5 font-[family-name:var(--font-mono)] text-[var(--font-size-xs)] text-[var(--color-neutral-800)]">
              {providerMode}
            </dd>
          </div>
        </dl>
      </div>

      {/* 끊겼을 때는 "그래서 지금 뭐가 도나"가 본문이 된다. */}
      <p
        className={cn(
          'mt-4 rounded-[var(--radius-md)] px-3 py-2 text-[var(--font-size-sm)]',
          state === 'down' || isFallbackCritical
            ? 'bg-[var(--color-neutral-0)] font-medium text-[var(--color-neutral-900)]'
            : 'text-[var(--color-neutral-600)]',
        )}
      >
        <span className="font-semibold">
          {state === 'down' ? '폴백 경로: ' : '장애 시: '}
        </span>
        {fallbackText}
      </p>
    </section>
  );
}
