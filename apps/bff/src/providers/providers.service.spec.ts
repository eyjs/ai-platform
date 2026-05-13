import { Repository } from 'typeorm';
import { ProvidersService } from './providers.service';
import { CacheEntry } from '../entities/cache-entry.entity';

function makeCacheRepo(queryMock: jest.Mock): jest.Mocked<Repository<CacheEntry>> {
  return {
    manager: { query: queryMock },
  } as unknown as jest.Mocked<Repository<CacheEntry>>;
}

function makeService(queryMock: jest.Mock): ProvidersService {
  const repo = makeCacheRepo(queryMock);
  return new ProvidersService(repo);
}

describe('ProvidersService', () => {
  describe('getProvidersStatus', () => {
    it('returns correct structure with provider stats', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{
          total_providers: 3,
          active_providers: 2,
          cache_entries: 10,
          expired_entries: 1,
        }])
        .mockResolvedValueOnce([
          { provider_type: 'llm', total_providers: 2, active_entries: 8, expired_entries: 1 },
          { provider_type: 'embedding', total_providers: 1, active_entries: 2, expired_entries: 0 },
        ])
        .mockResolvedValueOnce([
          {
            provider_id: 'openai',
            provider_type: 'llm',
            cache_entries: 5,
            expired_entries: 0,
            last_activity: new Date().toISOString(),
          },
        ]);

      const service = makeService(queryMock);
      const result = await service.getProvidersStatus();

      expect(result.totalProviders).toBe(3);
      expect(result.activeProviders).toBe(2);
      expect(result.cacheEntries).toBe(10);
      expect(result.expiredEntries).toBe(1);
    });

    it('returns providersByType with correct mapping', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ total_providers: 1, active_providers: 1, cache_entries: 5, expired_entries: 0 }])
        .mockResolvedValueOnce([
          { provider_type: 'llm', total_providers: 1, active_entries: 5, expired_entries: 0 },
        ])
        .mockResolvedValueOnce([]);

      const service = makeService(queryMock);
      const result = await service.getProvidersStatus();

      expect(result.providersByType).toHaveLength(1);
      expect(result.providersByType[0].providerType).toBe('llm');
      expect(result.providersByType[0].totalProviders).toBe(1);
      expect(result.providersByType[0].activeEntries).toBe(5);
      expect(result.providersByType[0].expiredEntries).toBe(0);
    });

    it('returns providerMetrics with isActive based on last_activity', async () => {
      const recentTime = new Date().toISOString();
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ total_providers: 1, active_providers: 1, cache_entries: 2, expired_entries: 0 }])
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([
          { provider_id: 'openai', provider_type: 'llm', cache_entries: 2, expired_entries: 0, last_activity: recentTime },
        ]);

      const service = makeService(queryMock);
      const result = await service.getProvidersStatus();

      expect(result.providerMetrics).toHaveLength(1);
      expect(result.providerMetrics[0].providerId).toBe('openai');
      expect(result.providerMetrics[0].isActive).toBe(true);
    });

    it('marks provider inactive when last_activity is older than 1 hour', async () => {
      const oldTime = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ total_providers: 1, active_providers: 0, cache_entries: 1, expired_entries: 1 }])
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([
          { provider_id: 'old-provider', provider_type: 'llm', cache_entries: 1, expired_entries: 1, last_activity: oldTime },
        ]);

      const service = makeService(queryMock);
      const result = await service.getProvidersStatus();

      expect(result.providerMetrics[0].isActive).toBe(false);
    });

    it('handles null last_activity gracefully', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ total_providers: 1, active_providers: 0, cache_entries: 1, expired_entries: 0 }])
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([
          { provider_id: 'no-activity', provider_type: null, cache_entries: 1, expired_entries: 0, last_activity: null },
        ]);

      const service = makeService(queryMock);
      const result = await service.getProvidersStatus();

      expect(result.providerMetrics[0].lastActivity).toBeNull();
      expect(result.providerMetrics[0].isActive).toBe(false);
      expect(result.providerMetrics[0].providerType).toBe('unknown');
    });

    it('returns zeros when queries fail', async () => {
      const queryMock = jest.fn().mockRejectedValue(new Error('DB error'));
      const service = makeService(queryMock);

      const result = await service.getProvidersStatus();

      expect(result.totalProviders).toBe(0);
      expect(result.activeProviders).toBe(0);
      expect(result.cacheEntries).toBe(0);
      expect(result.expiredEntries).toBe(0);
      expect(result.providersByType).toEqual([]);
      expect(result.providerMetrics).toEqual([]);
    });

    it('returns empty providersByType and providerMetrics when no providers', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([{ total_providers: 0, active_providers: 0, cache_entries: 0, expired_entries: 0 }])
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([]);

      const service = makeService(queryMock);
      const result = await service.getProvidersStatus();

      expect(result.providersByType).toEqual([]);
      expect(result.providerMetrics).toEqual([]);
    });
  });
});
