'use client';

import { useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import type { ValidationIssue } from '@/hooks/use-yaml-validation';

interface PreviewPanelProps {
  yamlContent: string;
  issues: ValidationIssue[];
}

export function PreviewPanel({ yamlContent, issues }: PreviewPanelProps) {
  const parsed = useMemo(() => {
    try {
      // 간단한 YAML -> 객체 파싱 (라인 기반)
      const obj: Record<string, string> = {};
      for (const line of yamlContent.split('\n')) {
        const match = line.match(/^(\w[\w_]*)\s*:\s*(.+)$/);
        if (match) obj[match[1]] = match[2].trim();
      }
      return obj;
    } catch {
      return null;
    }
  }, [yamlContent]);

  const errors = issues.filter((i) => i.severity === 'error');
  const warnings = issues.filter((i) => i.severity === 'warning');

  if (errors.length > 0) {
    return (
      <div className="flex flex-col gap-2 p-4">
        <h3 className="text-[var(--font-size-sm)] font-semibold text-[var(--color-error)]">
          유효성 오류 ({errors.length})
        </h3>
        {errors.map((issue, i) => (
          <div
            key={i}
            className="rounded-[var(--radius-sm)] bg-[var(--color-error-light)] border border-red-200 px-3 py-2 text-[var(--font-size-xs)] text-[var(--color-error)]"
          >
            <span className="font-mono">L{issue.line}</span>: {issue.message}
          </div>
        ))}
        {warnings.length > 0 && (
          <>
            <h3 className="mt-2 text-[var(--font-size-sm)] font-semibold text-[var(--color-warning)]">
              경고 ({warnings.length})
            </h3>
            {warnings.map((issue, i) => (
              <div
                key={i}
                className="rounded-[var(--radius-sm)] bg-[var(--color-warning-light)] border border-amber-200 px-3 py-2 text-[var(--font-size-xs)] text-[var(--color-warning)]"
              >
                {issue.message}
              </div>
            ))}
          </>
        )}
      </div>
    );
  }

  if (!parsed) {
    return (
      <div className="flex items-center justify-center p-8 text-[var(--color-neutral-400)]">
        YAML을 입력하면 미리보기가 표시됩니다
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 overflow-y-auto p-4">
      {/* 기본 정보 */}
      <Card variant="section" className="p-3">
        <h4 className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)] mb-2">
          기본 정보
        </h4>
        <p className="text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]">
          {parsed.name || '(이름 없음)'}
        </p>
        <p className="font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
          {parsed.id || '(ID 없음)'}
        </p>
        {parsed.description && (
          <p className="mt-1 text-[var(--font-size-sm)] text-[var(--color-neutral-600)]">
            {parsed.description}
          </p>
        )}
        <div className="mt-2 flex gap-2">
          {parsed.mode && (
            <Badge variant="primary">{parsed.mode}</Badge>
          )}
          {parsed.security_level_max && (
            <Badge variant="neutral">{parsed.security_level_max}</Badge>
          )}
        </div>
      </Card>

      {/* LLM 설정 */}
      {(parsed.router_model || parsed.main_model) && (
        <Card variant="section" className="p-3">
          <h4 className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)] mb-2">
            LLM 설정
          </h4>
          <div className="flex gap-4 text-[var(--font-size-sm)]">
            {parsed.router_model && (
              <span>
                <span className="text-[var(--color-neutral-500)]">Router:</span>{' '}
                <span className="font-medium">{parsed.router_model}</span>
              </span>
            )}
            {parsed.main_model && (
              <span>
                <span className="text-[var(--color-neutral-500)]">Main:</span>{' '}
                <span className="font-medium">{parsed.main_model}</span>
              </span>
            )}
          </div>
        </Card>
      )}

      {/* 응답 정책 */}
      {parsed.response_policy && (
        <Card variant="section" className="p-3">
          <h4 className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)] mb-2">
            응답 정책
          </h4>
          <Badge variant={parsed.response_policy === 'strict' ? 'warning' : 'success'}>
            {parsed.response_policy}
          </Badge>
        </Card>
      )}

      {/* 경고 */}
      {warnings.length > 0 && (
        <Card variant="section" className="border-[var(--color-warning)] p-3">
          <h4 className="text-[var(--font-size-xs)] font-medium text-[var(--color-warning)] mb-2">
            경고 ({warnings.length})
          </h4>
          {warnings.map((w, i) => (
            <p key={i} className="text-[var(--font-size-xs)] text-[var(--color-warning)]">
              {w.message}
            </p>
          ))}
        </Card>
      )}
    </div>
  );
}
