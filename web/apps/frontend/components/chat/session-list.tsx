'use client';

import { useMemo } from 'react';
import { groupSessionsByDate } from '@/lib/chat-storage';
import { SessionItem } from './session-item';
import type { ChatSession } from '@/types/chat';

interface SessionListProps {
  sessions: ChatSession[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string, title: string) => void;
}

export function SessionList({
  sessions,
  currentSessionId,
  onSelectSession,
  onDeleteSession,
  onRenameSession,
}: SessionListProps) {
  const groups = useMemo(() => groupSessionsByDate(sessions), [sessions]);

  if (sessions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center px-4 py-8 text-center">
        <p className="text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
          대화가 없습니다
        </p>
        <p className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
          새 대화를 시작해보세요
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1 px-2">
      {Array.from(groups.entries()).map(
        ([label, groupSessions]) =>
          groupSessions.length > 0 && (
            <div key={label}>
              <p className="px-3 py-1.5 text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-400)]">
                {label}
              </p>
              {groupSessions.map((session) => (
                <SessionItem
                  key={session.id}
                  session={session}
                  isActive={session.id === currentSessionId}
                  onClick={() => onSelectSession(session.id)}
                  onDelete={() => onDeleteSession(session.id)}
                  onRename={(title) => onRenameSession(session.id, title)}
                />
              ))}
            </div>
          ),
      )}
    </div>
  );
}
