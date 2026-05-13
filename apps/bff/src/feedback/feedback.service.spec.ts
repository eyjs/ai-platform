import { HttpException } from '@nestjs/common';
import { DataSource } from 'typeorm';
import { FeedbackService } from './feedback.service';

function makeDataSource(queryMock?: jest.Mock): jest.Mocked<DataSource> {
  return {
    query: queryMock ?? jest.fn().mockResolvedValue([]),
  } as unknown as jest.Mocked<DataSource>;
}

function makeService(dataSource: jest.Mocked<DataSource>): FeedbackService {
  return new FeedbackService(dataSource);
}

describe('FeedbackService', () => {
  const originalFetch = globalThis.fetch;
  const originalEnv = process.env.AIP_API_URL;

  beforeEach(() => {
    delete process.env.AIP_API_URL;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    if (originalEnv === undefined) {
      delete process.env.AIP_API_URL;
    } else {
      process.env.AIP_API_URL = originalEnv;
    }
  });

  describe('submit', () => {
    it('throws HttpException with UNAUTHORIZED when authorization is missing', async () => {
      const service = makeService(makeDataSource());
      const dto = { response_id: 'r1', score: 1 as const };

      await expect(
        service.submit(dto, undefined),
      ).rejects.toThrow(HttpException);

      try {
        await service.submit(dto, undefined);
      } catch (e) {
        const err = e as HttpException;
        expect(err.getStatus()).toBe(401);
        const body = err.getResponse() as { success: boolean; error: { code: string } };
        expect(body.error.code).toBe('UNAUTHORIZED');
      }
    });

    it('returns parsed JSON response on success', async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        text: async () => JSON.stringify({ success: true, data: { id: 'fb-1' } }),
      });

      const service = makeService(makeDataSource());
      const result = await service.submit(
        { response_id: 'r1', score: 1 as const, comment: 'great' },
        'Bearer token123',
      );

      expect(result).toEqual({ success: true, data: { id: 'fb-1' } });
    });

    it('forwards authorization header to api', async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        text: async () => '{}',
      });

      const service = makeService(makeDataSource());
      await service.submit(
        { response_id: 'r1', score: 1 as const },
        'Bearer my-token',
      );

      const fetchCall = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(fetchCall[1].headers['Authorization']).toBe('Bearer my-token');
    });

    it('posts to correct api endpoint', async () => {
      process.env.AIP_API_URL = 'http://api-server:8000';
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        text: async () => '{}',
      });

      const service = makeService(makeDataSource());
      await service.submit(
        { response_id: 'r1', score: 1 as const },
        'Bearer token',
      );

      const fetchCall = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(fetchCall[0]).toBe('http://api-server:8000/api/feedback');
    });

    it('throws HttpException with API_UNREACHABLE on network error', async () => {
      globalThis.fetch = jest.fn().mockRejectedValue(new Error('ECONNREFUSED'));

      const service = makeService(makeDataSource());
      const dto = { response_id: 'r1', score: 1 as const };

      await expect(
        service.submit(dto, 'Bearer token'),
      ).rejects.toThrow(HttpException);

      try {
        await service.submit(dto, 'Bearer token');
      } catch (e) {
        const err = e as HttpException;
        expect(err.getStatus()).toBe(502);
        const body = err.getResponse() as { error: { code: string } };
        expect(body.error.code).toBe('API_UNREACHABLE');
      }
    });

    it('throws HttpException with api status code on non-ok response', async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 422,
        text: async () => JSON.stringify({ success: false, error: { code: 'VALIDATION_ERROR', message: 'Invalid' } }),
      });

      const service = makeService(makeDataSource());

      try {
        await service.submit({ response_id: 'r1', score: 1 as const }, 'Bearer token');
        fail('expected to throw');
      } catch (e) {
        const err = e as HttpException;
        expect(err.getStatus()).toBe(422);
      }
    });

    it('returns empty object when response text is empty', async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        text: async () => '',
      });

      const service = makeService(makeDataSource());
      const result = await service.submit(
        { response_id: 'r1', score: 1 as const },
        'Bearer token',
      );

      expect(result).toEqual({});
    });
  });

  describe('list', () => {
    it('returns empty page when no feedback', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ c: 0 }]);
      const service = makeService(makeDataSource(queryMock));

      const result = await service.list({});

      expect(result.items).toEqual([]);
      expect(result.total).toBe(0);
    });

    it('maps feedback rows correctly', async () => {
      const now = new Date().toISOString();
      const row = {
        id: 'fb-1',
        response_id: 'r1',
        score: 1,
        comment: 'nice',
        created_at: now,
        user_id: 'user-1',
        profile_id: 'profile-1',
        faithfulness_score: 0.95,
        question_preview: 'Hello?',
        answer_preview: 'World!',
        response_ts: now,
      };
      const queryMock = jest.fn()
        .mockResolvedValueOnce([row])
        .mockResolvedValueOnce([{ c: 1 }]);
      const service = makeService(makeDataSource(queryMock));

      const result = await service.list({});

      expect(result.items).toHaveLength(1);
      expect(result.items[0].id).toBe('fb-1');
      expect(result.items[0].score).toBe(1);
      expect(result.items[0].comment).toBe('nice');
      expect(result.items[0].faithfulness_score).toBeCloseTo(0.95);
      expect(result.items[0].profile_id).toBe('profile-1');
    });

    it('applies only_negative filter', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ c: 0 }]);
      const service = makeService(makeDataSource(queryMock));

      await service.list({ only_negative: true });

      const listSql = queryMock.mock.calls[0][0] as string;
      expect(listSql).toContain('f.score = -1');
    });

    it('does not apply only_negative filter when false', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ c: 0 }]);
      const service = makeService(makeDataSource(queryMock));

      await service.list({ only_negative: false });

      const listSql = queryMock.mock.calls[0][0] as string;
      expect(listSql).not.toContain('f.score = -1');
    });

    it('applies date_from filter', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ c: 0 }]);
      const service = makeService(makeDataSource(queryMock));

      await service.list({ date_from: '2024-01-01' });

      const listSql = queryMock.mock.calls[0][0] as string;
      expect(listSql).toContain('f.created_at >=');
    });

    it('applies date_to filter', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ c: 0 }]);
      const service = makeService(makeDataSource(queryMock));

      await service.list({ date_to: '2024-12-31' });

      const listSql = queryMock.mock.calls[0][0] as string;
      expect(listSql).toContain('f.created_at <');
    });

    it('returns correct pagination params', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ c: 100 }]);
      const service = makeService(makeDataSource(queryMock));

      const result = await service.list({ limit: 10, offset: 20 });

      expect(result.limit).toBe(10);
      expect(result.offset).toBe(20);
    });

    it('caps limit at 200', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ c: 0 }]);
      const service = makeService(makeDataSource(queryMock));

      const result = await service.list({ limit: 999 });

      expect(result.limit).toBe(200);
    });

    it('uses minimum limit of 1', async () => {
      const queryMock = jest.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([{ c: 0 }]);
      const service = makeService(makeDataSource(queryMock));

      const result = await service.list({ limit: 0 });

      expect(result.limit).toBe(1);
    });

    it('throws HttpException with DB_ERROR on query failure', async () => {
      const queryMock = jest.fn().mockRejectedValue(new Error('connection lost'));
      const service = makeService(makeDataSource(queryMock));

      await expect(service.list({})).rejects.toThrow(HttpException);

      try {
        await service.list({});
      } catch (e) {
        const err = e as HttpException;
        expect(err.getStatus()).toBe(500);
        const body = err.getResponse() as { error: { code: string } };
        expect(body.error.code).toBe('DB_ERROR');
      }
    });

    it('handles null optional fields in feedback rows', async () => {
      const now = new Date().toISOString();
      const row = {
        id: 'fb-2',
        response_id: 'r2',
        score: -1,
        comment: null,
        created_at: now,
        user_id: 'user-2',
        profile_id: null,
        faithfulness_score: null,
        question_preview: null,
        answer_preview: null,
        response_ts: null,
      };
      const queryMock = jest.fn()
        .mockResolvedValueOnce([row])
        .mockResolvedValueOnce([{ c: 1 }]);
      const service = makeService(makeDataSource(queryMock));

      const result = await service.list({});

      expect(result.items[0].comment).toBeNull();
      expect(result.items[0].profile_id).toBeNull();
      expect(result.items[0].faithfulness_score).toBeNull();
      expect(result.items[0].question_preview).toBeNull();
      expect(result.items[0].answer_preview).toBeNull();
      expect(result.items[0].response_ts).toBeNull();
    });
  });
});
