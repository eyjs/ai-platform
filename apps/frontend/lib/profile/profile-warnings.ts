/**
 * 저장을 막지 않는 경고.
 *
 * 스키마 위반(= 저장 차단)과 구분된다. 여기 있는 것은 "문법상 유효하지만
 * 아마 의도한 게 아닐 것"에 대한 안내다.
 */

import type { FieldIssue, ProfileConfig } from '@/types/profile';

export function getProfileWarnings(config: ProfileConfig): FieldIssue[] {
  const warnings: FieldIssue[] = [];

  if (!config.system_prompt?.trim()) {
    warnings.push({
      field: 'system_prompt',
      path: '/system_prompt',
      message: '비어 있으면 플랫폼 기본 프롬프트가 사용됩니다',
      severity: 'warning',
    });
  }

  if (!config.tools || config.tools.length === 0) {
    warnings.push({
      field: 'tools',
      path: '/tools',
      message: '도구가 없으면 RAG 검색 없이 동작합니다',
      severity: 'warning',
    });
  }

  if (config.category_scopes && config.category_scopes.length > 0) {
    warnings.push({
      field: 'category_scopes',
      path: '/category_scopes',
      message: 'category_scopes 는 런타임에 배선되어 있지 않아 검색 결과에 영향을 주지 않습니다',
      severity: 'warning',
    });
  }

  return warnings;
}
