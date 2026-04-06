'use client';

import { useState, useMemo } from 'react';
import { cn } from '@/lib/cn';
import { Button } from './button';

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => React.ReactNode;
  sortable?: boolean;
  width?: string;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  sortable?: boolean;
  pagination?: boolean;
  pageSize?: number;
  totalItems?: number;
  currentPage?: number;
  onPageChange?: (page: number) => void;
  onSort?: (key: string, direction: 'asc' | 'desc') => void;
  emptyMessage?: string;
  className?: string;
}

export function DataTable<T extends Record<string, unknown>>({
  columns,
  data,
  sortable = false,
  pagination = false,
  pageSize = 10,
  totalItems,
  currentPage = 1,
  onPageChange,
  onSort,
  emptyMessage = '데이터가 없습니다',
  className,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

  const handleSort = (key: string) => {
    if (!sortable) return;
    const newDirection =
      sortKey === key && sortDirection === 'asc' ? 'desc' : 'asc';
    setSortKey(key);
    setSortDirection(newDirection);
    onSort?.(key, newDirection);
  };

  const sortedData = useMemo(() => {
    if (!sortKey || onSort) return data;
    return [...data].sort((a, b) => {
      const aVal = a[sortKey];
      const bVal = b[sortKey];
      if (aVal == null) return 1;
      if (bVal == null) return -1;
      const comparison = aVal < bVal ? -1 : aVal > bVal ? 1 : 0;
      return sortDirection === 'asc' ? comparison : -comparison;
    });
  }, [data, sortKey, sortDirection, onSort]);

  const total = totalItems ?? data.length;
  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="w-full text-[var(--font-size-sm)]">
        <thead>
          <tr className="border-b border-[var(--color-neutral-200)]">
            {columns.map((col) => (
              <th
                key={col.key}
                onClick={() =>
                  (sortable || col.sortable) && handleSort(col.key)
                }
                className={cn(
                  'px-4 py-3 text-left font-medium text-[var(--color-neutral-600)]',
                  (sortable || col.sortable) &&
                    'cursor-pointer hover:text-[var(--color-neutral-900)]',
                )}
                style={col.width ? { width: col.width } : undefined}
              >
                <span className="inline-flex items-center gap-1">
                  {col.header}
                  {sortKey === col.key && (
                    <span className="text-[var(--color-primary-500)]">
                      {sortDirection === 'asc' ? '\u2191' : '\u2193'}
                    </span>
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedData.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-4 py-12 text-center text-[var(--color-neutral-400)]"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            sortedData.map((row, rowIndex) => (
              <tr
                key={rowIndex}
                className="border-b border-[var(--color-neutral-100)] hover:bg-[var(--color-neutral-50)] transition-colors"
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className="px-4 py-3 text-[var(--color-neutral-800)]"
                  >
                    {col.render
                      ? col.render(row)
                      : (row[col.key] as React.ReactNode)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
      {pagination && totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-[var(--color-neutral-200)] px-4 py-3">
          <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
            {total}개 중 {(currentPage - 1) * pageSize + 1}-
            {Math.min(currentPage * pageSize, total)}
          </span>
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              disabled={currentPage <= 1}
              onClick={() => onPageChange?.(currentPage - 1)}
            >
              이전
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={currentPage >= totalPages}
              onClick={() => onPageChange?.(currentPage + 1)}
            >
              다음
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
