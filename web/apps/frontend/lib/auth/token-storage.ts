const ACCESS_TOKEN_KEY = 'aip-access-token';
const REFRESH_TOKEN_KEY = 'aip-refresh-token';

/** Access Token — 메모리 저장 (보안상 localStorage보다 안전) */
let memoryAccessToken: string | null = null;

export function getAccessToken(): string | null {
  return memoryAccessToken;
}

export function setAccessToken(token: string): void {
  memoryAccessToken = token;
}

export function clearAccessToken(): void {
  memoryAccessToken = null;
}

/** Refresh Token — localStorage 저장 (세션 유지용) */
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

/** 모든 토큰 삭제 */
export function clearAllTokens(): void {
  clearAccessToken();
  clearRefreshToken();
}
