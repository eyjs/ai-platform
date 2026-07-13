'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useParams, useSearchParams, useRouter } from 'next/navigation';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  getDocumentMeta,
  getDocumentChunks,
  type DocumentMeta,
  type DocumentChunk,
} from '@/lib/api/inspect';

/**
 * 원본 문서 뷰어 — 청크를 chunk_index 순으로 재조립해 파싱된 원본을 복원한다.
 * 트레이스 청크 뷰어에서 ?chunk={id} 로 진입하면 해당 청크를 하이라이트한다.
 * 섹션 경로(AST-lite)가 바뀌는 지점에 섹션 헤더를 표시해 문서 구조를 드러낸다.
 */

const PAGE_SIZE = 200;

function sectionKey(chunk: DocumentChunk): string {
  return (chunk.metadata?.section_path ?? []).join(' › ');
}

export default function DocumentViewerPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const documentId = params.id as string;
  const targetChunkId = searchParams.get('chunk');

  const [meta, setMeta] = useState<DocumentMeta | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const targetRef = useRef<HTMLDivElement | null>(null);

  const loadMore = useCallback(
    async (offset: number): Promise<DocumentChunk[]> => {
      const page = await getDocumentChunks(documentId, offset, PAGE_SIZE);
      return page.chunks;
    },
    [documentId],
  );

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    (async () => {
      try {
        const m = await getDocumentMeta(documentId);
        if (cancelled) return;
        setMeta(m);
        // 대상 청크가 있으면 그 청크가 포함될 때까지 순차 로드 (역추적 진입)
        let acc: DocumentChunk[] = [];
        let offset = 0;
        for (;;) {
          const page = await loadMore(offset);
          if (cancelled) return;
          acc = [...acc, ...page];
          offset += PAGE_SIZE;
          const hasTarget = !targetChunkId || acc.some((c) => c.chunk_id === targetChunkId);
          if (page.length < PAGE_SIZE || (hasTarget && acc.length > 0)) {
            if (hasTarget || page.length < PAGE_SIZE) break;
          }
        }
        setChunks(acc);
        setError(null);
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : '문서를 불러올 수 없습니다');
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [documentId, targetChunkId, loadMore]);

  // 대상 청크로 스크롤
  useEffect(() => {
    if (!isLoading && targetRef.current) {
      targetRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [isLoading, chunks.length]);

  const handleLoadMore = async () => {
    setIsLoadingMore(true);
    try {
      const page = await loadMore(chunks.length);
      setChunks((prev) => [...prev, ...page]);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '추가 로드 실패');
    } finally {
      setIsLoadingMore(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton height="40px" width="400px" />
        <Skeleton height="500px" />
      </div>
    );
  }

  if (error || !meta) {
    return (
      <Card>
        <CardContent>
          <p className="py-8 text-center text-[var(--font-size-sm)] text-[var(--color-error)]">
            {error ?? '문서를 찾을 수 없습니다'}
          </p>
          <div className="flex justify-center">
            <Button variant="secondary" onClick={() => router.back()}>
              돌아가기
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  const hasMore = chunks.length < meta.total_chunks;
  let prevSection = '__init__';

  return (
    <div className="flex flex-col gap-4">
      {/* 문서 메타 헤더 */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <CardTitle>{meta.title}</CardTitle>
              <p className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                {meta.file_name} · 도메인 {meta.domain_code} · 보안 {meta.security_level} ·{' '}
                {meta.total_chunks}청크
                {meta.created_at ? ` · 적재 ${meta.created_at.slice(0, 10)}` : ''}
              </p>
              {meta.external_id && (
                <p className="mt-0.5 font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
                  KMS 원본: {meta.external_id}
                </p>
              )}
            </div>
            <Button variant="secondary" onClick={() => router.back()}>
              ← 돌아가기
            </Button>
          </div>
        </CardHeader>
      </Card>

      {/* 청크 순서 재조립 본문 (원본 복원) */}
      <Card>
        <CardContent>
          <div className="flex flex-col">
            {chunks.map((chunk) => {
              const section = sectionKey(chunk);
              const showSection = section !== prevSection;
              prevSection = section;
              const isTarget = chunk.chunk_id === targetChunkId;
              return (
                <div key={chunk.chunk_id} ref={isTarget ? targetRef : undefined}>
                  {showSection && section && (
                    <div className="sticky top-0 mt-3 border-b border-[var(--color-neutral-200)] bg-[var(--color-surface-card)] py-1 text-[var(--font-size-xs)] font-semibold text-[var(--color-primary-600)]">
                      § {section}
                    </div>
                  )}
                  <div
                    className={`group relative border-b border-[var(--color-neutral-100)] py-2 ${
                      isTarget
                        ? 'rounded-[var(--radius-sm)] bg-[var(--color-warning-light)] px-2'
                        : ''
                    }`}
                  >
                    <div className="mb-0.5 flex items-center gap-2 text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
                      <span className="font-mono">#{chunk.chunk_index + 1}</span>
                      {isTarget && <Badge variant="warning">역추적 대상 청크</Badge>}
                      <span className="font-mono opacity-0 transition-opacity group-hover:opacity-100">
                        {chunk.chunk_id}
                      </span>
                    </div>
                    <pre className="whitespace-pre-wrap break-words font-sans text-[var(--font-size-sm)] leading-relaxed text-[var(--color-neutral-700)]">
                      {chunk.content}
                    </pre>
                  </div>
                </div>
              );
            })}
          </div>
          {hasMore && (
            <div className="mt-4 flex justify-center">
              <Button variant="secondary" onClick={handleLoadMore} disabled={isLoadingMore}>
                {isLoadingMore ? '불러오는 중…' : `더 보기 (${chunks.length}/${meta.total_chunks})`}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
