'use client';

import { useScrollAnchor } from '@/hooks/use-scroll-anchor';
import { ChatBubble } from './chat-bubble';
import { ChatEmptyState } from './chat-empty-state';
import { ScrollToBottomFab } from './scroll-to-bottom-fab';
import type { ChatMessage } from '@/types/chat';

interface ChatMessageListProps {
  messages: ChatMessage[];
  profileName?: string;
}

export function ChatMessageList({ messages, profileName }: ChatMessageListProps) {
  const { scrollRef, bottomRef, showScrollButton, scrollToBottom, handleScroll } =
    useScrollAnchor();

  if (messages.length === 0) {
    return <ChatEmptyState profileName={profileName} />;
  }

  return (
    <div className="relative flex-1">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto"
      >
        <div className="mx-auto max-w-[var(--content-max-width)] py-4">
          {messages.map((message) => (
            <ChatBubble key={message.id} message={message} />
          ))}
          <div ref={bottomRef} />
        </div>
      </div>
      <ScrollToBottomFab visible={showScrollButton} onClick={scrollToBottom} />
    </div>
  );
}
