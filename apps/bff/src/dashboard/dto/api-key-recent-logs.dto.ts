export interface RecentLogItemDto {
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

export type RecentLogsDto = RecentLogItemDto[];
