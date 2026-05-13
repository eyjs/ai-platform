'use client';

import Link from 'next/link';

export default function ArchitectureLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-[var(--surface-page)]">
      <header className="sticky top-0 z-10 border-b border-[var(--color-neutral-200)] bg-[var(--surface-card)] backdrop-blur-sm">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-6 py-3">
          <Link
            href="/"
            className="flex items-center gap-2 text-[var(--font-size-sm)] text-[var(--color-neutral-500)] hover:text-[var(--color-neutral-700)] transition-colors"
            aria-label="채팅으로 돌아가기"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            채팅
          </Link>
          <div className="h-4 w-px bg-[var(--color-neutral-200)]" />
          <h1 className="text-[var(--font-size-lg)] font-bold text-[var(--color-neutral-900)]">
            AI Platform Architecture
          </h1>
        </div>
      </header>
      <main>{children}</main>
    </div>
  );
}
