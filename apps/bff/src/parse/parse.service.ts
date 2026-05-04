import { Injectable, Logger, ServiceUnavailableException } from '@nestjs/common';

/** DocForge /v1/parse/sync 응답의 data 부분 */
interface DocForgeParseData {
  markdown: string;
  metadata: Record<string, unknown>;
  stats: Record<string, unknown>;
}

/** DocForge API 응답 공통 포맷 */
interface DocForgeResponse {
  success: boolean;
  data?: DocForgeParseData;
  error?: { code: string; message: string };
}

/** DocForge /v1/health 응답 data 부분 */
interface DocForgeHealthData {
  status: string;
  version: string;
}

interface DocForgeHealthResponse {
  success: boolean;
  data?: DocForgeHealthData;
  error?: { code: string; message: string };
}

/** 멀티파트 파일 (NestJS FileInterceptor가 주입) */
export interface UploadedFile {
  fieldname: string;
  originalname: string;
  encoding: string;
  mimetype: string;
  buffer: Buffer;
  size: number;
}

export interface ParseResult {
  markdown: string;
  metadata: Record<string, unknown>;
  stats: Record<string, unknown>;
}

export interface HealthResult {
  status: string;
  version: string;
}

@Injectable()
export class ParseService {
  private readonly logger = new Logger(ParseService.name);
  private readonly baseUrl: string;
  private readonly internalKey: string;
  private readonly timeoutMs: number;

  constructor() {
    this.baseUrl = (process.env.AIP_DOCFORGE_URL || '').replace(/\/+$/, '');
    this.internalKey = process.env.AIP_DOCFORGE_INTERNAL_KEY || '';
    this.timeoutMs = 130_000; // DocForge 120s + 10s 여유
  }

  /**
   * DocForge에 PDF를 전송하여 동기 파싱 결과를 받는다.
   */
  async uploadAndParse(file: UploadedFile): Promise<ParseResult> {
    this.ensureConfigured();

    const formData = new FormData();
    const uint8 = new Uint8Array(file.buffer);
    const blob = new Blob([uint8], { type: file.mimetype });
    formData.append('file', blob, file.originalname);

    const headers: Record<string, string> = {};
    if (this.internalKey) {
      headers['X-Internal-Key'] = this.internalKey;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const resp = await fetch(`${this.baseUrl}/v1/parse/sync`, {
        method: 'POST',
        headers,
        body: formData,
        signal: controller.signal,
      });

      const body = (await resp.json()) as DocForgeResponse;

      if (!resp.ok || !body.success) {
        const errorCode = body.error?.code || 'UNKNOWN';
        const errorMsg = body.error?.message || `HTTP ${resp.status}`;
        this.logger.error(`DocForge parse failed: [${errorCode}] ${errorMsg}`);
        throw new ServiceUnavailableException({
          success: false,
          error: {
            code: 'DOCFORGE_ERROR',
            message: `DocForge 파싱 실패: ${errorMsg}`,
          },
        });
      }

      const data = body.data;
      if (!data) {
        throw new ServiceUnavailableException({
          success: false,
          error: {
            code: 'DOCFORGE_EMPTY_RESPONSE',
            message: 'DocForge가 빈 응답을 반환했습니다.',
          },
        });
      }

      this.logger.log(
        `DocForge parse success: ${data.markdown.length} chars`,
      );

      return {
        markdown: data.markdown,
        metadata: data.metadata,
        stats: data.stats,
      };
    } catch (error) {
      if (error instanceof ServiceUnavailableException) {
        throw error;
      }

      const message =
        error instanceof Error ? error.message : 'Unknown error';
      this.logger.error(`DocForge request failed: ${message}`);
      throw new ServiceUnavailableException({
        success: false,
        error: {
          code: 'DOCFORGE_UNREACHABLE',
          message: `DocForge 서버에 연결할 수 없습니다: ${message}`,
        },
      });
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * DocForge 서버 상태 확인
   */
  async checkHealth(): Promise<HealthResult> {
    this.ensureConfigured();

    const headers: Record<string, string> = {};
    if (this.internalKey) {
      headers['X-Internal-Key'] = this.internalKey;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5_000);

    try {
      const resp = await fetch(`${this.baseUrl}/v1/health`, {
        method: 'GET',
        headers,
        signal: controller.signal,
      });

      const body = (await resp.json()) as DocForgeHealthResponse;

      if (!resp.ok || !body.success || !body.data) {
        throw new ServiceUnavailableException({
          success: false,
          error: {
            code: 'DOCFORGE_UNHEALTHY',
            message: 'DocForge 서버가 비정상 상태입니다.',
          },
        });
      }

      return {
        status: body.data.status,
        version: body.data.version,
      };
    } catch (error) {
      if (error instanceof ServiceUnavailableException) {
        throw error;
      }

      throw new ServiceUnavailableException({
        success: false,
        error: {
          code: 'DOCFORGE_UNREACHABLE',
          message: 'DocForge 서버에 연결할 수 없습니다.',
        },
      });
    } finally {
      clearTimeout(timer);
    }
  }

  private ensureConfigured(): void {
    if (!this.baseUrl) {
      throw new ServiceUnavailableException({
        success: false,
        error: {
          code: 'DOCFORGE_NOT_CONFIGURED',
          message: 'AIP_DOCFORGE_URL 환경변수가 설정되지 않았습니다.',
        },
      });
    }
    if (!this.internalKey) {
      throw new ServiceUnavailableException({
        success: false,
        error: {
          code: 'DOCFORGE_NOT_CONFIGURED',
          message: 'AIP_DOCFORGE_INTERNAL_KEY 환경변수가 설정되지 않았습니다.',
        },
      });
    }
  }
}
