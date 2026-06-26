'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchRequestLogDetail, type RequestLogSummary } from '@/lib/api/admin';
import { formatDuration, latencyColor } from '@/lib/format';

function statusVariantOf(code: number): 'success' | 'error' | 'warning' {
  if (code >= 500) return 'error';
  if (code >= 400) return 'warning';
  return 'success';
}

export default function RequestLogDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;

  const [detail, setDetail] = useState<RequestLogSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchRequestLogDetail(id)
      .then((data) => {
        if (!cancelled) {
          setDetail(data);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '로그를 불러올 수 없습니다');
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton height="40px" width="300px" />
        <Skeleton height="200px" />
        <Skeleton height="300px" />
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="flex flex-col items-center gap-4 py-20">
        <p className="text-[var(--font-size-base)] text-[var(--color-error)]">{error || '로그를 찾을 수 없습니다'}</p>
        <Button variant="secondary" onClick={() => router.push('/admin/request-logs')}>
          목록으로 돌아가기
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => router.push('/admin/request-logs')} aria-label="목록으로 돌아가기">
            ← 목록
          </Button>
          <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">요청 상세</h1>
          <Badge variant={statusVariantOf(detail.statusCode)}>{detail.statusCode}</Badge>
        </div>
        <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
          {new Date(detail.ts).toLocaleString('ko-KR')}
        </span>
      </div>

      {/* 요약 정보 */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card variant="section">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">ID</p>
          <p className="mt-1 font-mono text-[var(--font-size-sm)] text-[var(--color-neutral-800)]">{detail.id}</p>
        </Card>
        <Card variant="section">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">Profile</p>
          <p className="mt-1 text-[var(--font-size-sm)] text-[var(--color-neutral-800)]">{detail.profileId ?? '-'}</p>
        </Card>
        <Card variant="section">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">응답 시간</p>
          <p className="mt-1 font-mono text-[var(--font-size-sm)] font-medium" style={{ color: latencyColor(detail.latencyMs) }}>
            {formatDuration(detail.latencyMs)}
          </p>
        </Card>
        <Card variant="section">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">토큰 (입력/출력)</p>
          <p className="mt-1 font-mono text-[var(--font-size-sm)] text-[var(--color-neutral-800)]">
            {detail.promptTokens} / {detail.completionTokens}
          </p>
        </Card>
      </div>

      <Card variant="section">
        <div className="flex flex-wrap gap-x-8 gap-y-2 text-[var(--font-size-sm)]">
          <span className="text-[var(--color-neutral-600)]">
            Provider: <span className="text-[var(--color-neutral-800)]">{detail.providerId ?? '-'}</span>
          </span>
          <span className="text-[var(--color-neutral-600)]">
            캐시: <span className="text-[var(--color-neutral-800)]">{detail.cacheHit ? 'HIT' : 'MISS'}</span>
          </span>
          {detail.errorCode && (
            <span className="text-[var(--color-error)]">에러: {detail.errorCode}</span>
          )}
        </div>
      </Card>

      {/* 질문 / 응답 */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>질문</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-[var(--radius-md)] bg-[var(--color-neutral-100)] p-3 font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-700)]">
              {detail.requestPreview || '(없음)'}
            </pre>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>응답</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-[var(--radius-md)] bg-[var(--color-neutral-100)] p-3 font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-700)]">
              {detail.responsePreview || '(없음)'}
            </pre>
          </CardContent>
        </Card>
      </div>

      <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
        ※ 레이어별 처리시간(라우팅/RAG/Guard/LLM) 분석은 다음 단계(요청 레이어 관측성)에서 추가됩니다.
      </p>
    </div>
  );
}
