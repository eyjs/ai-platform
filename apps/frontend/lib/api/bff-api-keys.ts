import type {
  ApiKey,
  ApiKeyAuditEntry,
  ApiKeyCreateRequest,
  ApiKeyCreateResponse,
  ApiKeyUpdateRequest,
} from '@/types/api-key';
import { getAccessToken } from '@/lib/auth/token-storage';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001';

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    'Content-Type': 'application/json',
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

export async function listApiKeys(): Promise<ApiKey[]> {
  const res = await fetch(`${BFF_URL}/bff/api-keys`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function getApiKey(id: string): Promise<ApiKey> {
  const res = await fetch(`${BFF_URL}/bff/api-keys/${id}`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function createApiKey(
  dto: ApiKeyCreateRequest,
): Promise<ApiKeyCreateResponse> {
  const res = await fetch(`${BFF_URL}/bff/api-keys`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(dto),
  });
  return handleResponse(res);
}

export async function updateApiKey(
  id: string,
  dto: ApiKeyUpdateRequest,
): Promise<ApiKey> {
  const res = await fetch(`${BFF_URL}/bff/api-keys/${id}`, {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify(dto),
  });
  return handleResponse(res);
}

export async function revokeApiKey(id: string): Promise<ApiKey> {
  const res = await fetch(`${BFF_URL}/bff/api-keys/${id}/revoke`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function rotateApiKey(id: string): Promise<ApiKeyCreateResponse> {
  const res = await fetch(`${BFF_URL}/bff/api-keys/${id}/rotate`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function getAuditLog(id: string, limit = 50): Promise<ApiKeyAuditEntry[]> {
  const res = await fetch(
    `${BFF_URL}/bff/api-keys/${id}/audit?limit=${limit}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}
