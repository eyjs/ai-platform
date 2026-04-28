'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { fetchRequestLogDetail, type RequestLogDetail } from '@/lib/api/admin';

const statusVariant: Record<string, 'success' | 'error' | 'warning'> = {
  success: 'success',
  error: 'error',
  timeout: 'warning',
};

const statusLabel: Record<string, string> = {
  success: '성공',
  error: '오류',
  timeout: '타임아웃',
};

function latencyColor(ms: number): string {
  if (ms < 500) return 'var(--color-success)';
  if (ms <= 2000) return 'var(--color-warning)';
  return 'var(--color-error)';
}

function LatencyBar({ label, value, total }: { label: string; value: number; total: number }) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <span className="w-16 shrink-0 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
        {label}
      </span>
      <div className="flex-1 overflow-hidden rounded-full bg-[var(--color-neutral-200)]" style={{ height: '8px' }}>
        <div
          className="h-full rounded-full transition-[width] duration-[var(--duration-normal)]"
          style={{
            width: `${pct}%`,
            backgroundColor: latencyColor(value),
          }}
        />
      </div>
      <span
        className="w-16 shrink-0 text-right font-mono text-[var(--font-size-xs)] font-medium"
        style={{ color: latencyColor(value) }}
      >
        {value}ms
      </span>
    </div>
  );
}

export default function RequestLogDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;

  const [detail, setDetail] = useState<RequestLogDetail | null>(null);
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
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '로그를 불러올 수 없습니다');
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => { cancelled = true; };
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
        <p className="text-[var(--font-size-base)] text-[var(--color-error)]">
          {error || '로그를 찾을 수 없습니다'}
        </p>
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
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push('/admin/request-logs')}
            aria-label="목록으로 돌아가기"
          >
            ← 목록
          </Button>
          <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
            요청 상세
          </h1>
          <Badge variant={statusVariant[detail.status] ?? 'neutral'}>
            {statusLabel[detail.status] ?? detail.status}
          </Badge>
        </div>
        <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
          {new Date(detail.timestamp).toLocaleString('ko-KR')}
        </span>
      </div>

      {/* 요약 정보 */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card variant="section">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">ID</p>
          <p className="mt-1 font-mono text-[var(--font-size-sm)] text-[var(--color-neutral-800)]">
            {detail.id}
          </p>
        </Card>
        <Card variant="section">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">Profile</p>
          <p className="mt-1 text-[var(--font-size-sm)] text-[var(--color-neutral-800)]">
            {detail.profileName}
          </p>
        </Card>
        <Card variant="section">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">총 응답 시간</p>
          <p
            className="mt-1 font-mono text-[var(--font-size-sm)] font-medium"
            style={{ color: latencyColor(detail.latencyMs) }}
          >
            {detail.latencyMs}ms
          </p>
        </Card>
        <Card variant="section">
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">라우팅</p>
          <p className="mt-1 text-[var(--font-size-sm)] text-[var(--color-neutral-800)]">
            {detail.routing.selectedProvider} / {detail.routing.selectedModel}
          </p>
        </Card>
      </div>

      {/* Latency Breakdown */}
      <Card>
        <CardHeader>
          <CardTitle>응답 시간 분석</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <LatencyBar label="Routing" value={detail.latencyBreakdown.routingMs} total={detail.latencyBreakdown.totalMs} />
          <LatencyBar label="LLM" value={detail.latencyBreakdown.llmMs} total={detail.latencyBreakdown.totalMs} />
          <LatencyBar label="Tools" value={detail.latencyBreakdown.toolsMs} total={detail.latencyBreakdown.totalMs} />
        </CardContent>
      </Card>

      {/* Tool Calls */}
      {detail.toolCalls.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Tool 호출</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-[var(--font-size-sm)]">
                <thead>
                  <tr className="border-b border-[var(--color-neutral-200)]">
                    <th className="px-4 py-2 text-left font-medium text-[var(--color-neutral-600)]">Tool</th>
                    <th className="px-4 py-2 text-left font-medium text-[var(--color-neutral-600)]">소요 시간</th>
                    <th className="px-4 py-2 text-left font-medium text-[var(--color-neutral-600)]">상태</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.toolCalls.map((tool, idx) => (
                    <tr key={idx} className="border-b border-[var(--color-neutral-100)]">
                      <td className="px-4 py-2 font-mono text-[var(--color-neutral-800)]">{tool.name}</td>
                      <td className="px-4 py-2">
                        <span
                          className="font-mono font-medium"
                          style={{ color: latencyColor(tool.durationMs) }}
                        >
                          {tool.durationMs}ms
                        </span>
                      </td>
                      <td className="px-4 py-2">
                        <Badge variant={tool.status === 'success' ? 'success' : 'error'} size="sm">
                          {tool.status === 'success' ? '성공' : '오류'}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Request / Response */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Request</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2 text-[var(--font-size-sm)]">
              <Badge variant="primary" size="sm">{detail.request.method}</Badge>
              <span className="font-mono text-[var(--color-neutral-700)]">{detail.request.path}</span>
            </div>
            <pre className="mt-3 max-h-64 overflow-auto rounded-[var(--radius-md)] bg-[var(--color-neutral-100)] p-3 font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-700)]">
              {detail.request.body}
            </pre>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle>Response</CardTitle>
              <Badge
                variant={detail.response.statusCode < 400 ? 'success' : 'error'}
                size="sm"
              >
                {detail.response.statusCode}
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            <pre className="max-h-64 overflow-auto rounded-[var(--radius-md)] bg-[var(--color-neutral-100)] p-3 font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-700)]">
              {detail.response.body}
            </pre>
          </CardContent>
        </Card>
      </div>

      {/* Routing Decision */}
      <Card>
        <CardHeader>
          <CardTitle>라우팅 결정</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">Provider</dt>
              <dd className="mt-0.5 text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-800)]">
                {detail.routing.selectedProvider}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">Model</dt>
              <dd className="mt-0.5 text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-800)]">
                {detail.routing.selectedModel}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">사유</dt>
              <dd className="mt-0.5 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
                {detail.routing.reason}
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
