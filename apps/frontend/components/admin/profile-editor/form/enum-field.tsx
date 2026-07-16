'use client';

import { Dropdown } from '@/components/ui/dropdown';
import { cn } from '@/lib/cn';
import type { ProfileField } from '@/types/profile';
import { FieldShell } from './field-shell';
import { errorBorderClass, useField } from './use-field';

interface EnumFieldProps {
  fieldKey: ProfileField;
  label: string;
}

/**
 * 스키마 enum 기반 드롭다운.
 *
 * 스키마에 enum 이 없으면 선택지를 지어내지 않는다 — 비활성 상태로 그 사실을 드러낸다.
 */
export function EnumField({ fieldKey, label }: EnumFieldProps) {
  const field = useField(fieldKey);
  const values = field.meta?.enumValues ?? null;

  if (!values) {
    return (
      <FieldShell fieldKey={fieldKey} label={label}>
        <Dropdown
          options={[]}
          onChange={() => undefined}
          disabled
          placeholder="스키마에 선택지가 정의되어 있지 않습니다"
        />
      </FieldShell>
    );
  }

  const options = values.map((value) => ({ value, label: value }));
  const current = typeof field.value === 'string' ? field.value : undefined;

  return (
    <FieldShell fieldKey={fieldKey} label={label}>
      <div id={field.controlId} aria-describedby={field.describedBy}>
        <Dropdown
          options={options}
          value={current}
          onChange={(next) => field.setValue(next)}
          className={cn(errorBorderClass(field.hasError) && '[&>button]:border-[var(--color-error)]')}
        />
      </div>
    </FieldShell>
  );
}
