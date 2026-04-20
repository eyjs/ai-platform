import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, MoreThan } from 'typeorm';
import { AgentProfile } from '../entities/agent-profile.entity';
import {
  DashboardSummaryDto,
  DashboardUsageDto,
  DashboardLatencyDto,
  DashboardLogsDto,
} from './dto/dashboard.dto';
import type {
  DashboardRange,
  DashboardBucket,
  KeySummaryDto,
} from './dto/api-key-metrics.dto';
import type {
  TimelineDto,
  ProfileBreakdownDto,
} from './dto/api-key-timeline.dto';
import type { RecentLogsDto } from './dto/api-key-recent-logs.dto';

const RANGE_INTERVALS: Record<DashboardRange, string> = {
  '24h': '24 hours',
  '7d': '7 days',
  '30d': '30 days',
};

/**
 * conversation_sessions 테이블을 직접 쿼리한다.
 * TypeORM Entity 없이 raw query 사용 (기존 테이블 구조를 변경하지 않음)
 */
@Injectable()
export class DashboardService {
  constructor(
    @InjectRepository(AgentProfile)
    private readonly profileRepo: Repository<AgentProfile>,
  ) {}

  async getSummary(): Promise<DashboardSummaryDto> {
    const manager = this.profileRepo.manager;

    // 활성 세션: updated_at이 최근 30분 이내
    const thirtyMinAgo = new Date(Date.now() - 30 * 60 * 1000).toISOString();
    const activeResult = await manager.query(
      `SELECT COUNT(*) as count FROM conversation_sessions WHERE updated_at > $1`,
      [thirtyMinAgo],
    ).catch(() => [{ count: 0 }]);

    // 오늘 총 대화
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    const todayResult = await manager.query(
      `SELECT COUNT(*) as count FROM conversation_sessions WHERE created_at >= $1`,
      [todayStart.toISOString()],
    ).catch(() => [{ count: 0 }]);

    // 어제 총 대화 (변화율 계산용)
    const yesterdayStart = new Date(todayStart.getTime() - 86400000);
    const yesterdayResult = await manager.query(
      `SELECT COUNT(*) as count FROM conversation_sessions WHERE created_at >= $1 AND created_at < $2`,
      [yesterdayStart.toISOString(), todayStart.toISOString()],
    ).catch(() => [{ count: 0 }]);

    const activeSessions = Number(activeResult[0]?.count || 0);
    const todayConversations = Number(todayResult[0]?.count || 0);
    const yesterdayConversations = Number(yesterdayResult[0]?.count || 0);

    const conversationChange =
      yesterdayConversations > 0
        ? ((todayConversations - yesterdayConversations) /
            yesterdayConversations) *
          100
        : 0;

    return {
      activeSessions,
      todayConversations,
      avgResponseTimeMs: 0, // 별도 레이턴시 로그 없음
      errorRate: 0, // 별도 에러 로그 없음
      changes: {
        activeSessions: 0,
        todayConversations: Math.round(conversationChange * 10) / 10,
        avgResponseTime: 0,
        errorRate: 0,
      },
    };
  }

  async getUsage(period: string): Promise<DashboardUsageDto> {
    const manager = this.profileRepo.manager;
    const since = this.getPeriodStart(period);

    const result = await manager.query(
      `SELECT cs.profile_id as "profileId", ap.name as "profileName", COUNT(*) as count
       FROM conversation_sessions cs
       LEFT JOIN agent_profiles ap ON cs.profile_id = ap.id
       WHERE cs.created_at >= $1
       GROUP BY cs.profile_id, ap.name
       ORDER BY count DESC
       LIMIT 10`,
      [since.toISOString()],
    ).catch(() => []);

    return {
      period,
      data: result.map((r: Record<string, unknown>) => ({
        profileId: r.profileId || 'unknown',
        profileName: r.profileName || 'Unknown',
        count: Number(r.count),
      })),
    };
  }

  async getLatency(period: string): Promise<DashboardLatencyDto> {
    // 실제 레이턴시 데이터가 없으므로 빈 배열 반환
    // 향후 observability 로그 테이블 추가 시 구현
    return {
      period,
      data: [],
    };
  }

  async getLogs(
    page: number,
    size: number,
    sort: string,
  ): Promise<DashboardLogsDto> {
    const manager = this.profileRepo.manager;

    const [sortField, sortDir] = sort.split(':');
    const orderBy =
      sortField === 'responseTime' ? 'cs.updated_at' : 'cs.created_at';
    const direction = sortDir === 'asc' ? 'ASC' : 'DESC';
    const offset = (page - 1) * size;

    const [dataResult, countResult] = await Promise.all([
      manager.query(
        `SELECT cs.session_id as "sessionId", cs.profile_id as "profileId",
                ap.name as "profileName", cs.created_at as "timestamp"
         FROM conversation_sessions cs
         LEFT JOIN agent_profiles ap ON cs.profile_id = ap.id
         ORDER BY ${orderBy} ${direction}
         LIMIT $1 OFFSET $2`,
        [size, offset],
      ).catch(() => []),
      manager.query(
        `SELECT COUNT(*) as total FROM conversation_sessions`,
      ).catch(() => [{ total: 0 }]),
    ]);

    return {
      data: dataResult.map((r: Record<string, unknown>) => ({
        sessionId: String(r.sessionId || ''),
        profileId: String(r.profileId || ''),
        profileName: String(r.profileName || 'Unknown'),
        questionPreview: '', // 대화 내용은 별도 조회 필요
        responseTimeMs: 0,
        timestamp: r.timestamp
          ? new Date(r.timestamp as string).toISOString()
          : new Date().toISOString(),
      })),
      total: Number(countResult[0]?.total || 0),
      page,
      size,
    };
  }

  // ---- API Key 전용 집계 (Task 007) ----

  async getKeySummary(keyId: string, range: DashboardRange): Promise<KeySummaryDto> {
    const manager = this.profileRepo.manager;
    const intervalLiteral = this.rangeToInterval(range);

    const row = (await manager.query(
      `SELECT
         COUNT(*)::int AS request_count,
         SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)::int AS error_count,
         COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), 0)::int AS p50_latency_ms,
         COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0)::int AS p95_latency_ms,
         SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END)::int AS cache_hit_count,
         COALESCE(SUM(prompt_tokens), 0)::int AS prompt_tokens_total,
         COALESCE(SUM(completion_tokens), 0)::int AS completion_tokens_total
       FROM api_request_logs
       WHERE api_key_id = $1 AND ts > NOW() - $2::interval`,
      [keyId, intervalLiteral],
    ).catch(() => []))[0] as Record<string, number> | undefined;

    const req = Number(row?.request_count || 0);
    const err = Number(row?.error_count || 0);
    const hit = Number(row?.cache_hit_count || 0);

    return {
      range,
      request_count: req,
      error_count: err,
      error_rate: req > 0 ? err / req : 0,
      p50_latency_ms: Number(row?.p50_latency_ms || 0),
      p95_latency_ms: Number(row?.p95_latency_ms || 0),
      cache_hit_rate: req > 0 ? hit / req : 0,
      prompt_tokens_total: Number(row?.prompt_tokens_total || 0),
      completion_tokens_total: Number(row?.completion_tokens_total || 0),
    };
  }

  async getKeyProfileBreakdown(
    keyId: string,
    range: DashboardRange,
  ): Promise<ProfileBreakdownDto> {
    const manager = this.profileRepo.manager;
    const intervalLiteral = this.rangeToInterval(range);
    const rows = await manager.query(
      `SELECT profile_id,
              COUNT(*)::int AS request_count,
              (SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*), 0))::float AS error_rate
       FROM api_request_logs
       WHERE api_key_id = $1 AND ts > NOW() - $2::interval AND profile_id IS NOT NULL
       GROUP BY profile_id
       ORDER BY request_count DESC
       LIMIT 20`,
      [keyId, intervalLiteral],
    ).catch(() => []);
    return rows.map((r: Record<string, unknown>) => ({
      profile_id: String(r.profile_id),
      request_count: Number(r.request_count || 0),
      error_rate: Number(r.error_rate || 0),
    }));
  }

  async getKeyTimeline(
    keyId: string,
    range: DashboardRange,
    bucket: DashboardBucket,
  ): Promise<TimelineDto> {
    const manager = this.profileRepo.manager;
    const intervalLiteral = this.rangeToInterval(range);
    const bucketUnit = bucket === 'day' ? 'day' : 'hour';

    const rows = await manager.query(
      `SELECT date_trunc($3, ts) AS bucket_start,
              COUNT(*)::int AS request_count,
              SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)::int AS error_count,
              COALESCE(AVG(latency_ms), 0)::int AS avg_latency_ms,
              SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END)::int AS cache_hit_count
       FROM api_request_logs
       WHERE api_key_id = $1 AND ts > NOW() - $2::interval
       GROUP BY bucket_start
       ORDER BY bucket_start ASC`,
      [keyId, intervalLiteral, bucketUnit],
    ).catch(() => []);
    return rows.map((r: Record<string, unknown>) => ({
      bucket_start: new Date(r.bucket_start as string).toISOString(),
      request_count: Number(r.request_count || 0),
      error_count: Number(r.error_count || 0),
      avg_latency_ms: Number(r.avg_latency_ms || 0),
      cache_hit_count: Number(r.cache_hit_count || 0),
    }));
  }

  async getKeyRecentLogs(keyId: string, limit: number): Promise<RecentLogsDto> {
    const manager = this.profileRepo.manager;
    const rows = await manager.query(
      `SELECT id, ts, profile_id, provider_id, status_code, latency_ms,
              cache_hit, error_code, request_preview, response_preview
       FROM api_request_logs
       WHERE api_key_id = $1
       ORDER BY ts DESC
       LIMIT $2`,
      [keyId, Math.min(limit, 500)],
    ).catch(() => []);
    return rows.map((r: Record<string, unknown>) => ({
      id: String(r.id),
      ts: new Date(r.ts as string).toISOString(),
      profile_id: r.profile_id ? String(r.profile_id) : null,
      provider_id: r.provider_id ? String(r.provider_id) : null,
      status_code: Number(r.status_code),
      latency_ms: Number(r.latency_ms),
      cache_hit: Boolean(r.cache_hit),
      error_code: r.error_code ? String(r.error_code) : null,
      request_preview: r.request_preview ? String(r.request_preview) : null,
      response_preview: r.response_preview ? String(r.response_preview) : null,
    }));
  }

  private rangeToInterval(range: DashboardRange): string {
    return RANGE_INTERVALS[range] || RANGE_INTERVALS['24h'];
  }

  private getPeriodStart(period: string): Date {
    const now = new Date();
    switch (period) {
      case '7d':
        return new Date(now.getTime() - 7 * 86400000);
      case '30d':
        return new Date(now.getTime() - 30 * 86400000);
      default: {
        const today = new Date(now);
        today.setHours(0, 0, 0, 0);
        return today;
      }
    }
  }
}
