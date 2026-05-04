'use client';

import { Button } from '@/components/ui/button';
import type { ParseError } from '@/types/parse';

interface ParseErrorStateProps {
  error: ParseError;
  onRetry: () => void;
}

const ERROR_HINTS: Record<string, string> = {
  DOCFORGE_UNREACHABLE: '파싱 서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.',
  DOCFORGE_ERROR: '파싱 서버에서 오류가 발생했습니다. 다른 PDF 파일로 시도해보세요.',
  DOCFORGE_NOT_CONFIGURED: '파싱 서버가 설정되지 않았습니다. 관리자에게 문의하세요.',
  DOCFORGE_EMPTY_RESPONSE: '파싱 결과가 비어있습니다. PDF 내용을 확인해주세요.',
  DOCFORGE_UNHEALTHY: '파싱 서버가 비정상 상태입니다. 잠시 후 다시 시도해주세요.',
};

export function ParseErrorState({ error, onRetry }: ParseErrorStateProps) {
  const hint = ERROR_HINTS[error.code] || error.message;

  return (
    <div className="flex w-full max-w-md flex-col items-center gap-[var(--spacing-6)] rounded-[var(--radius-xl)] border border-[var(--color-error-light)] bg-[var(--color-error-light)] p-[var(--spacing-8)]">
      {/* Error Icon */}
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-[var(--color-error-light)]">
        <svg
          className="h-7 w-7 text-[var(--color-error)]"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={1.5}
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
          />
        </svg>
      </div>

      <div className="text-center">
        <h3 className="text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]">
          파싱 실패
        </h3>
        <p className="mt-[var(--spacing-2)] text-[var(--font-size-sm)] text-[var(--color-neutral-600)]">
          {hint}
        </p>
        {error.code && (
          <p className="mt-[var(--spacing-1)] text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
            오류 코드: {error.code}
          </p>
        )}
      </div>

      <Button
        variant="primary"
        size="md"
        onClick={onRetry}
        aria-label="파싱 재시도"
      >
        다시 시도
      </Button>
    </div>
  );
}
