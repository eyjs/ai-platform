'use client';

import { useState } from 'react';
import { cn } from '@/lib/cn';
import type { ChatSession } from '@/types/chat';

interface SessionItemProps {
  session: ChatSession;
  isActive: boolean;
  onClick: () => void;
  onDelete: () => void;
  onRename: (title: string) => void;
}

export function SessionItem({
  session,
  isActive,
  onClick,
  onDelete,
  onRename,
}: SessionItemProps) {
  const [showMenu, setShowMenu] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(session.title);

  const handleRename = () => {
    const trimmed = editTitle.trim();
    if (trimmed && trimmed !== session.title) {
      onRename(trimmed);
    }
    setIsEditing(false);
  };

  return (
    <div
      className={cn(
        'group relative flex items-center gap-2 rounded-[var(--radius-md)] px-3 py-2 cursor-pointer',
        'hover:bg-[var(--color-neutral-200)] transition-colors',
        isActive && 'bg-[var(--color-primary-50)] hover:bg-[var(--color-primary-100)]',
      )}
      onClick={!isEditing ? onClick : undefined}
    >
      <div className="flex-1 min-w-0">
        {isEditing ? (
          <input
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onBlur={handleRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleRename();
              if (e.key === 'Escape') setIsEditing(false);
            }}
            className="w-full bg-transparent text-[var(--font-size-sm)] outline-none"
            autoFocus
            onClick={(e) => e.stopPropagation()}
          />
        ) : (
          <>
            <p className="truncate text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-800)]">
              {session.title}
            </p>
            <p className="truncate text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
              {session.profileName}
            </p>
          </>
        )}
      </div>
      {!isEditing && (
        <div className="relative">
          <button
            onClick={(e) => {
              e.stopPropagation();
              setShowMenu(!showMenu);
            }}
            className={cn(
              'flex h-6 w-6 items-center justify-center rounded text-[var(--color-neutral-400)]',
              'opacity-0 group-hover:opacity-100 hover:bg-[var(--color-neutral-300)] transition-opacity',
            )}
          >
            ...
          </button>
          {showMenu && (
            <div
              className="absolute right-0 top-7 z-[var(--z-dropdown)] w-32 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-elevated)] py-1 shadow-[var(--shadow-md)]"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                onClick={() => {
                  setIsEditing(true);
                  setShowMenu(false);
                }}
                className="flex w-full items-center px-3 py-1.5 text-[var(--font-size-sm)] text-[var(--color-neutral-700)] hover:bg-[var(--color-neutral-100)]"
              >
                이름 변경
              </button>
              <button
                onClick={() => {
                  onDelete();
                  setShowMenu(false);
                }}
                className="flex w-full items-center px-3 py-1.5 text-[var(--font-size-sm)] text-[var(--color-error)] hover:bg-[var(--color-error-light)]"
              >
                삭제
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
