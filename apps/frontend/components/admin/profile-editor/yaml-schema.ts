/** YAML 스키마 정의 — 자동완성과 검증에 사용 */

export interface FieldSchema {
  key: string;
  description: string;
  type: 'string' | 'number' | 'boolean' | 'enum' | 'array' | 'object';
  required?: boolean;
  enumValues?: string[];
  defaultValue?: unknown;
}

export const PROFILE_SCHEMA: FieldSchema[] = [
  { key: 'id', description: 'Profile ID (영문 소문자+하이픈)', type: 'string', required: true },
  { key: 'name', description: 'Profile 이름', type: 'string', required: true },
  { key: 'description', description: '설명', type: 'string' },
  { key: 'domain_scopes', description: '도메인 범위', type: 'array' },
  { key: 'category_scopes', description: '카테고리 범위', type: 'array' },
  { key: 'security_level_max', description: '최대 보안 수준', type: 'enum', enumValues: ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'SECRET'] },
  { key: 'include_common', description: '공통 데이터 포함', type: 'boolean', defaultValue: true },
  { key: 'mode', description: '오케스트레이션 모드', type: 'enum', required: true, enumValues: ['deterministic', 'agentic', 'workflow', 'hybrid'] },
  { key: 'workflow_id', description: '워크플로우 ID (mode=workflow 시 필수)', type: 'string' },
  { key: 'hybrid_triggers', description: '하이브리드 트리거 (mode=hybrid 시 필수)', type: 'array' },
  { key: 'tools', description: '사용할 도구 목록', type: 'array' },
  { key: 'system_prompt', description: '시스템 프롬프트', type: 'string' },
  { key: 'response_policy', description: '응답 정책', type: 'enum', enumValues: ['strict', 'balanced'] },
  { key: 'guardrails', description: '안전장치', type: 'array', enumValues: ['faithfulness', 'pii_filter'] },
  { key: 'router_model', description: '라우터 LLM', type: 'enum', enumValues: ['haiku', 'sonnet', 'opus'] },
  { key: 'main_model', description: '메인 LLM', type: 'enum', enumValues: ['haiku', 'sonnet', 'opus'] },
  { key: 'memory_type', description: '메모리 타입', type: 'enum', enumValues: ['short', 'session', 'long'] },
  { key: 'memory_ttl_seconds', description: '메모리 TTL (초)', type: 'number' },
  { key: 'memory_scopes', description: '메모리 범위', type: 'array', enumValues: ['local', 'project'] },
  { key: 'memory_project_id', description: '메모리 프로젝트 ID', type: 'string' },
  { key: 'max_tool_calls', description: '최대 도구 호출 (1-20)', type: 'number' },
  { key: 'agent_timeout_seconds', description: '에이전트 타임아웃 (5-300)', type: 'number' },
  { key: 'execution_path', description: '실행 경로', type: 'string' },
  { key: 'validation_nudge_enabled', description: '검증 넛지 활성화', type: 'boolean' },
  { key: 'intent_hints', description: '커스텀 Intent 힌트', type: 'array' },
];

/** 자동완성 대상 필드와 값 */
export const COMPLETION_MAP: Record<string, string[]> = {
  mode: ['deterministic', 'agentic', 'workflow', 'hybrid'],
  security_level_max: ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'SECRET'],
  response_policy: ['strict', 'balanced'],
  router_model: ['haiku', 'sonnet', 'opus'],
  main_model: ['haiku', 'sonnet', 'opus'],
  memory_type: ['short', 'session', 'long'],
};

/** Tool 이름 자동완성 (동적 로드 가능) */
export const DEFAULT_TOOLS = ['rag_search', 'fact_lookup'];

/** Guardrail 자동완성 */
export const DEFAULT_GUARDRAILS = ['faithfulness', 'pii_filter'];
