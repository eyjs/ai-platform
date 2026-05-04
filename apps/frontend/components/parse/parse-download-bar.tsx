'use client';

import { useCallback, useState } from 'react';
import { Button } from '@/components/ui/button';

interface ParseDownloadBarProps {
  /** 다운로드할 마크다운 내용 */
  markdown: string;
  /** 원본 PDF 파일명 (확장자 포함) */
  originalFileName: string;
  /** 파싱 통계 (선택) */
  stats?: Record<string, unknown>;
  /** 새 파일 파싱 */
  onReset: () => void;
}

export function ParseDownloadBar({
  markdown,
  originalFileName,
  stats,
  onReset,
}: ParseDownloadBarProps) {
  const mdFileName = originalFileName.replace(/\.pdf$/i, '') + '.md';

  const handleDownload = useCallback(() => {
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = mdFileName;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }, [markdown, mdFileName]);

  const [copyFeedback, setCopyFeedback] = useState<'idle' | 'success' | 'error'>('idle');

  const handleCopyToClipboard = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(markdown);
      setCopyFeedback('success');
    } catch {
      setCopyFeedback('error');
    } finally {
      setTimeout(() => setCopyFeedback('idle'), 2000);
    }
  }, [markdown]);

  return (
    <div className="flex flex-wrap items-center justify-between gap-[var(--spacing-3)] rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-[var(--spacing-4)] py-[var(--spacing-3)]">
      {/* Left: Stats */}
      <div className="flex items-center gap-[var(--spacing-4)]">
        <span className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]">
          {mdFileName}
        </span>
        {stats && (
          <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
            {typeof stats.elapsed_ms === 'number' &&
              `${(stats.elapsed_ms / 1000).toFixed(1)}s`}
          </span>
        )}
        <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
          {markdown.length.toLocaleString()}자
        </span>
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-[var(--spacing-2)]">
        <Button
          variant="ghost"
          size="sm"
          onClick={handleCopyToClipboard}
          aria-label="마크다운을 클립보드에 복사"
        >
          <svg
            className="h-4 w-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15.666 3.888A2.25 2.25 0 0013.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 01-.75.75H9.75a.75.75 0 01-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 01-2.25 2.25H6.75A2.25 2.25 0 014.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 011.927-.184"
            />
          </svg>
          {copyFeedback === 'success' ? '복사됨' : copyFeedback === 'error' ? '복사 실패' : '복사'}
        </Button>

        <Button
          variant="primary"
          size="sm"
          onClick={handleDownload}
          aria-label="마크다운 파일 다운로드"
        >
          <svg
            className="h-4 w-4"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"
            />
          </svg>
          .md 다운로드
        </Button>

        <div className="mx-[var(--spacing-1)] h-5 w-px bg-[var(--color-neutral-200)]" />

        <Button
          variant="ghost"
          size="sm"
          onClick={onReset}
          aria-label="새 파일 파싱"
        >
          새 파일
        </Button>
      </div>
    </div>
  );
}
