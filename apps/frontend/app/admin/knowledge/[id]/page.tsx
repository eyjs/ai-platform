'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import {
  fetchKnowledgeDocumentDetail,
  type KnowledgeDocumentDetail,
} from '@/lib/api/bff-knowledge';

export default function KnowledgeDocumentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  const [doc, setDoc] = useState<KnowledgeDocumentDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  const loadDetail = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setDoc(await fetchKnowledgeDocumentDetail(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : '문서 로딩 실패');
    } finally {
      setIsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);


  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton height="32px" className="w-60" />
        <Skeleton height="120px" />
        <Skeleton height="200px" />
      </div>
    );
  }

  if (error || !doc) {
    return (
      <div className="flex flex-col items-center gap-3 py-12">
        <p className="text-[var(--color-error)]">{error ?? '문서를 찾을 수 없습니다'}</p>
        <Button variant="secondary" onClick={() => router.push('/admin/knowledge')}>
          목록으로
        </Button>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push('/admin/knowledge')}
            className="rounded-[var(--radius-sm)] text-[var(--color-neutral-500)] hover:text-[var(--color-neutral-700)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-500)]"
            aria-label="목록으로 돌아가기"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">{doc.title}</h1>
          {doc.domainCode && <Badge variant="secondary">{doc.domainCode}</Badge>}
        </div>
      </div>

      {/* Metadata */}
      <div className="mb-6 rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-5">
        <h2 className="mb-3 text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">메타데이터</h2>
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">ID</dt>
            <dd className="mt-0.5 font-mono text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">{doc.id}</dd>
          </div>
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">파일명</dt>
            <dd className="mt-0.5 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">{doc.fileName ?? '-'}</dd>
          </div>
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">보안등급</dt>
            <dd className="mt-0.5 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">{doc.securityLevel ?? '-'}</dd>
          </div>
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">청크 수</dt>
            <dd className="mt-0.5 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">{doc.chunkCount.toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">생성 일자</dt>
            <dd className="mt-0.5 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
              {new Date(doc.createdAt).toLocaleString('ko-KR')}
            </dd>
          </div>
          <div className="sm:col-span-3">
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">출처 URL</dt>
            <dd className="mt-0.5 truncate text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">{doc.sourceUrl ?? '-'}</dd>
          </div>
        </dl>
      </div>

      {/* Content */}
      <div className="rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-5">
        <h2 className="mb-3 text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">원문 (청크)</h2>
        <div className="max-h-96 overflow-y-auto rounded-[var(--radius-md)] bg-[var(--color-neutral-50)] p-4">
          <pre className="whitespace-pre-wrap font-mono text-[var(--font-size-sm)] leading-relaxed text-[var(--color-neutral-700)]">
            {doc.content ?? '내용이 없습니다'}
          </pre>
        </div>
      </div>

    </div>
  );
}
