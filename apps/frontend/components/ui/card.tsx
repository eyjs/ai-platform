import { forwardRef, type HTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

const variantStyles = {
  default: 'bg-[var(--surface-card)] shadow-[var(--shadow-sm)]',
  interactive:
    'bg-[var(--surface-card)] shadow-[var(--shadow-sm)] hover:shadow-[var(--shadow-md)] cursor-pointer transition-shadow',
  section: 'bg-[var(--surface-card)]',
} as const;

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: keyof typeof variantStyles;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant = 'default', ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          'rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] p-4',
          variantStyles[variant],
          className,
        )}
        {...props}
      />
    );
  },
);

Card.displayName = 'Card';

export const CardHeader = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('mb-3', className)} {...props} />
));

CardHeader.displayName = 'CardHeader';

export const CardTitle = forwardRef<
  HTMLHeadingElement,
  HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn(
      'text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]',
      className,
    )}
    {...props}
  />
));

CardTitle.displayName = 'CardTitle';

export const CardContent = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('', className)} {...props} />
));

CardContent.displayName = 'CardContent';
