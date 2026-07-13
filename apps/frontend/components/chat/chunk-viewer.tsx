'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { getChunkDetail, type ChunkDetail } from '@/lib/api/inspect';

/**
 * 청크 뷰어 — 트레이스의 chunk_id 를 역추적해 전문·섹션·소속 문서를 보여준다.
 * "이 근거가 어디서 왔나"의 역방향 분석 진입점. 원본 문서 뷰어로 이어진다.
 */
export function ChunkViewer({
  chunkId,
  onClose,
}: {
  chunkId: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<ChunkDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    getChunkDetail(chunkId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [chunkId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const sectionPath = detail?.metadata?.section_path;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-surface-overlay)] p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="청크 상세 뷰어"
    >
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-[var(--radius-lg)] bg-[var(--color-surface-elevated)] shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className="flex items-start justify-between gap-2 border-b border-[var(--color-neutral-200)] px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-[var(--font-size-sm)] font-semibold text-[var(--color-neutral-800)]">
              {detail?.document.title ?? '청크 로딩 중…'}
            </div>
            {detail && (
              <div className="mt-0.5 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                청크 #{detail.chunk_index + 1} / {detail.document.total_chunks} · 도메인{' '}
                {detail.domain_code} · 보안 {detail.security_level} · {detail.token_count}토큰
              </div>
            )}
            {sectionPath && sectionPath.length > 0 && (
              <div className="mt-0.5 truncate text-[var(--font-size-xs)] text-[var(--color-primary-600)]">
                섹션: {sectionPath.join(' › ')}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="닫기"
            className="shrink-0 rounded-[var(--radius-sm)] p-1 text-[var(--color-neutral-400)] transition-colors hover:bg-[var(--color-neutral-100)] hover:text-[var(--color-neutral-700)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-600)]"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* 본문 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {error && (
            <p className="text-[var(--font-size-sm)] text-[var(--color-error)]">{error}</p>
          )}
          {!error && !detail && (
            <p className="text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">불러오는 중…</p>
          )}
          {detail && (
            <pre className="whitespace-pre-wrap break-words font-sans text-[var(--font-size-sm)] leading-relaxed text-[var(--color-neutral-700)]">
              {detail.content}
            </pre>
          )}
        </div>

        {/* 푸터: 원본 문서 뷰어로 */}
        {detail && (
          <div className="flex items-center justify-between gap-2 border-t border-[var(--color-neutral-200)] px-4 py-2.5">
            <span className="truncate font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
              {detail.chunk_id}
            </span>
            <Link
              href={`/admin/documents/${detail.document_id}?chunk=${detail.chunk_id}`}
              className="shrink-0 rounded-[var(--radius-sm)] bg-[var(--color-primary-600)] px-3 py-1.5 text-[var(--font-size-xs)] font-semibold text-[var(--color-neutral-0)] transition-opacity hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-600)]"
            >
              원본 문서 뷰어 열기 →
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
