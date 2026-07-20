/**
 * FastAPI(apps/api) GET /api/health/llm-engines 응답 도메인 타입.
 *
 * 모든 object 타입은 `interface`가 아닌 `type` 별칭으로 선언한다.
 * DataTable<T extends Record<string, unknown>>에 그대로 넘기려면 암묵적 index
 * signature가 필요한데, interface 선언에는 그것이 붙지 않는다.
 */

/**
 * 링크 생존 여부는 3상태다.
 * - true  : 살아 있음(실제 프로브 성공)
 * - false : 죽음(프로브 실패)
 * - null  : 미확인/미감시 — 죽음이 아니다. 절대 false로 접어서는 안 된다.
 */
export type LinkState = 'up' | 'down' | 'unknown';

export type LlmLinkStatus = {
  up: boolean | null;
  /** UNIX epoch **초**(float). 밀리초가 아니다. */
  checkedAt: number | null;
  detail: string | null;
  /**
   * 프로브 응답시간(ms) = 부하 지표. up이어도 이 값이 크면 서버가 느려지는 중이다.
   * DGX는 여러 소비자가 공유하는 GPU라 부하가 상수 — up/down만으론 안 보인다.
   * null이면 아직 안 잼(미점검).
   */
  latencyMs: number | null;
};

export type DgxModel = {
  name: string;
  parameterSize: string | null;
  contextLength: number | null;
  capabilities: string[];
  isDefault: boolean;
};

export type DgxStatus = {
  configured: boolean;
  baseUrl: string | null;
  defaultModel: string | null;
  /** 역할별 모델 오버라이드. 빈 문자열이면 defaultModel을 쓴다는 뜻. */
  roleOverrides: Record<string, string>;
  link: LlmLinkStatus;
  models: DgxModel[];
  /** 모델 목록 조회 실패 사유. 있으면 목록은 신뢰할 수 없다. */
  modelsError: string | null;
};

export type MlxEngine = {
  roles: string[];
  url: string;
  model: string | null;
  link: LlmLinkStatus;
  modelError: string | null;
};

export type MlxStatus = {
  engines: MlxEngine[];
};

/**
 * provider 모드. 'development'(로컬 MLX) / 'anthropic'(Claude)이 알려진 값이나,
 * api가 새 모드를 추가할 수 있으므로 닫힌 union으로 고정하지 않는다.
 */
export type ProviderMode = string;

export type LlmEnginesHealth = {
  providerMode: ProviderMode;
  fallbackEnabled: boolean;
  dgx: DgxStatus;
  mlx: MlxStatus;
};
