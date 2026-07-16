'use client';

import { Toggle } from '@/components/ui/toggle';
import type { ProfileField } from '@/types/profile';
import { FieldShell } from './field-shell';
import { useField } from './use-field';

interface BooleanFieldProps {
  fieldKey: ProfileField;
  label: string;
}

/** 기본값은 스키마 default 를 따른다 — 값이 없을 때 무엇이 참인지 여기서 정하지 않는다. */
export function BooleanField({ fieldKey, label }: BooleanFieldProps) {
  const field = useField(fieldKey);

  const fallback = typeof field.meta?.defaultValue === 'boolean' ? field.meta.defaultValue : false;
  const checked = typeof field.value === 'boolean' ? field.value : fallback;

  return (
    <FieldShell fieldKey={fieldKey} label={label} showLabel={false}>
      <Toggle
        id={field.controlId}
        checked={checked}
        label={label}
        ariaLabel={label}
        ariaDescribedBy={field.describedBy}
        onChange={(next) => field.setValue(next)}
      />
    </FieldShell>
  );
}
