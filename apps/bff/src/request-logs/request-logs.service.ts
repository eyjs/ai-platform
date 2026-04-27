import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { ApiRequestLog } from '../entities/api-request-log.entity';
import {
  QueryRequestLogsDto,
  RequestLogsResponseDto,
  RequestLogItemDto,
  RequestLogsStatsDto,
} from './dto/query-request-logs.dto';

/**
 * 요청 로그 서비스
 * api_request_logs 테이블을 직접 조회 (ReadOnly)
 */
@Injectable()
export class RequestLogsService {
  constructor(
    @InjectRepository(ApiRequestLog)
    private readonly requestLogRepo: Repository<ApiRequestLog>,
  ) {}

  /**
   * 요청 로그 목록 조회 (필터링 + 페이지네이션)
   */
  async findLogs(query: QueryRequestLogsDto): Promise<RequestLogsResponseDto> {
    const manager = this.requestLogRepo.manager;
    const { profile_id, status, date_from, date_to, page = 1, size = 20 } = query;

    // WHERE 조건 빌드
    const conditions: string[] = [];
    const params: unknown[] = [];

    if (profile_id) {
      conditions.push(`profile_id = $${params.length + 1}`);
      params.push(profile_id);
    }

    if (status) {
      conditions.push(`status_code = $${params.length + 1}`);
      params.push(status);
    }

    if (date_from) {
      conditions.push(`ts >= $${params.length + 1}`);
      params.push(new Date(date_from));
    }

    if (date_to) {
      conditions.push(`ts <= $${params.length + 1}`);
      params.push(new Date(date_to));
    }

    const whereClause = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
    const offset = (page - 1) * size;

    // 데이터 조회
    const dataQuery = `
      SELECT id, ts, api_key_id, profile_id, provider_id, status_code,
             latency_ms, prompt_tokens, completion_tokens, cache_hit,
             error_code, request_preview, response_preview
      FROM api_request_logs
      ${whereClause}
      ORDER BY ts DESC
      LIMIT $${params.length + 1} OFFSET $${params.length + 2}
    `;

    // 총 개수 조회
    const countQuery = `
      SELECT COUNT(*) as total
      FROM api_request_logs
      ${whereClause}
    `;

    const [dataResult, countResult] = await Promise.all([
      manager.query(dataQuery, [...params, size, offset]).catch(() => []),
      manager.query(countQuery, params).catch(() => [{ total: 0 }]),
    ]);

    const items: RequestLogItemDto[] = dataResult.map((row: Record<string, unknown>) => ({
      id: String(row.id),
      ts: row.ts ? new Date(row.ts as string).toISOString() : new Date().toISOString(),
      apiKeyId: row.api_key_id ? String(row.api_key_id) : null,
      profileId: row.profile_id ? String(row.profile_id) : null,
      providerId: row.provider_id ? String(row.provider_id) : null,
      statusCode: Number(row.status_code),
      latencyMs: Number(row.latency_ms),
      promptTokens: Number(row.prompt_tokens || 0),
      completionTokens: Number(row.completion_tokens || 0),
      cacheHit: Boolean(row.cache_hit),
      errorCode: row.error_code ? String(row.error_code) : null,
      requestPreview: row.request_preview ? String(row.request_preview) : null,
      responsePreview: row.response_preview ? String(row.response_preview) : null,
    }));

    return {
      items,
      total: Number(countResult[0]?.total || 0),
      page,
      size,
    };
  }

  /**
   * 단건 로그 상세 조회
   */
  async findLogById(id: string): Promise<RequestLogItemDto | null> {
    const manager = this.requestLogRepo.manager;

    const result = await manager.query(
      `SELECT id, ts, api_key_id, profile_id, provider_id, status_code,
              latency_ms, prompt_tokens, completion_tokens, cache_hit,
              error_code, request_preview, response_preview
       FROM api_request_logs
       WHERE id = $1`,
      [id],
    ).catch(() => []);

    if (result.length === 0) {
      return null;
    }

    const row = result[0];
    return {
      id: String(row.id),
      ts: row.ts ? new Date(row.ts as string).toISOString() : new Date().toISOString(),
      apiKeyId: row.api_key_id ? String(row.api_key_id) : null,
      profileId: row.profile_id ? String(row.profile_id) : null,
      providerId: row.provider_id ? String(row.provider_id) : null,
      statusCode: Number(row.status_code),
      latencyMs: Number(row.latency_ms),
      promptTokens: Number(row.prompt_tokens || 0),
      completionTokens: Number(row.completion_tokens || 0),
      cacheHit: Boolean(row.cache_hit),
      errorCode: row.error_code ? String(row.error_code) : null,
      requestPreview: row.request_preview ? String(row.request_preview) : null,
      responsePreview: row.response_preview ? String(row.response_preview) : null,
    };
  }

  /**
   * 기간별 요청 통계
   */
  async getStats(hours = 24): Promise<RequestLogsStatsDto> {
    const manager = this.requestLogRepo.manager;
    const since = new Date(Date.now() - hours * 60 * 60 * 1000);

    // 전체 통계
    const statsResult = await manager.query(
      `SELECT
         COUNT(*)::int AS total_requests,
         SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)::int AS error_count,
         COALESCE(AVG(latency_ms), 0)::int AS avg_latency_ms,
         SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END)::int AS cache_hits,
         COALESCE(SUM(prompt_tokens + completion_tokens), 0)::int AS total_tokens
       FROM api_request_logs
       WHERE ts >= $1`,
      [since],
    ).catch(() => [{ total_requests: 0, error_count: 0, avg_latency_ms: 0, cache_hits: 0, total_tokens: 0 }]);

    // 시간별 요청 수
    const hourlyResult = await manager.query(
      `SELECT
         date_trunc('hour', ts) AS hour,
         COUNT(*)::int AS count
       FROM api_request_logs
       WHERE ts >= $1
       GROUP BY hour
       ORDER BY hour DESC`,
      [since],
    ).catch(() => []);

    const stats = statsResult[0];
    const totalRequests = Number(stats.total_requests);
    const errorCount = Number(stats.error_count);
    const cacheHits = Number(stats.cache_hits);

    return {
      totalRequests,
      errorCount,
      errorRate: totalRequests > 0 ? errorCount / totalRequests : 0,
      avgLatencyMs: Number(stats.avg_latency_ms),
      cacheHitRate: totalRequests > 0 ? cacheHits / totalRequests : 0,
      totalTokens: Number(stats.total_tokens),
      requestsByHour: hourlyResult.map((row: Record<string, unknown>) => ({
        hour: new Date(row.hour as string).toISOString(),
        count: Number(row.count),
      })),
    };
  }
}