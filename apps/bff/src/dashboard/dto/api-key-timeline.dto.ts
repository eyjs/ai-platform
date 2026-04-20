export interface TimelineBucketDto {
  bucket_start: string;
  request_count: number;
  error_count: number;
  avg_latency_ms: number;
  cache_hit_count: number;
}

export type TimelineDto = TimelineBucketDto[];

export interface ProfileBreakdownItemDto {
  profile_id: string;
  request_count: number;
  error_rate: number;
}

export type ProfileBreakdownDto = ProfileBreakdownItemDto[];
