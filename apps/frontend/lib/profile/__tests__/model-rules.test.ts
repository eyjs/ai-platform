import { describe, expect, it } from 'vitest';
import { getMainModelIssues } from '../model-rules';
import type { DgxModel, DgxModelsResponse, ProfileConfig } from '@/types/profile';

const TOOL_MODEL: DgxModel = {
  name: 'model-with-tools',
  parameterSize: '36.0B',
  contextLength: 262144,
  capabilities: ['vision', 'completion', 'tools', 'thinking'],
  isDefault: true,
};

const NO_TOOL_MODEL: DgxModel = {
  name: 'model-without-tools',
  parameterSize: '8.0B',
  contextLength: 32768,
  capabilities: ['completion'],
  isDefault: false,
};

const AVAILABLE: DgxModelsResponse = {
  models: [TOOL_MODEL, NO_TOOL_MODEL],
  activeDefault: TOOL_MODEL.name,
  source: 'dgx',
};

const UNAVAILABLE: DgxModelsResponse = {
  models: [],
  activeDefault: '',
  source: 'unavailable',
  error: 'DGX 연결 실패',
};

function config(patch: Partial<ProfileConfig>): ProfileConfig {
  return { id: 'p', name: 'p', mode: 'agentic', ...patch };
}

describe('main_model 규칙', () => {
  it('tools 지원 모델은 agentic 에서 문제없다', () => {
    expect(getMainModelIssues(config({ main_model: TOOL_MODEL.name }), AVAILABLE)).toEqual([]);
  });

  it('tools 미지원 모델 + agentic 은 저장을 막는 오류다', () => {
    const issues = getMainModelIssues(config({ main_model: NO_TOOL_MODEL.name }), AVAILABLE);
    expect(issues).toHaveLength(1);
    expect(issues[0].severity).toBe('error');
    expect(issues[0].field).toBe('main_model');
    expect(issues[0].message).toContain('tools');
  });

  it('tools 미지원 모델 + hybrid 도 막는다 (bind_tools 경로가 같다)', () => {
    const issues = getMainModelIssues(
      config({ mode: 'hybrid', main_model: NO_TOOL_MODEL.name }),
      AVAILABLE,
    );
    expect(issues[0]?.severity).toBe('error');
  });

  it('tools 미지원 모델도 deterministic 에서는 허용한다', () => {
    expect(
      getMainModelIssues(config({ mode: 'deterministic', main_model: NO_TOOL_MODEL.name }), AVAILABLE),
    ).toEqual([]);
  });

  it('목록에 없는 모델은 경고일 뿐 저장을 막지 않는다', () => {
    const issues = getMainModelIssues(config({ main_model: 'ghost-model' }), AVAILABLE);
    expect(issues).toHaveLength(1);
    expect(issues[0].severity).toBe('warning');
  });

  it('DGX 목록을 못 받으면 어떤 판정도 하지 않는다', () => {
    expect(getMainModelIssues(config({ main_model: 'anything' }), UNAVAILABLE)).toEqual([]);
    expect(getMainModelIssues(config({ main_model: 'anything' }), null)).toEqual([]);
  });
});
