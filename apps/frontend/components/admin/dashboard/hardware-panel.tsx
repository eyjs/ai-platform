'use client';

import { useEffect, useRef, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { usePolling } from '@/hooks/use-polling';
import { fetchHardware, type HardwareMetrics } from '@/lib/api/hardware';

/** 사용률(%) 임계값 → 게이지 색상 */
function pctColor(pct: number | null | undefined): string {
  if (pct == null) return 'var(--color-neutral-300)';
  if (pct < 60) return 'var(--color-success)';
  if (pct <= 85) return 'var(--color-warning)';
  return 'var(--color-error)';
}

const HISTORY_LEN = 40; // 롤링 그래프 보관 샘플 수 (≈ 4s × 40 = 160s)

interface Sample {
  cpu: number;
  gpuGb: number;
}

/** 게이지 바 — 라벨 + 값 + 임계값 색상 */
function Gauge({ label, pct, detail }: { label: string; pct: number | null; detail: string }) {
  const width = pct == null ? 0 : Math.min(Math.max(pct, 0), 100);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between">
        <span className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]">
          {label}
        </span>
        <span className="font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
          {detail}
        </span>
      </div>
      <div className="h-2.5 w-full overflow-hidden rounded-[var(--radius-full)] bg-[var(--color-neutral-100)]">
        <div
          className="h-full rounded-[var(--radius-full)] transition-all duration-[var(--duration-normal)]"
          style={{ width: `${width}%`, backgroundColor: pctColor(pct) }}
          role="img"
          aria-label={`${label} ${pct == null ? '알 수 없음' : `${pct.toFixed(1)}%`}`}
        />
      </div>
    </div>
  );
}

/** SVG sparkline — 0~max 정규화 롤링 라인 */
function Sparkline({ values, max, color, label }: { values: number[]; max: number; color: string; label: string }) {
  if (values.length < 2) {
    return (
      <div className="flex h-16 items-center justify-center text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
        샘플 수집 중…
      </div>
    );
  }
  const safeMax = max <= 0 ? 1 : max;
  const step = 100 / (HISTORY_LEN - 1);
  const points = values
    .map((v, i) => {
      const x = i * step;
      const y = 100 - Math.min(Math.max(v / safeMax, 0), 1) * 100;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');
  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      className="h-16 w-full"
      role="img"
      aria-label={label}
    >
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

function statusVariant(status: string): 'success' | 'error' | 'warning' {
  if (status === 'healthy' || status === 'ok') return 'success';
  if (status === 'unreachable') return 'error';
  return 'warning';
}

export function HardwarePanel() {
  const { data, error, isLoading } = usePolling<HardwareMetrics>({
    fetchFn: fetchHardware,
    interval: 4000,
  });

  const [history, setHistory] = useState<Sample[]>([]);
  const lastRef = useRef<HardwareMetrics | null>(null);

  useEffect(() => {
    if (!data || data === lastRef.current) return;
    lastRef.current = data;
    const cpu = data.host.cpu_pct ?? 0;
    const gpuGb = data.gpu_total_mb / 1024;
    setHistory((prev) => [...prev, { cpu, gpuGb }].slice(-HISTORY_LEN));
  }, [data]);

  if (isLoading && !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>하드웨어 · GPU 모니터링</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton height="220px" />
        </CardContent>
      </Card>
    );
  }

  if (error && !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>하드웨어 · GPU 모니터링</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="py-8 text-center text-[var(--font-size-sm)] text-[var(--color-error)]">
            {error.message}
          </p>
        </CardContent>
      </Card>
    );
  }

  if (!data) return null;

  const { host, servers, gpu_total_mb } = data;
  const gpuTotalGb = gpu_total_mb / 1024;
  const cpuValues = history.map((h) => h.cpu);
  const gpuValues = history.map((h) => h.gpuGb);
  const gpuMax = Math.max(...gpuValues, 1);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>하드웨어 · GPU 모니터링</CardTitle>
          <span className="flex items-center gap-1.5 text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
            <span className="h-1.5 w-1.5 animate-pulse rounded-[var(--radius-full)] bg-[var(--color-success)]" />
            실시간 · 4초
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-6">
          {/* 호스트 게이지 + 총 GPU */}
          <div className="grid gap-5 md:grid-cols-3">
            <Gauge
              label="호스트 CPU"
              pct={host.cpu_pct}
              detail={host.cpu_pct == null ? '-' : `${host.cpu_pct.toFixed(1)}%`}
            />
            <Gauge
              label="호스트 메모리"
              pct={host.mem_pct}
              detail={
                host.mem_used_gb == null || host.mem_total_gb == null
                  ? '-'
                  : `${host.mem_used_gb.toFixed(1)} / ${host.mem_total_gb.toFixed(1)} GB`
              }
            />
            <div className="flex flex-col justify-center gap-1 rounded-[var(--radius-md)] bg-[var(--color-neutral-50)] px-4 py-2">
              <span className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]">
                GPU 사용량 (합계)
              </span>
              <span className="font-mono text-[var(--font-size-xl)] font-bold text-[var(--color-primary-600)]">
                {gpuTotalGb.toFixed(1)} <span className="text-[var(--font-size-sm)] font-normal">GB</span>
              </span>
            </div>
          </div>

          {/* 롤링 그래프 */}
          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] p-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-600)]">
                  CPU 추이 (%)
                </span>
                <span className="font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
                  0–100
                </span>
              </div>
              <Sparkline values={cpuValues} max={100} color="var(--color-primary-500)" label="CPU 사용률 추이" />
            </div>
            <div className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] p-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-600)]">
                  GPU 추이 (GB)
                </span>
                <span className="font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
                  0–{gpuMax.toFixed(1)}
                </span>
              </div>
              <Sparkline values={gpuValues} max={gpuMax} color="var(--color-success)" label="GPU 사용량 추이" />
            </div>
          </div>

          {/* 서버별 GPU */}
          <div className="overflow-x-auto">
            <table className="w-full text-[var(--font-size-sm)]">
              <thead>
                <tr className="border-b border-[var(--color-neutral-200)]">
                  <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">서버</th>
                  <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">모델</th>
                  <th className="px-3 py-2 text-left font-medium text-[var(--color-neutral-600)]">상태</th>
                  <th className="px-3 py-2 text-right font-medium text-[var(--color-neutral-600)]">GPU 메모리</th>
                </tr>
              </thead>
              <tbody>
                {servers.map((s) => (
                  <tr
                    key={s.url}
                    className="border-b border-[var(--color-neutral-100)] transition-colors hover:bg-[var(--color-neutral-50)]"
                  >
                    <td className="px-3 py-2 font-medium text-[var(--color-neutral-800)]">{s.name}</td>
                    <td className="px-3 py-2 text-[var(--color-neutral-500)]">
                      <span className="block max-w-[220px] truncate font-mono text-[var(--font-size-xs)]">
                        {s.model || '-'}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <Badge variant={statusVariant(s.status)}>{s.status}</Badge>
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-700)]">
                      {s.gpu_active_mb == null ? '-' : `${(s.gpu_active_mb / 1024).toFixed(2)} GB`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
