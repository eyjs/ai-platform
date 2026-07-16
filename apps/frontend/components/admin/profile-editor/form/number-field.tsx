'use client';

import { Input } from '@/components/ui/input';
import type { ProfileField } from '@/types/profile';
import { FieldShell } from './field-shell';
import { errorBorderClass, useField } from './use-field';

interface NumberFieldProps {
  fieldKey: ProfileField;
  label: string;
  /** "초", "일" 같은 단위. 도움말과 입력창 우측에 표시된다. */
  unit?: string;
  placeholder?: string;
  isInteger?: boolean;
  step?: number;
}

function toText(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
}

/**
 * 숫자 입력. min/max 는 스키마에서 가져온다 (여기에 상수를 두지 않는다).
 * 빈 칸은 undefined = 미설정으로 저장되어 런타임 기본값이 적용된다.
 */
export function NumberField({
  fieldKey,
  label,
  unit,
  placeholder,
  isInteger = true,
  step,
}: NumberFieldProps) {
  const field = useField(fieldKey);
  const { meta } = field;

  return (
    <FieldShell fieldKey={fieldKey} label={label} hint={unit ? `단위: ${unit}` : undefined}>
      <Input
        id={field.controlId}
        type="number"
        inputMode={isInteger ? 'numeric' : 'decimal'}
        value={toText(field.value)}
        placeholder={placeholder ?? '미설정'}
        min={meta?.minimum ?? undefined}
        max={meta?.maximum ?? undefined}
        step={step ?? (isInteger ? 1 : 'any')}
        aria-invalid={field.hasError || undefined}
        aria-describedby={field.describedBy}
        className={errorBorderClass(field.hasError)}
        rightIcon={
          unit ? (
            <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
              {unit}
            </span>
          ) : undefined
        }
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === '') {
            field.setValue(undefined);
            return;
          }
          const parsed = isInteger ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
          // 파싱 실패값을 NaN 으로 넣으면 YAML 이 오염된다. 입력을 무시한다.
          field.setValue(Number.isNaN(parsed) ? undefined : parsed);
        }}
      />
    </FieldShell>
  );
}
