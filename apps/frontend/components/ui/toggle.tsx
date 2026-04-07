'use client';

import { cn } from '@/lib/cn';

const sizeStyles = {
  sm: { track: 'h-5 w-9', thumb: 'h-3.5 w-3.5', translate: 'translate-x-4' },
  md: { track: 'h-6 w-11', thumb: 'h-4.5 w-4.5', translate: 'translate-x-5' },
} as const;

export interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  size?: keyof typeof sizeStyles;
  disabled?: boolean;
  className?: string;
  label?: string;
}

export function Toggle({
  checked,
  onChange,
  size = 'md',
  disabled = false,
  className,
  label,
}: ToggleProps) {
  const styles = sizeStyles[size];

  return (
    <label
      className={cn(
        'inline-flex items-center gap-2',
        disabled && 'cursor-not-allowed opacity-50',
        !disabled && 'cursor-pointer',
        className,
      )}
    >
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={cn(
          'relative inline-flex shrink-0 rounded-[var(--radius-full)] border-2 border-transparent',
          'transition-colors duration-[var(--duration-fast)]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary-200)] focus-visible:ring-offset-2',
          styles.track,
          checked
            ? 'bg-[var(--color-primary-500)]'
            : 'bg-[var(--color-neutral-300)]',
        )}
      >
        <span
          className={cn(
            'pointer-events-none inline-block rounded-full bg-white shadow-[var(--shadow-xs)]',
            'transition-transform duration-[var(--duration-fast)]',
            styles.thumb,
            checked ? styles.translate : 'translate-x-0.5',
          )}
        />
      </button>
      {label && (
        <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
          {label}
        </span>
      )}
    </label>
  );
}
