'use client';

import { useState } from 'react';
import { cn } from '@/lib/cn';
import { Badge } from '@/components/ui/badge';

const INTEGRATION_STEPS = [
  {
    step: 1,
    title: 'API Key 발급',
    desc: 'AI Platform에서 클라이언트용 API Key를 발급받습니다.',
    code: `# 환경변수 설정 (docker-compose.yml)
AIP_FLOWSNS_API_KEY: fsk_9b50a293...
AIP_FLOWSNS_API_URL: http://host.docker.internal:3021`,
  },
  {
    step: 2,
    title: 'Profile YAML 작성',
    desc: '챗봇의 행동을 정의하는 Profile YAML을 작성합니다. 코드 변경 없이 이 파일만으로 새 챗봇이 생성됩니다.',
    code: `# seeds/profiles/flowsns-ops.yaml
id: flowsns-ops
name: FlowBot
description: flowSNS 마케팅 운영 챗봇
mode: agentic
system_prompt: |
  당신은 flowSNS 마케팅 운영 도우미입니다.
  사용자의 업무를 도와주세요.
tools:
  - flowsns_tasks
  - flowsns_clients
  - flowsns_accounts
  # ... 허용할 도구만 나열
constraints:
  max_tool_calls: 15
  timeout_seconds: 120`,
  },
  {
    step: 3,
    title: 'Tool 개발 (선택)',
    desc: '외부 시스템 API를 호출하는 Tool을 개발합니다. Tool은 scope/context만 받아 동작하며, 어떤 봇에서 호출됐는지 모릅니다.',
    code: `# src/tools/internal/flowsns_tasks.py
class FlowSNSTasksTool:
    name = "flowsns_tasks"
    description = "flowSNS 태스크 조회"

    async def execute(self, params, context):
        # Tool은 봇을 모름 — scope/context만 사용
        response = await self.client.get(
            "/api/tasks",
            params=params
        )
        return response.json()`,
  },
  {
    step: 4,
    title: '클라이언트에서 API 호출',
    desc: '외부 시스템에서 AI Platform의 채팅 API를 호출합니다.',
    code: `// flowSNS 앱에서 호출
const response = await fetch(
  "https://ai-platform.example/api/chat/stream",
  {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": "fsk_9b50a293...",
    },
    body: JSON.stringify({
      question: "이번 주 마감 임박 태스크 알려줘",
      chatbot_id: "flowsns-ops",
      session_id: "sess_abc123",
    }),
  }
);
// SSE 스트리밍으로 응답 수신`,
  },
];

const DESIGN_PRINCIPLES = [
  {
    title: 'Agent는 하나',
    desc: 'Universal Agent Runtime이 모든 요청을 처리. 새 챗봇 = Profile YAML 추가, 코드 변경 0줄.',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
      </svg>
    ),
  },
  {
    title: 'Tool이 보안의 핵심',
    desc: 'Profile의 tools 리스트에 없는 도구는 LLM이 존재를 모름. Tool 내부에서 봇 식별 금지.',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    ),
  },
  {
    title: 'PostgreSQL 단일 스택',
    desc: '벡터 검색, 캐시, 세션, 작업 큐 모두 PostgreSQL. Redis, Elasticsearch 불필요.',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" />
      </svg>
    ),
  },
  {
    title: '레이어 단방향 의존',
    desc: 'Gateway → Router → Agent → Tool. 역방향 참조 금지. 각 레이어는 상/하위 내부 구현을 모름.',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
      </svg>
    ),
  },
];

export function IntegrationPattern() {
  const [activeStep, setActiveStep] = useState(0);

  return (
    <div className="space-y-8">
      {/* Design Principles */}
      <div>
        <h3 className="mb-1 text-[var(--font-size-lg)] font-bold text-[var(--color-neutral-900)]">
          설계 원칙
        </h3>
        <p className="mb-4 text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
          AI Platform의 핵심 설계 원칙. 이 원칙들이 다중 클라이언트 연동과 데이터 격리를 가능하게 합니다.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          {DESIGN_PRINCIPLES.map((p) => (
            <div
              key={p.title}
              className="flex gap-3 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-4"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--radius-md)] bg-[var(--color-primary-50)] text-[var(--color-primary-600)]">
                {p.icon}
              </div>
              <div>
                <div className="text-[var(--font-size-sm)] font-bold text-[var(--color-neutral-800)]">
                  {p.title}
                </div>
                <div className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                  {p.desc}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Integration Steps */}
      <div>
        <h3 className="mb-1 text-[var(--font-size-lg)] font-bold text-[var(--color-neutral-900)]">
          연동 가이드
        </h3>
        <p className="mb-4 text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
          새로운 외부 시스템을 AI Platform에 연동하는 4단계. 대부분의 경우 코드 변경 없이 Profile YAML과 Tool 등록만으로 완료됩니다.
        </p>

        <div className="grid gap-4 md:grid-cols-[200px_1fr]">
          {/* Step selector */}
          <div className="flex flex-row gap-1 md:flex-col">
            {INTEGRATION_STEPS.map((s, idx) => (
              <button
                key={s.step}
                onClick={() => setActiveStep(idx)}
                aria-label={`Step ${s.step}: ${s.title}`}
                className={cn(
                  'flex items-center gap-2 rounded-[var(--radius-md)] px-3 py-2 text-left transition-all',
                  idx === activeStep
                    ? 'bg-[var(--color-primary-50)] border border-[var(--color-primary-300)]'
                    : 'hover:bg-[var(--color-neutral-50)]',
                )}
              >
                <div className={cn(
                  'flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[var(--font-size-xs)] font-bold',
                  idx === activeStep
                    ? 'bg-[var(--color-primary-500)] text-white'
                    : 'bg-[var(--color-neutral-200)] text-[var(--color-neutral-500)]',
                )}>
                  {s.step}
                </div>
                <span className={cn(
                  'text-[var(--font-size-xs)] font-medium',
                  idx === activeStep
                    ? 'text-[var(--color-primary-700)]'
                    : 'text-[var(--color-neutral-600)]',
                )}>
                  {s.title}
                </span>
              </button>
            ))}
          </div>

          {/* Step content */}
          <div className="rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-4">
            <div className="mb-2 flex items-center gap-2">
              <Badge variant="primary" size="sm">
                Step {INTEGRATION_STEPS[activeStep].step}
              </Badge>
              <h4 className="text-[var(--font-size-sm)] font-bold text-[var(--color-neutral-800)]">
                {INTEGRATION_STEPS[activeStep].title}
              </h4>
            </div>
            <p className="mb-3 text-[var(--font-size-sm)] text-[var(--color-neutral-600)]">
              {INTEGRATION_STEPS[activeStep].desc}
            </p>
            <div className="rounded-[var(--radius-md)] bg-[var(--color-neutral-900)] p-4">
              <pre className="overflow-x-auto text-[var(--font-size-xs)] leading-relaxed text-emerald-400 font-mono whitespace-pre-wrap">
                {INTEGRATION_STEPS[activeStep].code}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
