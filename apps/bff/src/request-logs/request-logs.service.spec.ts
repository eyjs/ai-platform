import { Repository } from 'typeorm';
import { RequestLogsService } from './request-logs.service';
import { ApiRequestLog } from '../entities/api-request-log.entity';

function makeRequestLogRepo(queryMock: jest.Mock): jest.Mocked<Repository<ApiRequestLog>> {
  return {
    manager: { query: queryMock },
  } as unknown as jest.Mocked<Repository<ApiRequestLog>>;
}

function makeService(queryMock: jest.Mock): RequestLogsService {
  const repo = makeRequestLogRepo(queryMock);
  return new RequestLogsService(repo);
}

function makeLogRow(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'log-uuid-1',
    ts: new Date().toISOString(),
    api_key_id: 'key-1',
    profile_id: 'profile-1',
    provider_id: 'openai',
    status_code: 200,
    latency_ms: 300,
    prompt_tokens: 100,
    completion_tokens: 50,
    cache_hit: false,
    error_code: null,
    request_preview: 'Hello?',
    response_preview: 'World!',
    ...overrides,
  };
}

describe('RequestLogsService', () => {
  describe('findLogs', () => {
    it('returns empty items when no logs', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ total: 0 }]);
      const service = makeService(queryMock);

      const result = await service.findLogs({});

      expect(result.items).toEqual([]);
      expect(result.total).toBe(0);
      expect(result.page).toBe(1);
      expect(result.size).toBe(20);
    });

    it('maps log row fields correctly', async () => {
      const row = makeLogRow();
      const queryMock = jest.fn()
        .mockResolvedValueOnce([row])
        .mockResolvedValueOnce([{ total: 1 }]);
      const service = makeService(queryMock);

      const result = await service.findLogs({});

      expect(result.items).toHaveLength(1);
      expect(result.items[0].id).toBe('log-uuid-1');
      expect(result.items[0].apiKeyId).toBe('key-1');
      expect(result.items[0].profileId).toBe('profile-1');
      expect(result.items[0].providerId).toBe('openai');
      expect(result.items[0].statusCode).toBe(200);
      expect(result.items[0].latencyMs).toBe(300);
      expect(result.items[0].promptTokens).toBe(100);
      expect(result.items[0].completionTokens).toBe(50);
      expect(result.items[0].cacheHit).toBe(false);
    });

    it('uses WHERE clause when profile_id filter is provided', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.findLogs({ profile_id: 'profile-x' });

      const dataQuerySql = queryMock.mock.calls[0][0] as string;
      expect(dataQuerySql).toContain('profile_id = $');
      expect(queryMock.mock.calls[0][1]).toContain('profile-x');
    });

    it('applies status filter when provided', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.findLogs({ status: 200 });

      const dataQuerySql = queryMock.mock.calls[0][0] as string;
      expect(dataQuerySql).toContain('status_code = $');
    });

    it('applies date_from filter when provided', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.findLogs({ date_from: '2024-01-01' });

      const dataQuerySql = queryMock.mock.calls[0][0] as string;
      expect(dataQuerySql).toContain('ts >= $');
    });

    it('applies date_to filter when provided', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.findLogs({ date_to: '2024-12-31' });

      const dataQuerySql = queryMock.mock.calls[0][0] as string;
      expect(dataQuerySql).toContain('ts <= $');
    });

    it('handles combined filters', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.findLogs({ profile_id: 'p1', status: 400, date_from: '2024-01-01', date_to: '2024-12-31' });

      const dataQuerySql = queryMock.mock.calls[0][0] as string;
      expect(dataQuerySql).toContain('profile_id = $');
      expect(dataQuerySql).toContain('status_code = $');
      expect(dataQuerySql).toContain('ts >= $');
      expect(dataQuerySql).toContain('ts <= $');
    });

    it('calculates correct offset for pagination', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.findLogs({ page: 3, size: 10 });

      // offset = (3-1)*10 = 20
      const dataQueryParams = queryMock.mock.calls[0][1] as unknown[];
      expect(dataQueryParams).toContain(20);
    });

    it('sets null for optional null fields', async () => {
      const row = makeLogRow({ api_key_id: null, profile_id: null, provider_id: null, error_code: null, request_preview: null, response_preview: null });
      const queryMock = jest.fn()
        .mockResolvedValueOnce([row])
        .mockResolvedValueOnce([{ total: 1 }]);
      const service = makeService(queryMock);

      const result = await service.findLogs({});

      expect(result.items[0].apiKeyId).toBeNull();
      expect(result.items[0].profileId).toBeNull();
      expect(result.items[0].providerId).toBeNull();
      expect(result.items[0].errorCode).toBeNull();
      expect(result.items[0].requestPreview).toBeNull();
      expect(result.items[0].responsePreview).toBeNull();
    });

    it('handles query failures gracefully', async () => {
      const queryMock = jest.fn().mockRejectedValue(new Error('timeout'));
      const service = makeService(queryMock);

      const result = await service.findLogs({});

      expect(result.items).toEqual([]);
      expect(result.total).toBe(0);
    });
  });

  describe('findLogById', () => {
    it('returns null when log not found', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      const result = await service.findLogById('nonexistent');

      expect(result).toBeNull();
    });

    it('returns log item when found', async () => {
      const row = makeLogRow();
      const queryMock = jest.fn().mockResolvedValue([row]);
      const service = makeService(queryMock);

      const result = await service.findLogById('log-uuid-1');

      expect(result).not.toBeNull();
      expect(result!.id).toBe('log-uuid-1');
      expect(result!.statusCode).toBe(200);
    });

    it('queries by id correctly', async () => {
      const queryMock = jest.fn().mockResolvedValue([]);
      const service = makeService(queryMock);

      await service.findLogById('my-log-id');

      const callParams = queryMock.mock.calls[0];
      expect(callParams[1]).toEqual(['my-log-id']);
    });

    it('handles query failure gracefully', async () => {
      const queryMock = jest.fn().mockRejectedValue(new Error('DB error'));
      const service = makeService(queryMock);

      const result = await service.findLogById('log-id');

      expect(result).toBeNull();
    });
  });

  describe('getStats', () => {
    it('returns stats with correct structure', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{
          total_requests: 100,
          error_count: 5,
          avg_latency_ms: 250,
          cache_hits: 30,
          total_tokens: 15000,
        }])
        .mockResolvedValueOnce([
          { hour: new Date().toISOString(), count: 10 },
        ]);
      const service = makeService(queryMock);

      const result = await service.getStats(24);

      expect(result.totalRequests).toBe(100);
      expect(result.errorCount).toBe(5);
      expect(result.errorRate).toBeCloseTo(0.05);
      expect(result.avgLatencyMs).toBe(250);
      expect(result.cacheHitRate).toBeCloseTo(0.3);
      expect(result.totalTokens).toBe(15000);
      expect(result.requestsByHour).toHaveLength(1);
    });

    it('returns zero rates when totalRequests is zero', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ total_requests: 0, error_count: 0, avg_latency_ms: 0, cache_hits: 0, total_tokens: 0 }])
        .mockResolvedValueOnce([]);
      const service = makeService(queryMock);

      const result = await service.getStats();

      expect(result.errorRate).toBe(0);
      expect(result.cacheHitRate).toBe(0);
    });

    it('handles query failures and returns defaults', async () => {
      const queryMock = jest.fn().mockRejectedValue(new Error('timeout'));
      const service = makeService(queryMock);

      const result = await service.getStats();

      expect(result.totalRequests).toBe(0);
      expect(result.errorRate).toBe(0);
    });

    it('maps requestsByHour correctly', async () => {
      const hourStr = new Date().toISOString();
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ total_requests: 50, error_count: 2, avg_latency_ms: 100, cache_hits: 10, total_tokens: 5000 }])
        .mockResolvedValueOnce([{ hour: hourStr, count: 50 }]);
      const service = makeService(queryMock);

      const result = await service.getStats(48);

      expect(result.requestsByHour[0].count).toBe(50);
    });
  });
});
