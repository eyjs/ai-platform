import { cn } from '@/lib/cn';

export interface ProviderCardProps {
  name: string;
  type: string;
  status: 'healthy' | 'degraded' | 'error';
  avgLatencyMs: number;
  errorRate: number;
  lastError: string | null;
  className?: string;
}

function getStatusColor(errorRate: number): 'green' | 'yellow' | 'red' {
  if (errorRate < 1) return 'green';
  if (errorRate < 5) return 'yellow';
  return 'red';
}

const statusDotStyles = {
  green: 'bg-[var(--color-success)] animate-pulse',
  yellow: 'bg-[var(--color-warning)]',
  red: 'bg-[var(--color-error)]',
} as const;

const statusLabel = {
  green: '정상',
  yellow: '주의',
  red: '오류',
} as const;

const typeLabel: Record<string, string> = {
  llm: 'LLM',
  embedding: 'Embedding',
  reranker: 'Reranker',
};

export function ProviderCard({
  name,
  type,
  avgLatencyMs,
  errorRate,
  lastError,
  className,
}: ProviderCardProps) {
  const color = getStatusColor(errorRate);

  return (
    <div
      className={cn(
        'rounded-[var(--radius-lg)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] p-5',
        className,
      )}
    >
      <div className="flex items-center justify-between">
        <div>
          <p className="text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
            {name}
          </p>
          <p className="mt-0.5 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
            {typeLabel[type] ?? type}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={cn('h-2.5 w-2.5 rounded-full', statusDotStyles[color])} />
          <span className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]">
            {statusLabel[color]}
          </span>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <div>
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">평균 지연</p>
          <p className="mt-0.5 text-[var(--font-size-lg)] font-bold text-[var(--color-neutral-900)]">
            {avgLatencyMs.toFixed(0)}
            <span className="text-[var(--font-size-xs)] font-normal text-[var(--color-neutral-500)]"> ms</span>
          </p>
        </div>
        <div>
          <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">오류율</p>
          <p
            className={cn(
              'mt-0.5 text-[var(--font-size-lg)] font-bold',
              color === 'green' && 'text-[var(--color-success)]',
              color === 'yellow' && 'text-[var(--color-warning)]',
              color === 'red' && 'text-[var(--color-error)]',
            )}
          >
            {errorRate.toFixed(1)}%
          </p>
        </div>
      </div>

      {lastError && (
        <div className="mt-4 rounded-[var(--radius-md)] bg-[var(--color-error-light)] px-3 py-2">
          <p className="text-[var(--font-size-xs)] font-medium text-[var(--color-error)]">
            마지막 오류
          </p>
          <p className="mt-0.5 truncate text-[var(--font-size-xs)] text-[var(--color-neutral-700)]">
            {lastError}
          </p>
        </div>
      )}
    </div>
  );
}
