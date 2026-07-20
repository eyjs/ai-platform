import type { LinkState, ProviderMode } from '@/types/llm-engines';

/**
 * 3상태 매핑. null(미확인)을 down으로 접지 않는 것이 이 함수의 존재 이유다.
 */
export function toLinkState(up: boolean | null): LinkState {
  if (up === true) return 'up';
  if (up === false) return 'down';
  return 'unknown';
}

export const LINK_STATE_LABEL: Record<LinkState, string> = {
  up: '연결됨',
  down: '연결 끊김',
  unknown: '확인 안 됨',
};

/** 색맹/흑백 환경에서도 구분되도록 색과 별개로 붙이는 기호. */
export const LINK_STATE_SYMBOL: Record<LinkState, string> = {
  up: '●', // ●
  down: '✕', // ✕
  unknown: '○', // ○
};

/**
 * checkedAt은 UNIX epoch **초**(float)다. Date는 밀리초를 받으므로 1000을 곱한다.
 * 여기서 단위를 틀리면 1970년 또는 수만 년 후가 찍힌다.
 */
export function checkedAtToDate(checkedAt: number | null): Date | null {
  if (checkedAt === null || !Number.isFinite(checkedAt)) return null;
  return new Date(checkedAt * 1000);
}

/** 부하 등급 임계(ms). 정상 프로브는 수백 ms — 초 단위로 넘어가면 부하가 실린 것. */
export const LATENCY_WARN_MS = 2_000;
export const LATENCY_SLOW_MS = 5_000;

export type LatencyLevel = 'fast' | 'warn' | 'slow';

/** 응답시간을 부하 등급으로. null(미측정)은 등급을 매기지 않는다. */
export function toLatencyLevel(latencyMs: number | null): LatencyLevel | null {
  if (latencyMs === null || !Number.isFinite(latencyMs)) return null;
  if (latencyMs >= LATENCY_SLOW_MS) return 'slow';
  if (latencyMs >= LATENCY_WARN_MS) return 'warn';
  return 'fast';
}

/** "0.4초"·"18.1초" 같은 사람이 읽는 응답시간. 없으면 null. */
export function formatLatency(latencyMs: number | null): string | null {
  if (latencyMs === null || !Number.isFinite(latencyMs)) return null;
  if (latencyMs < 1_000) return `${Math.round(latencyMs)}ms`;
  return `${(latencyMs / 1_000).toFixed(1)}초`;
}

/**
 * "N초 전 확인" 상대 시각. 데이터가 없으면 null(문구 없음) — 추측하지 않는다.
 */
export function formatRelativeCheckedAt(
  checkedAt: number | null,
  nowMs: number = Date.now(),
): string | null {
  if (checkedAt === null || !Number.isFinite(checkedAt)) return null;

  const diffSeconds = Math.round(nowMs / 1000 - checkedAt);
  // 미세한 시계 오차로 미래가 나오면 방금으로 취급한다.
  if (diffSeconds < 0) return '방금 확인';
  if (diffSeconds < 60) return `${diffSeconds}초 전 확인`;
  if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}분 전 확인`;
  if (diffSeconds < 86400) return `${Math.floor(diffSeconds / 3600)}시간 전 확인`;
  return `${Math.floor(diffSeconds / 86400)}일 전 확인`;
}

/**
 * 262144 -> "262K", 1048576 -> "1M". (1000 기준 축약)
 */
export function formatContextLength(contextLength: number | null): string {
  if (contextLength === null || !Number.isFinite(contextLength)) return '-';
  if (contextLength < 0) return '-';
  if (contextLength >= 1_000_000) {
    const millions = contextLength / 1_000_000;
    const rounded = Math.round(millions * 10) / 10;
    return `${rounded}M`;
  }
  if (contextLength >= 1_000) return `${Math.round(contextLength / 1_000)}K`;
  return String(contextLength);
}

/** agentic 프로필은 bind_tools에 의존한다 → tools 없는 모델은 쓸 수 없다. */
export const TOOLS_CAPABILITY = 'tools';

export function hasToolsCapability(capabilities: string[]): boolean {
  return capabilities.includes(TOOLS_CAPABILITY);
}

/**
 * DGX가 끊겼을 때 실제로 무엇이 대신 도는지.
 * fallbackEnabled=false면 대체 경로가 없다(전 LLM 정지).
 */
export function describeFallback(
  providerMode: ProviderMode,
  fallbackEnabled: boolean,
): string {
  if (!fallbackEnabled) {
    return '폴백이 꺼져 있습니다. DGX가 끊기면 대체 경로 없이 모든 LLM 호출이 중단됩니다.';
  }
  if (providerMode === 'development') {
    return 'DGX가 끊기면 호스트 MLX 엔진으로 폴백합니다.';
  }
  if (providerMode === 'anthropic') {
    return 'DGX가 끊기면 Claude(Anthropic)로 폴백합니다.';
  }
  return `DGX가 끊기면 폴백이 동작합니다 (provider 모드: ${providerMode}).`;
}
