'use client';

import { useState } from 'react';
import { cn } from '@/lib/cn';
import { Badge } from '@/components/ui/badge';

const CHATBOT_SCOPES = [
  {
    id: 'flowsns-ops',
    name: 'FlowBot',
    color: 'primary' as const,
    tools: ['flowsns_tasks', 'flowsns_clients', 'flowsns_accounts', '...+7'],
    data: 'flowSNS API (외부)',
    sessions: 'flowSNS 전용 세션',
  },
  {
    id: 'fortune-saju',
    name: 'SajuBot',
    color: 'secondary' as const,
    tools: ['saju_lookup', 'saju_report_paper', 'saju_report_compat'],
    data: 'Saju Backend (외부)',
    sessions: '사주 전용 세션',
  },
  {
    id: 'insurance-qa',
    name: 'InsuranceBot',
    color: 'success' as const,
    tools: ['rag_search', 'fact_lookup'],
    data: 'document_chunks (내부 RAG)',
    sessions: '보험 전용 세션',
  },
];

const ID_CHAIN = [
  {
    key: 'chatbot_id',
    label: 'chatbot_id',
    desc: '어떤 챗봇(Profile)을 사용할지',
    example: '"flowsns-ops"',
    source: '클라이언트가 요청 시 전달',
    determines: 'Profile YAML 로딩 → 도구 목록, 프롬프트, 제한사항 결정',
  },
  {
    key: 'session_id',
    label: 'session_id',
    desc: '어떤 대화인지 (멀티턴 맥락 유지)',
    example: '"sess_abc123"',
    source: '클라이언트가 생성하여 전달',
    determines: '대화 히스토리 로딩, 컨텍스트 유지',
  },
  {
    key: 'user_id',
    label: 'user_id',
    desc: '누가 요청했는지 (인증된 사용자)',
    example: '"usr_flowsns_001"',
    source: 'JWT 토큰에서 추출 (sub claim)',
    determines: '권한 체크, 보안 레벨, 사용량 제한',
  },
  {
    key: 'api_key',
    label: 'X-API-Key',
    desc: '어떤 시스템에서 요청했는지',
    example: '"fsk_9b50a293..."',
    source: 'HTTP 헤더로 전달',
    determines: '클라이언트 시스템 인증, 접근 범위',
  },
];

export function DataIsolationView() {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  return (
    <div className="space-y-8">
      {/* Data Isolation */}
      <div>
        <h3 className="mb-1 text-[var(--font-size-lg)] font-bold text-[var(--color-neutral-900)]">
          데이터 격리
        </h3>
        <p className="mb-4 text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
          각 챗봇(Profile)은 허용된 도구만 접근할 수 있습니다. Agent는 하나지만, Profile이 도구 목록을 제한하여 완전한 격리를 보장합니다.
        </p>

        <div className="grid gap-4 md:grid-cols-3">
          {CHATBOT_SCOPES.map((scope) => (
            <div
              key={scope.id}
              className="rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-4 shadow-[var(--shadow-xs)]"
            >
              <div className="mb-3 flex items-center justify-between">
                <span className="text-[var(--font-size-sm)] font-bold text-[var(--color-neutral-800)]">
                  {scope.name}
                </span>
                <Badge variant={scope.color} size="sm">{scope.id}</Badge>
              </div>

              <div className="space-y-2">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-neutral-400)]">
                    허용 도구
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {scope.tools.map((tool) => (
                      <span
                        key={tool}
                        className="rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-1.5 py-0.5 text-[10px] font-mono text-[var(--color-neutral-600)]"
                      >
                        {tool}
                      </span>
                    ))}
                  </div>
                </div>

                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-neutral-400)]">
                    데이터 소스
                  </div>
                  <div className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
                    {scope.data}
                  </div>
                </div>

                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-neutral-400)]">
                    세션 격리
                  </div>
                  <div className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
                    {scope.sessions}
                  </div>
                </div>
              </div>

              <div className="mt-3 rounded-[var(--radius-sm)] bg-[var(--color-neutral-50)] p-2">
                <div className="flex items-center gap-1 text-[10px] text-[var(--color-neutral-500)]">
                  <svg className="h-3 w-3 text-[var(--color-success)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                  다른 Profile의 도구는 존재 자체를 모름
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Request Identification */}
      <div>
        <h3 className="mb-1 text-[var(--font-size-lg)] font-bold text-[var(--color-neutral-900)]">
          요청 식별 체계
        </h3>
        <p className="mb-4 text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
          모든 요청은 4개의 식별자로 추적됩니다. 이를 통해 어디서, 누가, 어떤 맥락으로 요청했는지 정확히 파악합니다.
        </p>

        <div className="space-y-2">
          {ID_CHAIN.map((item) => {
            const isExpanded = expandedId === item.key;
            return (
              <button
                key={item.key}
                onClick={() => setExpandedId(isExpanded ? null : item.key)}
                aria-label={`${item.label} 상세 정보`}
                className={cn(
                  'w-full rounded-[var(--radius-md)] border text-left transition-all',
                  isExpanded
                    ? 'border-[var(--color-primary-300)] bg-[var(--color-primary-50)]'
                    : 'border-[var(--color-neutral-200)] bg-[var(--surface-card)] hover:border-[var(--color-neutral-300)]',
                )}
              >
                <div className="flex items-center gap-3 p-3">
                  <code className={cn(
                    'rounded-[var(--radius-sm)] px-2 py-1 text-[var(--font-size-xs)] font-bold font-mono',
                    isExpanded
                      ? 'bg-[var(--color-primary-500)] text-white'
                      : 'bg-[var(--color-neutral-100)] text-[var(--color-neutral-700)]',
                  )}>
                    {item.label}
                  </code>
                  <span className="flex-1 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
                    {item.desc}
                  </span>
                  <svg
                    className={cn(
                      'h-4 w-4 text-[var(--color-neutral-400)] transition-transform',
                      isExpanded && 'rotate-180',
                    )}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </div>
                {isExpanded && (
                  <div className="border-t border-[var(--color-primary-200)] px-3 py-3">
                    <div className="grid gap-3 sm:grid-cols-3">
                      <div>
                        <div className="text-[10px] font-semibold uppercase text-[var(--color-neutral-400)]">
                          예시 값
                        </div>
                        <code className="mt-1 block text-[var(--font-size-xs)] font-mono text-[var(--color-primary-600)]">
                          {item.example}
                        </code>
                      </div>
                      <div>
                        <div className="text-[10px] font-semibold uppercase text-[var(--color-neutral-400)]">
                          출처
                        </div>
                        <div className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
                          {item.source}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] font-semibold uppercase text-[var(--color-neutral-400)]">
                          역할
                        </div>
                        <div className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
                          {item.determines}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
