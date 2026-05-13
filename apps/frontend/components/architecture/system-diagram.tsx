'use client';

import { cn } from '@/lib/cn';
import { Badge } from '@/components/ui/badge';

export interface ClientInfo {
  id: string;
  name: string;
  port: string;
  desc: string;
  tools: string[];
  profileId: string;
}

export const CLIENTS: ClientInfo[] = [
  {
    id: 'flowsns',
    name: 'flowSNS',
    port: ':3021',
    desc: 'SNS 마케팅 운영 자동화',
    tools: ['flowsns_tasks', 'flowsns_clients', 'flowsns_accounts', 'flowsns_dashboard', 'flowsns_calendar', 'flowsns_task_actions', 'flowsns_approval', 'flowsns_notifications', 'flowsns_reports', 'flowsns_profiles'],
    profileId: 'flowsns-ops',
  },
  {
    id: 'saju',
    name: 'Saju Backend',
    port: ':8002',
    desc: '사주 분석 & 리포트',
    tools: ['saju_lookup', 'saju_report_paper', 'saju_report_compatibility'],
    profileId: 'fortune-saju',
  },
  {
    id: 'custom',
    name: 'Your Service',
    port: ':????',
    desc: 'Profile YAML 추가로 연동',
    tools: ['your_custom_tools'],
    profileId: 'your-profile',
  },
];

const LAYERS = [
  { id: 'gateway', name: 'Gateway', desc: '인증 · Profile 로딩 · SSE' },
  { id: 'router', name: 'Router', desc: 'Context → Intent → Mode → Strategy' },
  { id: 'agent', name: 'Agent', desc: 'Tool 선택 · 답변 생성' },
  { id: 'tool', name: 'Tool System', desc: 'Registry · Permission · Scope 주입' },
];

interface SystemDiagramProps {
  selectedClient: string | null;
  activeLayer: string | null;
  onClientSelect: (id: string) => void;
}

export function SystemDiagram({
  selectedClient,
  activeLayer,
  onClientSelect,
}: SystemDiagramProps) {
  return (
    <div className="relative w-full overflow-x-auto">
      <div className="mx-auto flex min-w-[800px] max-w-5xl items-stretch gap-3 px-4 py-8">
        {/* Left: Client nodes */}
        <div className="flex w-48 shrink-0 flex-col gap-3">
          <div className="mb-1 text-center text-[var(--font-size-xs)] font-semibold uppercase tracking-wider text-[var(--color-neutral-400)]">
            External Clients
          </div>
          {CLIENTS.map((client) => {
            const isSelected = selectedClient === client.id;
            const isCustom = client.id === 'custom';
            return (
              <button
                key={client.id}
                onClick={() => !isCustom && onClientSelect(client.id)}
                disabled={isCustom}
                aria-label={`${client.name} 클라이언트 선택`}
                className={cn(
                  'group relative rounded-[var(--radius-lg)] border-2 p-3 text-left transition-all duration-300',
                  isSelected
                    ? 'border-[var(--color-primary-500)] bg-[var(--color-primary-50)] shadow-[0_0_20px_var(--color-primary-200)]'
                    : isCustom
                      ? 'cursor-default border-dashed border-[var(--color-neutral-300)] bg-[var(--color-neutral-50)]'
                      : 'border-[var(--color-neutral-200)] bg-[var(--surface-card)] hover:border-[var(--color-primary-300)] hover:shadow-[var(--shadow-md)]',
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="text-[var(--font-size-base)]">
                    {client.id === 'flowsns' ? '🔗' : client.id === 'saju' ? '🔮' : '🚀'}
                  </span>
                  <span className={cn(
                    'font-semibold text-[var(--font-size-sm)]',
                    isSelected ? 'text-[var(--color-primary-700)]' : 'text-[var(--color-neutral-800)]',
                  )}>
                    {client.name}
                  </span>
                </div>
                <div className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                  {client.desc}
                </div>
                {!isCustom && (
                  <Badge
                    variant={isSelected ? 'primary' : 'neutral'}
                    size="sm"
                    className="mt-2"
                  >
                    {client.port}
                  </Badge>
                )}
                {isCustom && (
                  <div className="mt-2 text-[10px] italic text-[var(--color-neutral-400)]">
                    클릭하여 연동 가이드 →
                  </div>
                )}
              </button>
            );
          })}
        </div>

        {/* Connection arrows */}
        <div className="flex w-16 shrink-0 flex-col items-center justify-center gap-3">
          {CLIENTS.map((client) => {
            const isSelected = selectedClient === client.id;
            const isCustom = client.id === 'custom';
            return (
              <div key={client.id} className="flex h-20 items-center">
                <div className="relative h-0.5 w-full overflow-hidden">
                  <div className={cn(
                    'absolute inset-0',
                    isCustom ? 'border-t-2 border-dashed border-[var(--color-neutral-300)]' : 'bg-[var(--color-neutral-300)]',
                    isSelected && !isCustom && 'bg-[var(--color-primary-500)]',
                  )} />
                  {isSelected && !isCustom && (
                    <div className="animate-flow-packet absolute top-1/2 -translate-y-1/2 h-2 w-2 rounded-full bg-[var(--color-primary-500)] shadow-[0_0_8px_var(--color-primary-400)]" />
                  )}
                </div>
                <svg className={cn('h-3 w-3 shrink-0', isSelected && !isCustom ? 'text-[var(--color-primary-500)]' : 'text-[var(--color-neutral-300)]')} viewBox="0 0 12 12" fill="currentColor">
                  <path d="M0 0L12 6L0 12z" />
                </svg>
              </div>
            );
          })}
        </div>

        {/* Center: AI Platform Core */}
        <div className="flex flex-1 flex-col rounded-[var(--radius-xl)] border-2 border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-4 shadow-[var(--shadow-sm)]">
          <div className="mb-3 flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-md)] bg-[var(--color-primary-500)]">
              <svg className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
              </svg>
            </div>
            <span className="text-[var(--font-size-base)] font-bold text-[var(--color-neutral-900)]">
              AI Platform Core
            </span>
            <Badge variant="primary" size="sm">Universal Agent Runtime</Badge>
          </div>

          <div className="grid grid-cols-2 gap-2">
            {LAYERS.map((layer, idx) => {
              const isActive = activeLayer === layer.id;
              const layerColors = [
                'border-blue-200 bg-blue-50',
                'border-purple-200 bg-purple-50',
                'border-amber-200 bg-amber-50',
                'border-emerald-200 bg-emerald-50',
              ];
              return (
                <div
                  key={layer.id}
                  className={cn(
                    'relative rounded-[var(--radius-md)] border p-3 transition-all duration-500',
                    isActive
                      ? 'border-[var(--color-primary-500)] bg-[var(--color-primary-50)] shadow-[0_0_12px_var(--color-primary-200)] scale-[1.02]'
                      : layerColors[idx],
                  )}
                >
                  {isActive && (
                    <div className="absolute -right-1 -top-1 h-3 w-3 rounded-full bg-[var(--color-primary-500)] animate-pulse" />
                  )}
                  <div className="flex items-center gap-1">
                    <span className="text-[var(--font-size-xs)] font-bold text-[var(--color-neutral-700)]">
                      {layer.name}
                    </span>
                    {idx < 3 && (
                      <svg className="h-3 w-3 text-[var(--color-neutral-400)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                    )}
                  </div>
                  <div className="mt-1 text-[10px] leading-tight text-[var(--color-neutral-500)]">
                    {layer.desc}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-3 flex gap-2">
            <div className="flex-1 rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-2 py-1 text-center text-[10px] text-[var(--color-neutral-500)]">
              Profile YAML
            </div>
            <div className="flex-1 rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-2 py-1 text-center text-[10px] text-[var(--color-neutral-500)]">
              Sessions
            </div>
            <div className="flex-1 rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-2 py-1 text-center text-[10px] text-[var(--color-neutral-500)]">
              Job Queue
            </div>
          </div>
        </div>

        {/* Connection arrow to DB */}
        <div className="flex w-10 shrink-0 items-center justify-center">
          <div className="relative flex items-center">
            <div className={cn(
              'h-0.5 w-full',
              selectedClient ? 'bg-[var(--color-primary-400)]' : 'bg-[var(--color-neutral-300)]',
            )} />
            <svg className={cn('h-3 w-3 shrink-0', selectedClient ? 'text-[var(--color-primary-400)]' : 'text-[var(--color-neutral-300)]')} viewBox="0 0 12 12" fill="currentColor">
              <path d="M0 0L12 6L0 12z" />
            </svg>
          </div>
        </div>

        {/* Right: PostgreSQL */}
        <div className="flex w-40 shrink-0 flex-col justify-center">
          <div className={cn(
            'rounded-[var(--radius-lg)] border-2 p-3 transition-all duration-500',
            selectedClient
              ? 'border-[var(--color-success)] bg-[var(--color-success-light)]'
              : 'border-[var(--color-neutral-200)] bg-[var(--surface-card)]',
          )}>
            <div className="flex items-center gap-2">
              <span className="text-[var(--font-size-lg)]">🐘</span>
              <div>
                <div className="text-[var(--font-size-sm)] font-bold text-[var(--color-neutral-800)]">
                  PostgreSQL 16
                </div>
                <div className="text-[10px] text-[var(--color-neutral-500)]">
                  + pgvector + pg_trgm
                </div>
              </div>
            </div>
            <div className="mt-2 space-y-1">
              {['documents', 'document_chunks', 'sessions', 'job_queue', 'profiles'].map((t) => (
                <div
                  key={t}
                  className="rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-2 py-0.5 text-[10px] font-mono text-[var(--color-neutral-600)]"
                >
                  {t}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
