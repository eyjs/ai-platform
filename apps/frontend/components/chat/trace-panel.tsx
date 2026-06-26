'use client';

import { useState } from 'react';
import { formatDuration, latencyColor } from '@/lib/format';

/** 답변별 RAG 파이프라인 트레이스 — SSE trace 이벤트를 단계로 펼쳐 본다. */
export function TracePanel({ events }: { events: Array<Record<string, unknown>> }) {
  const [open, setOpen] = useState(false);
  if (!events || events.length === 0) return null;

  return (
    <div className="mt-2 border-t border-[var(--color-neutral-200)] pt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-1 py-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)] transition-colors hover:text-[var(--color-neutral-700)]"
        aria-expanded={open}
        aria-label="RAG 파이프라인 트레이스 펼치기"
      >
        <svg
          className={`h-3.5 w-3.5 shrink-0 transition-transform duration-[var(--duration-fast)] ${open ? 'rotate-90' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span>RAG 파이프라인 트레이스 ({events.length}단계)</span>
      </button>
      {open && (
        <ol className="mt-1 space-y-0.5 pl-1">
          {events.map((e, i) => {
            const step = String(e.step ?? e.node ?? 'event');
            const tool = e.tool ? String(e.tool) : null;
            const msVal =
              typeof e.ms === 'number' ? e.ms : typeof e.latency_ms === 'number' ? e.latency_ms : null;
            const failed = e.success === false;
            const label = tool && tool !== step ? `${step}: ${tool}` : step;
            return (
              <li key={i} className="flex items-center gap-2 text-[var(--font-size-xs)]">
                <span className="w-4 shrink-0 text-right text-[var(--color-neutral-400)]">{i + 1}</span>
                <span className="font-mono text-[var(--color-neutral-700)]">{label}</span>
                {failed && <span className="text-[var(--color-error)]">실패</span>}
                {msVal != null && (
                  <span className="ml-auto shrink-0 font-mono" style={{ color: latencyColor(msVal) }}>
                    {formatDuration(msVal)}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
