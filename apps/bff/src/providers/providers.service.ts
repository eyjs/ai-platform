import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { CacheEntry } from '../entities/cache-entry.entity';
import {
  ProvidersStatusDto,
  ProviderTypeStatusDto,
  ProviderMetricsDto,
} from './dto/provider-status.dto';

/**
 * Providers 서비스
 * cache_entries 테이블에서 Provider 상태 조회
 */
@Injectable()
export class ProvidersService {
  constructor(
    @InjectRepository(CacheEntry)
    private readonly cacheEntryRepo: Repository<CacheEntry>,
  ) {}

  /**
   * Provider 상태 조회
   */
  async getProvidersStatus(): Promise<ProvidersStatusDto> {
    const manager = this.cacheEntryRepo.manager;
    const now = new Date();

    // 전체 통계
    const overallResult = await manager.query(
      `SELECT
         COUNT(DISTINCT provider_id) FILTER (WHERE provider_id IS NOT NULL)::int AS total_providers,
         COUNT(DISTINCT provider_id) FILTER (WHERE provider_id IS NOT NULL AND (expires_at IS NULL OR expires_at > NOW()))::int AS active_providers,
         COUNT(*)::int AS cache_entries,
         COUNT(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at <= NOW())::int AS expired_entries
       FROM cache_entries
       WHERE provider_id IS NOT NULL`,
    ).catch(() => [{ total_providers: 0, active_providers: 0, cache_entries: 0, expired_entries: 0 }]);

    // 타입별 통계
    const typeResult = await manager.query(
      `SELECT
         provider_type,
         COUNT(DISTINCT provider_id)::int AS total_providers,
         COUNT(*) FILTER (WHERE expires_at IS NULL OR expires_at > NOW())::int AS active_entries,
         COUNT(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at <= NOW())::int AS expired_entries
       FROM cache_entries
       WHERE provider_type IS NOT NULL AND provider_id IS NOT NULL
       GROUP BY provider_type
       ORDER BY total_providers DESC`,
    ).catch(() => []);

    // 개별 Provider 메트릭
    const metricsResult = await manager.query(
      `SELECT
         provider_id,
         provider_type,
         COUNT(*)::int AS cache_entries,
         COUNT(*) FILTER (WHERE expires_at IS NOT NULL AND expires_at <= NOW())::int AS expired_entries,
         MAX(updated_at) AS last_activity
       FROM cache_entries
       WHERE provider_id IS NOT NULL
       GROUP BY provider_id, provider_type
       ORDER BY last_activity DESC NULLS LAST
       LIMIT 20`,
    ).catch(() => []);

    const overall = overallResult[0];

    return {
      totalProviders: Number(overall.total_providers),
      activeProviders: Number(overall.active_providers),
      cacheEntries: Number(overall.cache_entries),
      expiredEntries: Number(overall.expired_entries),
      providersByType: typeResult.map((row: Record<string, unknown>) => ({
        providerType: String(row.provider_type),
        totalProviders: Number(row.total_providers),
        activeEntries: Number(row.active_entries),
        expiredEntries: Number(row.expired_entries),
      })),
      providerMetrics: metricsResult.map((row: Record<string, unknown>) => {
        const lastActivity = row.last_activity ? new Date(row.last_activity as string) : null;
        const isActive = lastActivity ? (now.getTime() - lastActivity.getTime()) < 60 * 60 * 1000 : false; // 1시간 이내

        return {
          providerId: String(row.provider_id),
          providerType: row.provider_type ? String(row.provider_type) : 'unknown',
          cacheEntries: Number(row.cache_entries),
          expiredEntries: Number(row.expired_entries),
          lastActivity: lastActivity ? lastActivity.toISOString() : null,
          isActive,
        };
      }),
    };
  }
}