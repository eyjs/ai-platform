'use client';

import { useState } from 'react';
import { Card } from '@/components/ui/card';
import { DataTable, type Column } from '@/components/ui/data-table';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchLogs, type LogsData } from '@/lib/api/bff-dashboard';

interface ConversationLogTableProps {
  initialData: LogsData | null;
  isLoading: boolean;
}

interface LogRow {
  sessionId: string;
  profileId: string;
  profileName: string;
  questionPreview: string;
  responseTimeMs: number;
  timestamp: string;
  [key: string]: unknown;
}

const columns: Column<LogRow>[] = [
  {
    key: 'sessionId',
    header: '세션 ID',
    width: '120px',
    render: (row: LogRow) => (
      <span className="font-mono text-[var(--font-size-xs)]">
        {row.sessionId.slice(0, 8)}...
      </span>
    ),
  },
  { key: 'profileName', header: 'Profile' },
  {
    key: 'questionPreview',
    header: '질문',
    render: (row: LogRow) => (
      <span className="truncate max-w-[200px] block">
        {row.questionPreview || '-'}
      </span>
    ),
  },
  {
    key: 'responseTimeMs',
    header: '응답 시간',
    sortable: true,
    render: (row: LogRow) =>
      row.responseTimeMs > 0 ? `${row.responseTimeMs}ms` : '-',
  },
  {
    key: 'timestamp',
    header: '시각',
    sortable: true,
    render: (row: LogRow) =>
      new Date(row.timestamp).toLocaleString('ko-KR'),
  },
];

export function ConversationLogTable({ initialData, isLoading }: ConversationLogTableProps) {
  const [data, setData] = useState<LogsData | null>(initialData);
  const [currentPage, setCurrentPage] = useState(1);

  const handlePageChange = async (page: number) => {
    setCurrentPage(page);
    try {
      const result = await fetchLogs(page, 10);
      setData(result);
    } catch {
      // 에러 시 기존 데이터 유지
    }
  };

  if (isLoading || !data) {
    return <Skeleton height="400px" />;
  }

  return (
    <Card className="overflow-hidden">
      <div className="px-4 pt-4">
        <h3 className="text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
          최근 대화 로그
        </h3>
      </div>
      <DataTable
        columns={columns}
        data={data.data as unknown as LogRow[]}
        pagination
        pageSize={10}
        totalItems={data.total}
        currentPage={currentPage}
        onPageChange={handlePageChange}
        emptyMessage="대화 로그가 없습니다"
      />
    </Card>
  );
}
