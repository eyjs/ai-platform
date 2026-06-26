/**
 * 공통 포맷 유틸 — 시간/숫자 표현을 한 곳에서.
 */

/**
 * 밀리초를 사람이 읽기 쉬운 시간으로. (1000ms→1s, 60000ms→1m, 90000ms→1m 30s)
 * - < 1초: `750ms`
 * - < 1분: `30.7s`
 * - >= 1분: `1m 30s` (초가 0이면 `1m`)
 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '-';
  if (ms < 1000) return `${Math.round(ms).toLocaleString()}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const totalSec = Math.round(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return s > 0 ? `${m.toLocaleString()}m ${s}s` : `${m.toLocaleString()}m`;
}

/** 천 단위 콤마. (30714 → "30,714") */
export function formatNumber(n: number | null | undefined): string {
  return n != null && Number.isFinite(n) ? n.toLocaleString() : '-';
}

/** 레이턴시 색상 — 디자인 토큰 CSS 변수 반환. */
export function latencyColor(ms: number): string {
  if (ms < 500) return 'var(--color-success)';
  if (ms <= 2000) return 'var(--color-warning)';
  return 'var(--color-error)';
}
