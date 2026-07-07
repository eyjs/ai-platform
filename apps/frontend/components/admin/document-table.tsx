'use client';

import Link from 'next/link';
import { cn } from '@/lib/cn';
import { Badge } from '@/components/ui/badge';
import type { KnowledgeDocument } from '@/lib/api/bff-knowledge';

export interface DocumentTableProps {
  documents: KnowledgeDocument[];
  className?: string;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

export function DocumentTable({ documents, className }: DocumentTableProps) {
  if (documents.length === 0) {
    return <div className="py-12 text-center text-[var(--color-neutral-400)]">문서가 없습니다</div>;
  }

  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="w-full text-[var(--font-size-sm)]">
        <thead>
          <tr className="border-b border-[var(--color-neutral-200)]">
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">제목</th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">파일명</th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">도메인</th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">보안등급</th>
            <th className="px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]">생성 일자</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr
              key={doc.id}
              className="border-b border-[var(--color-neutral-100)] transition-colors hover:bg-[var(--color-neutral-50)]"
            >
              <td className="px-4 py-3">
                <Link
                  href={`/admin/knowledge/${doc.id}`}
                  className="rounded-[var(--radius-sm)] font-medium text-[var(--color-primary-600)] hover:underline focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-500)]"
                >
                  {doc.title}
                </Link>
              </td>
              <td className="px-4 py-3 text-[var(--color-neutral-700)]">{doc.fileName ?? '-'}</td>
              <td className="px-4 py-3">
                {doc.domainCode ? <Badge variant="secondary">{doc.domainCode}</Badge> : '-'}
              </td>
              <td className="px-4 py-3 text-[var(--color-neutral-700)]">{doc.securityLevel ?? '-'}</td>
              <td className="px-4 py-3 text-[var(--color-neutral-500)]">{formatDate(doc.createdAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
