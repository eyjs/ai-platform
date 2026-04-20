import type {
  DashboardBucket,
  DashboardRange,
  KeySummary,
  ProfileBreakdownItem,
  RecentLogItem,
  TimelineBucket,
} from '@/types/key-dashboard';
import { getAccessToken } from '@/lib/auth/token-storage';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001';

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
    throw new Error(err.message || `요청 실패 (${res.status})`);
  }
  return res.json();
}

export function getSummary(id: string, range: DashboardRange): Promise<KeySummary> {
  return fetch(`${BFF_URL}/bff/dashboard/api-keys/${id}/summary?range=${range}`, {
    headers: authHeaders(),
  }).then((r) => handle<KeySummary>(r));
}

export function getProfileBreakdown(
  id: string,
  range: DashboardRange,
): Promise<ProfileBreakdownItem[]> {
  return fetch(
    `${BFF_URL}/bff/dashboard/api-keys/${id}/profile-breakdown?range=${range}`,
    { headers: authHeaders() },
  ).then((r) => handle<ProfileBreakdownItem[]>(r));
}

export function getTimeline(
  id: string,
  range: DashboardRange,
  bucket: DashboardBucket,
): Promise<TimelineBucket[]> {
  return fetch(
    `${BFF_URL}/bff/dashboard/api-keys/${id}/timeline?range=${range}&bucket=${bucket}`,
    { headers: authHeaders() },
  ).then((r) => handle<TimelineBucket[]>(r));
}

export function getRecentLogs(id: string, limit = 100): Promise<RecentLogItem[]> {
  return fetch(`${BFF_URL}/bff/dashboard/api-keys/${id}/recent?limit=${limit}`, {
    headers: authHeaders(),
  }).then((r) => handle<RecentLogItem[]>(r));
}
