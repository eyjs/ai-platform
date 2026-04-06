'use client';

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/cn';

interface EditorToolbarProps {
  profileName: string;
  hasChanges: boolean;
  isSaving: boolean;
  isNew: boolean;
  onSave: () => void;
  onBack: () => void;
  onHistoryToggle: () => void;
}

export function EditorToolbar({
  profileName,
  hasChanges,
  isSaving,
  isNew,
  onSave,
  onBack,
  onHistoryToggle,
}: EditorToolbarProps) {
  return (
    <div
      className={cn(
        'flex items-center justify-between border-b border-[var(--color-neutral-200)]',
        'bg-[var(--surface-card)] px-4',
      )}
      style={{ height: 'var(--editor-toolbar-height)' }}
    >
      <div className="flex items-center gap-3">
        <button
          onClick={onBack}
          className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-md)] text-[var(--color-neutral-500)] hover:bg-[var(--color-neutral-100)]"
        >
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <span className="text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
          {isNew ? '새 Profile' : profileName}
        </span>
        {hasChanges && (
          <Badge variant="warning" size="sm">변경됨</Badge>
        )}
      </div>
      <div className="flex items-center gap-2">
        {!isNew && (
          <Button variant="ghost" size="sm" onClick={onHistoryToggle}>
            히스토리
          </Button>
        )}
        <Button
          variant="primary"
          size="sm"
          onClick={onSave}
          loading={isSaving}
          disabled={!hasChanges}
        >
          저장
        </Button>
      </div>
    </div>
  );
}
