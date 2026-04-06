import { cn } from '@/lib/cn';
import type { ValidationIssue } from '@/hooks/use-yaml-validation';

interface EditorStatusBarProps {
  issues: ValidationIssue[];
  isValid: boolean;
}

export function EditorStatusBar({ issues, isValid }: EditorStatusBarProps) {
  const errorCount = issues.filter((i) => i.severity === 'error').length;
  const warningCount = issues.filter((i) => i.severity === 'warning').length;

  return (
    <div
      className={cn(
        'flex items-center justify-between border-t border-[var(--color-neutral-200)]',
        'bg-[var(--surface-card)] px-4 text-[var(--font-size-xs)]',
      )}
      style={{ height: 'var(--editor-status-height)' }}
    >
      <div className="flex items-center gap-4">
        <span className={cn(
          'flex items-center gap-1',
          isValid ? 'text-[var(--color-success)]' : 'text-[var(--color-error)]',
        )}>
          {isValid ? '\u2713 유효' : '\u2717 오류 있음'}
        </span>
        {errorCount > 0 && (
          <span className="text-[var(--color-error)]">
            오류 {errorCount}
          </span>
        )}
        {warningCount > 0 && (
          <span className="text-[var(--color-warning)]">
            경고 {warningCount}
          </span>
        )}
      </div>
      <span className="text-[var(--color-neutral-400)]">YAML | UTF-8</span>
    </div>
  );
}
