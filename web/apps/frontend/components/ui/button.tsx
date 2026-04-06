import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

const variantStyles = {
  primary:
    'bg-[var(--color-primary-500)] text-white hover:bg-[var(--color-primary-600)] active:bg-[var(--color-primary-700)] focus-visible:ring-[var(--color-primary-200)]',
  secondary:
    'bg-[var(--color-neutral-100)] text-[var(--color-neutral-800)] hover:bg-[var(--color-neutral-200)] active:bg-[var(--color-neutral-300)] focus-visible:ring-[var(--color-neutral-300)]',
  ghost:
    'bg-transparent text-[var(--color-neutral-700)] hover:bg-[var(--color-neutral-100)] active:bg-[var(--color-neutral-200)] focus-visible:ring-[var(--color-neutral-300)]',
  danger:
    'bg-[var(--color-error)] text-white hover:bg-red-700 active:bg-red-800 focus-visible:ring-red-200',
} as const;

const sizeStyles = {
  sm: 'h-8 px-3 text-[var(--font-size-sm)] gap-1.5 rounded-[var(--radius-md)]',
  md: 'h-10 px-4 text-[var(--font-size-sm)] gap-2 rounded-[var(--radius-md)]',
  lg: 'h-12 px-6 text-[var(--font-size-base)] gap-2 rounded-[var(--radius-md)]',
} as const;

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof variantStyles;
  size?: keyof typeof sizeStyles;
  loading?: boolean;
  fullWidth?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant = 'primary',
      size = 'md',
      loading = false,
      fullWidth = false,
      disabled,
      children,
      ...props
    },
    ref,
  ) => {
    return (
      <button
        ref={ref}
        className={cn(
          'inline-flex items-center justify-center font-semibold transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
          'disabled:pointer-events-none disabled:opacity-50',
          variantStyles[variant],
          sizeStyles[size],
          fullWidth && 'w-full',
          className,
        )}
        disabled={disabled || loading}
        {...props}
      >
        {loading && (
          <svg
            className="h-4 w-4 animate-spin"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
        )}
        {children}
      </button>
    );
  },
);

Button.displayName = 'Button';
