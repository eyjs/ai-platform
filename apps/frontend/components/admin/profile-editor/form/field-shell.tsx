'use client';

import type { ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { formatRange } from '@/lib/profile/schema-meta';
import type { ProfileField } from '@/types/profile';
import { useField } from './use-field';

interface FieldShellProps {
  fieldKey: ProfileField;
  label: string;
  /**
   * false 면 <label> 을 그리지 않는다. 컨트롤이 자체 라벨을 가진 경우(Toggle) 사용.
   * 중첩 <label> 을 만들지 않기 위한 장치다.
   */
  showLabel?: boolean;
  /** "동작 안 함" 같은 상태 배지. */
  badge?: ReactNode;
  /** 스키마 description 뒤에 덧붙일 단위·부가 설명. */
  hint?: string;
  children: ReactNode;
}

/**
 * 라벨 + 스키마 설명(도움말) + 이슈 메시지를 그리는 필드 껍데기.
 * 도움말 문구는 전부 스키마 description 에서 온다 — 화면에 문구를 따로 적지 않는다.
 */
export function FieldShell({
  fieldKey,
  label,
  showLabel = true,
  badge,
  hint,
  children,
}: FieldShellProps) {
  const { meta, issues, controlId, helpId, errorId } = useField(fieldKey);

  const range = meta ? formatRange(meta) : null;
  const helpParts = [meta?.description, hint, range ? `범위: ${range}` : null].filter(
    (part): part is string => Boolean(part),
  );

  return (
    <div className="flex flex-col gap-1.5">
      {(showLabel || badge) && (
        <div className="flex items-center gap-2">
          {showLabel && (
            <label
              htmlFor={controlId}
              className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]"
            >
              {label}
              {meta?.isRequired && (
                <span className="ml-0.5 text-[var(--color-error)]" aria-hidden="true">
                  *
                </span>
              )}
            </label>
          )}
          {badge}
          {!meta && (
            <Badge variant="warning" size="sm">
              스키마에 없음
            </Badge>
          )}
        </div>
      )}

      {children}

      {helpParts.length > 0 && (
        <p id={helpId} className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
          {helpParts.join(' · ')}
        </p>
      )}

      {issues.length > 0 && (
        <ul id={errorId} className="flex flex-col gap-0.5">
          {issues.map((issue, index) => (
            <li
              key={`${issue.path}-${index}`}
              className={
                issue.severity === 'error'
                  ? 'text-[var(--font-size-xs)] text-[var(--color-error)]'
                  : 'text-[var(--font-size-xs)] text-[var(--color-warning)]'
              }
            >
              {issue.severity === 'error' ? '오류: ' : '경고: '}
              {issue.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
