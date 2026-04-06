import type { ProfileListItem, ProfileDetail, ProfileHistoryItem, ToolItem } from '@/types/profile';
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

export async function fetchProfiles(): Promise<ProfileListItem[]> {
  const res = await fetch(`${BFF_URL}/bff/profiles`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function fetchProfile(id: string): Promise<ProfileDetail> {
  const res = await fetch(`${BFF_URL}/bff/profiles/${id}`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function createProfile(yamlContent: string): Promise<ProfileDetail> {
  const res = await fetch(`${BFF_URL}/bff/profiles`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ yamlContent }),
  });
  return handleResponse(res);
}

export async function updateProfile(id: string, yamlContent: string): Promise<ProfileDetail> {
  const res = await fetch(`${BFF_URL}/bff/profiles/${id}`, {
    method: 'PUT',
    headers: authHeaders(),
    body: JSON.stringify({ yamlContent }),
  });
  return handleResponse(res);
}

export async function deleteProfile(id: string): Promise<void> {
  const res = await fetch(`${BFF_URL}/bff/profiles/${id}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error('삭제 실패');
}

export async function activateProfile(id: string): Promise<ProfileDetail> {
  const res = await fetch(`${BFF_URL}/bff/profiles/${id}/activate`, {
    method: 'PATCH',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function deactivateProfile(id: string): Promise<ProfileDetail> {
  const res = await fetch(`${BFF_URL}/bff/profiles/${id}/deactivate`, {
    method: 'PATCH',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchProfileHistory(id: string): Promise<ProfileHistoryItem[]> {
  const res = await fetch(`${BFF_URL}/bff/profiles/${id}/history`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function restoreProfile(id: string, historyId: string): Promise<ProfileDetail> {
  const res = await fetch(`${BFF_URL}/bff/profiles/${id}/restore`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ historyId }),
  });
  return handleResponse(res);
}

export async function fetchTools(): Promise<ToolItem[]> {
  const res = await fetch(`${BFF_URL}/bff/tools`, { headers: authHeaders() });
  return handleResponse(res);
}
