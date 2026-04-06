'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { cn } from '@/lib/cn';

export interface DropdownOption {
  value: string;
  label: string;
}

export interface DropdownProps {
  options: DropdownOption[];
  value?: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

export function Dropdown({
  options,
  value,
  onChange,
  placeholder = '선택하세요',
  disabled = false,
  className,
}: DropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  const handleClickOutside = useCallback((e: globalThis.MouseEvent) => {
    if (
      containerRef.current &&
      !containerRef.current.contains(e.target as Node)
    ) {
      setIsOpen(false);
    }
  }, []);

  useEffect(() => {
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [handleClickOutside]);

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      <button
        type="button"
        onClick={() => !disabled && setIsOpen((prev) => !prev)}
        disabled={disabled}
        className={cn(
          'flex h-10 w-full items-center justify-between rounded-[var(--radius-md)] border border-[var(--color-neutral-200)]',
          'bg-[var(--surface-input)] px-3 text-[var(--font-size-sm)]',
          'focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-200)]',
          'disabled:cursor-not-allowed disabled:opacity-50',
          selected
            ? 'text-[var(--color-neutral-800)]'
            : 'text-[var(--color-neutral-400)]',
        )}
      >
        <span className="truncate">{selected?.label || placeholder}</span>
        <svg
          className={cn(
            'h-4 w-4 text-[var(--color-neutral-400)] transition-transform',
            isOpen && 'rotate-180',
          )}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>
      {isOpen && (
        <div
          className={cn(
            'absolute z-[var(--z-dropdown)] mt-1 w-full rounded-[var(--radius-md)]',
            'border border-[var(--color-neutral-200)] bg-[var(--surface-elevated)] py-1 shadow-[var(--shadow-md)]',
          )}
        >
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                onChange(option.value);
                setIsOpen(false);
              }}
              className={cn(
                'flex w-full items-center px-3 py-2 text-left text-[var(--font-size-sm)]',
                'hover:bg-[var(--color-neutral-100)] transition-colors',
                option.value === value
                  ? 'text-[var(--color-primary-600)] font-medium'
                  : 'text-[var(--color-neutral-700)]',
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
