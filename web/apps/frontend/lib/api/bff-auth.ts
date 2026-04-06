import type { TokenResponse, CurrentUser } from '@/types/auth';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001';

export async function login(
  email: string,
  password: string,
): Promise<TokenResponse> {
  const res = await fetch(`${BFF_URL}/bff/auth/login`, {
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
  const res = await fetch(`${BFF_URL}/bff/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refreshToken: token }),
  });
  if (!res.ok) throw new Error('토��� 갱신 실패');
  return res.json();
}

export async function getMe(accessToken: string): Promise<CurrentUser> {
  const res = await fetch(`${BFF_URL}/bff/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error('사용자 정보 조회 실패');
  return res.json();
}
