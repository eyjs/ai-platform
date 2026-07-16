'use client';

import { useMemo } from 'react';
import { getProfileField } from '@/lib/profile/profile-yaml';
import { getFieldMeta, type FieldMeta } from '@/lib/profile/schema-meta';
import type { FieldIssue, ProfileField } from '@/types/profile';
import { useProfileForm } from './form-context';

export interface FieldState {
  /** 스키마에서 온 필드 메타. 스키마에 없는 필드면 null. */
  meta: FieldMeta | null;
  value: unknown;
  setValue: (value: unknown) => void;
  issues: FieldIssue[];
  hasError: boolean;
  controlId: string;
  helpId: string;
  errorId: string;
  /** aria-describedby 에 넣을 id 목록. 없으면 undefined. */
  describedBy: string | undefined;
}

const NO_ISSUES: FieldIssue[] = [];

/** 필드 하나에 필요한 것(값·스키마 메타·이슈·a11y id)을 한 번에 꺼낸다. */
export function useField(key: ProfileField): FieldState {
  const { config, schema, issuesByField, setField } = useProfileForm();

  const meta = useMemo(() => getFieldMeta(schema, key), [schema, key]);
  const issues = issuesByField[key] ?? NO_ISSUES;
  const hasError = issues.some((issue) => issue.severity === 'error');

  const controlId = `profile-field-${key}`;
  const helpId = `${controlId}-help`;
  const errorId = `${controlId}-error`;

  const describedByIds = [
    meta?.description ? helpId : null,
    issues.length > 0 ? errorId : null,
  ].filter((id): id is string => id !== null);

  return {
    meta,
    value: getProfileField(config, key),
    setValue: (value: unknown) => setField(key, value),
    issues,
    hasError,
    controlId,
    helpId,
    errorId,
    describedBy: describedByIds.length > 0 ? describedByIds.join(' ') : undefined,
  };
}

/** 오류 상태일 때 컨트롤 테두리를 덮어쓰는 클래스. */
export function errorBorderClass(hasError: boolean): string {
  return hasError
    ? 'border-[var(--color-error)] focus:ring-[var(--color-error-light)] focus:border-[var(--color-error)]'
    : '';
}
