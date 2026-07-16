'use client';

import { DataTable, type Column } from '@/components/ui/data-table';
import { Badge } from '@/components/ui/badge';
import type { MlxEngine, MlxStatus } from '@/types/llm-engines';
import { LinkStatusIndicator } from './link-status-indicator';

const columns: Column<MlxEngine>[] = [
  {
    key: 'roles',
    header: '역할',
    render: (engine) =>
      engine.roles.length > 0 ? (
        <span className="inline-flex flex-wrap gap-1">
          {engine.roles.map((role) => (
            <Badge key={role} size="sm" variant="secondary">
              {role}
            </Badge>
          ))}
        </span>
      ) : (
        <span className="text-[var(--color-neutral-400)]">-</span>
      ),
  },
  {
    key: 'model',
    header: '모델',
    render: (engine) =>
      engine.modelError ? (
        <span className="text-[var(--font-size-xs)] text-[var(--color-error)]">
          {engine.modelError}
        </span>
      ) : (
        <span className="font-[family-name:var(--font-mono)] text-[var(--font-size-xs)] text-[var(--color-neutral-900)]">
          {engine.model ?? '-'}
        </span>
      ),
  },
  {
    key: 'url',
    header: 'URL',
    render: (engine) => (
      <span className="font-[family-name:var(--font-mono)] text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
        {engine.url}
      </span>
    ),
  },
  {
    key: 'link',
    header: '상태',
    render: (engine) => <LinkStatusIndicator link={engine.link} />,
  },
];

export interface MlxEngineTableProps {
  mlx: MlxStatus;
}

/** 호스트 MLX 엔진 목록. development 모드에서 DGX 폴백 대상이 되는 엔진들이다. */
export function MlxEngineTable({ mlx }: MlxEngineTableProps) {
  return (
    <DataTable
      columns={columns}
      data={mlx.engines}
      emptyMessage="구성된 MLX 엔진이 없습니다"
    />
  );
}
