const ACCESS_TOKEN_KEY = 'aip-access-token';
const REFRESH_TOKEN_KEY = 'aip-refresh-token';
const AUTH_MARKER_COOKIE = 'aip_authenticated';
const AUTH_MARKER_MAX_AGE_SECONDS = 60 * 60 * 24; // 1일

/**
 * Access Token — localStorage 저장
 * 지인 소수 대상 + 비용 시스템 아님 → localStorage 채택 (사용자 결정)
 */
export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function setAccessToken(token: string): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(ACCESS_TOKEN_KEY, token);
}

export function clearAccessToken(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(ACCESS_TOKEN_KEY);
}

/** Refresh Token — localStorage 저장 */
export function getRefreshToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setRefreshToken(token: string): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(REFRESH_TOKEN_KEY, token);
}

export function clearRefreshToken(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

/**
 * 인증 마커 쿠키 — middleware 가 읽는 존재 플래그
 * 비밀 정보 아님. 실제 JWT 는 localStorage 에만 존재
 */
export function setAuthMarkerCookie(): void {
  if (typeof document === 'undefined') return;
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `${AUTH_MARKER_COOKIE}=1; Max-Age=${AUTH_MARKER_MAX_AGE_SECONDS}; Path=/; SameSite=Lax${secure}`;
}

export function clearAuthMarkerCookie(): void {
  if (typeof document === 'undefined') return;
  document.cookie = `${AUTH_MARKER_COOKIE}=; Max-Age=0; Path=/; SameSite=Lax`;
}

/** 모든 토큰 + 마커 쿠키 삭제 */
export function clearAllTokens(): void {
  clearAccessToken();
  clearRefreshToken();
  clearAuthMarkerCookie();
}
