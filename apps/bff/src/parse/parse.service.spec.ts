import { ServiceUnavailableException } from '@nestjs/common';
import { ParseService, UploadedFile } from './parse.service';

const mockFile: UploadedFile = {
  fieldname: 'file',
  originalname: 'test.pdf',
  encoding: '7bit',
  mimetype: 'application/pdf',
  buffer: Buffer.from('fake-pdf-bytes'),
  size: 14,
};

function makeService(env: Record<string, string> = {}): ParseService {
  const prev: Record<string, string | undefined> = {};
  for (const [k, v] of Object.entries(env)) {
    prev[k] = process.env[k];
    process.env[k] = v;
  }
  const svc = new ParseService();
  for (const [k] of Object.entries(env)) {
    if (prev[k] === undefined) delete process.env[k];
    else process.env[k] = prev[k];
  }
  return svc;
}

describe('ParseService', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  describe('ensureConfigured (via uploadAndParse)', () => {
    it('throws when AIP_DOCFORGE_URL is empty', async () => {
      const svc = makeService({
        AIP_DOCFORGE_URL: '',
        AIP_DOCFORGE_INTERNAL_KEY: 'key',
      });
      await expect(svc.uploadAndParse(mockFile)).rejects.toThrow(
        ServiceUnavailableException,
      );
    });

    it('throws when AIP_DOCFORGE_INTERNAL_KEY is empty', async () => {
      const svc = makeService({
        AIP_DOCFORGE_URL: 'http://localhost:5051',
        AIP_DOCFORGE_INTERNAL_KEY: '',
      });
      await expect(svc.uploadAndParse(mockFile)).rejects.toThrow(
        ServiceUnavailableException,
      );
    });
  });

  describe('uploadAndParse', () => {
    let svc: ParseService;

    beforeEach(() => {
      svc = makeService({
        AIP_DOCFORGE_URL: 'http://localhost:5051',
        AIP_DOCFORGE_INTERNAL_KEY: 'test-key',
      });
    });

    it('returns ParseResult on success', async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          success: true,
          data: {
            markdown: '# Hello',
            metadata: { pages: 1 },
            stats: { elapsed_ms: 500 },
          },
        }),
      });

      const result = await svc.uploadAndParse(mockFile);
      expect(result).toEqual({
        markdown: '# Hello',
        metadata: { pages: 1 },
        stats: { elapsed_ms: 500 },
      });

      const call = (globalThis.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toBe('http://localhost:5051/v1/parse/sync');
      expect(call[1].headers['X-Internal-Key']).toBe('test-key');
    });

    it('throws ServiceUnavailableException on DocForge error response', async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 415,
        json: async () => ({
          success: false,
          error: { code: 'UNSUPPORTED_MEDIA_TYPE', message: 'Not PDF' },
        }),
      });

      await expect(svc.uploadAndParse(mockFile)).rejects.toThrow(
        ServiceUnavailableException,
      );
    });

    it('throws ServiceUnavailableException when data is null', async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ success: true, data: null }),
      });

      await expect(svc.uploadAndParse(mockFile)).rejects.toThrow(
        ServiceUnavailableException,
      );
    });

    it('throws DOCFORGE_UNREACHABLE on network error', async () => {
      globalThis.fetch = jest
        .fn()
        .mockRejectedValue(new Error('ECONNREFUSED'));

      try {
        await svc.uploadAndParse(mockFile);
        fail('should have thrown');
      } catch (e) {
        expect(e).toBeInstanceOf(ServiceUnavailableException);
        const resp = (e as ServiceUnavailableException).getResponse();
        expect((resp as Record<string, unknown>).error).toMatchObject({
          code: 'DOCFORGE_UNREACHABLE',
        });
      }
    });

    it('strips trailing slashes from baseUrl', async () => {
      const svc2 = makeService({
        AIP_DOCFORGE_URL: 'http://localhost:5051///',
        AIP_DOCFORGE_INTERNAL_KEY: 'test-key',
      });
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          success: true,
          data: { markdown: 'ok', metadata: {}, stats: {} },
        }),
      });

      await svc2.uploadAndParse(mockFile);
      const url = (globalThis.fetch as jest.Mock).mock.calls[0][0];
      expect(url).toBe('http://localhost:5051/v1/parse/sync');
    });
  });

  describe('checkHealth', () => {
    let svc: ParseService;

    beforeEach(() => {
      svc = makeService({
        AIP_DOCFORGE_URL: 'http://localhost:5051',
        AIP_DOCFORGE_INTERNAL_KEY: 'test-key',
      });
    });

    it('returns HealthResult on success', async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          success: true,
          data: { status: 'ok', version: '1.0.0' },
        }),
      });

      const result = await svc.checkHealth();
      expect(result).toEqual({ status: 'ok', version: '1.0.0' });
    });

    it('throws ServiceUnavailableException on unhealthy response', async () => {
      globalThis.fetch = jest.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ success: false }),
      });

      await expect(svc.checkHealth()).rejects.toThrow(
        ServiceUnavailableException,
      );
    });

    it('throws DOCFORGE_UNREACHABLE on network error', async () => {
      globalThis.fetch = jest
        .fn()
        .mockRejectedValue(new Error('ECONNREFUSED'));

      await expect(svc.checkHealth()).rejects.toThrow(
        ServiceUnavailableException,
      );
    });
  });
});
