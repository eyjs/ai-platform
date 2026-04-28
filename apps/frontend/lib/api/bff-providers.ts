import { getAccessToken } from '@/lib/auth/token-storage';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001';

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
    throw new Error(error.message || `요청 실패 (${res.status})`);
  }
  return res.json();
}

export interface ProviderStatus {
  name: string;
  type: 'llm' | 'embedding' | 'reranker';
  status: 'healthy' | 'degraded' | 'error';
  avgLatencyMs: number;
  errorRate: number;
  lastError: string | null;
  lastCheckedAt: string;
}

export async function fetchProviderStatus(): Promise<ProviderStatus[]> {
  const res = await fetch(`${BFF_URL}/bff/providers/status`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}
