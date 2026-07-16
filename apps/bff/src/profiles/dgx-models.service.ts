import { Injectable, Logger } from '@nestjs/common';

/**
 * DGX Spark(ollama)가 실제로 서빙 중인 모델 목록. 관리자 UI 의 main_model 드롭다운은
 * 오직 이 목록만 후보로 쓴다 — 하드코딩 목록을 두면 예전 haiku/sonnet/opus 처럼
 * 서빙 실체와 어긋난 선택지를 사용자에게 보여주게 된다.
 *
 * bff 가 apps/api 를 호출하지 않는다는 경계 규칙은 지킨다. DGX 는 apps/api 가 아니라
 * 외부 LLM 호스트이므로 직접 조회한다.
 *
 * DGX 가 안 잡히면 목록을 지어내지 않고 source:'unavailable' 로 정직하게 알린다.
 */

export interface DgxModel {
  name: string;
  parameterSize: string;
  contextLength: number | null;
  capabilities: string[];
  isDefault: boolean;
}

export interface DgxModelsResponse {
  models: DgxModel[];
  activeDefault: string;
  source: 'dgx' | 'unavailable';
  error?: string;
}

interface OllamaTag {
  name?: string;
  details?: { parameter_size?: string; context_length?: number };
  capabilities?: string[];
}

const CACHE_TTL_MS = 60_000;
const FETCH_TIMEOUT_MS = 4_000;

@Injectable()
export class DgxModelsService {
  private readonly logger = new Logger(DgxModelsService.name);
  private cache: { at: number; value: DgxModelsResponse } | null = null;

  private get baseUrl(): string {
    return (process.env.AIP_DGX_LLM_URL || '').replace(/\/$/, '');
  }

  private get defaultModel(): string {
    return process.env.AIP_DGX_MAIN_MODEL || '';
  }

  async list(): Promise<DgxModelsResponse> {
    const now = Date.now();
    if (this.cache && now - this.cache.at < CACHE_TTL_MS) return this.cache.value;

    const value = await this.fetchFresh();
    // 실패 응답은 캐싱하지 않는다 — DGX 가 돌아오면 즉시 반영되어야 한다.
    if (value.source === 'dgx') this.cache = { at: now, value };
    return value;
  }

  private async fetchFresh(): Promise<DgxModelsResponse> {
    const base = this.baseUrl;
    if (!base) {
      return this.unavailable('AIP_DGX_LLM_URL 이 설정되지 않았습니다');
    }

    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
      let res: Response;
      try {
        res = await fetch(`${base}/api/tags`, { signal: controller.signal });
      } finally {
        clearTimeout(timer);
      }

      if (!res.ok) return this.unavailable(`DGX 응답 오류: HTTP ${res.status}`);

      const body = (await res.json()) as { models?: OllamaTag[] };
      const tags = body.models ?? [];
      const models: DgxModel[] = tags
        .filter((t): t is OllamaTag & { name: string } => typeof t.name === 'string')
        .map((t) => ({
          name: t.name,
          parameterSize: t.details?.parameter_size ?? '',
          contextLength: t.details?.context_length ?? null,
          capabilities: t.capabilities ?? [],
          isDefault: t.name === this.defaultModel,
        }))
        .sort((a, b) => a.name.localeCompare(b.name));

      return { models, activeDefault: this.defaultModel, source: 'dgx' };
    } catch (err) {
      const message = err instanceof Error ? err.message : 'unknown';
      this.logger.warn(`[profiles] DGX 모델 목록 조회 실패: ${message}`);
      return this.unavailable(`DGX 에 연결할 수 없습니다: ${message}`);
    }
  }

  private unavailable(error: string): DgxModelsResponse {
    return {
      models: [],
      activeDefault: this.defaultModel,
      source: 'unavailable',
      error,
    };
  }
}
