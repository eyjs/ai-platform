/**
 * main_model 선택 규칙.
 *
 * 모델 이름은 이 파일 어디에도 없다 — 목록은 항상 DGX(GET /profiles/models)에서 온다.
 * 여기 있는 것은 "모델의 성질과 모드의 조합"에 대한 규칙뿐이다.
 */

import type { DgxModel, DgxModelsResponse, FieldIssue, ProfileConfig } from '@/types/profile';

/** bind_tools 를 호출하는 실행 모드. 이 모드에서는 tools capability 가 없는 모델은 못 쓴다. */
const TOOL_CALLING_MODES = ['agentic', 'hybrid'];

const TOOLS_CAPABILITY = 'tools';

export function hasToolsCapability(model: DgxModel): boolean {
  return model.capabilities.includes(TOOLS_CAPABILITY);
}

export function isToolCallingMode(mode: string): boolean {
  return TOOL_CALLING_MODES.includes(mode);
}

export function findModel(models: DgxModel[], name: string | undefined): DgxModel | null {
  if (!name) return null;
  return models.find((model) => model.name === name) ?? null;
}

/**
 * main_model 필드에 붙일 이슈.
 *
 * - DGX 목록을 못 받았으면(unavailable) 판단 자체를 하지 않는다. 목록 없이 내리는
 *   "이 모델은 없다" 판정은 근거가 없다.
 * - tools 미지원 모델 + tool calling 모드 = 저장 차단(error). 런타임에서 bind_tools 가 깨진다.
 */
export function getMainModelIssues(
  config: ProfileConfig,
  modelsResponse: DgxModelsResponse | null,
): FieldIssue[] {
  if (!modelsResponse || modelsResponse.source === 'unavailable') return [];

  const selected = config.main_model;
  if (!selected) return [];

  const model = findModel(modelsResponse.models, selected);

  if (!model) {
    return [
      {
        field: 'main_model',
        path: '/main_model',
        message: `DGX 목록에 없는 모델입니다: ${selected}`,
        severity: 'warning',
      },
    ];
  }

  if (isToolCallingMode(config.mode) && !hasToolsCapability(model)) {
    return [
      {
        field: 'main_model',
        path: '/main_model',
        message: `이 모델은 tools 를 지원하지 않아 mode=${config.mode} 에서 동작하지 않습니다`,
        severity: 'error',
      },
    ];
  }

  return [];
}
