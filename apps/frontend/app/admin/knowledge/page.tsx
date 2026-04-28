'use client';

import { useState, useEffect, useCallback } from 'react';
import { StatCard } from '@/components/ui/stat-card';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Dropdown } from '@/components/ui/dropdown';
import { useToast } from '@/components/ui/toast';
import { DocumentTable } from '@/components/admin/document-table';
import { ConfirmDialog } from '@/components/admin/confirm-dialog';
import {
  fetchKnowledgeStats,
  fetchKnowledgeDocuments,
  reindexDocument,
  type KnowledgeStats,
  type KnowledgeDocument,
} from '@/lib/api/bff-knowledge';

const PAGE_SIZE = 10;

const STATUS_OPTIONS = [
  { value: '', label: '전체 상태' },
  { value: 'indexed', label: 'Indexed' },
  { value: 'processing', label: 'Processing' },
  { value: 'error', label: 'Error' },
];

export default function KnowledgePipelinePage() {
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [domainFilter, setDomainFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reindexingId, setReindexingId] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<string | null>(null);
  const { toast } = useToast();

  const loadStats = useCallback(async () => {
    try {
      const data = await fetchKnowledgeStats();
      setStats(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : '통계 로딩 실패');
    }
  }, []);

  const loadDocuments = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchKnowledgeDocuments({
        page,
        size: PAGE_SIZE,
        domainCode: domainFilter || undefined,
        status: statusFilter || undefined,
      });
      setDocuments(data.data);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : '문서 목록 로딩 실패');
    } finally {
      setIsLoading(false);
    }
  }, [page, domainFilter, statusFilter]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  const handleReindexRequest = (id: string) => {
    setConfirmTarget(id);
  };

  const handleReindexConfirm = async () => {
    if (!confirmTarget) return;
    setConfirmTarget(null);
    setReindexingId(confirmTarget);
    try {
      await reindexDocument(confirmTarget);
      toast('재인덱싱이 시작되었습니다', 'success');
      await loadDocuments();
    } catch {
      toast('재인덱싱 실패', 'error');
    } finally {
      setReindexingId(null);
    }
  };

  const domainOptions = [
    { value: '', label: '전체 도메인' },
    ...(stats?.domainDistribution.map((d) => ({
      value: d.domainCode,
      label: d.domainName,
    })) ?? []),
  ];

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div>
      <h1 className="mb-6 text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
        Knowledge Pipeline
      </h1>

      {/* Stats Cards */}
      {stats ? (
        <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="총 문서 수" value={stats.totalDocuments.toLocaleString()} />
          <StatCard label="총 청크 수" value={stats.totalChunks.toLocaleString()} />
          {stats.domainDistribution.slice(0, 2).map((d) => (
            <StatCard key={d.domainCode} label={d.domainName} value={d.count.toLocaleString()} />
          ))}
        </div>
      ) : (
        <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} height="88px" />
          ))}
        </div>
      )}

      {/* Domain Distribution */}
      {stats && stats.domainDistribution.length > 0 && (
        <div className="mb-6 rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-5">
          <h2 className="mb-3 text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
            도메인 분포
          </h2>
          <div className="flex flex-wrap gap-3">
            {stats.domainDistribution.map((d) => {
              const ratio = stats.totalDocuments > 0 ? (d.count / stats.totalDocuments) * 100 : 0;
              return (
                <div key={d.domainCode} className="flex items-center gap-2">
                  <div className="h-2 rounded-full bg-[var(--color-primary-500)]" style={{ width: `${Math.max(ratio, 4)}px` }} />
                  <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
                    {d.domainName}
                  </span>
                  <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                    {d.count}건 ({ratio.toFixed(1)}%)
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <Dropdown
          options={domainOptions}
          value={domainFilter}
          onChange={(v) => {
            setDomainFilter(v);
            setPage(1);
          }}
          placeholder="전체 도메인"
          className="w-48"
        />
        <Dropdown
          options={STATUS_OPTIONS}
          value={statusFilter}
          onChange={(v) => {
            setStatusFilter(v);
            setPage(1);
          }}
          placeholder="전체 상태"
          className="w-40"
        />
      </div>

      {/* Document Table */}
      <div className="rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)]">
        {isLoading ? (
          <div className="space-y-3 p-4">
            {[1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} height="48px" />
            ))}
          </div>
        ) : error ? (
          <div className="flex flex-col items-center gap-3 py-12">
            <p className="text-[var(--color-error)]">{error}</p>
            <Button variant="secondary" onClick={loadDocuments}>
              재시도
            </Button>
          </div>
        ) : (
          <>
            <DocumentTable
              documents={documents}
              onReindex={handleReindexRequest}
              reindexingId={reindexingId}
            />
            {totalPages > 1 && (
              <div className="flex items-center justify-between border-t border-[var(--color-neutral-200)] px-4 py-3">
                <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                  {total}개 중 {(page - 1) * PAGE_SIZE + 1}-{Math.min(page * PAGE_SIZE, total)}
                </span>
                <div className="flex gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={page <= 1}
                    onClick={() => setPage((p) => p - 1)}
                    aria-label="이전 페이지"
                  >
                    이전
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={page >= totalPages}
                    onClick={() => setPage((p) => p + 1)}
                    aria-label="다음 페이지"
                  >
                    다음
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <ConfirmDialog
        isOpen={!!confirmTarget}
        title="문서 재인덱싱"
        message="이 문서를 재인덱싱하시겠습니까? 기존 청크가 새로 생성됩니다."
        onConfirm={handleReindexConfirm}
        onCancel={() => setConfirmTarget(null)}
        variant="default"
        confirmLabel="재인덱싱"
        cancelLabel="취소"
      />
    </div>
  );
}
