import { cn } from '@/lib/cn';

const sizeStyles = {
  sm: 'h-8 w-8 text-[var(--font-size-xs)]',
  md: 'h-10 w-10 text-[var(--font-size-sm)]',
  lg: 'h-12 w-12 text-[var(--font-size-base)]',
} as const;

export interface AvatarProps {
  variant?: 'icon' | 'initials' | 'image';
  size?: keyof typeof sizeStyles;
  src?: string;
  alt?: string;
  initials?: string;
  className?: string;
}

export function Avatar({
  variant = 'initials',
  size = 'md',
  src,
  alt = '',
  initials,
  className,
}: AvatarProps) {
  if (variant === 'image' && src) {
    return (
      <img
        src={src}
        alt={alt}
        className={cn(
          'rounded-[var(--radius-full)] object-cover',
          sizeStyles[size],
          className,
        )}
      />
    );
  }

  if (variant === 'icon') {
    return (
      <div
        className={cn(
          'flex items-center justify-center rounded-[var(--radius-full)]',
          'bg-[var(--color-neutral-100)] text-[var(--color-neutral-500)]',
          sizeStyles[size],
          className,
        )}
      >
        <svg
          className="h-1/2 w-1/2"
          fill="currentColor"
          viewBox="0 0 24 24"
        >
          <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" />
        </svg>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'flex items-center justify-center rounded-[var(--radius-full)]',
        'bg-[var(--color-primary-100)] text-[var(--color-primary-700)] font-medium',
        sizeStyles[size],
        className,
      )}
    >
      {initials?.slice(0, 2).toUpperCase() || '?'}
    </div>
  );
}
