import { describe, it, expect } from 'vitest';
import { normalizeLlmEnginesHealth } from './llm-engines';

const validPayload = {
  providerMode: 'development',
  fallbackEnabled: true,
  dgx: {
    configured: true,
    baseUrl: 'http://100.102.16.62:11434',
    defaultModel: 'qwen3.6:35b-a3b',
    roleOverrides: { report: '', router: '', orchestration: '', fortune: '' },
    link: { up: true, checkedAt: 1784173221.5, detail: 'generate ok' },
    models: [
      {
        name: 'qwen3.6:35b-a3b',
        parameterSize: '36.0B',
        contextLength: 262144,
        capabilities: ['vision', 'completion', 'tools', 'thinking'],
        isDefault: true,
      },
    ],
    modelsError: null,
  },
  mlx: {
    engines: [
      {
        roles: ['main', 'fortune'],
        url: 'http://host.docker.internal:8106',
        model: 'mlx-community/Qwen3.5-9B-4bit',
        link: { up: true, checkedAt: 1784173221.5, detail: 'generate ok' },
        modelError: null,
      },
    ],
  },
};

describe('normalizeLlmEnginesHealth', () => {
  it('계약대로의 응답을 그대로 통과시킨다', () => {
    const result = normalizeLlmEnginesHealth(validPayload);
    expect(result.providerMode).toBe('development');
    expect(result.fallbackEnabled).toBe(true);
    expect(result.dgx.models).toHaveLength(1);
    expect(result.dgx.models[0].capabilities).toContain('tools');
    expect(result.mlx.engines[0].roles).toEqual(['main', 'fortune']);
  });

  it('link.up이 누락되면 null(미확인)이다 — false로 접지 않는다', () => {
    const result = normalizeLlmEnginesHealth({
      ...validPayload,
      dgx: { ...validPayload.dgx, link: { checkedAt: 1784173221.5, detail: 'n/a' } },
    });
    expect(result.dgx.link.up).toBeNull();
  });

  it('link.up이 false면 false를 유지한다', () => {
    const result = normalizeLlmEnginesHealth({
      ...validPayload,
      dgx: {
        ...validPayload.dgx,
        link: { up: false, checkedAt: 1784173221.5, detail: 'connection refused' },
      },
    });
    expect(result.dgx.link.up).toBe(false);
  });

  it('link 자체가 없어도 터지지 않고 미확인으로 둔다', () => {
    const result = normalizeLlmEnginesHealth({
      ...validPayload,
      dgx: { ...validPayload.dgx, link: null },
    });
    expect(result.dgx.link).toEqual({ up: null, checkedAt: null, detail: null, latencyMs: null });
  });

  it('modelsError를 보존한다', () => {
    const result = normalizeLlmEnginesHealth({
      ...validPayload,
      dgx: { ...validPayload.dgx, models: [], modelsError: 'connection refused' },
    });
    expect(result.dgx.modelsError).toBe('connection refused');
    expect(result.dgx.models).toEqual([]);
  });

  it('이름 없는 모델은 버린다 (표에 빈 행을 만들지 않는다)', () => {
    const result = normalizeLlmEnginesHealth({
      ...validPayload,
      dgx: { ...validPayload.dgx, models: [{ parameterSize: '7B' }, null, 'junk'] },
    });
    expect(result.dgx.models).toEqual([]);
  });

  it('mlx가 없으면 빈 목록이다', () => {
    const result = normalizeLlmEnginesHealth({ ...validPayload, mlx: undefined });
    expect(result.mlx.engines).toEqual([]);
  });

  it('응답이 객체가 아니면 던진다', () => {
    expect(() => normalizeLlmEnginesHealth(null)).toThrow();
    expect(() => normalizeLlmEnginesHealth('down')).toThrow();
  });

  it('dgx 블록이 없으면 던진다 (조용히 빈 화면을 만들지 않는다)', () => {
    expect(() => normalizeLlmEnginesHealth({ providerMode: 'development' })).toThrow(
      /dgx/,
    );
  });
});
