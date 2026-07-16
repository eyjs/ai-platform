'use client';

import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import type { FieldIssue, ProfileConfig } from '@/types/profile';

interface PreviewPanelProps {
  /** 파싱된 설정. YAML 이 깨져 있으면 null. */
  config: ProfileConfig | null;
  issues: FieldIssue[];
}

function IssueList({ issues, variant }: { issues: FieldIssue[]; variant: 'error' | 'warning' }) {
  const isError = variant === 'error';
  return (
    <div className="flex flex-col gap-2">
      <h3
        className={
          isError
            ? 'text-[var(--font-size-sm)] font-semibold text-[var(--color-error)]'
            : 'text-[var(--font-size-sm)] font-semibold text-[var(--color-warning)]'
        }
      >
        {isError ? '유효성 오류' : '경고'} ({issues.length})
      </h3>
      {issues.map((issue, index) => (
        <div
          key={`${issue.path}-${index}`}
          className={
            isError
              ? 'rounded-[var(--radius-sm)] border border-red-200 bg-[var(--color-error-light)] px-3 py-2 text-[var(--font-size-xs)] text-[var(--color-error)]'
              : 'rounded-[var(--radius-sm)] border border-amber-200 bg-[var(--color-warning-light)] px-3 py-2 text-[var(--font-size-xs)] text-[var(--color-warning)]'
          }
        >
          <span className="font-[family-name:var(--font-mono)]">
            {issue.field ?? issue.path ?? '(전체)'}
          </span>
          : {issue.message}
        </div>
      ))}
    </div>
  );
}

/** 저장 전 설정 요약. 값은 파싱된 config 에서만 읽는다 (텍스트를 다시 파싱하지 않는다). */
export function PreviewPanel({ config, issues }: PreviewPanelProps) {
  const errors = issues.filter((issue) => issue.severity === 'error');
  const warnings = issues.filter((issue) => issue.severity === 'warning');

  if (!config) {
    return (
      <div className="flex items-center justify-center p-8 text-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
        YAML 을 파싱할 수 없어 미리보기를 표시할 수 없습니다
      </div>
    );
  }

  const toolNames = (config.tools ?? []).map((tool) =>
    typeof tool === 'string' ? tool : tool.name,
  );

  return (
    <div className="flex flex-col gap-3 overflow-y-auto p-4">
      {errors.length > 0 && <IssueList issues={errors} variant="error" />}

      <Card variant="section" className="p-3">
        <h4 className="mb-2 text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)]">
          기본 정보
        </h4>
        <p className="text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]">
          {config.name || '(이름 없음)'}
        </p>
        <p className="font-[family-name:var(--font-mono)] text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
          {config.id || '(ID 없음)'}
        </p>
        {config.description && (
          <p className="mt-1 text-[var(--font-size-sm)] text-[var(--color-neutral-600)]">
            {config.description}
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          {config.mode && <Badge variant="primary">{config.mode}</Badge>}
          {config.security_level_max && <Badge variant="neutral">{config.security_level_max}</Badge>}
          {config.response_policy && (
            <Badge variant={config.response_policy === 'strict' ? 'warning' : 'success'}>
              {config.response_policy}
            </Badge>
          )}
        </div>
      </Card>

      <Card variant="section" className="p-3">
        <h4 className="mb-2 text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)]">
          모델
        </h4>
        <div className="flex flex-col gap-1 text-[var(--font-size-sm)]">
          <span>
            <span className="text-[var(--color-neutral-500)]">main:</span>{' '}
            <span className="font-medium">{config.main_model || '(미설정)'}</span>
          </span>
        </div>
      </Card>

      {toolNames.length > 0 && (
        <Card variant="section" className="p-3">
          <h4 className="mb-2 text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)]">
            도구 ({toolNames.length})
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {toolNames.map((name, index) => (
              <Badge key={`${name}-${index}`} variant="neutral">
                {name || '(이름 없음)'}
              </Badge>
            ))}
          </div>
        </Card>
      )}

      {(config.guardrails?.length ?? 0) > 0 && (
        <Card variant="section" className="p-3">
          <h4 className="mb-2 text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)]">
            가드레일
          </h4>
          <div className="flex flex-wrap gap-1.5">
            {config.guardrails?.map((name) => (
              <Badge key={name} variant="success">
                {name}
              </Badge>
            ))}
          </div>
        </Card>
      )}

      {warnings.length > 0 && <IssueList issues={warnings} variant="warning" />}
    </div>
  );
}
