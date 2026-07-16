/**
 * Profile YAML <-> 폼 상태 변환.
 *
 * BFF 와의 통신 포맷은 여전히 `{ yamlContent: string }` 이므로, 폼은 저장 직전
 * 항상 이 모듈을 통해 YAML 로 직렬화된다.
 */

import { dump, load, YAMLException } from 'js-yaml';
import type { ProfileConfig, ProfileField } from '@/types/profile';

export interface YamlParseResult {
  config: ProfileConfig | null;
  /** 파싱 실패 사유. 성공 시 null. */
  error: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * YAML 텍스트 → 설정 객체.
 *
 * 여기서는 **스키마 검증을 하지 않는다.** 문법이 맞으면 통과시키고,
 * 값의 옳고 짐은 ajv 가 판단한다 (검증 출처를 한 곳으로 유지).
 */
export function parseProfileYaml(text: string): YamlParseResult {
  if (!text.trim()) {
    return { config: null, error: 'YAML 이 비어 있습니다' };
  }

  try {
    const parsed: unknown = load(text);
    if (!isRecord(parsed)) {
      return { config: null, error: 'YAML 최상위는 키-값 매핑이어야 합니다' };
    }
    return { config: parsed as unknown as ProfileConfig, error: null };
  } catch (err) {
    if (err instanceof YAMLException) {
      const line = err.mark ? ` (${err.mark.line + 1}번째 줄)` : '';
      return { config: null, error: `YAML 문법 오류${line}: ${err.reason}` };
    }
    return {
      config: null,
      error: err instanceof Error ? `YAML 파싱 실패: ${err.message}` : 'YAML 파싱 실패',
    };
  }
}

/**
 * 직렬화 시 키 순서. 폼 섹션 순서와 같게 두어 YAML 탭이 읽기 쉬운 상태를 유지한다.
 * 목록에 없는 키는 (스키마가 늘어난 경우에도) 뒤에 원래 순서대로 붙는다.
 */
const FIELD_ORDER: ProfileField[] = [
  'id',
  'name',
  'description',
  'mode',
  'workflow_id',
  'hybrid_triggers',
  'domain_scopes',
  'category_scopes',
  'include_common',
  'security_level_max',
  'rag_min_rerank_score',
  'tools',
  'system_prompt',
  'response_policy',
  'guardrails',
  'empty_response_fallback',
  'main_model',
  'memory_type',
  'memory_ttl_seconds',
  'memory_scopes',
  'memory_project_id',
  'memory_max_turns',
  'memory_retention_days',
  'max_tool_calls',
  'agent_timeout_seconds',
  'planning_disabled',
  'max_output_tokens',
  'context_adapter',
  'cache',
  'cache_config',
  'cache_padding_text',
  'intent_hints',
  'workflow_action_endpoint',
  'workflow_action_headers',
];

/**
 * 설정 객체 → YAML 텍스트.
 *
 * `undefined` 인 키는 생략한다 (= 미설정). `null` 은 명시적 값이므로 남긴다.
 */
export function serializeProfileYaml(config: ProfileConfig): string {
  const source: Record<string, unknown> = { ...config };
  const ordered: Record<string, unknown> = {};

  for (const key of FIELD_ORDER) {
    if (key in source && source[key] !== undefined) {
      ordered[key] = source[key];
    }
  }
  for (const [key, value] of Object.entries(source)) {
    if (!(key in ordered) && value !== undefined) {
      ordered[key] = value;
    }
  }

  return dump(ordered, {
    lineWidth: -1,
    noRefs: true,
    sortKeys: false,
    quotingType: '"',
  });
}

/** 불변 업데이트. 값이 undefined 면 키를 제거한다. */
export function setProfileField(
  config: ProfileConfig,
  key: ProfileField,
  value: unknown,
): ProfileConfig {
  const next: Record<string, unknown> = { ...config };
  if (value === undefined) {
    delete next[key];
  } else {
    next[key] = value;
  }
  return next as unknown as ProfileConfig;
}

/**
 * 필드 값 읽기.
 *
 * 폼 컨트롤은 필드 키를 문자열로 다루므로 값 타입은 unknown 으로 나간다.
 * 각 컨트롤이 타입 가드로 좁혀서 쓴다 (any 를 쓰지 않기 위한 경계).
 */
export function getProfileField(config: ProfileConfig, key: ProfileField): unknown {
  return (config as unknown as Record<string, unknown>)[key];
}
