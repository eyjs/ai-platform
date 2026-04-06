'use client';

import { useState, useRef, useCallback, type KeyboardEvent } from 'react';
import { cn } from '@/lib/cn';

interface ChatInputProps {
  onSend: (message: string) => void;
  onStop?: () => void;
  isStreaming?: boolean;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}

export function ChatInput({
  onSend,
  onStop,
  isStreaming = false,
  disabled = false,
  placeholder = '메시지를 입력하세요...',
  className,
}: ChatInputProps) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjustHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  }, []);

  const handleSend = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled || isStreaming) return;
    onSend(trimmed);
    setValue('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [value, disabled, isStreaming, onSend]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      className={cn(
        'flex items-end gap-2 rounded-[var(--radius-2xl)] border border-[var(--color-neutral-200)]',
        'bg-[var(--surface-input)] px-4 py-3 shadow-[var(--shadow-sm)]',
        'focus-within:border-[var(--color-primary-400)] focus-within:ring-2 focus-within:ring-[var(--color-primary-100)]',
        className,
      )}
    >
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          adjustHeight();
        }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        rows={1}
        className={cn(
          'flex-1 resize-none bg-transparent text-[var(--font-size-base)] text-[var(--color-neutral-800)]',
          'placeholder:text-[var(--color-neutral-400)]',
          'focus:outline-none',
          'disabled:opacity-50',
        )}
        style={{ maxHeight: 'var(--chat-input-max-height)' }}
      />
      {isStreaming ? (
        <button
          onClick={onStop}
          className={cn(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-full',
            'bg-[var(--color-neutral-800)] text-white hover:bg-[var(--color-neutral-700)]',
            'transition-colors',
          )}
          title="응답 중단"
        >
          <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
        </button>
      ) : (
        <button
          onClick={handleSend}
          disabled={!value.trim() || disabled}
          className={cn(
            'flex h-9 w-9 shrink-0 items-center justify-center rounded-full',
            'bg-[var(--color-primary-500)] text-white',
            'hover:bg-[var(--color-primary-600)] transition-colors',
            'disabled:opacity-30 disabled:cursor-not-allowed',
          )}
          title="전송"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12h14M12 5l7 7-7 7" />
          </svg>
        </button>
      )}
    </div>
  );
}
