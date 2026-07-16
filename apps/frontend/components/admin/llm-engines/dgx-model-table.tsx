'use client';

import { DataTable, type Column } from '@/components/ui/data-table';
import { Badge } from '@/components/ui/badge';
import type { DgxModel, DgxStatus } from '@/types/llm-engines';
import { CapabilityBadges } from './capability-badges';
import { formatContextLength, hasToolsCapability } from './llm-engine-format';

const columns: Column<DgxModel>[] = [
  {
    key: 'name',
    header: '모델명',
    render: (model) => (
      <span className="font-[family-name:var(--font-mono)] text-[var(--font-size-xs)] text-[var(--color-neutral-900)]">
        {model.name}
      </span>
    ),
  },
  {
    key: 'parameterSize',
    header: '파라미터',
    render: (model) => (
      <span className="text-[var(--color-neutral-700)]">{model.parameterSize ?? '-'}</span>
    ),
  },
  {
    key: 'contextLength',
    header: '컨텍스트',
    render: (model) => (
      <span
        className="text-[var(--color-neutral-700)]"
        title={model.contextLength !== null ? `${model.contextLength} 토큰` : undefined}
      >
        {formatContextLength(model.contextLength)}
      </span>
    ),
  },
  {
    key: 'capabilities',
    header: '기능',
    render: (model) => <CapabilityBadges capabilities={model.capabilities} />,
  },
  {
    key: 'isDefault',
    header: '기본',
    render: (model) =>
      model.isDefault ? (
        <Badge variant="primary" size="sm">
          기본
        </Badge>
      ) : (
        <span className="text-[var(--color-neutral-400)]">-</span>
      ),
  },
];

export interface DgxModelTableProps {
  dgx: DgxStatus;
}

/**
 * DGX가 서빙 중인 모델 목록.
 * 미설정/조회 실패 시에는 절대 목록을 지어내지 않고 사유를 그대로 보여준다.
 */
export function DgxModelTable({ dgx }: DgxModelTableProps) {
  if (!dgx.configured) {
    return (
      <p className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--color-neutral-50)] px-4 py-8 text-center text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
        DGX가 구성되지 않았습니다. 조회할 모델이 없습니다.
      </p>
    );
  }

  if (dgx.modelsError) {
    return (
      <div
        role="alert"
        className="rounded-[var(--radius-md)] border border-[var(--color-error)] bg-[var(--color-error-light)] px-4 py-3"
      >
        <p className="text-[var(--font-size-sm)] font-medium text-[var(--color-error)]">
          모델 목록을 불러오지 못했습니다
        </p>
        <p className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-700)]">
          {dgx.modelsError}
        </p>
      </div>
    );
  }

  const hasToolsGap = dgx.models.some((model) => !hasToolsCapability(model.capabilities));

  return (
    <div className="space-y-3">
      <DataTable
        columns={columns}
        data={dgx.models}
        emptyMessage="DGX가 보고한 모델이 없습니다"
      />
      {hasToolsGap && (
        <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
          ⚠ <span className="font-medium">tools 없음</span> 표시가 붙은 모델은 도구 호출을
          지원하지 않습니다. agentic 프로필에 지정하면 bind_tools 단계에서 실패합니다.
        </p>
      )}
    </div>
  );
}
