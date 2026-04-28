'use client';

import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import type { AdminFeedbackItem } from '@/types/feedback';

interface FeedbackListProps {
  items: AdminFeedbackItem[];
  isLoading: boolean;
  total: number;
}

export function FeedbackList({ items, isLoading, total }: FeedbackListProps) {
  if (isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} height="72px" width="100%" />
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="rounded-[var(--radius-md)] border border-dashed border-[var(--color-neutral-300)] bg-[var(--surface-card)] px-4 py-8 text-center text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
        피드백이 없습니다.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
        총 {total.toLocaleString()}건
      </div>
      {items.map((item) => (
        <article
          key={item.id}
          className="flex flex-col gap-2 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-4 py-3"
          aria-label="피드백 항목"
        >
          <header className="flex flex-wrap items-center gap-2">
            <FeedbackScoreBadge score={item.score} />
            {item.profile_id && (
              <Badge variant="secondary" size="sm">
                {item.profile_id}
              </Badge>
            )}
            <FaithfulnessBadge score={item.faithfulness_score} />
            <span className="ml-auto text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
              {formatDate(item.created_at)}
            </span>
          </header>
          <dl className="grid grid-cols-1 gap-1 md:grid-cols-2">
            <div>
              <dt className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)]">
                질문
              </dt>
              <dd
                className="truncate text-[var(--font-size-sm)] text-[var(--color-neutral-800)]"
                title={item.question_preview ?? ''}
              >
                {item.question_preview ?? '-'}
              </dd>
            </div>
            <div>
              <dt className="text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)]">
                응답
              </dt>
              <dd
                className="line-clamp-2 text-[var(--font-size-sm)] text-[var(--color-neutral-800)]"
                title={item.answer_preview ?? ''}
              >
                {item.answer_preview ?? '-'}
              </dd>
            </div>
          </dl>
          {(item.routing_info || (item.tools_used && item.tools_used.length > 0)) && (
            <div className="flex flex-wrap items-center gap-1">
              {item.routing_info && (
                <Badge variant="primary" size="sm">
                  {item.routing_info}
                </Badge>
              )}
              {item.tools_used?.map((tool) => (
                <Badge key={tool} variant="secondary" size="sm">
                  {tool}
                </Badge>
              ))}
            </div>
          )}
          {item.comment && (
            <p className="rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-2 py-1 text-[var(--font-size-xs)] text-[var(--color-neutral-700)]">
              "{item.comment}"
            </p>
          )}
        </article>
      ))}
    </div>
  );
}

function FeedbackScoreBadge({ score }: { score: number }) {
  if (score === 1) {
    return (
      <Badge variant="success" size="sm">
        좋아요
      </Badge>
    );
  }
  if (score === -1) {
    return (
      <Badge variant="error" size="sm">
        별로예요
      </Badge>
    );
  }
  return <Badge size="sm">{score}</Badge>;
}

function FaithfulnessBadge({ score }: { score: number | null }) {
  if (score == null) {
    return (
      <Badge variant="neutral" size="sm">
        신뢰도 -
      </Badge>
    );
  }
  const variant = score >= 0.8 ? 'success' : score >= 0.5 ? 'warning' : 'error';
  return (
    <Badge variant={variant} size="sm">
      신뢰도 {score.toFixed(2)}
    </Badge>
  );
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString('ko-KR', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}
