import { type HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

const variantStyles = {
  primary:
    'bg-[var(--color-primary-50)] text-[var(--color-primary-700)] border-[var(--color-primary-200)]',
  secondary:
    'bg-[var(--color-secondary-50)] text-[var(--color-secondary-700)] border-[var(--color-secondary-200)]',
  success:
    'bg-[var(--color-success-light)] text-[var(--color-success)] border-green-200',
  warning:
    'bg-[var(--color-warning-light)] text-[var(--color-warning)] border-amber-200',
  error:
    'bg-[var(--color-error-light)] text-[var(--color-error)] border-red-200',
  neutral:
    'bg-[var(--color-neutral-100)] text-[var(--color-neutral-600)] border-[var(--color-neutral-200)]',
} as const;

const sizeStyles = {
  sm: 'px-1.5 py-0.5 text-[10px]',
  md: 'px-2 py-0.5 text-[var(--font-size-xs)]',
} as const;

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: keyof typeof variantStyles;
  size?: keyof typeof sizeStyles;
}

export function Badge({
  className,
  variant = 'neutral',
  size = 'md',
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-[var(--radius-sm)] border font-medium leading-none',
        variantStyles[variant],
        sizeStyles[size],
        className,
      )}
      {...props}
    />
  );
}
