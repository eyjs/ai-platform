import { cn } from '@/lib/cn';

export interface SkeletonProps {
  width?: string;
  height?: string;
  className?: string;
}

export function Skeleton({ width, height, className }: SkeletonProps) {
  return (
    <div
      className={cn(
        'skeleton-pulse rounded-[var(--radius-md)] bg-[var(--color-neutral-200)]',
        className,
      )}
      style={{ width, height }}
    />
  );
}
