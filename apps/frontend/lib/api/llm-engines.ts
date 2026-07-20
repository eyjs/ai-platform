import { getAccessToken } from '@/lib/auth/token-storage';
import type {
  DgxModel,
  DgxStatus,
  LlmEnginesHealth,
  LlmLinkStatus,
  MlxEngine,
  MlxStatus,
} from '@/types/llm-engines';

/**
 * FastAPI(apps/api) GET /api/health/llm-engines — DGX/MLX 서빙 실시간 상태.
 * bff는 DB-direct라 라이브 상태에 부적합 → hardware.ts와 동일하게 api를 직접 호출한다.
 * ADMIN JWT 필수.
 *
 * env 주의: apps/frontend의 CLAUDE.md는 `NEXT_PUBLIC_API_URL`을 문서화하지만,
 * 실제로 배선되어 동작 중인 변수는 `NEXT_PUBLIC_FASTAPI_URL`이다
 * (chat.ts / hardware.ts / inspect.ts 전부 후자를 쓴다). 운영에서 조용히
 * localhost로 폴백하는 사고를 막기 위해 실사용 변수를 우선하고,
 * 문서상의 이름도 함께 허용한다.
 */
const API_BASE_URL =
  process.env.NEXT_PUBLIC_FASTAPI_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  'http://localhost:8000';

const ENDPOINT_PATH = '/api/health/llm-engines';

/** 외부 데이터 불신 — 경계에서 unknown을 좁힌다. `any` 금지. */
function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function asFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** 엄격한 boolean. true/false가 아니면 null(미확인)이다. */
function asStrictBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string');
}

function asStringRecord(value: unknown): Record<string, string> {
  const raw = asRecord(value);
  if (!raw) return {};
  return Object.entries(raw).reduce<Record<string, string>>((acc, [key, val]) => {
    return typeof val === 'string' ? { ...acc, [key]: val } : acc;
  }, {});
}

/**
 * up이 boolean이 아니면 null(미확인)로 남긴다.
 * 여기서 false로 접으면 살아 있는 엔진을 죽었다고 보고하게 된다.
 */
function normalizeLink(value: unknown): LlmLinkStatus {
  const raw = asRecord(value);
  if (!raw) return { up: null, checkedAt: null, detail: null, latencyMs: null };
  return {
    up: asStrictBoolean(raw.up),
    checkedAt: asFiniteNumber(raw.checkedAt),
    detail: asString(raw.detail),
    latencyMs: asFiniteNumber(raw.latencyMs),
  };
}

function normalizeDgxModel(value: unknown): DgxModel | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const name = asString(raw.name);
  if (!name) return null;
  return {
    name,
    parameterSize: asString(raw.parameterSize),
    contextLength: asFiniteNumber(raw.contextLength),
    capabilities: asStringArray(raw.capabilities),
    isDefault: asStrictBoolean(raw.isDefault) ?? false,
  };
}

function normalizeDgx(value: unknown): DgxStatus {
  const raw = asRecord(value);
  if (!raw) {
    throw new Error('LLM 엔진 상태 응답에 dgx 정보가 없습니다');
  }
  const rawModels = Array.isArray(raw.models) ? raw.models : [];
  return {
    configured: asStrictBoolean(raw.configured) ?? false,
    baseUrl: asString(raw.baseUrl),
    defaultModel: asString(raw.defaultModel),
    roleOverrides: asStringRecord(raw.roleOverrides),
    link: normalizeLink(raw.link),
    models: rawModels
      .map(normalizeDgxModel)
      .filter((model): model is DgxModel => model !== null),
    modelsError: asString(raw.modelsError),
  };
}

function normalizeMlxEngine(value: unknown): MlxEngine | null {
  const raw = asRecord(value);
  if (!raw) return null;
  const url = asString(raw.url);
  if (!url) return null;
  return {
    roles: asStringArray(raw.roles),
    url,
    model: asString(raw.model),
    link: normalizeLink(raw.link),
    modelError: asString(raw.modelError),
  };
}

function normalizeMlx(value: unknown): MlxStatus {
  const raw = asRecord(value);
  const rawEngines = raw && Array.isArray(raw.engines) ? raw.engines : [];
  return {
    engines: rawEngines
      .map(normalizeMlxEngine)
      .filter((engine): engine is MlxEngine => engine !== null),
  };
}

export function normalizeLlmEnginesHealth(value: unknown): LlmEnginesHealth {
  const raw = asRecord(value);
  if (!raw) {
    throw new Error('LLM 엔진 상태 응답 형식이 올바르지 않습니다');
  }
  return {
    providerMode: asString(raw.providerMode) ?? 'unknown',
    fallbackEnabled: asStrictBoolean(raw.fallbackEnabled) ?? false,
    dgx: normalizeDgx(raw.dgx),
    mlx: normalizeMlx(raw.mlx),
  };
}

function toErrorMessage(status: number): string {
  if (status === 401 || status === 403) return '권한이 없습니다';
  if (status === 404) {
    return 'LLM 엔진 상태 API를 찾을 수 없습니다 (HTTP 404)';
  }
  return `LLM 엔진 상태를 불러올 수 없습니다 (HTTP ${status})`;
}

export async function fetchLlmEngines(signal?: AbortSignal): Promise<LlmEnginesHealth> {
  const token = getAccessToken();
  const res = await fetch(`${API_BASE_URL}${ENDPOINT_PATH}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: 'no-store',
    signal,
  });

  if (!res.ok) {
    throw new Error(toErrorMessage(res.status));
  }

  const payload: unknown = await res.json().catch(() => {
    throw new Error('LLM 엔진 상태 응답을 해석할 수 없습니다');
  });

  return normalizeLlmEnginesHealth(payload);
}
