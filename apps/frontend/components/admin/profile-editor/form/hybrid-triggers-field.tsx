'use client';

import { Input } from '@/components/ui/input';
import { cn } from '@/lib/cn';
import type { HybridTrigger } from '@/types/profile';
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

function readTriggers(value: unknown): HybridTrigger[] {
  if (!Array.isArray(value)) return [];
  return value.map((item): HybridTrigger => {
    if (!isRecord(item)) {
      return { keyword_patterns: [], intent_types: [], workflow_id: '' };
    }
    return {
      keyword_patterns: toStringArray(item.keyword_patterns),
      intent_types: toStringArray(item.intent_types),
      workflow_id: typeof item.workflow_id === 'string' ? item.workflow_id : '',
      description: typeof item.description === 'string' ? item.description : undefined,
    };
  });
}

const EMPTY_TRIGGER: HybridTrigger = {
  keyword_patterns: [],
  intent_types: [],
  workflow_id: '',
};

/** mode=hybrid 에서 워크플로우로 분기시키는 트리거 편집기. */
export function HybridTriggersField() {
  const field = useField('hybrid_triggers');
  const triggers = readTriggers(field.value);

  const write = (next: HybridTrigger[]) => field.setValue(next);
  const update = (index: number, patch: Partial<HybridTrigger>) =>
    write(triggers.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  const labelClass = 'text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-600)]';

  return (
    <FieldShell fieldKey="hybrid_triggers" label="하이브리드 트리거">
      <RepeatableList
        isEmpty={triggers.length === 0}
        emptyText="트리거가 없습니다"
        addLabel="트리거 추가"
        onAdd={() => write([...triggers, { ...EMPTY_TRIGGER }])}
      >
        <div className="flex flex-col gap-2">
          {triggers.map((trigger, index) => {
            const base = `${field.controlId}-${index}`;
            return (
              <RepeatableRow
                key={index}
                title={`트리거 ${index + 1}`}
                removeLabel={`트리거 ${index + 1} 삭제`}
                onRemove={() => write(triggers.filter((_, i) => i !== index))}
              >
                <div className="flex flex-col gap-1">
                  <label htmlFor={`${base}-keywords`} className={labelClass}>
                    keyword_patterns (쉼표로 구분)
                  </label>
                  <CommaListInput
                    id={`${base}-keywords`}
                    value={trigger.keyword_patterns}
                    placeholder="사주, 운세"
                    onChange={(next) => update(index, { keyword_patterns: next })}
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label htmlFor={`${base}-intents`} className={labelClass}>
                    intent_types (쉼표로 구분)
                  </label>
                  <CommaListInput
                    id={`${base}-intents`}
                    value={trigger.intent_types}
                    placeholder="fortune_request"
                    onChange={(next) => update(index, { intent_types: next })}
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label htmlFor={`${base}-workflow`} className={labelClass}>
                    workflow_id
                  </label>
                  <Input
                    id={`${base}-workflow`}
                    size="sm"
                    value={trigger.workflow_id}
                    aria-invalid={field.hasError || undefined}
                    className={cn(
                      'font-[family-name:var(--font-mono)]',
                      errorBorderClass(field.hasError),
                    )}
                    onChange={(event) => update(index, { workflow_id: event.target.value })}
                  />
                </div>

                <div className="flex flex-col gap-1">
                  <label htmlFor={`${base}-description`} className={labelClass}>
                    설명 (선택)
                  </label>
                  <Input
                    id={`${base}-description`}
                    size="sm"
                    value={trigger.description ?? ''}
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
