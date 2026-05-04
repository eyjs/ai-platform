import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'PDF Parser - AI Platform',
  description: 'PDF 문서를 마크다운으로 변환합니다',
};

export default function ParseLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex min-h-screen flex-col bg-[var(--surface-page)]">
      {/* Simple Navigation Header */}
      <header className="flex h-14 shrink-0 items-center border-b border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-[var(--spacing-6)]">
        <a
          href="/"
          className="text-[var(--font-size-sm)] text-[var(--color-neutral-500)] hover:text-[var(--color-neutral-700)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary-200)] focus-visible:rounded-[var(--radius-sm)]"
          aria-label="홈으로 돌아가기"
        >
          AI Platform
        </a>
        <span className="mx-[var(--spacing-2)] text-[var(--color-neutral-300)]">/</span>
        <span className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-800)]">
          PDF Parser
        </span>
      </header>

      {/* Main Content */}
      <main className="flex flex-1 flex-col">{children}</main>
    </div>
  );
}
