'use client';

import { useState, type ReactNode } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/cn';
import type { ProfileField } from '@/types/profile';
import { FieldShell } from './field-shell';
import { errorBorderClass, useField } from './use-field';

interface StringListFieldProps {
  fieldKey: ProfileField;
  label: string;
  placeholder?: string;
  disabled?: boolean;
  badge?: ReactNode;
  hint?: string;
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string');
}

/** 자유 문자열 배열(도메인 범위 등)을 칩으로 편집한다. */
export function StringListField({
  fieldKey,
  label,
  placeholder = '입력 후 Enter',
  disabled = false,
  badge,
  hint,
}: StringListFieldProps) {
  const field = useField(fieldKey);
  const [draft, setDraft] = useState('');
  const items = toStringArray(field.value);

  const handleAdd = () => {
    const trimmed = draft.trim();
    if (!trimmed || items.includes(trimmed)) {
      setDraft('');
      return;
    }
    field.setValue([...items, trimmed]);
    setDraft('');
  };

  const handleRemove = (target: string) => {
    field.setValue(items.filter((item) => item !== target));
  };

  return (
    <FieldShell fieldKey={fieldKey} label={label} badge={badge} hint={hint}>
      {!disabled && (
        <div className="flex items-end gap-2">
          <Input
            id={field.controlId}
            value={draft}
            placeholder={placeholder}
            aria-invalid={field.hasError || undefined}
            aria-describedby={field.describedBy}
            className={cn('flex-1', errorBorderClass(field.hasError))}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                handleAdd();
              }
            }}
          />
          <Button type="button" variant="secondary" size="sm" onClick={handleAdd}>
            추가
          </Button>
        </div>
      )}

      {items.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5">
          {items.map((item) => (
            <li
              key={item}
              className={cn(
                'flex items-center gap-1 rounded-[var(--radius-sm)] border border-[var(--color-neutral-200)]',
                'bg-[var(--color-neutral-100)] py-0.5 pl-2 text-[var(--font-size-xs)] text-[var(--color-neutral-700)]',
                disabled ? 'pr-2 opacity-60' : 'pr-1',
              )}
            >
              <span className="font-[family-name:var(--font-mono)]">{item}</span>
              {!disabled && (
                <button
                  type="button"
                  aria-label={`${item} 제거`}
                  onClick={() => handleRemove(item)}
                  className={cn(
                    'flex h-4 w-4 items-center justify-center rounded-[var(--radius-sm)]',
                    'text-[var(--color-neutral-400)] hover:text-[var(--color-error)]',
                    'focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary-200)]',
                  )}
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
          {disabled ? '설정된 값 없음' : '비어 있음'}
        </p>
      )}
    </FieldShell>
  );
}
