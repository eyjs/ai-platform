'use client';

import { Dropdown, type DropdownOption } from '@/components/ui/dropdown';
import { Input } from '@/components/ui/input';
import { hasToolsCapability } from '@/lib/profile/model-rules';
import type { DgxModel } from '@/types/profile';
import { FieldShell } from '../field-shell';
import { FormSection } from '../form-section';
import { useProfileForm } from '../form-context';
import { useField } from '../use-field';

/** 262144 → "262K". 옵션 라벨을 짧게 유지하기 위한 표시용 변환. */
function formatContextLength(tokens: number): string {
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`;
  return String(tokens);
}

function toOption(model: DgxModel, activeDefault: string): DropdownOption {
  const badges: string[] = [];
  if (model.isDefault || model.name === activeDefault) badges.push('기본');
  if (!hasToolsCapability(model)) badges.push('tools 미지원');

  const facts = [
    model.parameterSize,
    `컨텍스트 ${formatContextLength(model.contextLength)}`,
    model.capabilities.join(' / '),
  ].filter(Boolean);

  return {
    value: model.name,
    label: model.name,
    description: facts.join(' · '),
    badges,
  };
}

/**
 * main_model 선택.
 *
 * 선택지는 오직 DGX(GET /profiles/models)에서 온다. 목록을 못 받으면 드롭다운을
 * 비활성화하고 저장된 값을 읽기 전용으로 보여준다 — 하드코딩 목록으로 대체하지 않는다.
 */
function MainModelField() {
  const { modelsResponse, isModelsLoading } = useProfileForm();
  const field = useField('main_model');
  const current = typeof field.value === 'string' ? field.value : undefined;

  if (isModelsLoading) {
    return (
      <FieldShell fieldKey="main_model" label="main_model">
        <Dropdown options={[]} onChange={() => undefined} disabled placeholder="모델 목록 불러오는 중..." />
      </FieldShell>
    );
  }

  const isUnavailable = !modelsResponse || modelsResponse.source === 'unavailable';

  if (isUnavailable) {
    return (
      <FieldShell fieldKey="main_model" label="main_model">
        <Input
          id={field.controlId}
          value={current ?? '(미설정)'}
          readOnly
          disabled
          aria-describedby={field.describedBy}
          className="font-[family-name:var(--font-mono)]"
        />
        <p className="text-[var(--font-size-xs)] text-[var(--color-error)]">
          DGX 모델 목록을 가져올 수 없습니다
          {modelsResponse?.error ? `: ${modelsResponse.error}` : ''}
        </p>
        <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
          목록을 받을 수 없어 저장된 값을 그대로 표시합니다. 모델을 바꾸려면 DGX 연결을 복구하세요.
        </p>
      </FieldShell>
    );
  }

  const options = modelsResponse.models.map((model) =>
    toOption(model, modelsResponse.activeDefault),
  );

  return (
    <FieldShell fieldKey="main_model" label="main_model">
      <div id={field.controlId} aria-describedby={field.describedBy}>
        <Dropdown
          options={options}
          value={current}
          onChange={(next) => field.setValue(next)}
          placeholder="모델을 선택하세요"
        />
      </div>
      {options.length === 0 && (
        <p className="text-[var(--font-size-xs)] text-[var(--color-warning)]">
          DGX 가 서빙 중인 모델이 없습니다
        </p>
      )}
    </FieldShell>
  );
}

export function ModelSection({ errorCount }: { errorCount: number }) {
  return (
    <FormSection title="모델" errorCount={errorCount}>
      <MainModelField />
    </FormSection>
  );
}
