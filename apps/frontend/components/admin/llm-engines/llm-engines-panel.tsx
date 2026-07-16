'use client';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useLlmEngines } from '@/hooks/use-llm-engines';
import { DgxModelTable } from './dgx-model-table';
import { DgxStatusHeadline } from './dgx-status-headline';
import { MlxEngineTable } from './mlx-engine-table';
import { RoleOverrideList } from './role-override-list';

const TAB_DGX = 'dgx';
const TAB_MLX = 'mlx';

function LoadingState() {
  return (
    <div className="space-y-6">
      <Skeleton height="140px" />
      <Skeleton height="40px" />
      <Skeleton height="280px" />
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-[var(--radius-lg)] border border-[var(--color-error)] bg-[var(--color-error-light)] py-12"
    >
      <p className="text-[var(--font-size-base)] font-medium text-[var(--color-error)]">
        {message}
      </p>
      <Button variant="secondary" onClick={onRetry} aria-label="LLM 엔진 상태 다시 불러오기">
        재시도
      </Button>
    </div>
  );
}

/**
 * LLM 서빙 대시보드 본체.
 * - 헤드라인: DGX가 지금 붙어 있나
 * - 탭: DGX Spark(기본) / 호스트 MLX 엔진
 */
export function LlmEnginesPanel() {
  const { data, error, isLoading, refresh } = useLlmEngines();

  // 최초 로딩(데이터 없음)
  if (isLoading && !data) return <LoadingState />;

  // 한 번도 성공하지 못한 경우에만 전체 에러 화면
  if (error && !data) {
    return <ErrorState message={error.message} onRetry={refresh} />;
  }

  if (!data) {
    return <ErrorState message="LLM 엔진 상태를 불러올 수 없습니다" onRetry={refresh} />;
  }

  return (
    <div className="space-y-6">
      {/* 폴링이 실패해도 직전 스냅샷은 남는다 → 최신인 척하지 않게 알린다. */}
      {error && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border border-[var(--color-warning)] bg-[var(--color-warning-light)] px-4 py-2"
        >
          <p className="text-[var(--font-size-sm)] text-[var(--color-warning)]">
            갱신 실패 — 아래는 마지막으로 확인된 상태입니다 ({error.message})
          </p>
          <Button
            variant="ghost"
            size="sm"
            onClick={refresh}
            aria-label="LLM 엔진 상태 다시 불러오기"
          >
            재시도
          </Button>
        </div>
      )}

      <DgxStatusHeadline health={data} />

      <Card>
        <CardHeader>
          <CardTitle>서빙 모델</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue={TAB_DGX}>
            <TabsList>
              <TabsTrigger value={TAB_DGX}>
                DGX Spark ({data.dgx.models.length})
              </TabsTrigger>
              <TabsTrigger value={TAB_MLX}>
                호스트 MLX 엔진 ({data.mlx.engines.length})
              </TabsTrigger>
            </TabsList>

            <TabsContent value={TAB_DGX} className="space-y-4">
              <DgxModelTable dgx={data.dgx} />
              <RoleOverrideList
                roleOverrides={data.dgx.roleOverrides}
                defaultModel={data.dgx.defaultModel}
              />
            </TabsContent>

            <TabsContent value={TAB_MLX} className="space-y-3">
              <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
                호스트에서 직접 도는 MLX 서버입니다.
                {data.providerMode === 'development' && data.fallbackEnabled
                  ? ' 현재 설정에서는 DGX 장애 시 이 엔진들이 폴백을 받습니다.'
                  : ''}
              </p>
              <MlxEngineTable mlx={data.mlx} />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
