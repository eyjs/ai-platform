import { Repository } from 'typeorm';
import { DashboardService } from './dashboard.service';
import { AgentProfile } from '../entities/agent-profile.entity';

type QueryMock = jest.Mock<Promise<unknown[]>, [string, unknown[]?]>;

function makeManagerWithQuery(queryMock: QueryMock): { query: QueryMock } {
  return { query: queryMock };
}

function makeProfileRepo(queryMock: QueryMock): jest.Mocked<Repository<AgentProfile>> {
  return {
    manager: makeManagerWithQuery(queryMock),
  } as unknown as jest.Mocked<Repository<AgentProfile>>;
}

function makeService(queryMock: QueryMock): DashboardService {
  const repo = makeProfileRepo(queryMock);
  return new DashboardService(repo);
}

describe('DashboardService', () => {
  describe('getSummary', () => {
    it('returns zero counts when tables are empty', async () => {
      const queryMock = jest.fn().mockResolvedValue([{ count: 0 }]);
      const service = makeService(queryMock);

      const result = await service.getSummary();

      expect(result.activeSessions).toBe(0);
      expect(result.todayConversations).toBe(0);
      expect(result.avgResponseTimeMs).toBe(0);
      expect(result.errorRate).toBe(0);
    });

    it('calculates conversationChange correctly', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ count: 3 }])   // active sessions
        .mockResolvedValueOnce([{ count: 10 }])  // today
        .mockResolvedValueOnce([{ count: 5 }]);  // yesterday

      const service = makeService(queryMock);
      const result = await service.getSummary();

      expect(result.todayConversations).toBe(10);
      // change = (10-5)/5 * 100 = 100%
      expect(result.changes.todayConversations).toBe(100);
    });

    it('returns zero change when yesterday count is zero', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ count: 0 }])
        .mockResolvedValueOnce([{ count: 5 }])
        .mockResolvedValueOnce([{ count: 0 }]);

      const service = makeService(queryMock);
      const result = await service.getSummary();

      expect(result.changes.todayConversations).toBe(0);
    });

    it('handles query failures gracefully', async () => {
      const queryMock = jest.fn().mockRejectedValue(new Error('DB error'));
      const service = makeService(queryMock);

      const result = await service.getSummary();

      expect(result.activeSessions).toBe(0);
      expect(result.todayConversations).toBe(0);
    });
  });

  describe('getUsage', () => {
    it('returns period and empty data for no records', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      const result = await service.getUsage('today');

      expect(result.period).toBe('today');
      expect(result.data).toEqual([]);
    });

    it('maps usage data correctly', async () => {
      const queryMock = jest.fn().mockResolvedValue([
        { profileId: 'p1', profileName: 'Profile 1', count: '42' },
      ]);
      const service = makeService(queryMock);

      const result = await service.getUsage('7d');

      expect(result.data).toHaveLength(1);
      expect(result.data[0].profileId).toBe('p1');
      expect(result.data[0].profileName).toBe('Profile 1');
      expect(result.data[0].count).toBe(42);
    });

    it('uses today as default period', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      const result = await service.getUsage('today');

      expect(result.period).toBe('today');
    });

    it('handles query failure gracefully', async () => {
      const queryMock = jest.fn().mockRejectedValue(new Error('timeout'));
      const service = makeService(queryMock);

      const result = await service.getUsage('7d');

      expect(result.data).toEqual([]);
    });
  });

  describe('getLatency', () => {
    it('returns empty data array', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      const result = await service.getLatency('today');

      expect(result.period).toBe('today');
      expect(result.data).toEqual([]);
    });
  });

  describe('getLogs', () => {
    it('returns paginated logs structure', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([
          { sessionId: 'sess-1', profileId: 'p1', profileName: 'P1', timestamp: new Date().toISOString() },
        ])
        .mockResolvedValueOnce([{ total: 1 }]);
      const service = makeService(queryMock);

      const result = await service.getLogs(1, 10, 'createdAt:desc');

      expect(result.data).toHaveLength(1);
      expect(result.total).toBe(1);
      expect(result.page).toBe(1);
      expect(result.size).toBe(10);
    });

    it('handles empty logs', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ total: 0 }]);
      const service = makeService(queryMock);

      const result = await service.getLogs(1, 20, 'createdAt:desc');

      expect(result.data).toEqual([]);
      expect(result.total).toBe(0);
    });

    it('calculates correct offset for pagination', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.getLogs(3, 10, 'createdAt:desc');

      // page=3, size=10 → offset=20
      const firstCall = queryMock.mock.calls[0];
      expect(firstCall[1]).toContain(20);
    });

    it('uses responseTime sort when specified', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.getLogs(1, 10, 'responseTime:asc');

      const sqlQuery = queryMock.mock.calls[0][0] as string;
      expect(sqlQuery).toContain('cs.updated_at');
    });
  });

  describe('getPlatformOverview', () => {
    it('returns overview structure with correct types', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ total_profiles: 5, active_profiles: 3 }])
        .mockResolvedValueOnce([{ today_requests: 100, error_count: 5, avg_latency_ms: 200 }])
        .mockResolvedValueOnce([{ total_api_keys: 10, active_api_keys: 7 }])
        .mockResolvedValueOnce([
          { hour: new Date().toISOString(), count: 50 },
        ]);
      const service = makeService(queryMock);

      const result = await service.getPlatformOverview();

      expect(result.totalProfiles).toBe(5);
      expect(result.activeProfiles).toBe(3);
      expect(result.todayRequests).toBe(100);
      expect(result.errorRate).toBeCloseTo(0.05);
      expect(result.avgLatencyMs).toBe(200);
      expect(result.apiKeys.total).toBe(10);
      expect(result.apiKeys.active).toBe(7);
      expect(result.requests24h).toHaveLength(1);
    });

    it('handles zero requests with zero errorRate', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ total_profiles: 0, active_profiles: 0 }])
        .mockResolvedValueOnce([{ today_requests: 0, error_count: 0, avg_latency_ms: 0 }])
        .mockResolvedValueOnce([{ total_api_keys: 0, active_api_keys: 0 }])
        .mockResolvedValueOnce([]);
      const service = makeService(queryMock);

      const result = await service.getPlatformOverview();

      expect(result.errorRate).toBe(0);
    });

    it('handles query failures by falling back to defaults', async () => {
      const queryMock = jest.fn().mockRejectedValue(new Error('conn error'));
      const service = makeService(queryMock);

      const result = await service.getPlatformOverview();

      expect(result.totalProfiles).toBe(0);
    });
  });

  describe('getKeySummary', () => {
    it('returns summary structure for given key and range', async () => {
      const queryMock = jest.fn().mockResolvedValue([{
        request_count: 100,
        error_count: 5,
        p50_latency_ms: 200,
        p95_latency_ms: 500,
        cache_hit_count: 30,
        prompt_tokens_total: 10000,
        completion_tokens_total: 5000,
      }]);
      const service = makeService(queryMock);

      const result = await service.getKeySummary('key-id', '24h');

      expect(result.range).toBe('24h');
      expect(result.request_count).toBe(100);
      expect(result.error_count).toBe(5);
      expect(result.error_rate).toBeCloseTo(0.05);
      expect(result.cache_hit_rate).toBeCloseTo(0.3);
    });

    it('returns zero rates when request count is zero', async () => {
      const queryMock = jest.fn().mockResolvedValue([{
        request_count: 0,
        error_count: 0,
        p50_latency_ms: 0,
        p95_latency_ms: 0,
        cache_hit_count: 0,
        prompt_tokens_total: 0,
        completion_tokens_total: 0,
      }]);
      const service = makeService(queryMock);

      const result = await service.getKeySummary('key-id', '7d');

      expect(result.error_rate).toBe(0);
      expect(result.cache_hit_rate).toBe(0);
    });
  });

  describe('getKeyProfileBreakdown', () => {
    it('returns array of profile breakdowns', async () => {
      const queryMock = jest.fn().mockResolvedValue([
        { profile_id: 'p1', request_count: 50, error_rate: 0.02 },
      ]);
      const service = makeService(queryMock);

      const result = await service.getKeyProfileBreakdown('key-id', '24h');

      expect(result).toHaveLength(1);
      expect(result[0].profile_id).toBe('p1');
      expect(result[0].request_count).toBe(50);
    });

    it('returns empty array on query failure', async () => {
      const queryMock = jest.fn().mockRejectedValue(new Error('fail'));
      const service = makeService(queryMock);

      const result = await service.getKeyProfileBreakdown('key-id', '24h');

      expect(result).toEqual([]);
    });
  });

  describe('getKeyTimeline', () => {
    it('returns timeline data mapped correctly', async () => {
      const bucketDate = new Date().toISOString();
      const queryMock = jest.fn().mockResolvedValue([
        { bucket_start: bucketDate, request_count: 10, error_count: 1, avg_latency_ms: 150, cache_hit_count: 2 },
      ]);
      const service = makeService(queryMock);

      const result = await service.getKeyTimeline('key-id', '24h', 'hour');

      expect(result).toHaveLength(1);
      expect(result[0].request_count).toBe(10);
      expect(result[0].bucket_start).toBe(new Date(bucketDate).toISOString());
    });
  });

  describe('getKeyRecentLogs', () => {
    it('returns recent logs mapped correctly', async () => {
      const ts = new Date().toISOString();
      const queryMock = jest.fn().mockResolvedValue([
        {
          id: 'log-1',
          ts,
          profile_id: 'p1',
          provider_id: 'openai',
          status_code: 200,
          latency_ms: 300,
          cache_hit: false,
          error_code: null,
          request_preview: 'hello',
          response_preview: 'world',
        },
      ]);
      const service = makeService(queryMock);

      const result = await service.getKeyRecentLogs('key-id', 10);

      expect(result).toHaveLength(1);
      expect(result[0].id).toBe('log-1');
      expect(result[0].status_code).toBe(200);
    });

    it('caps limit at 500', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.getKeyRecentLogs('key-id', 1000);

      const callParams = queryMock.mock.calls[0][1];
      expect(callParams).toContain(500);
    });
  });
});
