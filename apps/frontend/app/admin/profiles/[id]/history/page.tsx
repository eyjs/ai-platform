'use client';

import { use, useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { YamlDiffViewer } from '@/components/admin/yaml-diff-viewer';
import { RollbackConfirmModal } from '@/components/admin/profile-editor/rollback-confirm-modal';
import { useToast } from '@/components/ui/toast';
import { fetchProfileHistory, restoreProfile } from '@/lib/api/bff-profiles';
import type { ProfileHistoryItem } from '@/types/profile';

interface ProfileHistoryPageProps {
  params: Promise<{ id: string }>;
}

export default function ProfileHistoryPage({ params }: ProfileHistoryPageProps) {
  const { id } = use(params);
  const router = useRouter();
  const { toast } = useToast();

  const [history, setHistory] = useState<ProfileHistoryItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const [compareHistoryId, setCompareHistoryId] = useState<string | null>(null);
  const [rollbackHistoryId, setRollbackHistoryId] = useState<string | null>(null);
  const [isRestoring, setIsRestoring] = useState(false);

  const selectedItem = selectedHistoryId
    ? history.find(h => h.id === selectedHistoryId)
    : null;

  const compareItem = compareHistoryId
    ? history.find(h => h.id === compareHistoryId)
    : null;

  useEffect(() => {
    setIsLoading(true);
    fetchProfileHistory(id)
      .then((data) => {
        setHistory(data);
        // Auto-select the latest (first) item
        if (data.length > 0) {
          setSelectedHistoryId(data[0].id);
          // Auto-select second item for comparison if available
          if (data.length > 1) {
            setCompareHistoryId(data[1].id);
          }
        }
      })
      .catch(() => {
        toast('히스토리를 불러오는데 실패했습니다', 'error');
        setHistory([]);
      })
      .finally(() => setIsLoading(false));
  }, [id, toast]);

  const handleRestore = async () => {
    if (!rollbackHistoryId) return;

    setIsRestoring(true);
    try {
      await restoreProfile(id, rollbackHistoryId);
      toast('버전이 복원되었습니다', 'success');
      // Refresh history
      const updatedHistory = await fetchProfileHistory(id);
      setHistory(updatedHistory);
      setRollbackHistoryId(null);
    } catch (err) {
      toast(err instanceof Error ? err.message : '복원 실패', 'error');
    } finally {
      setIsRestoring(false);
    }
  };

  const getChangeTypeBadgeVariant = (changeType: string) => {
    switch (changeType) {
      case 'create':
        return 'success' as const;
      case 'restore':
        return 'warning' as const;
      default:
        return 'neutral' as const;
    }
  };

  const getChangeTypeLabel = (changeType: string) => {
    switch (changeType) {
      case 'create':
        return '생성';
      case 'restore':
        return '복원';
      default:
        return '수정';
    }
  };

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <span className="text-[var(--color-neutral-400)]">히스토리 로딩 중...</span>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      {/* Header */}
      <div className="border-b border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-[var(--spacing-4)] py-[var(--spacing-3)]">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href={`/admin/profiles/${id}`}
              className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-md)] text-[var(--color-neutral-500)] hover:bg-[var(--color-neutral-100)]"
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </Link>
            <h1 className="text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]">
              Profile 변경 이력
            </h1>
          </div>
          <Link href="/admin/profiles">
            <Button variant="ghost" size="sm">
              목록으로
            </Button>
          </Link>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* History Timeline */}
        <div className="w-80 border-r border-[var(--color-neutral-200)] bg-[var(--surface-card)] overflow-y-auto">
          <div className="p-[var(--spacing-4)]">
            <h3 className="text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)] mb-[var(--spacing-3)]">
              타임라인
            </h3>
            {history.length === 0 ? (
              <p className="text-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
                변경 이력이 없습니다
              </p>
            ) : (
              <div className="space-y-[var(--spacing-2)]">
                {history.map((item, index) => (
                  <div
                    key={item.id}
                    className={`rounded-[var(--radius-md)] border p-[var(--spacing-3)] cursor-pointer transition-colors ${
                      selectedHistoryId === item.id
                        ? 'border-[var(--color-primary-300)] bg-[var(--color-primary-25)]'
                        : 'border-[var(--color-neutral-200)] hover:bg-[var(--color-neutral-50)]'
                    }`}
                    onClick={() => {
                      setSelectedHistoryId(item.id);
                      // Auto-select previous item for comparison
                      if (index + 1 < history.length) {
                        setCompareHistoryId(history[index + 1].id);
                      } else if (index > 0) {
                        setCompareHistoryId(history[index - 1].id);
                      } else {
                        setCompareHistoryId(null);
                      }
                    }}
                  >
                    <div className="flex items-center justify-between mb-[var(--spacing-1)]">
                      <div className="flex items-center gap-2">
                        <Badge
                          variant={getChangeTypeBadgeVariant(item.changeType)}
                          size="sm"
                        >
                          {getChangeTypeLabel(item.changeType)}
                        </Badge>
                        <span className="text-[var(--font-size-sm)] font-semibold text-[var(--color-neutral-700)]">
                          v{item.version}
                        </span>
                      </div>
                    </div>

                    <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)] mb-[var(--spacing-1)]">
                      {new Date(item.changedAt).toLocaleString('ko-KR')}
                    </p>

                    <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)] mb-[var(--spacing-2)]">
                      {item.changedBy}
                    </p>

                    {item.comment && (
                      <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-600)] mb-[var(--spacing-2)]">
                        {item.comment}
                      </p>
                    )}

                    <div className="flex gap-[var(--spacing-1)]">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={(e) => {
                          e.stopPropagation();
                          setCompareHistoryId(item.id);
                        }}
                        className="text-[10px] h-6 px-2"
                      >
                        비교
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          setRollbackHistoryId(item.id);
                        }}
                        className="text-[10px] h-6 px-2"
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

        {/* Diff Viewer */}
        <div className="flex-1 overflow-hidden">
          {selectedItem && compareItem ? (
            <div className="h-full p-[var(--spacing-4)]">
              <div className="flex items-center justify-between mb-[var(--spacing-3)]">
                <h3 className="text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
                  버전 비교 (v{compareItem.version} → v{selectedItem.version})
                </h3>
              </div>
              <YamlDiffViewer
                previousYaml={compareItem.yamlContent}
                currentYaml={selectedItem.yamlContent}
                className="h-[calc(100%-60px)]"
              />
            </div>
          ) : selectedItem ? (
            <div className="h-full p-[var(--spacing-4)]">
              <div className="flex items-center justify-between mb-[var(--spacing-3)]">
                <h3 className="text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
                  버전 v{selectedItem.version} 내용
                </h3>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => setRollbackHistoryId(selectedItem.id)}
                >
                  이 버전으로 복원
                </Button>
              </div>
              <div className="border border-[var(--color-neutral-200)] rounded-[var(--radius-md)] h-[calc(100%-60px)] overflow-hidden">
                <div className="bg-[var(--color-neutral-50)] px-3 py-2 border-b border-[var(--color-neutral-200)]">
                  <h4 className="text-[var(--font-size-sm)] font-semibold text-[var(--color-neutral-700)]">
                    YAML 내용
                  </h4>
                </div>
                <div className="h-[calc(100%-44px)] overflow-y-auto font-mono text-[var(--font-size-sm)] p-[var(--spacing-3)] bg-[var(--surface-card)]">
                  <pre className="whitespace-pre-wrap break-all">
                    {selectedItem.yamlContent}
                  </pre>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
              히스토리 항목을 선택하세요
            </div>
          )}
        </div>
      </div>

      {/* Rollback Confirmation Modal */}
      {rollbackHistoryId && (
        <RollbackConfirmModal
          historyId={rollbackHistoryId}
          version={history.find(h => h.id === rollbackHistoryId)?.version}
          onConfirm={handleRestore}
          onCancel={() => setRollbackHistoryId(null)}
        />
      )}
    </div>
  );
}