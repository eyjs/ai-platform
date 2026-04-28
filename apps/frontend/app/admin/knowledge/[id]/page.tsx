'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { ConfirmDialog } from '@/components/admin/confirm-dialog';
import {
  fetchKnowledgeDocumentDetail,
  reindexDocument,
  type KnowledgeDocumentDetail,
} from '@/lib/api/bff-knowledge';

const statusConfig = {
  indexed: { variant: 'success' as const, label: 'Indexed' },
  processing: { variant: 'warning' as const, label: 'Processing' },
  error: { variant: 'error' as const, label: 'Error' },
} as const;

const embeddingStatusConfig = {
  completed: { variant: 'success' as const, label: '완료' },
  pending: { variant: 'warning' as const, label: '대기' },
  error: { variant: 'error' as const, label: '오류' },
} as const;

export default function KnowledgeDocumentDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  const [doc, setDoc] = useState<KnowledgeDocumentDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isReindexing, setIsReindexing] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const { toast } = useToast();

  const loadDetail = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchKnowledgeDocumentDetail(id);
      setDoc(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : '문서 로딩 실패');
    } finally {
      setIsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  const handleReindex = async () => {
    setShowConfirm(false);
    setIsReindexing(true);
    try {
      await reindexDocument(id);
      toast('재인덱싱이 시작되었습니다', 'success');
      await loadDetail();
    } catch {
      toast('재인덱싱 실패', 'error');
    } finally {
      setIsReindexing(false);
    }
  };

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

  const status = statusConfig[doc.status];

  return (
    <div>
      {/* Header */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <button
            onClick={() => router.push('/admin/knowledge')}
            className="text-[var(--color-neutral-500)] hover:text-[var(--color-neutral-700)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-500)] rounded-[var(--radius-sm)]"
            aria-label="목록으로 돌아가기"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
            {doc.title}
          </h1>
          <Badge variant={status.variant}>{status.label}</Badge>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => setShowConfirm(true)}
          loading={isReindexing}
          aria-label="문서 재인덱싱"
        >
          Reindex
        </Button>
      </div>

      {/* Metadata */}
      <div className="mb-6 rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-5">
        <h2 className="mb-3 text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
          메타데이터
        </h2>
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">ID</dt>
            <dd className="mt-0.5 text-[var(--font-size-sm)] font-mono text-[var(--color-neutral-700)]">{doc.id}</dd>
          </div>
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">도메인</dt>
            <dd className="mt-0.5 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">{doc.domainName}</dd>
          </div>
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">인덱싱 일자</dt>
            <dd className="mt-0.5 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
              {new Date(doc.indexedAt).toLocaleString('ko-KR')}
            </dd>
          </div>
          <div>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">청크 수</dt>
            <dd className="mt-0.5 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">{doc.chunks.length}</dd>
          </div>
        </dl>
      </div>

      {/* Content Preview */}
      <div className="mb-6 rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-5">
        <h2 className="mb-3 text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
          원문 미리보기
        </h2>
        <div className="max-h-48 overflow-y-auto rounded-[var(--radius-md)] bg-[var(--color-neutral-50)] p-4">
          <pre className="whitespace-pre-wrap text-[var(--font-size-sm)] leading-relaxed text-[var(--color-neutral-700)] font-mono">
            {doc.contentPreview}
          </pre>
        </div>
      </div>

      {/* Chunks */}
      <div className="rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)]">
        <div className="border-b border-[var(--color-neutral-200)] px-5 py-4">
          <h2 className="text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
            청크 목록 ({doc.chunks.length})
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[var(--font-size-sm)]">
            <thead>
              <tr className="border-b border-[var(--color-neutral-200)]">
                <th className="px-5 py-3 text-left font-medium text-[var(--color-neutral-600)]">순서</th>
                <th className="px-5 py-3 text-right font-medium text-[var(--color-neutral-600)]">길이</th>
                <th className="px-5 py-3 text-left font-medium text-[var(--color-neutral-600)]">임베딩 상태</th>
              </tr>
            </thead>
            <tbody>
              {doc.chunks.map((chunk) => {
                const embStatus = embeddingStatusConfig[chunk.embeddingStatus];
                return (
                  <tr
                    key={chunk.order}
                    className="border-b border-[var(--color-neutral-100)] transition-colors hover:bg-[var(--color-neutral-50)]"
                  >
                    <td className="px-5 py-3 text-[var(--color-neutral-700)]">#{chunk.order}</td>
                    <td className="px-5 py-3 text-right text-[var(--color-neutral-700)]">
                      {chunk.length.toLocaleString()} chars
                    </td>
                    <td className="px-5 py-3">
                      <Badge variant={embStatus.variant}>{embStatus.label}</Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <ConfirmDialog
        isOpen={showConfirm}
        title="문서 재인덱싱"
        message="이 문서를 재인덱싱하시겠습니까? 기존 청크가 삭제되고 새로 생성됩니다."
        onConfirm={handleReindex}
        onCancel={() => setShowConfirm(false)}
        variant="danger"
        confirmLabel="재인덱싱"
        cancelLabel="취소"
      />
    </div>
  );
}
