'use client';

import { useCallback, useEffect, useRef, type MouseEvent } from 'react';
import { cn } from '@/lib/cn';
import { Button } from '@/components/ui/button';

export interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
  variant?: 'default' | 'danger';
  confirmLabel?: string;
  cancelLabel?: string;
  loading?: boolean;
}

export function ConfirmDialog({
  isOpen,
  title,
  message,
  onConfirm,
  onCancel,
  variant = 'default',
  confirmLabel = '확인',
  cancelLabel = '취소',
  loading = false,
}: ConfirmDialogProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const handleEscape = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    },
    [onCancel],
  );

  useEffect(() => {
    if (!isOpen) return;
    document.addEventListener('keydown', handleEscape);
    document.body.style.overflow = 'hidden';
    contentRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = '';
    };
  }, [isOpen, handleEscape]);

  const handleOverlayClick = (e: MouseEvent) => {
    if (e.target === overlayRef.current) onCancel();
  };

  if (!isOpen) return null;

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 flex items-center justify-center bg-[var(--surface-overlay)] z-[var(--z-modal)]"
      onClick={handleOverlayClick}
    >
      <div
        ref={contentRef}
        className="w-full max-w-sm rounded-[var(--radius-xl)] bg-[var(--surface-elevated)] p-6 shadow-[var(--shadow-lg)]"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <h2 className="text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]">
          {title}
        </h2>
        <p className="mt-2 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
          {message}
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <Button variant="secondary" size="sm" onClick={onCancel} aria-label={cancelLabel}>
            {cancelLabel}
          </Button>
          <Button
            variant={variant === 'danger' ? 'danger' : 'primary'}
            size="sm"
            onClick={onConfirm}
            loading={loading}
            aria-label={confirmLabel}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
