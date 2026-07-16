'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { cn } from '@/lib/cn';

export interface DropdownOption {
  value: string;
  label: string;
  /** 선택지 아래 보조 설명 (선택). */
  description?: string;
  /** 선택지 옆 배지 문구 (선택). */
  badges?: string[];
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
            'absolute z-[var(--z-dropdown)] mt-1 max-h-80 w-full overflow-y-auto rounded-[var(--radius-md)]',
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
                'flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-[var(--font-size-sm)]',
                'hover:bg-[var(--color-neutral-100)] transition-colors',
                'focus:outline-none focus-visible:bg-[var(--color-neutral-100)]',
                option.value === value
                  ? 'text-[var(--color-primary-600)] font-medium'
                  : 'text-[var(--color-neutral-700)]',
              )}
            >
              <span className="flex flex-wrap items-center gap-1.5">
                <span>{option.label}</span>
                {option.badges?.map((badge) => (
                  <span
                    key={badge}
                    className={cn(
                      'rounded-[var(--radius-sm)] border border-[var(--color-neutral-200)]',
                      'bg-[var(--color-neutral-100)] px-1.5 py-0.5 text-[10px] font-medium',
                      'leading-none text-[var(--color-neutral-600)]',
                    )}
                  >
                    {badge}
                  </span>
                ))}
              </span>
              {option.description && (
                <span className="text-[var(--font-size-xs)] font-normal text-[var(--color-neutral-500)]">
                  {option.description}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
