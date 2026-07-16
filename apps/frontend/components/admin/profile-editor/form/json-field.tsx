'use client';

import { useEffect, useState } from 'react';
import { TextArea } from '@/components/ui/textarea';
import { cn } from '@/lib/cn';
import type { ProfileField } from '@/types/profile';
import { FieldShell } from './field-shell';
import { errorBorderClass, useField } from './use-field';

interface JsonFieldProps {
  fieldKey: ProfileField;
  label: string;
  rows?: number;
}

function format(value: unknown): string {
  if (value === undefined) return '';
  return JSON.stringify(value, null, 2);
}

/**
 * 자유 형태 객체(cache, workflow_action_headers)를 JSON 으로 편집한다.
 *
 * 입력 도중에는 JSON 이 깨져 있는 게 정상이므로 텍스트는 로컬 상태로 들고 있다가
 * 파싱에 성공한 순간에만 폼 상태로 올린다. 깨진 JSON 은 이 필드에서 직접 알린다
 * (스키마 검증까지 갈 수 없는 입력이라 ajv 가 볼 수 없다).
 */
export function JsonField({ fieldKey, label, rows = 5 }: JsonFieldProps) {
  const field = useField(fieldKey);
  const [text, setText] = useState(() => format(field.value));
  const [parseError, setParseError] = useState<string | null>(null);

  // YAML 탭 등 외부에서 값이 바뀐 경우에만 텍스트를 되돌린다.
  useEffect(() => {
    setText((current) => {
      try {
        const parsed: unknown = current.trim() === '' ? undefined : JSON.parse(current);
        if (JSON.stringify(parsed) === JSON.stringify(field.value)) return current;
      } catch {
        // 로컬 텍스트가 깨져 있으면 외부 값으로 덮어쓴다.
      }
      return format(field.value);
    });
  }, [field.value]);

  const handleChange = (next: string) => {
    setText(next);

    if (next.trim() === '') {
      setParseError(null);
      field.setValue(undefined);
      return;
    }

    try {
      const parsed: unknown = JSON.parse(next);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setParseError('JSON 객체여야 합니다');
        return;
      }
      setParseError(null);
      field.setValue(parsed);
    } catch (err) {
      setParseError(err instanceof Error ? err.message : 'JSON 파싱 오류');
    }
  };

  const hasError = field.hasError || parseError !== null;

  return (
    <FieldShell fieldKey={fieldKey} label={label} hint="JSON 형식">
      <TextArea
        id={field.controlId}
        rows={rows}
        value={text}
        placeholder="{}"
        aria-invalid={hasError || undefined}
        aria-describedby={field.describedBy}
        className={cn('resize-y font-[family-name:var(--font-mono)]', errorBorderClass(hasError))}
        onChange={(event) => handleChange(event.target.value)}
      />
      {parseError && (
        <p className="text-[var(--font-size-xs)] text-[var(--color-error)]">오류: {parseError}</p>
      )}
    </FieldShell>
  );
}
