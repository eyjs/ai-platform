'use client';

import { cn } from '@/lib/cn';
import { Avatar } from '@/components/ui/avatar';
import { MarkdownRenderer } from './markdown-renderer';
import type { ChatMessage } from '@/types/chat';
import type { FeedbackScore } from '@/types/feedback';

interface ChatBubbleProps {
  message: ChatMessage;
  className?: string;
  /**
   * 👍/👎 클릭 핸들러. assistant 응답이고 responseId 가 있을 때만 버튼이 렌더된다.
   * 없으면 피드백 UI 비활성.
   */
  onFeedback?: (messageId: string, responseId: string, score: FeedbackScore) => void;
}

export function ChatBubble({ message, className, onFeedback }: ChatBubbleProps) {
  const isUser = message.role === 'user';
  const canFeedback =
    !isUser &&
    !message.isStreaming &&
    !message.isError &&
    Boolean(message.responseId) &&
    Boolean(onFeedback);

  return (
    <div
      className={cn(
        'flex gap-3 px-4 py-3',
        isUser ? 'justify-end' : 'justify-start',
        className,
      )}
    >
      {!isUser && (
        <Avatar
          variant="initials"
          initials="AI"
          size="sm"
          className="mt-1 shrink-0 bg-[var(--color-primary-100)] text-[var(--color-primary-700)]"
        />
      )}
      <div
        className={cn(
          'max-w-[var(--content-max-width)] rounded-[var(--radius-xl)]',
          isUser
            ? 'bg-[var(--color-primary-500)] text-white px-4 py-2.5'
            : 'bg-[var(--surface-card)] border border-[var(--color-neutral-200)] px-4 py-3',
          message.isError &&
            'border-[var(--color-error)] bg-[var(--color-error-light)]',
        )}
      >
        {message.isError ? (
          <p className="text-[var(--font-size-sm)] text-[var(--color-error)]">
            {message.errorMessage || '응답 생성 중 오류가 발생했습니다'}
          </p>
        ) : isUser ? (
          <p className="text-[var(--font-size-base)] whitespace-pre-wrap">
            {message.content}
          </p>
        ) : (
          <div
            className={cn(
              'prose prose-sm max-w-none text-[var(--color-neutral-800)]',
              message.isStreaming && 'streaming-cursor',
            )}
          >
            <MarkdownRenderer content={message.content} />
          </div>
        )}
        {message.sources && message.sources.length > 0 && (
          <div className="mt-2 border-t border-[var(--color-neutral-200)] pt-2">
            <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)] mb-1">
              출처
            </p>
            <div className="flex flex-wrap gap-1">
              {message.sources.map((source, i) => (
                <span
                  key={i}
                  className="inline-flex items-center rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-2 py-0.5 text-[var(--font-size-xs)] text-[var(--color-neutral-600)]"
                >
                  {source.title}
                </span>
              ))}
            </div>
          </div>
        )}
        {canFeedback && (
          <div
            className="mt-2 flex items-center gap-1 border-t border-[var(--color-neutral-200)] pt-2"
            aria-label="응답 피드백"
          >
            <FeedbackButton
              score={1}
              active={message.feedback === 1}
              onClick={() =>
                onFeedback?.(message.id, message.responseId!, 1)
              }
            />
            <FeedbackButton
              score={-1}
              active={message.feedback === -1}
              onClick={() =>
                onFeedback?.(message.id, message.responseId!, -1)
              }
            />
          </div>
        )}
      </div>
      {isUser && (
        <Avatar
          variant="icon"
          size="sm"
          className="mt-1 shrink-0"
        />
      )}
    </div>
  );
}

interface FeedbackButtonProps {
  score: FeedbackScore;
  active: boolean;
  onClick: () => void;
}

function FeedbackButton({ score, active, onClick }: FeedbackButtonProps) {
  const isPositive = score === 1;
  const label = isPositive ? '응답 좋아요' : '응답 별로예요';
  const activeColor = isPositive
    ? 'var(--color-primary-600)'
    : 'var(--color-error)';

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={cn(
        'inline-flex h-7 w-7 items-center justify-center rounded-[var(--radius-sm)]',
        'text-[var(--color-neutral-400)] transition-colors',
        'hover:bg-[var(--color-neutral-100)] hover:text-[var(--color-neutral-700)]',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
        'focus-visible:outline-[var(--color-primary-500)]',
      )}
      style={active ? { color: activeColor } : undefined}
    >
      {isPositive ? (
        <svg
          className="h-4 w-4"
          fill={active ? 'currentColor' : 'none'}
          viewBox="0 0 24 24"
          stroke="currentColor"
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.8}
            d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9A2 2 0 0019.66 9H14zM7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"
          />
        </svg>
      ) : (
        <svg
          className="h-4 w-4"
          fill={active ? 'currentColor' : 'none'}
          viewBox="0 0 24 24"
          stroke="currentColor"
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.8}
            d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9A2 2 0 004.34 15H10zm7-13h2.67A2.31 2.31 0 0122 4v7a2.31 2.31 0 01-2.33 2H17"
          />
        </svg>
      )}
    </button>
  );
}
