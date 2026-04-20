'use client';

import { useEffect, useState } from 'react';
import { getAccessToken } from '@/lib/auth/token-storage';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001';

interface SchemaResponse {
  schema: Record<string, unknown>;
}

let cached: Record<string, unknown> | null = null;

/**
 * Profile JSON Schema 를 BFF 에서 1회 fetch 하여 메모리에 캐싱.
 * Monaco YAML validation 에 주입하는 용도.
 */
export function useProfileSchema() {
  const [schema, setSchema] = useState<Record<string, unknown> | null>(cached);
  const [loading, setLoading] = useState<boolean>(cached === null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (cached !== null) return;
    const token = getAccessToken();
    fetch(`${BFF_URL}/bff/profiles/schema`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as SchemaResponse;
      })
      .then((res) => {
        cached = res.schema;
        setSchema(cached);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return { schema, loading, error };
}
