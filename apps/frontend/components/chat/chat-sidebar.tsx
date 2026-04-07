'use client';

import { useState, useEffect } from 'react';
import { cn } from '@/lib/cn';
import { Button } from '@/components/ui/button';
import { Dropdown, type DropdownOption } from '@/components/ui/dropdown';
import { SessionList } from './session-list';
import { fetchProfiles } from '@/lib/api/chat';
import type { ChatSession, ChatProfileOption } from '@/types/chat';

interface ChatSidebarProps {
  sessions: ChatSession[];
  currentSessionId: string | null;
  selectedProfileId: string;
  onProfileChange: (id: string, name: string) => void;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string, title: string) => void;
  isOpen: boolean;
  onToggle: () => void;
}

export function ChatSidebar({
  sessions,
  currentSessionId,
  selectedProfileId,
  onProfileChange,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  onRenameSession,
  isOpen,
  onToggle,
}: ChatSidebarProps) {
  const [profiles, setProfiles] = useState<ChatProfileOption[]>([]);

  useEffect(() => {
    fetchProfiles()
      .then(setProfiles)
      .catch(() => {
        // Profile 로드 실패 시 빈 목록
      });
  }, []);

  const profileOptions: DropdownOption[] = [
    { value: '', label: '자동 선택' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  return (
    <>
      {/* 모바일 백드롭 */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-[var(--surface-overlay)] z-[var(--z-sidebar)] lg:hidden"
          onClick={onToggle}
        />
      )}
      <aside
        className={cn(
          'fixed left-0 top-0 z-[var(--z-sidebar)] flex h-full flex-col',
          'w-[var(--sidebar-width)] bg-[var(--surface-sidebar)] border-r border-[var(--color-neutral-200)]',
          'transition-transform duration-[var(--duration-normal)]',
          'lg:relative lg:translate-x-0',
          isOpen ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between border-b border-[var(--color-neutral-200)] p-4">
          <h1 className="text-[var(--font-size-lg)] font-bold text-[var(--color-neutral-900)]">
            AI Platform
          </h1>
          <button
            onClick={onToggle}
            className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-md)] text-[var(--color-neutral-500)] hover:bg-[var(--color-neutral-200)] lg:hidden"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* 새 대화 + Profile 선택 */}
        <div className="flex flex-col gap-3 p-4">
          <Button variant="primary" fullWidth onClick={onNewChat}>
            + 새 대화
          </Button>
          <Dropdown
            options={profileOptions}
            value={selectedProfileId}
            onChange={(value) => {
              const profile = profiles.find((p) => p.id === value);
              onProfileChange(value, profile?.name || '자동 선택');
            }}
            placeholder="Profile 선택"
          />
        </div>

        {/* 세션 목록 */}
        <div className="flex-1 overflow-y-auto">
          <SessionList
            sessions={sessions}
            currentSessionId={currentSessionId}
            onSelectSession={onSelectSession}
            onDeleteSession={onDeleteSession}
            onRenameSession={onRenameSession}
          />
        </div>
      </aside>
    </>
  );
}
