import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/cn';

const sizeStyles = {
  sm: 'h-8 px-2.5 text-[var(--font-size-sm)]',
  md: 'h-10 px-3 text-[var(--font-size-sm)]',
  lg: 'h-12 px-4 text-[var(--font-size-base)]',
} as const;

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  size?: keyof typeof sizeStyles;
  error?: string;
  label?: string;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  (
    {
      className,
      size = 'md',
      error,
      label,
      leftIcon,
      rightIcon,
      id,
      ...props
    },
    ref,
  ) => {
    const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');

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
        <div className="relative">
          {leftIcon && (
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-neutral-400)]">
              {leftIcon}
            </span>
          )}
          <input
            ref={ref}
            id={inputId}
            className={cn(
              'w-full rounded-[var(--radius-md)] border bg-[var(--surface-input)] transition-colors',
              'placeholder:text-[var(--color-neutral-400)]',
              'focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-200)] focus:border-[var(--color-primary-500)]',
              'disabled:cursor-not-allowed disabled:opacity-50',
              error
                ? 'border-[var(--color-error)] focus:ring-[var(--color-error-light)]'
                : 'border-[var(--color-neutral-200)]',
              sizeStyles[size],
              leftIcon && 'pl-10',
              rightIcon && 'pr-10',
              className,
            )}
            {...props}
          />
          {rightIcon && (
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--color-neutral-400)]">
              {rightIcon}
            </span>
          )}
        </div>
        {error && (
          <p className="text-[var(--font-size-xs)] text-[var(--color-error)]">
            {error}
          </p>
        )}
      </div>
    );
  },
);

Input.displayName = 'Input';
