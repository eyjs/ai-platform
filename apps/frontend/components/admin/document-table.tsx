'use client';

import Link from 'next/link';
import { cn } from '@/lib/cn';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { KnowledgeDocument } from '@/lib/api/bff-knowledge';

export interface DocumentTableProps {
  documents: KnowledgeDocument[];
  onReindex: (id: string) => void;
  reindexingId: string | null;
  className?: string;
}

const statusConfig = {
  indexed: { variant: 'success' as const, label: 'Indexed' },
  processing: { variant: 'warning' as const, label: 'Processing' },
  error: { variant: 'error' as const, label: 'Error' },
} as const;

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
}

export function DocumentTable({
  documents,
  onReindex,
  reindexingId,
  className,
}: DocumentTableProps) {
  if (documents.length === 0) {
    return (
      <div className="py-12 text-center text-[var(--color-neutral-400)]">
        문서가 없습니다
      </div>
    );
  }

  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="w-full text-[var(--font-size-sm)]">
        <thead>
          <tr className="border-b border-[var(--color-neutral-200)]">
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">
              제목
            </th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">
              도메인
            </th>
            <th className="px-4 py-3 text-right font-medium text-[var(--color-neutral-600)]">
              청크
            </th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">
              상태
            </th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">
              인덱싱 일자
            </th>
            <th className="px-4 py-3 text-right font-medium text-[var(--color-neutral-600)]">
              작업
            </th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => {
            const status = statusConfig[doc.status];
            return (
              <tr
                key={doc.id}
                className="border-b border-[var(--color-neutral-100)] transition-colors hover:bg-[var(--color-neutral-50)]"
              >
                <td className="px-4 py-3">
                  <Link
                    href={`/admin/knowledge/${doc.id}`}
                    className="font-medium text-[var(--color-primary-600)] hover:underline focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-500)] rounded-[var(--radius-sm)]"
                  >
                    {doc.title}
                  </Link>
                </td>
                <td className="px-4 py-3 text-[var(--color-neutral-700)]">
                  {doc.domainName}
                </td>
                <td className="px-4 py-3 text-right text-[var(--color-neutral-700)]">
                  {doc.chunkCount.toLocaleString()}
                </td>
                <td className="px-4 py-3">
                  <Badge variant={status.variant}>{status.label}</Badge>
                </td>
                <td className="px-4 py-3 text-[var(--color-neutral-500)]">
                  {formatDate(doc.indexedAt)}
                </td>
                <td className="px-4 py-3 text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onReindex(doc.id)}
                    loading={reindexingId === doc.id}
                    aria-label={`${doc.title} 재인덱싱`}
                  >
                    Reindex
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
