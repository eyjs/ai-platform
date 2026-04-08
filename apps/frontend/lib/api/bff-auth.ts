import type { TokenResponse, CurrentUser } from '@/types/auth';

/**
 * BFF base URL.
 * 요구사항: NEXT_PUBLIC_BFF_URL 는 `/bff` 를 포함한 베이스 (예: http://localhost:4000/bff).
 * 내부에서는 `/auth/...` 를 붙여서 호출한다 (이중 `/bff` 방지).
 */
const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:4000/bff';

export async function login(
  email: string,
  password: string,
): Promise<TokenResponse> {
  const res = await fetch(`${BFF_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: '로그인 실패' }));
    throw new Error(error.message || '이메일 또는 비밀번호가 올바르지 않습니다');
  }
  return res.json();
}

export async function refreshToken(
  token: string,
): Promise<TokenResponse> {
  const res = await fetch(`${BFF_URL}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refreshToken: token }),
  });
  if (!res.ok) throw new Error('토��� 갱신 실패');
  return res.json();
}

export async function getMe(accessToken: string): Promise<CurrentUser> {
  const res = await fetch(`${BFF_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error('사용자 정보 조회 실패');
  return res.json();
}
