'use client';

import { useEffect, useState } from 'react';
import { Input } from '@/components/ui/input';
import { TextArea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/cn';
import type { ToolEntry } from '@/types/profile';
import { FieldShell } from './field-shell';
import { RepeatableList, RepeatableRow } from './repeatable-list';
import { errorBorderClass, useField } from './use-field';

/**
 * tools 항목은 {name, config} 객체다. 예전 프로필에는 문자열로 저장된 것이 있을 수 있어
 * 화면에서는 이름으로 보여주되, 사용자가 직접 편집하기 전까지 원본을 바꾸지 않는다
 * (스키마 위반은 ajv 가 오류로 드러낸다).
 */
interface ToolRowValue {
  name: string;
  config?: Record<string, unknown>;
  isLegacyString: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readTools(value: unknown): ToolRowValue[] {
  if (!Array.isArray(value)) return [];
  return value.map((item): ToolRowValue => {
    if (typeof item === 'string') return { name: item, isLegacyString: true };
    if (isRecord(item)) {
      return {
        name: typeof item.name === 'string' ? item.name : '',
        config: isRecord(item.config) ? item.config : undefined,
        isLegacyString: false,
      };
    }
    return { name: '', isLegacyString: false };
  });
}

function toEntry(row: ToolRowValue): ToolEntry {
  return row.config === undefined ? { name: row.name } : { name: row.name, config: row.config };
}

interface ToolRowProps {
  index: number;
  row: ToolRowValue;
  controlId: string;
  hasError: boolean;
  onChange: (next: ToolRowValue) => void;
  onRemove: () => void;
}

function ToolRow({ index, row, controlId, hasError, onChange, onRemove }: ToolRowProps) {
  const [configText, setConfigText] = useState(() =>
    row.config === undefined ? '' : JSON.stringify(row.config, null, 2),
  );
  const [configError, setConfigError] = useState<string | null>(null);

  useEffect(() => {
    setConfigText((current) => {
      try {
        const parsed: unknown = current.trim() === '' ? undefined : JSON.parse(current);
        if (JSON.stringify(parsed) === JSON.stringify(row.config)) return current;
      } catch {
        // 로컬 텍스트가 깨져 있으면 외부 값으로 되돌린다.
      }
      return row.config === undefined ? '' : JSON.stringify(row.config, null, 2);
    });
  }, [row.config]);

  const handleConfigChange = (next: string) => {
    setConfigText(next);
    if (next.trim() === '') {
      setConfigError(null);
      onChange({ ...row, config: undefined, isLegacyString: false });
      return;
    }
    try {
      const parsed: unknown = JSON.parse(next);
      if (!isRecord(parsed)) {
        setConfigError('JSON 객체여야 합니다');
        return;
      }
      setConfigError(null);
      onChange({ ...row, config: parsed, isLegacyString: false });
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : 'JSON 파싱 오류');
    }
  };

  const nameId = `${controlId}-${index}-name`;
  const configId = `${controlId}-${index}-config`;

  return (
    <RepeatableRow title={`도구 ${index + 1}`} removeLabel={`도구 ${index + 1} 삭제`} onRemove={onRemove}>
      <div className="flex flex-col gap-1">
        <label
          htmlFor={nameId}
          className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-600)]"
        >
          이름
        </label>
        <div className="flex items-center gap-2">
          <Input
            id={nameId}
            size="sm"
            value={row.name}
            placeholder="rag_search"
            aria-invalid={hasError || undefined}
            className={cn('flex-1 font-[family-name:var(--font-mono)]', errorBorderClass(hasError))}
            onChange={(event) =>
              onChange({ ...row, name: event.target.value, isLegacyString: false })
            }
          />
          {row.isLegacyString && (
            <Badge variant="warning" size="sm">
              문자열 형식 (수정 시 객체로 변환)
            </Badge>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label
          htmlFor={configId}
          className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-600)]"
        >
          config (선택, JSON)
        </label>
        <TextArea
          id={configId}
          rows={3}
          value={configText}
          placeholder="{}"
          aria-invalid={configError !== null || undefined}
          className={cn(
            'resize-y font-[family-name:var(--font-mono)]',
            errorBorderClass(configError !== null),
          )}
          onChange={(event) => handleConfigChange(event.target.value)}
        />
        {configError && (
          <p className="text-[var(--font-size-xs)] text-[var(--color-error)]">오류: {configError}</p>
        )}
      </div>
    </RepeatableRow>
  );
}

export function ToolsField() {
  const field = useField('tools');
  const rows = readTools(field.value);

  const writeRows = (next: ToolRowValue[]) => {
    field.setValue(next.map(toEntry));
  };

  return (
    <FieldShell fieldKey="tools" label="도구">
      <RepeatableList
        isEmpty={rows.length === 0}
        emptyText="등록된 도구가 없습니다"
        addLabel="도구 추가"
        onAdd={() => writeRows([...rows, { name: '', isLegacyString: false }])}
      >
        <div className="flex flex-col gap-2">
          {rows.map((row, index) => (
            <ToolRow
              key={index}
              index={index}
              row={row}
              controlId={field.controlId}
              hasError={field.hasError}
              onChange={(next) => writeRows(rows.map((item, i) => (i === index ? next : item)))}
              onRemove={() => writeRows(rows.filter((_, i) => i !== index))}
            />
          ))}
        </div>
      </RepeatableList>
    </FieldShell>
  );
}
