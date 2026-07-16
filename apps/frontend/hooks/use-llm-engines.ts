'use client';

import { useCallback } from 'react';
import { usePolling } from '@/hooks/use-polling';
import { fetchLlmEngines } from '@/lib/api/llm-engines';
import type { LlmEnginesHealth } from '@/types/llm-engines';

/** DGX 연결 상태는 운영자가 "지금" 봐야 하는 값이라 짧게 잡는다. */
export const LLM_ENGINES_POLL_INTERVAL_MS = 10_000;

/**
 * GET /api/health/llm-engines 를 주기적으로 폴링한다.
 * 타이머는 기존 usePolling에 위임한다(별도 setInterval 금지).
 *
 * 주의: usePolling은 실패 시 직전 data를 유지한 채 error만 채운다.
 * 따라서 data가 있는데 error도 있으면 "마지막으로 성공한 스냅샷"이며,
 * 화면은 이를 최신으로 오인시키지 않아야 한다.
 */
export function useLlmEngines(interval: number = LLM_ENGINES_POLL_INTERVAL_MS) {
  const fetchFn = useCallback(() => fetchLlmEngines(), []);
  return usePolling<LlmEnginesHealth>({ fetchFn, interval });
}
