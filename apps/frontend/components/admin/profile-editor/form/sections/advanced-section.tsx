'use client';

import { BooleanField } from '../boolean-field';
import { FormSection } from '../form-section';
import { IntentHintsField } from '../intent-hints-field';
import { JsonField } from '../json-field';
import { NumberField } from '../number-field';
import { TextAreaField, TextField } from '../text-field';

export function AdvancedSection({ errorCount }: { errorCount: number }) {
  return (
    <FormSection title="고급" errorCount={errorCount} defaultOpen={false}>
      <NumberField fieldKey="max_tool_calls" label="max_tool_calls" unit="회" />
      <NumberField fieldKey="agent_timeout_seconds" label="agent_timeout_seconds" unit="초" />
      <BooleanField fieldKey="planning_disabled" label="planning_disabled (Planner 끄기)" />
      <NumberField fieldKey="max_output_tokens" label="max_output_tokens" unit="토큰" />
      <TextField fieldKey="context_adapter" label="context_adapter" placeholder="saju" />
      <JsonField fieldKey="cache" label="cache" />
      <TextAreaField fieldKey="cache_padding_text" label="cache_padding_text" rows={3} isMonospace />
      <IntentHintsField />
      <TextField fieldKey="workflow_action_endpoint" label="workflow_action_endpoint" />
      <JsonField fieldKey="workflow_action_headers" label="workflow_action_headers" rows={4} />
    </FormSection>
  );
}
