'use client';

import { useState, useCallback, useEffect } from 'react';
import type { ChatSession, ChatMessage } from '@/types/chat';
import {
  loadSessions,
  saveSessions,
  createNewSession,
  addMessageToSession,
  updateLastMessage as updateLastMsg,
} from '@/lib/chat-storage';

export function useChatSessions() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);

  // 초기 로드
  useEffect(() => {
    const loaded = loadSessions();
    setSessions(loaded);
  }, []);

  // 변경 시 저장
  useEffect(() => {
    if (sessions.length > 0) {
      saveSessions(sessions);
    }
  }, [sessions]);

  const currentSession =
    sessions.find((s) => s.id === currentSessionId) || null;

  const createSession = useCallback(
    (profileId: string, profileName: string): ChatSession => {
      const session = createNewSession(profileId, profileName);
      setSessions((prev) => [session, ...prev]);
      setCurrentSessionId(session.id);
      return session;
    },
    [],
  );

  const switchSession = useCallback((sessionId: string) => {
    setCurrentSessionId(sessionId);
  }, []);

  const deleteSession = useCallback(
    (sessionId: string) => {
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (currentSessionId === sessionId) {
        setCurrentSessionId(null);
      }
    },
    [currentSessionId],
  );

  const updateSessionTitle = useCallback(
    (sessionId: string, title: string) => {
      setSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? { ...s, title } : s)),
      );
    },
    [],
  );

  const addMessage = useCallback(
    (sessionId: string, message: ChatMessage) => {
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId ? addMessageToSession(s, message) : s,
        ),
      );
    },
    [],
  );

  const updateLastMessage = useCallback(
    (sessionId: string, updater: (msg: ChatMessage) => ChatMessage) => {
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessionId ? updateLastMsg(s, updater) : s,
        ),
      );
    },
    [],
  );

  const updateMessageById = useCallback(
    (
      sessionId: string,
      messageId: string,
      updater: (msg: ChatMessage) => ChatMessage,
    ) => {
      setSessions((prev) =>
        prev.map((s) => {
          if (s.id !== sessionId) return s;
          return {
            ...s,
            messages: s.messages.map((m) =>
              m.id === messageId ? updater(m) : m,
            ),
            updatedAt: new Date().toISOString(),
          };
        }),
      );
    },
    [],
  );

  const clearCurrentSession = useCallback(() => {
    setCurrentSessionId(null);
  }, []);

  return {
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
  };
}
