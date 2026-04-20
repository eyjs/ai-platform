import { IsEnum, IsOptional } from 'class-validator';

export type DashboardRange = '24h' | '7d' | '30d';
export type DashboardBucket = 'hour' | 'day';

export class RangeQueryDto {
  @IsOptional()
  @IsEnum(['24h', '7d', '30d'])
  range?: DashboardRange;
}

export class RangeBucketQueryDto extends RangeQueryDto {
  @IsOptional()
  @IsEnum(['hour', 'day'])
  bucket?: DashboardBucket;
}

export interface KeySummaryDto {
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
