'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/cn';

interface ParseResultViewerProps {
  /** 파싱 결과 마크다운 */
  markdown: string;
  /** 마크다운 편집 콜백 */
  onMarkdownChange: (value: string) => void;
  /** 업로드된 원본 PDF File 객체 (브라우저 내장 뷰어용) */
  pdfFile: File | null;
}

const MIN_PANEL_WIDTH_PX = 200;

export function ParseResultViewer({
  markdown,
  onMarkdownChange,
  pdfFile,
}: ParseResultViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [splitRatio, setSplitRatio] = useState(0.5);
  const [isDragging, setIsDragging] = useState(false);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);

  // PDF Blob URL 생성/해제
  useEffect(() => {
    if (!pdfFile) {
      setPdfUrl(null);
      return;
    }
    const url = URL.createObjectURL(pdfFile);
    setPdfUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [pdfFile]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      const container = containerRef.current;
      if (!container) return;

      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const containerWidth = rect.width;

      // 최소 패널 폭 보장
      const minRatio = MIN_PANEL_WIDTH_PX / containerWidth;
      const maxRatio = 1 - minRatio;
      const newRatio = Math.min(Math.max(x / containerWidth, minRatio), maxRatio);
      setSplitRatio(newRatio);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging]);

  const leftWidthPercent = `${splitRatio * 100}%`;
  const rightWidthPercent = `${(1 - splitRatio) * 100}%`;

  return (
    <div
      ref={containerRef}
      className={cn(
        'flex h-full w-full overflow-hidden rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)]',
        isDragging && 'select-none',
      )}
    >
      {/* Left: PDF Preview */}
      <div
        className="flex flex-col overflow-hidden"
        style={{ width: leftWidthPercent }}
      >
        <div className="flex h-10 shrink-0 items-center border-b border-[var(--color-neutral-200)] bg-[var(--color-neutral-50)] px-[var(--spacing-4)]">
          <span className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]">
            PDF 원본
          </span>
        </div>
        <div className="flex-1 overflow-hidden">
          {pdfUrl ? (
            <object
              data={pdfUrl}
              type="application/pdf"
              className="h-full w-full"
              aria-label="PDF 원본 미리보기"
            >
              <div className="flex h-full items-center justify-center p-[var(--spacing-6)]">
                <p className="text-center text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
                  브라우저에서 PDF를 표시할 수 없습니다.
                </p>
              </div>
            </object>
          ) : (
            <div className="flex h-full items-center justify-center">
              <p className="text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
                PDF 미리보기 없음
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Divider / Resize Handle */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="패널 크기 조절"
        tabIndex={0}
        onMouseDown={handleMouseDown}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft') {
            e.preventDefault();
            setSplitRatio((prev) => Math.max(prev - 0.02, 0.1));
          } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            setSplitRatio((prev) => Math.min(prev + 0.02, 0.9));
          }
        }}
        className={cn(
          'flex w-2 shrink-0 cursor-col-resize items-center justify-center',
          'bg-[var(--color-neutral-100)] transition-colors',
          'hover:bg-[var(--color-primary-100)]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-primary-200)]',
          isDragging && 'bg-[var(--color-primary-200)]',
        )}
      >
        <div className="h-8 w-0.5 rounded-full bg-[var(--color-neutral-300)]" />
      </div>

      {/* Right: Markdown Editor */}
      <div
        className="flex flex-col overflow-hidden"
        style={{ width: rightWidthPercent }}
      >
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-[var(--color-neutral-200)] bg-[var(--color-neutral-50)] px-[var(--spacing-4)]">
          <span className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]">
            Markdown
          </span>
          <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
            {markdown.length.toLocaleString()}자
          </span>
        </div>
        <div className="flex-1 overflow-hidden">
          <textarea
            value={markdown}
            onChange={(e) => onMarkdownChange(e.target.value)}
            className={cn(
              'h-full w-full resize-none p-[var(--spacing-4)]',
              'bg-[var(--surface-input)] text-[var(--font-size-sm)] leading-relaxed',
              'font-[var(--font-mono)]',
              'text-[var(--color-neutral-800)]',
              'placeholder:text-[var(--color-neutral-400)]',
              'focus:outline-none',
              'border-none',
            )}
            placeholder="파싱 결과가 여기에 표시됩니다..."
            spellCheck={false}
            aria-label="파싱된 마크다운 편집"
          />
        </div>
      </div>
    </div>
  );
}
