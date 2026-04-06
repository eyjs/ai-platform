'use client';

import { cn } from '@/lib/cn';
import { Avatar } from '@/components/ui/avatar';
import { MarkdownRenderer } from './markdown-renderer';
import type { ChatMessage } from '@/types/chat';

interface ChatBubbleProps {
  message: ChatMessage;
  className?: string;
}

export function ChatBubble({ message, className }: ChatBubbleProps) {
  const isUser = message.role === 'user';

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
