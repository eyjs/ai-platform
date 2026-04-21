'use client';

import { useState, useCallback, useMemo } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { ChatSidebar } from '@/components/chat/chat-sidebar';
import { ChatInput } from '@/components/chat/chat-input';
import { ChatMessageList } from '@/components/chat/chat-message-list';
import { useChatSessions } from '@/hooks/use-chat-sessions';
import { useChatStream } from '@/hooks/use-chat-stream';
import { useAuth } from '@/lib/auth/auth-context';
import { submitFeedback } from '@/lib/api/bff-feedback';
import type { ChatMessage } from '@/types/chat';
import type { FeedbackScore } from '@/types/feedback';

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const { accessToken } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [selectedProfileId, setSelectedProfileId] = useState('');
  const [selectedProfileName, setSelectedProfileName] = useState('자동 선택');

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

  // 세션 ID를 URL에서 추출하여 동기화
  const sessionIdFromUrl = useMemo(() => {
    const match = pathname.match(/^\/([^/]+)$/);
    return match ? match[1] : null;
  }, [pathname]);

  // URL 변경 시 세션 전환
  useMemo(() => {
    if (sessionIdFromUrl && sessionIdFromUrl !== currentSessionId) {
      const exists = sessions.find((s) => s.id === sessionIdFromUrl);
      if (exists) {
        switchSession(sessionIdFromUrl);
      }
    }
  }, [sessionIdFromUrl, currentSessionId, sessions, switchSession]);

  const streamCallbacks = useMemo(
    () => ({
      onToken: (text: string) => {
        if (!currentSessionId) return;
        updateLastMessage(currentSessionId, (msg) => ({
          ...msg,
          content: msg.content + text,
        }));
      },
      onReplace: (text: string) => {
        if (!currentSessionId) return;
        updateLastMessage(currentSessionId, (msg) => ({
          ...msg,
          content: text,
        }));
      },
      onTrace: (data: Record<string, unknown>) => {
        if (!currentSessionId) return;
        updateLastMessage(currentSessionId, (msg) => ({
          ...msg,
          traceData: data,
        }));
      },
      onDone: (data: {
        sources?: Array<{ title: string; url?: string }>;
        response_id?: string;
      }) => {
        if (!currentSessionId) return;
        updateLastMessage(currentSessionId, (msg) => ({
          ...msg,
          isStreaming: false,
          sources: data.sources,
          // Task 014: 피드백 키 저장. 버튼은 이 값이 있을 때만 활성.
          responseId: data.response_id ?? msg.responseId,
        }));
      },
      onError: (error: Error) => {
        if (!currentSessionId) return;
        updateLastMessage(currentSessionId, (msg) => ({
          ...msg,
          isStreaming: false,
          isError: true,
          errorMessage: error.message,
        }));
      },
    }),
    [currentSessionId, updateLastMessage],
  );

  const { sendMessage, isStreaming, abort } = useChatStream(streamCallbacks);

  const handleSend = useCallback(
    async (text: string) => {
      let activeSessionId = currentSessionId;
      let session = currentSession;

      // 세션이 없으면 생성
      if (!session) {
        session = createSession(selectedProfileId, selectedProfileName);
        activeSessionId = session.id;
        router.push(`/${session.id}`);
      }

      // 사용자 메시지 추가
      const userMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content: text,
        timestamp: new Date().toISOString(),
        isStreaming: false,
        isError: false,
      };
      addMessage(activeSessionId!, userMessage);

      // AI 응답 placeholder 추가
      const aiMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        isStreaming: true,
        isError: false,
      };
      addMessage(activeSessionId!, aiMessage);

      // SSE 스트리밍 시작 — AuthProvider 의 accessToken 을 사용
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
      router,
      accessToken,
    ],
  );

  const handleNewChat = useCallback(() => {
    clearCurrentSession();
    router.push('/');
  }, [clearCurrentSession, router]);

  const handleSelectSession = useCallback(
    (id: string) => {
      switchSession(id);
      router.push(`/${id}`);
    },
    [switchSession, router],
  );

  // Task 014: 응답 말풍선 👍/👎 클릭 처리.
  // 낙관적 UI 업데이트 → BFF POST. 실패 시 원상 복구.
  const handleFeedback = useCallback(
    async (messageId: string, responseId: string, score: FeedbackScore) => {
      if (!currentSessionId) return;
      // 이전 값 저장 (롤백용)
      const prev = currentSession?.messages.find((m) => m.id === messageId)
        ?.feedback;
      // optimistic
      updateMessageById(currentSessionId, messageId, (msg) => ({
        ...msg,
        feedback: score,
      }));
      try {
        await submitFeedback({ response_id: responseId, score });
      } catch (err) {
        // 롤백
        updateMessageById(currentSessionId, messageId, (msg) => ({
          ...msg,
          feedback: prev ?? null,
        }));
        // eslint-disable-next-line no-console
        console.warn('feedback submit failed', err);
      }
    },
    [currentSessionId, currentSession, updateMessageById],
  );

  return (
    <div className="flex h-screen overflow-hidden">
      <ChatSidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        selectedProfileId={selectedProfileId}
        onProfileChange={(id, name) => {
          setSelectedProfileId(id);
          setSelectedProfileName(name);
        }}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onDeleteSession={deleteSession}
        onRenameSession={updateSessionTitle}
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(!sidebarOpen)}
      />
      <main className="flex flex-1 flex-col overflow-hidden">
        {/* 모바일 헤더 */}
        <div className="flex items-center gap-2 border-b border-[var(--color-neutral-200)] px-4 py-3 lg:hidden">
          <button
            onClick={() => setSidebarOpen(true)}
            className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-md)] text-[var(--color-neutral-500)] hover:bg-[var(--color-neutral-100)]"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]">
            {currentSession?.title || 'AI Platform'}
          </span>
        </div>
        {/* 메시지 영역 */}
        <ChatMessageList
          messages={currentSession?.messages || []}
          profileName={selectedProfileName !== '자동 선택' ? selectedProfileName : undefined}
          onFeedback={handleFeedback}
        />
        {/* 입력 영역 */}
        <div className="border-t border-[var(--color-neutral-200)] bg-[var(--surface-page)] px-4 py-3">
          <div className="mx-auto max-w-[var(--content-max-width)]">
            <ChatInput
              onSend={handleSend}
              onStop={abort}
              isStreaming={isStreaming}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
