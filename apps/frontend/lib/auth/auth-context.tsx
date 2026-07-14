'use client';

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from 'react';
import { useRouter } from 'next/navigation';
import type { CurrentUser } from '@/types/auth';
import * as authApi from '@/lib/api/bff-auth';
import {
  getAccessToken,
  setAccessToken,
  getRefreshToken,
  setRefreshToken,
  clearAllTokens,
  setAuthMarkerCookie,
  isTokenExpiringSoon,
} from './token-storage';

interface AuthContextValue {
  user: CurrentUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  accessToken: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  /**
   * 유효한 access token을 보장해 반환한다.
   * 만료/임박 시 refresh로 재발급하고, 실패하면 로그아웃 + /login 이동 후 null.
   * (13분 인터벌 갱신은 탭이 잠들면 안 돌아 만료를 놓친다 — API 호출 직전 선제 보강용)
   */
  ensureFreshAccessToken: () => Promise<string | null>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [accessToken, setAccessTokenState] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  const applyTokens = useCallback(
    (access: string, refresh: string) => {
      setAccessToken(access);
      setRefreshToken(refresh);
      setAuthMarkerCookie();
      setAccessTokenState(access);
    },
    [],
  );

  const initAuth = useCallback(async () => {
    try {
      let token = getAccessToken();

      // localStorage 에 access 가 없으면 refresh 로 재발급 시도
      if (!token) {
        const refresh = getRefreshToken();
        if (refresh) {
          const result = await authApi.refreshToken(refresh);
          applyTokens(result.accessToken, result.refreshToken);
          token = result.accessToken;
        }
      } else {
        // 기존 토큰 존재 시 마커 쿠키도 보장 (세션 복구)
        setAuthMarkerCookie();
        setAccessTokenState(token);
      }

      if (token) {
        const me = await authApi.getMe(token);
        setUser(me);
      }
    } catch {
      clearAllTokens();
      setUser(null);
      setAccessTokenState(null);
    } finally {
      setIsLoading(false);
    }
  }, [applyTokens]);

  useEffect(() => {
    initAuth();
  }, [initAuth]);

  // 토큰 자동 갱신 (13분마다 — 15분 만료 전)
  useEffect(() => {
    if (!accessToken) return;

    const interval = setInterval(async () => {
      const refresh = getRefreshToken();
      if (!refresh) return;
      try {
        const result = await authApi.refreshToken(refresh);
        applyTokens(result.accessToken, result.refreshToken);
      } catch {
        clearAllTokens();
        setUser(null);
        setAccessTokenState(null);
        router.push('/login');
      }
    }, 13 * 60 * 1000);

    return () => clearInterval(interval);
  }, [accessToken, router, applyTokens]);

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await authApi.login(email, password);
      applyTokens(result.accessToken, result.refreshToken);
      const me = await authApi.getMe(result.accessToken);
      setUser(me);
    },
    [applyTokens],
  );

  const logout = useCallback(() => {
    clearAllTokens();
    setUser(null);
    setAccessTokenState(null);
    router.push('/login');
  }, [router]);

  const ensureFreshAccessToken = useCallback(async (): Promise<string | null> => {
    const current = getAccessToken();
    if (current && !isTokenExpiringSoon(current)) {
      return current;
    }
    const refresh = getRefreshToken();
    if (refresh) {
      try {
        const result = await authApi.refreshToken(refresh);
        applyTokens(result.accessToken, result.refreshToken);
        return result.accessToken;
      } catch {
        // 아래 공통 로그아웃 처리로 진행
      }
    }
    clearAllTokens();
    setUser(null);
    setAccessTokenState(null);
    router.push('/login');
    return null;
  }, [applyTokens, router]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        accessToken,
        login,
        logout,
        ensureFreshAccessToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
