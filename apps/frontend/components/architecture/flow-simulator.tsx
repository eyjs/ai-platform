'use client';

import { useState, useEffect, useCallback } from 'react';
import { cn } from '@/lib/cn';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { ClientInfo } from './system-diagram';

interface FlowStep {
  layer: string;
  title: string;
  description: string;
  dataSnippet: string;
  detail: string;
}

function getFlowSteps(client: ClientInfo): FlowStep[] {
  const isFlowSNS = client.id === 'flowsns';
  const isSaju = client.id === 'saju';

  return [
    {
      layer: 'client',
      title: `${client.name}에서 요청 전송`,
      description: `POST /api/chat/stream`,
      dataSnippet: JSON.stringify({
        question: isFlowSNS ? '이번 주 마감 임박 태스크 알려줘' : isSaju ? '1990년 5월 15일 사주 분석해줘' : '질문 내용',
        chatbot_id: client.profileId,
        session_id: 'sess_abc123',
      }, null, 2),
      detail: `클라이언트가 chatbot_id="${client.profileId}"로 요청합니다. 이 ID가 어떤 Profile(챗봇)을 사용할지 결정합니다.`,
    },
    {
      layer: 'gateway',
      title: 'Gateway: 인증 & Profile 로딩',
      description: 'JWT/API Key 검증 → UserContext 생성 → Profile YAML 로딩',
      dataSnippet: JSON.stringify({
        user_id: 'usr_flowsns_001',
        user_role: 'operator',
        profile: client.profileId,
        allowed_tools: client.tools.slice(0, 3).join(', ') + '...',
      }, null, 2),
      detail: `X-API-Key 헤더로 클라이언트를 인증합니다. chatbot_id로 "${client.profileId}" Profile YAML을 로드하여 허용된 도구, 프롬프트, 제한사항을 파악합니다.`,
    },
    {
      layer: 'router',
      title: 'Router: 4-Layer 분류',
      description: 'Context → Intent → Mode → Strategy',
      dataSnippet: JSON.stringify({
        context_resolved: true,
        intent: isFlowSNS ? 'LOOKUP' : isSaju ? 'ANALYSIS' : 'GENERAL',
        mode: 'agentic',
        strategy: { max_tool_calls: isFlowSNS ? 15 : 5, timeout: isFlowSNS ? 120 : 60 },
      }, null, 2),
      detail: `Router가 질문을 분석합니다. Layer 0: 대명사 해소, Layer 1: Intent 분류(8종), Layer 2: Mode 결정(agentic/deterministic), Layer 3: 실행 전략 생성`,
    },
    {
      layer: 'agent',
      title: 'Agent: Tool 선택 & 실행',
      description: `LLM이 Profile에서 허용된 ${client.tools.length}개 도구 중 최적의 도구를 선택`,
      dataSnippet: JSON.stringify({
        selected_tool: isFlowSNS ? 'flowsns_tasks' : isSaju ? 'saju_lookup' : 'rag_search',
        tool_input: isFlowSNS
          ? { action: 'list', filters: { due_this_week: true } }
          : isSaju
            ? { birth_date: '1990-05-15', birth_time: '14:30' }
            : { query: '...' },
      }, null, 2),
      detail: `Universal Agent는 하나지만, Profile이 LLM에게 제공하는 도구 목록과 시스템 프롬프트가 다릅니다. Agent는 프로필에 없는 도구의 존재를 알지 못합니다.`,
    },
    {
      layer: 'tool',
      title: 'Tool: 외부 API 호출',
      description: `Tool이 scope/context를 받아 ${client.name} API 호출`,
      dataSnippet: isFlowSNS
        ? `GET http://host.docker.internal:3021/api/tasks\nHeaders: { Authorization: "Bearer fsk_..." }\nResponse: [{ title: "인스타 게시물 작성", due: "2026-05-15" }, ...]`
        : isSaju
          ? `POST http://host.docker.internal:8002/api/saju/lookup\nBody: { birth_date: "1990-05-15" }\nResponse: { pillars: {...}, elements: {...} }`
          : `Tool은 scope/context만 받아서 동작합니다.\n어떤 봇이 호출했는지 모릅니다.`,
      detail: `Tool은 "어떤 봇에서 호출됐는지" 모릅니다. scope와 context만 전달받아 동작합니다. 이것이 데이터 격리의 핵심입니다.`,
    },
    {
      layer: 'response',
      title: '응답: SSE 스트리밍',
      description: 'Agent가 Tool 결과를 바탕으로 답변을 생성, SSE로 스트리밍',
      dataSnippet: isFlowSNS
        ? `data: {"type":"delta","content":"이번 주 마감 임박 태스크는 3건입니다:\\n1. 인스타 게시물 작성 (5/15)\\n2. ..."}`
        : isSaju
          ? `data: {"type":"delta","content":"1990년 5월 15일생의 사주를 분석하겠습니다.\\n\\n## 사주 구성\\n경오(庚午)년..."}`
          : `data: {"type":"delta","content":"답변 내용..."}`,
      detail: `LLM이 Tool 결과를 사용자 친화적인 응답으로 변환합니다. SSE(Server-Sent Events)로 토큰 단위 스트리밍하여 실시간 응답을 제공합니다.`,
    },
  ];
}

interface FlowSimulatorProps {
  client: ClientInfo;
  onLayerChange: (layer: string | null) => void;
}

export function FlowSimulator({ client, onLayerChange }: FlowSimulatorProps) {
  const steps = getFlowSteps(client);
  const [activeStep, setActiveStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);

  const advance = useCallback(() => {
    setActiveStep((prev) => (prev + 1) % steps.length);
  }, [steps.length]);

  useEffect(() => {
    setActiveStep(0);
    setIsPlaying(true);
  }, [client.id]);

  useEffect(() => {
    onLayerChange(steps[activeStep]?.layer ?? null);
  }, [activeStep, steps, onLayerChange]);

  useEffect(() => {
    if (!isPlaying) return;
    const timer = setInterval(advance, 3000);
    return () => clearInterval(timer);
  }, [isPlaying, advance]);

  const layerColors: Record<string, string> = {
    client: 'bg-[var(--color-info)] text-white',
    gateway: 'bg-blue-500 text-white',
    router: 'bg-purple-500 text-white',
    agent: 'bg-amber-500 text-white',
    tool: 'bg-emerald-500 text-white',
    response: 'bg-[var(--color-primary-500)] text-white',
  };

  return (
    <div className="rounded-[var(--radius-xl)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-6 shadow-[var(--shadow-sm)]">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-[var(--font-size-base)] font-bold text-[var(--color-neutral-900)]">
            요청 흐름 시뮬레이션
          </h3>
          <Badge variant="primary" size="sm">{client.name}</Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setIsPlaying(!isPlaying)}
            aria-label={isPlaying ? '일시정지' : '재생'}
          >
            {isPlaying ? (
              <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M6 4h4v16H6zM14 4h4v16h-4z" />
              </svg>
            ) : (
              <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z" />
              </svg>
            )}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setActiveStep(0)} aria-label="처음부터">
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0019 16V8a1 1 0 00-1.6-.8l-5.333 4zM4.066 11.2a1 1 0 000 1.6l5.334 4A1 1 0 0011 16V8a1 1 0 00-1.6-.8l-5.334 4z" />
            </svg>
          </Button>
        </div>
      </div>

      {/* Timeline */}
      <div className="mb-6 flex items-center gap-1">
        {steps.map((step, idx) => (
          <button
            key={idx}
            onClick={() => { setActiveStep(idx); setIsPlaying(false); }}
            aria-label={`Step ${idx + 1}: ${step.title}`}
            className="group flex flex-1 flex-col items-center gap-1"
          >
            <div className={cn(
              'flex h-8 w-8 items-center justify-center rounded-full text-[var(--font-size-xs)] font-bold transition-all duration-500',
              idx === activeStep
                ? cn(layerColors[step.layer], 'scale-110 shadow-[var(--shadow-md)]')
                : idx < activeStep
                  ? 'bg-[var(--color-neutral-300)] text-white'
                  : 'border-2 border-[var(--color-neutral-300)] text-[var(--color-neutral-400)]',
            )}>
              {idx + 1}
            </div>
            <span className={cn(
              'text-[10px] transition-colors',
              idx === activeStep ? 'font-semibold text-[var(--color-neutral-800)]' : 'text-[var(--color-neutral-400)]',
            )}>
              {step.layer}
            </span>
          </button>
        ))}
      </div>

      {/* Active step content */}
      {steps.map((step, idx) => (
        <div
          key={idx}
          className={cn(
            'transition-all duration-500',
            idx === activeStep ? 'opacity-100' : 'hidden opacity-0',
          )}
        >
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <div className="mb-2 flex items-center gap-2">
                <span className={cn('inline-flex rounded-[var(--radius-sm)] px-2 py-0.5 text-[var(--font-size-xs)] font-bold', layerColors[step.layer])}>
                  {step.layer.toUpperCase()}
                </span>
                <h4 className="text-[var(--font-size-sm)] font-bold text-[var(--color-neutral-900)]">
                  {step.title}
                </h4>
              </div>
              <p className="mb-3 text-[var(--font-size-sm)] text-[var(--color-neutral-600)]">
                {step.description}
              </p>
              <p className="text-[var(--font-size-xs)] leading-relaxed text-[var(--color-neutral-500)]">
                {step.detail}
              </p>
            </div>
            <div className="rounded-[var(--radius-md)] bg-[var(--color-neutral-900)] p-4">
              <pre className="overflow-x-auto text-[var(--font-size-xs)] leading-relaxed text-emerald-400 font-mono whitespace-pre-wrap">
                {step.dataSnippet}
              </pre>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
