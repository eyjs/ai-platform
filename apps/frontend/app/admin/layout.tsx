'use client';

import { useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { useAuth } from '@/lib/auth/auth-context';
import { AdminSidebar } from '@/components/admin/admin-sidebar';
import { Skeleton } from '@/components/ui/skeleton';

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, isAuthenticated, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  // 채팅·프로필 에디터는 풀높이(에디터/입력 하단 고정)라 패딩·max-width 컨테이너
  // 없이 풀블리드로 렌더한다. 프로필 목록/이력은 일반 패딩 레이아웃 유지.
  const isProfileEditor =
    pathname.startsWith('/admin/profiles/') && !pathname.endsWith('/history');
  const fullBleed = pathname === '/admin/chat' || isProfileEditor;

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push(`/login?callbackUrl=${encodeURIComponent(window.location.pathname)}`);
    }
  }, [isLoading, isAuthenticated, router]);

  useEffect(() => {
    // 비-ADMIN은 로그인으로(루트는 /admin으로 리다이렉트되므로 루프 방지).
    if (!isLoading && user && user.role !== 'ADMIN') {
      router.push('/login');
    }
  }, [isLoading, user, router]);

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Skeleton width="200px" height="24px" />
      </div>
    );
  }

  if (!isAuthenticated || !user || user.role !== 'ADMIN') {
    return null;
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <AdminSidebar />
      <main className="flex-1 overflow-hidden bg-[var(--surface-page)]">
        {fullBleed ? (
          children
        ) : (
          <div className="h-full overflow-y-auto">
            <div className="mx-auto max-w-[var(--admin-content-max-width)] p-6">
              {children}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
