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
} from './token-storage';

interface AuthContextValue {
  user: CurrentUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  accessToken: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  const initAuth = useCallback(async () => {
    try {
      let token = getAccessToken();

      // 메모리에 없으면 refresh 시도
      if (!token) {
        const refresh = getRefreshToken();
        if (refresh) {
          const result = await authApi.refreshToken(refresh);
          setAccessToken(result.accessToken);
          setRefreshToken(result.refreshToken);
          token = result.accessToken;
        }
      }

      if (token) {
        const me = await authApi.getMe(token);
        setUser(me);
      }
    } catch {
      clearAllTokens();
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    initAuth();
  }, [initAuth]);

  // 토큰 자동 갱신 (만료 1분 전)
  useEffect(() => {
    const token = getAccessToken();
    if (!token) return;

    const interval = setInterval(async () => {
      const refresh = getRefreshToken();
      if (!refresh) return;
      try {
        const result = await authApi.refreshToken(refresh);
        setAccessToken(result.accessToken);
        setRefreshToken(result.refreshToken);
      } catch {
        clearAllTokens();
        setUser(null);
        router.push('/login');
      }
    }, 13 * 60 * 1000); // 13분마다 갱신 (15분 만료 전)

    return () => clearInterval(interval);
  }, [user, router]);

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await authApi.login(email, password);
      setAccessToken(result.accessToken);
      setRefreshToken(result.refreshToken);
      const me = await authApi.getMe(result.accessToken);
      setUser(me);
    },
    [],
  );

  const logout = useCallback(() => {
    clearAllTokens();
    setUser(null);
    router.push('/login');
  }, [router]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        accessToken: getAccessToken(),
        login,
        logout,
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
