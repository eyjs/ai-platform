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
