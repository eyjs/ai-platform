'use client';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import type { LatencyData } from '@/lib/api/bff-dashboard';

interface LatencyChartProps {
  data: LatencyData | null;
  isLoading: boolean;
}

export function LatencyChart({ data, isLoading }: LatencyChartProps) {
  if (isLoading || !data) {
    return <Skeleton height="300px" />;
  }

  return (
    <Card className="p-4">
      <h3 className="mb-4 text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
        응답 레이턴시
      </h3>
      {data.data.length === 0 ? (
        <div className="flex h-48 items-center justify-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
          레이턴시 데이터가 아직 없습니다
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex gap-4 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
            <span className="flex items-center gap-1">
              <span className="h-2 w-4 rounded bg-[var(--color-primary-500)]" /> p50
            </span>
            <span className="flex items-center gap-1">
              <span className="h-2 w-4 rounded border border-dashed border-[var(--color-primary-400)]" /> p95
            </span>
          </div>
          {data.data.map((item, i) => (
            <div key={i} className="flex items-center gap-3 text-[var(--font-size-xs)]">
              <span className="w-20 text-[var(--color-neutral-500)]">
                {new Date(item.timestamp).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}
              </span>
              <span className="text-[var(--color-neutral-700)]">p50: {item.p50}ms</span>
              <span className="text-[var(--color-neutral-500)]">p95: {item.p95}ms</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
