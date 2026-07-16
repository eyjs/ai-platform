'use client';

import { FormSection } from '../form-section';
import { ToolsField } from '../tools-field';

export function ToolsSection({ errorCount }: { errorCount: number }) {
  return (
    <FormSection title="도구" errorCount={errorCount}>
      <ToolsField />
    </FormSection>
  );
}
