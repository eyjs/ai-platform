import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  getAccessToken,
  setAccessToken,
  clearAccessToken,
  getRefreshToken,
  setRefreshToken,
  clearRefreshToken,
  setAuthMarkerCookie,
  clearAuthMarkerCookie,
  clearAllTokens,
} from './token-storage';

const ACCESS_TOKEN_KEY = 'aip-access-token';
const REFRESH_TOKEN_KEY = 'aip-refresh-token';
const AUTH_MARKER_COOKIE = 'aip_authenticated';

beforeEach(() => {
  localStorage.clear();
  // document.cookie를 초기화 (jsdom은 쿠키를 누적하므로 max-age=0으로 제거)
  document.cookie = `${AUTH_MARKER_COOKIE}=; Max-Age=0; Path=/`;
});

describe('getAccessToken()', () => {
  it('localStorage에 토큰이 없으면 null을 반환한다', () => {
    expect(getAccessToken()).toBeNull();
  });

  it('저장된 토큰을 반환한다', () => {
    localStorage.setItem(ACCESS_TOKEN_KEY, 'my-access-token');
    expect(getAccessToken()).toBe('my-access-token');
  });
});

describe('setAccessToken()', () => {
  it('localStorage에 토큰을 저장한다', () => {
    setAccessToken('new-access-token');
    expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBe('new-access-token');
  });
});

describe('clearAccessToken()', () => {
  it('localStorage에서 access token을 제거한다', () => {
    localStorage.setItem(ACCESS_TOKEN_KEY, 'my-token');
    clearAccessToken();
    expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
  });
});

describe('getRefreshToken()', () => {
  it('localStorage에 refresh token이 없으면 null을 반환한다', () => {
    expect(getRefreshToken()).toBeNull();
  });

  it('저장된 refresh token을 반환한다', () => {
    localStorage.setItem(REFRESH_TOKEN_KEY, 'my-refresh-token');
    expect(getRefreshToken()).toBe('my-refresh-token');
  });
});

describe('setRefreshToken()', () => {
  it('localStorage에 refresh token을 저장한다', () => {
    setRefreshToken('new-refresh-token');
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBe('new-refresh-token');
  });
});

describe('clearRefreshToken()', () => {
  it('localStorage에서 refresh token을 제거한다', () => {
    localStorage.setItem(REFRESH_TOKEN_KEY, 'my-refresh-token');
    clearRefreshToken();
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull();
  });
});

describe('setAuthMarkerCookie()', () => {
  it('document.cookie에 인증 마커 쿠키를 설정한다', () => {
    setAuthMarkerCookie();
    expect(document.cookie).toContain(AUTH_MARKER_COOKIE);
  });

  it('쿠키에 Max-Age를 포함하여 설정한다 (직접 설정 확인)', () => {
    const spy = vi.spyOn(document, 'cookie', 'set');
    setAuthMarkerCookie();
    expect(spy).toHaveBeenCalled();
    const cookieValue = spy.mock.calls[0][0] as string;
    expect(cookieValue).toContain(`${AUTH_MARKER_COOKIE}=1`);
    expect(cookieValue).toContain('Max-Age=');
    expect(cookieValue).toContain('Path=/');
    expect(cookieValue).toContain('SameSite=Lax');
    spy.mockRestore();
  });
});

describe('clearAuthMarkerCookie()', () => {
  it('document.cookie에 Max-Age=0으로 쿠키를 만료시킨다', () => {
    const spy = vi.spyOn(document, 'cookie', 'set');
    clearAuthMarkerCookie();
    expect(spy).toHaveBeenCalled();
    const cookieValue = spy.mock.calls[0][0] as string;
    expect(cookieValue).toContain(`${AUTH_MARKER_COOKIE}=`);
    expect(cookieValue).toContain('Max-Age=0');
    spy.mockRestore();
  });
});

describe('clearAllTokens()', () => {
  it('access token, refresh token을 모두 제거한다', () => {
    localStorage.setItem(ACCESS_TOKEN_KEY, 'access');
    localStorage.setItem(REFRESH_TOKEN_KEY, 'refresh');
    clearAllTokens();
    expect(localStorage.getItem(ACCESS_TOKEN_KEY)).toBeNull();
    expect(localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull();
  });

  it('clearAuthMarkerCookie도 함께 호출한다', () => {
    const spy = vi.spyOn(document, 'cookie', 'set');
    clearAllTokens();
    expect(spy).toHaveBeenCalled();
    const cookieValue = spy.mock.calls[0][0] as string;
    expect(cookieValue).toContain('Max-Age=0');
    spy.mockRestore();
  });
});
