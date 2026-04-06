'use client';

import {
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
  type MouseEvent,
} from 'react';
import { cn } from '@/lib/cn';
import { Button } from './button';

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  variant?: 'default' | 'confirm';
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm?: () => void;
  confirmLoading?: boolean;
  className?: string;
}

export function Modal({
  isOpen,
  onClose,
  title,
  children,
  variant = 'default',
  confirmLabel = '확인',
  cancelLabel = '취소',
  onConfirm,
  confirmLoading = false,
  className,
}: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  const handleEscape = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    },
    [onClose],
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
      document.body.style.overflow = 'hidden';
      return () => {
        document.removeEventListener('keydown', handleEscape);
        document.body.style.overflow = '';
      };
    }
  }, [isOpen, handleEscape]);

  const handleOverlayClick = (e: MouseEvent) => {
    if (e.target === overlayRef.current) onClose();
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
        className={cn(
          'w-full max-w-md rounded-[var(--radius-xl)] bg-[var(--surface-elevated)] p-6 shadow-[var(--shadow-lg)]',
          'animate-in fade-in zoom-in-95',
          className,
        )}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        {title && (
          <h2 className="mb-4 text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]">
            {title}
          </h2>
        )}
        <div className="text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
          {children}
        </div>
        {variant === 'confirm' && (
          <div className="mt-6 flex justify-end gap-3">
            <Button variant="secondary" size="sm" onClick={onClose}>
              {cancelLabel}
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={onConfirm}
              loading={confirmLoading}
            >
              {confirmLabel}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
