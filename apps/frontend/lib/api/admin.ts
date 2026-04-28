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
    const body = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
    throw new Error(body.message || `요청 실패 (${res.status})`);
  }
  return res.json();
}

export interface PlatformOverview {
  totalRequests: number;
  errorRate: number;
  avgLatencyMs: number;
  activeProfiles: number;
  changes: {
    totalRequests: number;
    errorRate: number;
    avgLatency: number;
    activeProfiles: number;
  };
  hourlyTrend: Array<{ hour: string; count: number }>;
  recentLogs: Array<RequestLogSummary>;
}

export interface RequestLogSummary {
  id: string;
  profileId: string;
  profileName: string;
  status: 'success' | 'error' | 'timeout';
  latencyMs: number;
  questionPreview: string;
  timestamp: string;
}

export interface RequestLogDetail {
  id: string;
  profileId: string;
  profileName: string;
  status: 'success' | 'error' | 'timeout';
  latencyMs: number;
  timestamp: string;
  request: {
    method: string;
    path: string;
    headers: Record<string, string>;
    body: string;
  };
  response: {
    statusCode: number;
    body: string;
  };
  routing: {
    selectedProvider: string;
    selectedModel: string;
    reason: string;
  };
  toolCalls: Array<{
    name: string;
    durationMs: number;
    status: 'success' | 'error';
  }>;
  latencyBreakdown: {
    routingMs: number;
    llmMs: number;
    toolsMs: number;
    totalMs: number;
  };
}

export interface RequestLogsResponse {
  data: RequestLogSummary[];
  total: number;
  page: number;
  size: number;
}

export interface RequestLogFilters {
  profileId?: string;
  status?: string;
  startDate?: string;
  endDate?: string;
  page?: number;
  size?: number;
}

export interface RequestLogStats {
  totalToday: number;
  errorCount: number;
  avgLatencyMs: number;
}

export async function fetchPlatformOverview(): Promise<PlatformOverview> {
  const res = await fetch(`${BFF_URL}/bff/dashboard/overview`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchRequestLogs(
  filters: RequestLogFilters = {},
): Promise<RequestLogsResponse> {
  const params = new URLSearchParams();
  if (filters.profileId) params.set('profileId', filters.profileId);
  if (filters.status) params.set('status', filters.status);
  if (filters.startDate) params.set('startDate', filters.startDate);
  if (filters.endDate) params.set('endDate', filters.endDate);
  params.set('page', String(filters.page ?? 1));
  params.set('size', String(filters.size ?? 20));

  const res = await fetch(`${BFF_URL}/bff/request-logs?${params}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchRequestLogDetail(id: string): Promise<RequestLogDetail> {
  const res = await fetch(`${BFF_URL}/bff/request-logs/${encodeURIComponent(id)}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchRequestLogStats(): Promise<RequestLogStats> {
  const res = await fetch(`${BFF_URL}/bff/request-logs/stats`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}
