import { getAccessToken } from '@/lib/auth/token-storage';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001/bff';

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

export interface OverviewLog {
  ts: string;
  profileId: string | null;
  statusCode: number;
  latencyMs: number;
  errorCode: string | null;
  requestPreview: string | null;
  responsePreview: string | null;
}

export interface PlatformOverview {
  totalProfiles: number;
  activeProfiles: number;
  todayRequests: number;
  errorRate: number;
  avgLatencyMs: number;
  p95LatencyMs: number;
  apiKeys: { total: number; active: number };
  requests24h: Array<{ hour: string; count: number }>;
  recentLogs: OverviewLog[];
}

export interface RequestLogSummary {
  id: string;
  ts: string;
  apiKeyId: string | null;
  profileId: string | null;
  providerId: string | null;
  statusCode: number;
  latencyMs: number;
  promptTokens: number;
  completionTokens: number;
  cacheHit: boolean;
  errorCode: string | null;
  requestPreview: string | null;
  responsePreview: string | null;
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
  items: RequestLogSummary[];
  total: number;
  page: number;
  size: number;
}

export interface RequestLogFilters {
  profileId?: string;
  status?: number;
  startDate?: string;
  endDate?: string;
  page?: number;
  size?: number;
}

export interface RequestLogStats {
  totalRequests: number;
  errorCount: number;
  errorRate: number;
  avgLatencyMs: number;
  cacheHitRate: number;
  totalTokens: number;
  requestsByHour: Array<{ hour: string; count: number }>;
}

export async function fetchPlatformOverview(): Promise<PlatformOverview> {
  const res = await fetch(`${BFF_URL}/dashboard/overview`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchRequestLogs(
  filters: RequestLogFilters = {},
): Promise<RequestLogsResponse> {
  const params = new URLSearchParams();
  if (filters.profileId) params.set('profile_id', filters.profileId);
  if (filters.status != null) params.set('status', String(filters.status));
  if (filters.startDate) params.set('date_from', filters.startDate);
  if (filters.endDate) params.set('date_to', filters.endDate);
  params.set('page', String(filters.page ?? 1));
  params.set('size', String(filters.size ?? 20));

  const res = await fetch(`${BFF_URL}/request-logs?${params}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchRequestLogDetail(id: string): Promise<RequestLogSummary> {
  const res = await fetch(`${BFF_URL}/request-logs/${encodeURIComponent(id)}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchRequestLogStats(): Promise<RequestLogStats> {
  const res = await fetch(`${BFF_URL}/request-logs/stats`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}
