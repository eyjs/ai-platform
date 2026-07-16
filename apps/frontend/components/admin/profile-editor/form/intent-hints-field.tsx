'use client';

import { Input } from '@/components/ui/input';
import { cn } from '@/lib/cn';
import type { IntentHint } from '@/types/profile';
import { CommaListInput } from './comma-list-input';
import { FieldShell } from './field-shell';
import { RepeatableList, RepeatableRow } from './repeatable-list';
import { errorBorderClass, useField } from './use-field';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string');
}

function readHints(value: unknown): IntentHint[] {
  if (!Array.isArray(value)) return [];
  return value.map((item): IntentHint => {
    if (!isRecord(item)) return { name: '', patterns: [] };
    return {
      name: typeof item.name === 'string' ? item.name : '',
      patterns: toStringArray(item.patterns),
      description: typeof item.description === 'string' ? item.description : undefined,
    };
  });
}

/** 커스텀 Intent 힌트 편집기. */
export function IntentHintsField() {
  const field = useField('intent_hints');
  const hints = readHints(field.value);

  const write = (next: IntentHint[]) => field.setValue(next);
  const update = (index: number, patch: Partial<IntentHint>) =>
    write(hints.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  const labelClass = 'text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-600)]';

  return (
    <FieldShell fieldKey="intent_hints" label="Intent 힌트">
      <RepeatableList
        isEmpty={hints.length === 0}
        emptyText="힌트가 없습니다"
        addLabel="힌트 추가"
        onAdd={() => write([...hints, { name: '', patterns: [] }])}
      >
        <div className="flex flex-col gap-2">
          {hints.map((hint, index) => {
            const base = `${field.controlId}-${index}`;
            return (
              <RepeatableRow
                key={index}
                title={`힌트 ${index + 1}`}
                removeLabel={`힌트 ${index + 1} 삭제`}
                onRemove={() => write(hints.filter((_, i) => i !== index))}
              >
                <div className="flex flex-col gap-1">
                  <label htmlFor={`${base}-name`} className={labelClass}>
                    name
                  </label>
                  <Input
                    id={`${base}-name`}
                    size="sm"
                    value={hint.name}
                    aria-invalid={field.hasError || undefined}
                    className={cn(
                      'font-[family-name:var(--font-mono)]',
                      errorBorderClass(field.hasError),
                    )}
                    onChange={(event) => update(index, { name: event.target.value })}
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label htmlFor={`${base}-patterns`} className={labelClass}>
                    patterns (쉼표로 구분)
                  </label>
                  <CommaListInput
                    id={`${base}-patterns`}
                    value={hint.patterns}
                    onChange={(next) => update(index, { patterns: next })}
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label htmlFor={`${base}-description`} className={labelClass}>
                    설명 (선택)
                  </label>
                  <Input
                    id={`${base}-description`}
                    size="sm"
                    value={hint.description ?? ''}
                    onChange={(event) =>
                      update(index, {
                        description: event.target.value === '' ? undefined : event.target.value,
                      })
                    }
                  />
                </div>
              </RepeatableRow>
            );
          })}
        </div>
      </RepeatableList>
    </FieldShell>
  );
}
