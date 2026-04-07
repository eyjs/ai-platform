'use client';

import {
  forwardRef,
  useCallback,
  useEffect,
  useRef,
  type TextareaHTMLAttributes,
} from 'react';
import { cn } from '@/lib/cn';

export interface TextAreaProps
  extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  autoResize?: boolean;
  maxHeight?: number;
  error?: string;
  label?: string;
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(
  (
    {
      className,
      autoResize = false,
      maxHeight = 200,
      error,
      label,
      id,
      onChange,
      ...props
    },
    ref,
  ) => {
    const internalRef = useRef<HTMLTextAreaElement | null>(null);
    const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');

    const setRef = useCallback(
      (node: HTMLTextAreaElement | null) => {
        internalRef.current = node;
        if (typeof ref === 'function') {
          ref(node);
        } else if (ref) {
          (ref as React.MutableRefObject<HTMLTextAreaElement | null>).current =
            node;
        }
      },
      [ref],
    );

    const adjustHeight = useCallback(() => {
      const textarea = internalRef.current;
      if (!textarea || !autoResize) return;
      textarea.style.height = 'auto';
      textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
    }, [autoResize, maxHeight]);

    useEffect(() => {
      adjustHeight();
    }, [adjustHeight, props.value]);

    return (
      <div className="flex flex-col gap-1.5">
        {label && (
          <label
            htmlFor={inputId}
            className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]"
          >
            {label}
          </label>
        )}
        <textarea
          ref={setRef}
          id={inputId}
          className={cn(
            'w-full rounded-[var(--radius-md)] border bg-[var(--surface-input)] px-3 py-2',
            'text-[var(--font-size-sm)] placeholder:text-[var(--color-neutral-400)]',
            'focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-200)] focus:border-[var(--color-primary-500)]',
            'disabled:cursor-not-allowed disabled:opacity-50',
            'resize-none transition-colors',
            error
              ? 'border-[var(--color-error)] focus:ring-[var(--color-error-light)]'
              : 'border-[var(--color-neutral-200)]',
            className,
          )}
          onChange={(e) => {
            onChange?.(e);
            adjustHeight();
          }}
          {...props}
        />
        {error && (
          <p className="text-[var(--font-size-xs)] text-[var(--color-error)]">
            {error}
          </p>
        )}
      </div>
    );
  },
);

TextArea.displayName = 'TextArea';
