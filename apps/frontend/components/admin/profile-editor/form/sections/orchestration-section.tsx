'use client';

import { EnumField } from '../enum-field';
import { FormSection } from '../form-section';
import { useProfileForm } from '../form-context';
import { HybridTriggersField } from '../hybrid-triggers-field';
import { TextField } from '../text-field';

/**
 * 조건부 필드는 해당 모드일 때만 그린다.
 * (workflow_id 는 mode=workflow 에서만, hybrid_triggers 는 mode=hybrid 에서만)
 * 값 자체는 모드를 바꿔도 지우지 않는다 — 모드를 되돌렸을 때 설정이 사라지면 안 된다.
 */
export function OrchestrationSection({ errorCount }: { errorCount: number }) {
  const { config } = useProfileForm();

  return (
    <FormSection title="오케스트레이션" errorCount={errorCount}>
      <EnumField fieldKey="mode" label="mode" />
      {config.mode === 'workflow' && (
        <TextField
          fieldKey="workflow_id"
          label="workflow_id"
          placeholder="workflow-id"
          omitWhenEmpty={false}
        />
      )}
      {config.mode === 'hybrid' && <HybridTriggersField />}
    </FormSection>
  );
}
