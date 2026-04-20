export type DashboardRange = '24h' | '7d' | '30d';
export type DashboardBucket = 'hour' | 'day';

export interface KeySummary {
  range: DashboardRange;
  request_count: number;
  error_count: number;
  error_rate: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  cache_hit_rate: number;
  prompt_tokens_total: number;
  completion_tokens_total: number;
}

export interface ProfileBreakdownItem {
  profile_id: string;
  request_count: number;
  error_rate: number;
}

export interface TimelineBucket {
  bucket_start: string;
  request_count: number;
  error_count: number;
  avg_latency_ms: number;
  cache_hit_count: number;
}

export interface RecentLogItem {
  id: string;
  ts: string;
  profile_id: string | null;
  provider_id: string | null;
  status_code: number;
  latency_ms: number;
  cache_hit: boolean;
  error_code: string | null;
  request_preview: string | null;
  response_preview: string | null;
}
