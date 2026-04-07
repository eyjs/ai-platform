'use client';

import { useState } from 'react';
import { cn } from '@/lib/cn';

interface DebugPanelProps {
  traceData: Record<string, unknown>;
  className?: string;
}

export function DebugPanel({ traceData, className }: DebugPanelProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div
      className={cn(
        'rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--color-neutral-50)]',
        className,
      )}
    >
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center justify-between px-3 py-2 text-[var(--font-size-xs)] text-[var(--color-neutral-500)] hover:text-[var(--color-neutral-700)]"
      >
        <span>디버그 정보</span>
        <span>{isExpanded ? '\u25B2' : '\u25BC'}</span>
      </button>
      {isExpanded && (
        <pre className="max-h-48 overflow-auto border-t border-[var(--color-neutral-200)] px-3 py-2 font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
          {JSON.stringify(traceData, null, 2)}
        </pre>
      )}
    </div>
  );
}
