'use client';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import type { UsageData } from '@/lib/api/bff-dashboard';

interface UsageChartProps {
  data: UsageData | null;
  isLoading: boolean;
}

export function UsageChart({ data, isLoading }: UsageChartProps) {
  if (isLoading || !data) {
    return <Skeleton height="300px" />;
  }

  const maxCount = Math.max(...data.data.map((d) => d.count), 1);

  return (
    <Card className="p-4">
      <h3 className="mb-4 text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
        Profile별 사용량
      </h3>
      {data.data.length === 0 ? (
        <div className="flex h-48 items-center justify-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
          데이터가 없습니다
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {data.data.map((item) => (
            <div key={item.profileId} className="flex items-center gap-3">
              <span className="w-32 truncate text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
                {item.profileName}
              </span>
              <div className="flex-1">
                <div
                  className="h-6 rounded-[var(--radius-sm)] bg-[var(--color-primary-500)] transition-all"
                  style={{ width: `${(item.count / maxCount) * 100}%` }}
                />
              </div>
              <span className="w-12 text-right text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-800)]">
                {item.count}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
