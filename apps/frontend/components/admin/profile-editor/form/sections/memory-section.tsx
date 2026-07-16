'use client';

import { EnumField } from '../enum-field';
import { FormSection } from '../form-section';
import { useProfileForm } from '../form-context';
import { MultiEnumField } from '../multi-enum-field';
import { NumberField } from '../number-field';
import { TextField } from '../text-field';

function hasProjectScope(scopes: string[] | undefined): boolean {
  return Array.isArray(scopes) && scopes.includes('project');
}

export function MemorySection({ errorCount }: { errorCount: number }) {
  const { config } = useProfileForm();
  const isProjectScoped = hasProjectScope(config.memory_scopes);

  return (
    <FormSection title="메모리" errorCount={errorCount}>
      <EnumField fieldKey="memory_type" label="memory_type" />
      <NumberField fieldKey="memory_ttl_seconds" label="memory_ttl_seconds" unit="초" />
      <MultiEnumField
        fieldKey="memory_scopes"
        label="memory_scopes"
        optionHints={{
          local: '세션 턴',
          user: 'tenant_memory 테이블',
          project: 'project_memory 테이블 (memory_project_id 필수)',
        }}
      />
      {/* project 스코프일 때만 노출. 없으면 런타임이 프로필 생성 시 예외를 던진다. */}
      {isProjectScoped && (
        <TextField fieldKey="memory_project_id" label="memory_project_id" omitWhenEmpty={false} />
      )}
      <NumberField fieldKey="memory_max_turns" label="memory_max_turns" unit="턴" />
      <NumberField fieldKey="memory_retention_days" label="memory_retention_days" unit="일" />
    </FormSection>
  );
}
