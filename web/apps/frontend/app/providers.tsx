'use client';

import type { ReactNode } from 'react';
import { AuthProvider } from '@/lib/auth/auth-context';
import { ToastProvider } from '@/components/ui/toast';

export function Providers({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <ToastProvider>{children}</ToastProvider>
    </AuthProvider>
  );
}
