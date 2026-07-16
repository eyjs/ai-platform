'use client';

import type { ReactNode } from 'react';
import { Input } from '@/components/ui/input';
import { TextArea } from '@/components/ui/textarea';
import { cn } from '@/lib/cn';
import type { ProfileField } from '@/types/profile';
import { FieldShell } from './field-shell';
import { errorBorderClass, useField } from './use-field';

interface TextFieldProps {
  fieldKey: ProfileField;
  label: string;
  placeholder?: string;
  disabled?: boolean;
  badge?: ReactNode;
  hint?: string;
  /**
   * 빈 문자열을 undefined(키 제거)로 볼지 여부.
   * 필수 필드는 false 로 두어 빈 값이 스키마 오류로 드러나게 한다.
   */
  omitWhenEmpty?: boolean;
}

function toText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

export function TextField({
  fieldKey,
  label,
  placeholder,
  disabled = false,
  badge,
  hint,
  omitWhenEmpty = true,
}: TextFieldProps) {
  const field = useField(fieldKey);

  return (
    <FieldShell fieldKey={fieldKey} label={label} badge={badge} hint={hint}>
      <Input
        id={field.controlId}
        value={toText(field.value)}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={field.hasError || undefined}
        aria-describedby={field.describedBy}
        className={errorBorderClass(field.hasError)}
        onChange={(event) => {
          const next = event.target.value;
          field.setValue(next === '' && omitWhenEmpty ? undefined : next);
        }}
      />
    </FieldShell>
  );
}

interface TextAreaFieldProps extends Omit<TextFieldProps, 'omitWhenEmpty'> {
  rows?: number;
  isMonospace?: boolean;
  omitWhenEmpty?: boolean;
}

export function TextAreaField({
  fieldKey,
  label,
  placeholder,
  disabled = false,
  badge,
  hint,
  rows = 6,
  isMonospace = false,
  omitWhenEmpty = true,
}: TextAreaFieldProps) {
  const field = useField(fieldKey);

  return (
    <FieldShell fieldKey={fieldKey} label={label} badge={badge} hint={hint}>
      <TextArea
        id={field.controlId}
        rows={rows}
        value={toText(field.value)}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={field.hasError || undefined}
        aria-describedby={field.describedBy}
        className={cn(
          'resize-y',
          isMonospace && 'font-[family-name:var(--font-mono)]',
          errorBorderClass(field.hasError),
        )}
        onChange={(event) => {
          const next = event.target.value;
          field.setValue(next === '' && omitWhenEmpty ? undefined : next);
        }}
      />
    </FieldShell>
  );
}
