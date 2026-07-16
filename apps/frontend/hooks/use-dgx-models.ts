'use client';

import { useEffect, useState } from 'react';
import { fetchDgxModels } from '@/lib/api/bff-profiles';
import type { DgxModelsResponse } from '@/types/profile';

let cached: DgxModelsResponse | null = null;

/**
 * DGX 서빙 모델 목록.
 *
 * 요청 자체가 실패해도 하드코딩 목록으로 대체하지 않는다. `source: 'unavailable'`
 * 로 정규화해서 "목록을 못 받았다"는 사실을 UI 가 그대로 말하게 한다.
 */
export function useDgxModels() {
  const [models, setModels] = useState<DgxModelsResponse | null>(cached);
  const [isLoading, setIsLoading] = useState<boolean>(cached === null);

  useEffect(() => {
    if (cached !== null) return;
    let isActive = true;

    fetchDgxModels()
      .then((result) => {
        cached = result;
        if (isActive) setModels(result);
      })
      .catch((err: unknown) => {
        const fallback: DgxModelsResponse = {
          models: [],
          activeDefault: '',
          source: 'unavailable',
          error: err instanceof Error ? err.message : '모델 목록 요청 실패',
        };
        // 실패 응답은 캐싱하지 않는다 — 다음 진입에서 재시도할 수 있어야 한다.
        if (isActive) setModels(fallback);
      })
      .finally(() => {
        if (isActive) setIsLoading(false);
      });

    return () => {
      isActive = false;
    };
  }, []);

  return { models, isLoading };
}
