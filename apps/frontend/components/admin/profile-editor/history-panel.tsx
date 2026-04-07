'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchProfileHistory } from '@/lib/api/bff-profiles';
import type { ProfileHistoryItem } from '@/types/profile';

interface HistoryPanelProps {
  profileId: string;
  isOpen: boolean;
  onClose: () => void;
  onRestore: (historyId: string) => void;
}

export function HistoryPanel({
  profileId,
  isOpen,
  onClose,
  onRestore,
}: HistoryPanelProps) {
  const [history, setHistory] = useState<ProfileHistoryItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (isOpen && profileId) {
      setIsLoading(true);
      fetchProfileHistory(profileId)
        .then(setHistory)
        .catch(() => setHistory([]))
        .finally(() => setIsLoading(false));
    }
  }, [isOpen, profileId]);

  if (!isOpen) return null;

  return (
    <div className="fixed right-0 top-0 z-[var(--z-sidebar)] flex h-full w-80 flex-col border-l border-[var(--color-neutral-200)] bg-[var(--surface-card)] shadow-[var(--shadow-lg)]">
      <div className="flex items-center justify-between border-b border-[var(--color-neutral-200)] px-4 py-3">
        <h3 className="text-[var(--font-size-base)] font-semibold">변경 히스토리</h3>
        <button
          onClick={onClose}
          className="text-[var(--color-neutral-400)] hover:text-[var(--color-neutral-600)]"
        >
          x
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="flex flex-col gap-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} height="60px" />
            ))}
          </div>
        ) : history.length === 0 ? (
          <p className="text-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
            히스토리가 없습니다
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {history.map((item) => (
              <div
                key={item.id}
                className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] p-3"
              >
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                      {new Date(item.changedAt).toLocaleString('ko-KR')}
                    </p>
                    <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
                      {item.changedBy}
                    </p>
                    {item.comment && (
                      <p className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
                        {item.comment}
                      </p>
                    )}
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onRestore(item.id)}
                  >
                    복원
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
