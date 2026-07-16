'use client';

import { EnumField } from '../enum-field';
import { FormSection } from '../form-section';
import { MultiEnumField } from '../multi-enum-field';
import { TextAreaField, TextField } from '../text-field';

export function PromptSection({ errorCount }: { errorCount: number }) {
  return (
    <FormSection title="프롬프트/정책" errorCount={errorCount}>
      <TextAreaField
        fieldKey="system_prompt"
        label="system_prompt"
        rows={10}
        isMonospace
        placeholder="비우면 플랫폼 기본 프롬프트가 사용됩니다"
      />
      <EnumField fieldKey="response_policy" label="response_policy" />
      <MultiEnumField fieldKey="guardrails" label="guardrails" />
      <TextField fieldKey="empty_response_fallback" label="empty_response_fallback" />
    </FormSection>
  );
}
