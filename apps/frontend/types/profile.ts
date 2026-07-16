export type ProfileMode = 'deterministic' | 'agentic' | 'workflow' | 'hybrid';

export interface ProfileListItem {
  id: string;
  name: string;
  description: string | null;
  mode: ProfileMode;
  domainScopes: string[];
  securityLevelMax: string;
  isActive: boolean;
  toolsCount: number;
  mainModel: string;
}

export interface ProfileDetail extends ProfileListItem {
  yamlContent: string;
  config: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface ProfileHistoryItem {
  id: string;
  profileId: string;
  yamlContent: string;
  changedBy: string;
  changedAt: string;
  comment: string | null;
  changeType: 'create' | 'update' | 'restore';
  version: number;
}

export interface ToolItem {
  name: string;
  description: string;
}

/* ------------------------------------------------------------------ *
 * Profile 설정 (YAML 본문)
 *
 * 진실원천은 BFF 가 GET /profiles/schema 로 내려주는 JSON Schema 다.
 * enum 값(mode, security_level_max, guardrails ...)을 여기서 리터럴 유니온으로
 * 다시 적지 않는 것은 의도적이다 — enum 사본을 하나 더 만들면 스키마와 어긋난다.
 * 값 검증은 전적으로 ajv + 스키마가 담당한다.
 * ------------------------------------------------------------------ */

export interface ToolEntry {
  name: string;
  config?: Record<string, unknown>;
}

export interface HybridTrigger {
  keyword_patterns: string[];
  intent_types: string[];
  workflow_id: string;
  description?: string;
}

export interface IntentHint {
  name: string;
  patterns: string[];
  description?: string;
}

export interface ProfileCache {
  enabled?: boolean;
  ttl_seconds?: number;
  agentic_enabled?: boolean;
  [key: string]: unknown;
}

export interface ProfileConfig {
  id: string;
  name: string;
  mode: string;
  description?: string;
  domain_scopes?: string[];
  category_scopes?: string[];
  security_level_max?: string;
  include_common?: boolean;
  workflow_id?: string | null;
  hybrid_triggers?: HybridTrigger[];
  tools?: ToolEntry[];
  rag_min_rerank_score?: number | null;
  system_prompt?: string;
  response_policy?: string;
  guardrails?: string[];
  main_model?: string;
  max_output_tokens?: number | null;
  memory_type?: string;
  memory_ttl_seconds?: number;
  memory_scopes?: string[];
  memory_project_id?: string | null;
  memory_max_turns?: number;
  memory_retention_days?: number | null;
  max_tool_calls?: number;
  agent_timeout_seconds?: number;
  planning_disabled?: boolean;
  workflow_action_endpoint?: string | null;
  workflow_action_headers?: Record<string, unknown>;
  context_adapter?: string | null;
  cache_padding_text?: string;
  empty_response_fallback?: string | null;
  cache?: ProfileCache;
  cache_config?: Record<string, unknown>;
  intent_hints?: IntentHint[];
}

export type ProfileField = keyof ProfileConfig;

export type ProfileFieldValue = ProfileConfig[ProfileField];

/* ------------------------------------------------------------------ *
 * 검증 이슈
 * ------------------------------------------------------------------ */

export type IssueSeverity = 'error' | 'warning';

export interface FieldIssue {
  /** 이슈가 속한 최상위 필드 키. 특정할 수 없으면 null. */
  field: ProfileField | null;
  /** ajv instancePath (예: /tools/0/name). 루트는 빈 문자열. */
  path: string;
  message: string;
  severity: IssueSeverity;
}

/* ------------------------------------------------------------------ *
 * DGX 모델 목록 (GET /profiles/models)
 * ------------------------------------------------------------------ */

export interface DgxModel {
  name: string;
  parameterSize: string;
  contextLength: number;
  capabilities: string[];
  isDefault: boolean;
}

export type DgxModelsSource = 'dgx' | 'unavailable';

export interface DgxModelsResponse {
  models: DgxModel[];
  activeDefault: string;
  source: DgxModelsSource;
  /** source === 'unavailable' 일 때만 존재. */
  error?: string;
}
