'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
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

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push(`/login?callbackUrl=${encodeURIComponent(window.location.pathname)}`);
    }
  }, [isLoading, isAuthenticated, router]);

  useEffect(() => {
    if (!isLoading && user && user.role !== 'ADMIN') {
      router.push('/');
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
      <main className="flex-1 overflow-y-auto bg-[var(--surface-page)]">
        <div className="mx-auto max-w-[var(--admin-content-max-width)] p-6">
          {children}
        </div>
      </main>
    </div>
  );
}
