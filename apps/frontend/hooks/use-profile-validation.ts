'use client';

import { useMemo } from 'react';
import type { ValidateFunction } from 'ajv';
import { compileProfileValidator, validateProfile } from '@/lib/profile/schema-validator';
import { getProfileWarnings } from '@/lib/profile/profile-warnings';
import { getMainModelIssues } from '@/lib/profile/model-rules';
import type { JsonSchema } from '@/lib/profile/schema-meta';
import type {
  DgxModelsResponse,
  FieldIssue,
  ProfileConfig,
  ProfileField,
} from '@/types/profile';

export type IssueMap = Partial<Record<ProfileField, FieldIssue[]>>;

export interface ProfileValidationResult {
  issues: FieldIssue[];
  issuesByField: IssueMap;
  /** 어느 필드에도 매달 수 없는 이슈 (상태바/미리보기에서만 보인다). */
  globalIssues: FieldIssue[];
  errors: FieldIssue[];
  warnings: FieldIssue[];
  /** 스키마 오류가 없으면 true. 경고는 저장을 막지 않는다. */
  isValid: boolean;
}

function groupByField(issues: FieldIssue[]): IssueMap {
  const map: IssueMap = {};
  for (const issue of issues) {
    if (!issue.field) continue;
    const list = map[issue.field];
    if (list) list.push(issue);
    else map[issue.field] = [issue];
  }
  return map;
}

/**
 * 스키마 + 모델 목록 + 경고를 합친 단일 검증 결과.
 *
 * 스키마 검증은 전부 ajv 가 한다. 이 훅에는 enum·범위 상수가 없다.
 */
export function useProfileValidation(
  schema: JsonSchema | null,
  config: ProfileConfig | null,
  modelsResponse: DgxModelsResponse | null,
): ProfileValidationResult {
  const validator: ValidateFunction | null = useMemo(() => {
    if (!schema) return null;
    try {
      return compileProfileValidator(schema);
    } catch {
      // 스키마 자체가 컴파일되지 않는 경우. 아래에서 전역 오류로 보고한다.
      return null;
    }
  }, [schema]);

  return useMemo(() => {
    if (!config) {
      return {
        issues: [],
        issuesByField: {},
        globalIssues: [],
        errors: [],
        warnings: [],
        isValid: false,
      };
    }

    if (!validator) {
      const issue: FieldIssue = {
        field: null,
        path: '',
        message: schema
          ? '스키마를 해석할 수 없어 검증할 수 없습니다'
          : '스키마를 불러오지 못해 검증할 수 없습니다',
        severity: 'error',
      };
      return {
        issues: [issue],
        issuesByField: {},
        globalIssues: [issue],
        errors: [issue],
        warnings: [],
        isValid: false,
      };
    }

    const issues: FieldIssue[] = [
      ...validateProfile(validator, config),
      ...getMainModelIssues(config, modelsResponse),
      ...getProfileWarnings(config),
    ];

    const errors = issues.filter((issue) => issue.severity === 'error');

    return {
      issues,
      issuesByField: groupByField(issues),
      globalIssues: issues.filter((issue) => issue.field === null),
      errors,
      warnings: issues.filter((issue) => issue.severity === 'warning'),
      isValid: errors.length === 0,
    };
  }, [validator, schema, config, modelsResponse]);
}
