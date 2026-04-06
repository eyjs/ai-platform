'use client';

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from 'react';
import { cn } from '@/lib/cn';

type ToastVariant = 'success' | 'error' | 'warning' | 'info';

interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (message: string, variant?: ToastVariant) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const variantStyles: Record<ToastVariant, string> = {
  success:
    'bg-[var(--color-success-light)] border-[var(--color-success)] text-[var(--color-success)]',
  error:
    'bg-[var(--color-error-light)] border-[var(--color-error)] text-[var(--color-error)]',
  warning:
    'bg-[var(--color-warning-light)] border-[var(--color-warning)] text-[var(--color-warning)]',
  info: 'bg-[var(--color-info-light)] border-[var(--color-info)] text-[var(--color-info)]',
};

const icons: Record<ToastVariant, string> = {
  success: '\u2713',
  error: '\u2717',
  warning: '\u26A0',
  info: '\u2139',
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const addToast = useCallback(
    (message: string, variant: ToastVariant = 'info') => {
      const id = crypto.randomUUID();
      setToasts((prev) => [...prev, { id, message, variant }]);
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, 5000);
    },
    [],
  );

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ toast: addToast }}>
      {children}
      <div className="fixed top-4 right-4 z-[var(--z-toast)] flex flex-col gap-2">
        {toasts.map((item) => (
          <div
            key={item.id}
            className={cn(
              'flex items-center gap-2 rounded-[var(--radius-md)] border px-4 py-3 shadow-[var(--shadow-md)]',
              'text-[var(--font-size-sm)] font-medium',
              'animate-in slide-in-from-right',
              variantStyles[item.variant],
            )}
          >
            <span>{icons[item.variant]}</span>
            <span className="flex-1">{item.message}</span>
            <button
              onClick={() => removeToast(item.id)}
              className="ml-2 opacity-70 hover:opacity-100"
            >
              x
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}
