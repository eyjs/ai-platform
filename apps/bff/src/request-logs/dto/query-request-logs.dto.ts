import { IsOptional, IsString, IsDateString, IsInt, Min, Max } from 'class-validator';
import { Transform, Type } from 'class-transformer';

/**
 * 요청 로그 목록 조회 DTO
 */
export class QueryRequestLogsDto {
  @IsOptional()
  @IsString()
  profile_id?: string;

  @IsOptional()
  @IsInt()
  @Min(100)
  @Max(599)
  @Type(() => Number)
  status?: number;

  @IsOptional()
  @IsDateString()
  date_from?: string;

  @IsOptional()
  @IsDateString()
  date_to?: string;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Type(() => Number)
  @Transform(({ value }) => value || 1)
  page?: number = 1;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(100)
  @Type(() => Number)
  @Transform(({ value }) => value || 20)
  size?: number = 20;
}

/**
 * 요청 로그 목록 응답 DTO
 */
export class RequestLogsResponseDto {
  items: RequestLogItemDto[];
  total: number;
  page: number;
  size: number;
}

/**
 * 요청 로그 단건 응답 DTO
 */
export class RequestLogItemDto {
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

/**
 * 요청 로그 통계 응답 DTO
 */
export class RequestLogsStatsDto {
  totalRequests: number;
  errorCount: number;
  errorRate: number;
  avgLatencyMs: number;
  cacheHitRate: number;
  totalTokens: number;
  requestsByHour: { hour: string; count: number }[];
}