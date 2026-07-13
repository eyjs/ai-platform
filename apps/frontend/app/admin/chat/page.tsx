'use client';

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Dropdown, type DropdownOption } from '@/components/ui/dropdown';
import { SessionList } from '@/components/chat/session-list';
import { ChatInput } from '@/components/chat/chat-input';
import { ChatMessageList } from '@/components/chat/chat-message-list';
import { useChatSessions } from '@/hooks/use-chat-sessions';
import { useChatStream } from '@/hooks/use-chat-stream';
import { useAuth } from '@/lib/auth/auth-context';
import { fetchProfiles } from '@/lib/api/chat';
import { submitFeedback } from '@/lib/api/bff-feedback';
import type { ChatMessage, ChatProfileOption } from '@/types/chat';
import type { FeedbackScore } from '@/types/feedback';

/** RAG 검증용 채팅 — 어드민 셸(/admin) 안에서 동작하는 풀높이 페이지. */
export default function AdminChatPage() {
  const { accessToken } = useAuth();
  const [selectedProfileId, setSelectedProfileId] = useState('');
  const [selectedProfileName, setSelectedProfileName] = useState('자동 선택');
  const [profiles, setProfiles] = useState<ChatProfileOption[]>([]);
  // 스트림 콜백은 handleSend 호출 시점의 세션 id 가 필요하다. state 는 비동기라
  // 새 세션의 첫 메시지에서 stale null 이 잡히므로 ref 로 활성 세션을 추적한다.
  const activeSessionIdRef = useRef<string | null>(null);

  const {
    sessions,
    currentSession,
    currentSessionId,
    createSession,
    switchSession,
    deleteSession,
    updateSessionTitle,
    addMessage,
    updateLastMessage,
    updateMessageById,
    clearCurrentSession,
  } = useChatSessions();

  useEffect(() => {
    fetchProfiles().then(setProfiles).catch(() => {});
  }, []);

  const profileOptions: DropdownOption[] = [
    { value: '', label: '자동 선택' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  const streamCallbacks = useMemo(
    () => ({
      onToken: (text: string) => {
        const sid = activeSessionIdRef.current;
        if (!sid) return;
        updateLastMessage(sid, (msg) => ({ ...msg, content: msg.content + text }));
      },
      onReplace: (text: string) => {
        const sid = activeSessionIdRef.current;
        if (!sid) return;
        updateLastMessage(sid, (msg) => ({ ...msg, content: text }));
      },
      onTrace: (data: Record<string, unknown>) => {
        const sid = activeSessionIdRef.current;
        if (!sid) return;
        updateLastMessage(sid, (msg) => ({
          ...msg,
          traceData: data,
          traceEvents: [...(msg.traceEvents ?? []), data],
        }));
      },
      onDone: (data: {
        answer?: string;
        sources?: Array<{ title: string; url?: string }>;
        response_id?: string;
      }) => {
        const sid = activeSessionIdRef.current;
        if (!sid) return;
        updateLastMessage(sid, (msg) => ({
          ...msg,
          isStreaming: false,
          content: data.answer && data.answer.length > 0 ? data.answer : msg.content,
          sources: data.sources,
          responseId: data.response_id ?? msg.responseId,
        }));
      },
      onError: (error: Error) => {
        const sid = activeSessionIdRef.current;
        if (!sid) return;
        updateLastMessage(sid, (msg) => ({
          ...msg,
          isStreaming: false,
          isError: true,
          errorMessage: error.message,
        }));
      },
      onIncomplete: (reason: 'aborted' | 'no_done') => {
        const sid = activeSessionIdRef.current;
        if (!sid) return;
        updateLastMessage(sid, (msg) => {
          if (msg.content.length > 0) {
            // 부분 응답은 남기고 스트리밍 상태만 마감
            return { ...msg, isStreaming: false };
          }
          return {
            ...msg,
            isStreaming: false,
            isError: true,
            errorMessage:
              reason === 'aborted'
                ? '응답이 중단되었습니다.'
                : '연결이 끊겨 응답을 받지 못했습니다. 다시 시도해주세요.',
          };
        });
      },
    }),
    [updateLastMessage],
  );

  const { sendMessage, isStreaming, abort } = useChatStream(streamCallbacks);

  const handleSend = useCallback(
    async (text: string) => {
      let activeSessionId = currentSessionId;
      let session = currentSession;

      if (!session) {
        session = createSession(selectedProfileId, selectedProfileName);
        activeSessionId = session.id;
      }

      // 스트림 콜백이 참조할 활성 세션 id. state 반영을 기다리지 않고 즉시 세팅.
      activeSessionIdRef.current = activeSessionId ?? null;

      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: text,
        timestamp: new Date().toISOString(),
        isStreaming: false,
        isError: false,
      };
      addMessage(activeSessionId!, userMessage);

      const aiMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        isStreaming: true,
        isError: false,
      };
      addMessage(activeSessionId!, aiMessage);

      await sendMessage(
        {
          question: text,
          chatbot_id: selectedProfileId || undefined,
          session_id: activeSessionId || undefined,
        },
        accessToken ?? '',
      );
    },
    [
      currentSessionId,
      currentSession,
      selectedProfileId,
      selectedProfileName,
      createSession,
      addMessage,
      sendMessage,
      accessToken,
    ],
  );

  const handleFeedback = useCallback(
    async (messageId: string, responseId: string, score: FeedbackScore) => {
      if (!currentSessionId) return;
      const prev = currentSession?.messages.find((m) => m.id === messageId)?.feedback;
      updateMessageById(currentSessionId, messageId, (msg) => ({ ...msg, feedback: score }));
      try {
        await submitFeedback({ response_id: responseId, score });
      } catch (err) {
        updateMessageById(currentSessionId, messageId, (msg) => ({ ...msg, feedback: prev ?? null }));
        // eslint-disable-next-line no-console
        console.warn('feedback submit failed', err);
      }
    },
    [currentSessionId, currentSession, updateMessageById],
  );

  return (
    <div className="flex h-full overflow-hidden">
      {/* 세션 패널 (인페이지) */}
      <aside className="hidden w-[var(--sidebar-width)] flex-col border-r border-[var(--color-neutral-200)] bg-[var(--surface-sidebar)] md:flex">
        <div className="flex flex-col gap-3 border-b border-[var(--color-neutral-200)] p-4">
          <Button variant="primary" fullWidth onClick={clearCurrentSession}>
            + 새 대화
          </Button>
          <Dropdown
            options={profileOptions}
            value={selectedProfileId}
            onChange={(value) => {
              const profile = profiles.find((p) => p.id === value);
              setSelectedProfileId(value);
              setSelectedProfileName(profile?.name || '자동 선택');
            }}
            placeholder="Profile 선택"
          />
        </div>
        <div className="flex-1 overflow-y-auto">
          <SessionList
            sessions={sessions}
            currentSessionId={currentSessionId}
            onSelectSession={switchSession}
            onDeleteSession={deleteSession}
            onRenameSession={updateSessionTitle}
          />
        </div>
      </aside>

      {/* 채팅 영역 */}
      <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <ChatMessageList
          messages={currentSession?.messages || []}
          profileName={selectedProfileName !== '자동 선택' ? selectedProfileName : undefined}
          onFeedback={handleFeedback}
        />
        <div className="border-t border-[var(--color-neutral-200)] bg-[var(--surface-page)] px-4 py-3">
          <div className="mx-auto max-w-[var(--content-max-width)]">
            <ChatInput onSend={handleSend} onStop={abort} isStreaming={isStreaming} />
          </div>
        </div>
      </main>
    </div>
  );
}
