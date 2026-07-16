'use client';

import { cn } from '@/lib/cn';
import type { ProfileField } from '@/types/profile';
import { FieldShell } from './field-shell';
import { useField } from './use-field';

interface MultiEnumFieldProps {
  fieldKey: ProfileField;
  label: string;
  /** 선택지별 부가 설명 (선택). 스키마에 없는 값은 그리지 않는다. */
  optionHints?: Record<string, string>;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string');
}

/**
 * 스키마 items.enum 기반 체크박스 그룹.
 * 선택지 목록은 전적으로 스키마에서 온다.
 */
export function MultiEnumField({ fieldKey, label, optionHints }: MultiEnumFieldProps) {
  const field = useField(fieldKey);
  const values = field.meta?.itemEnumValues ?? null;
  const selected = toStringArray(field.value);

  if (!values) {
    return (
      <FieldShell fieldKey={fieldKey} label={label} showLabel={false}>
        <p className="text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
          {label}: 스키마에 선택지가 정의되어 있지 않습니다
        </p>
      </FieldShell>
    );
  }

  const handleToggle = (option: string, isChecked: boolean) => {
    // 스키마 enum 순서를 유지해 직렬화 결과가 흔들리지 않게 한다.
    const next = values.filter((value) =>
      value === option ? isChecked : selected.includes(value),
    );
    field.setValue(next);
  };

  return (
    <FieldShell fieldKey={fieldKey} label={label}>
      <div
        role="group"
        aria-labelledby={undefined}
        aria-describedby={field.describedBy}
        className="flex flex-col gap-2"
      >
        {values.map((option) => {
          const optionId = `${field.controlId}-${option}`;
          const isChecked = selected.includes(option);
          return (
            <label
              key={option}
              htmlFor={optionId}
              className="flex cursor-pointer items-start gap-2 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]"
            >
              <input
                id={optionId}
                type="checkbox"
                checked={isChecked}
                aria-invalid={field.hasError || undefined}
                onChange={(event) => handleToggle(option, event.target.checked)}
                className={cn(
                  'mt-0.5 h-4 w-4 shrink-0 rounded-[var(--radius-sm)] border border-[var(--color-neutral-300)]',
                  'accent-[var(--color-primary-500)]',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary-200)]',
                )}
              />
              <span className="flex flex-col">
                <span className="font-[family-name:var(--font-mono)]">{option}</span>
                {optionHints?.[option] && (
                  <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                    {optionHints[option]}
                  </span>
                )}
              </span>
            </label>
          );
        })}
      </div>
    </FieldShell>
  );
}
