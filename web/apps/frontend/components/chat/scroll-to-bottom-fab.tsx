'use client';

import { cn } from '@/lib/cn';

interface ScrollToBottomFabProps {
  visible: boolean;
  onClick: () => void;
}

export function ScrollToBottomFab({ visible, onClick }: ScrollToBottomFabProps) {
  if (!visible) return null;

  return (
    <button
      onClick={onClick}
      className={cn(
        'absolute bottom-24 right-6 flex h-10 w-10 items-center justify-center',
        'rounded-full bg-[var(--surface-card)] border border-[var(--color-neutral-200)]',
        'shadow-[var(--shadow-md)] hover:bg-[var(--color-neutral-50)] transition-all',
        'z-10',
      )}
      title="최신 메시지로"
    >
      <svg className="h-5 w-5 text-[var(--color-neutral-600)]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
      </svg>
    </button>
  );
}
