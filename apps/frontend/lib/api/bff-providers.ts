import { getAccessToken } from '@/lib/auth/token-storage';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001/bff';

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

export interface ProviderTypeStatus {
  providerType: string;
  totalProviders: number;
  activeEntries: number;
  expiredEntries: number;
}

export interface ProviderMetrics {
  providerId: string;
  providerType: string;
  cacheEntries: number;
  expiredEntries: number;
  lastActivity: string | null;
  isActive: boolean;
}

/** bff `/providers/status` 응답 — provider 캐시 상태(헬스 아님). */
export interface ProvidersStatus {
  totalProviders: number;
  activeProviders: number;
  cacheEntries: number;
  expiredEntries: number;
  providersByType: ProviderTypeStatus[];
  providerMetrics: ProviderMetrics[];
}

export async function fetchProviderStatus(): Promise<ProvidersStatus> {
  const res = await fetch(`${BFF_URL}/providers/status`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}
