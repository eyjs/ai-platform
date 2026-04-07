import { getAccessToken } from '@/lib/auth/token-storage';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001';

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export interface DashboardSummary {
  activeSessions: number;
  todayConversations: number;
  avgResponseTimeMs: number;
  errorRate: number;
  changes: {
    activeSessions: number;
    todayConversations: number;
    avgResponseTime: number;
    errorRate: number;
  };
}

export interface UsageData {
  period: string;
  data: Array<{ profileId: string; profileName: string; count: number }>;
}

export interface LatencyData {
  period: string;
  data: Array<{ timestamp: string; p50: number; p95: number }>;
}

export interface LogsData {
  data: Array<{
    sessionId: string;
    profileId: string;
    profileName: string;
    questionPreview: string;
    responseTimeMs: number;
    timestamp: string;
  }>;
  total: number;
  page: number;
  size: number;
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`Dashboard API 실패 (${res.status})`);
  return res.json();
}

export async function fetchSummary(): Promise<DashboardSummary> {
  const res = await fetch(`${BFF_URL}/bff/dashboard/summary`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function fetchUsage(period: string = 'today'): Promise<UsageData> {
  const res = await fetch(`${BFF_URL}/bff/dashboard/usage?period=${period}`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function fetchLatency(period: string = 'today'): Promise<LatencyData> {
  const res = await fetch(`${BFF_URL}/bff/dashboard/latency?period=${period}`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function fetchLogs(page = 1, size = 10, sort = 'timestamp:desc'): Promise<LogsData> {
  const res = await fetch(
    `${BFF_URL}/bff/dashboard/logs?page=${page}&size=${size}&sort=${sort}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}
