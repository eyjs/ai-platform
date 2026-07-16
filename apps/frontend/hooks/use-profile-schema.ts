'use client';

import { useEffect, useState } from 'react';
import { fetchProfileSchema } from '@/lib/api/bff-profiles';
import type { JsonSchema } from '@/lib/profile/schema-meta';

let cached: JsonSchema | null = null;

/**
 * Profile JSON Schema 를 BFF 에서 1회 fetch 하여 메모리에 캐싱.
 *
 * 폼 렌더링(설명·enum·범위)과 저장 검증(ajv)이 모두 이 스키마 하나에 매달린다.
 * 스키마를 못 받으면 폼을 렌더링하지 않는다 — 추측으로 필드를 그리면 스키마와
 * 어긋난 UI 가 만들어진다.
 */
export function useProfileSchema() {
  const [schema, setSchema] = useState<JsonSchema | null>(cached);
  const [isLoading, setIsLoading] = useState<boolean>(cached === null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (cached !== null) return;
    let isActive = true;

    fetchProfileSchema()
      .then((result) => {
        cached = result;
        if (isActive) setSchema(result);
      })
      .catch((err: unknown) => {
        if (isActive) {
          setError(err instanceof Error ? err.message : '스키마를 불러오지 못했습니다');
        }
      })
      .finally(() => {
        if (isActive) setIsLoading(false);
      });

    return () => {
      isActive = false;
    };
  }, []);

  return { schema, isLoading, error };
}
