import { getAccessToken } from '@/lib/auth/token-storage';

/**
 * FastAPI(apps/api) GET /api/health/hardware — 하드웨어/GPU 실시간 메트릭.
 * bff는 DB-direct라 라이브 메트릭에 부적합 → SSE 채팅처럼 api를 직접 호출한다.
 * ADMIN JWT 필수 (인프라 정보 노출 게이팅).
 */

const FASTAPI_URL =
  process.env.NEXT_PUBLIC_FASTAPI_URL || 'http://localhost:8000';

export interface HardwareServer {
  name: string;
  url: string;
  status: string;
  model?: string | null;
  gpu_active_mb?: number | null;
  host_cpu_pct?: number | null;
  host_mem_used_gb?: number | null;
  host_mem_total_gb?: number | null;
  host_mem_pct?: number | null;
  error?: string;
}

export interface HardwareHost {
  cpu_pct: number | null;
  mem_used_gb: number | null;
  mem_total_gb: number | null;
  mem_pct: number | null;
}

export interface HardwareMetrics {
  servers: HardwareServer[];
  host: HardwareHost;
  gpu_total_mb: number;
}

export async function fetchHardware(): Promise<HardwareMetrics> {
  const token = getAccessToken();
  const res = await fetch(`${FASTAPI_URL}/api/health/hardware`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new Error(
      res.status === 401 || res.status === 403
        ? '하드웨어 메트릭은 관리자만 조회할 수 있습니다'
        : `하드웨어 메트릭을 불러올 수 없습니다 (HTTP ${res.status})`,
    );
  }
  return (await res.json()) as HardwareMetrics;
}
