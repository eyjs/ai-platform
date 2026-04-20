'use client';

interface DiffResult {
  added: Record<string, unknown>;
  removed: Record<string, unknown>;
  changed: Record<string, { before: unknown; after: unknown }>;
}

interface Props {
  diff: DiffResult | null;
  onRollback?: () => void;
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return '∅';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function HistoryDiffViewer({ diff, onRollback }: Props) {
  if (!diff) return <p className="text-[var(--color-neutral-500)]">로딩 중…</p>;

  const addedKeys = Object.keys(diff.added);
  const removedKeys = Object.keys(diff.removed);
  const changedKeys = Object.keys(diff.changed);
  const total = addedKeys.length + removedKeys.length + changedKeys.length;

  return (
    <div
      aria-label="프로파일 diff 뷰어"
      className="flex flex-col gap-[var(--spacing-3)]"
    >
      {total === 0 && (
        <p className="text-[var(--color-neutral-500)]">변경사항이 없습니다.</p>
      )}

      {addedKeys.length > 0 && (
        <section>
          <h3 className="text-[var(--font-size-sm)] font-medium text-[var(--color-success)]">
            추가 ({addedKeys.length})
          </h3>
          <ul className="mt-[var(--spacing-1)] flex flex-col gap-[var(--spacing-1)]">
            {addedKeys.map((k) => (
              <li
                key={k}
                className="rounded-[var(--radius-sm)] border-l-2 border-[var(--color-success)] bg-[var(--color-success)]/10 px-[var(--spacing-2)] py-[var(--spacing-1)] font-mono text-[var(--font-size-xs)]"
              >
                + {k}: {fmt(diff.added[k])}
              </li>
            ))}
          </ul>
        </section>
      )}

      {removedKeys.length > 0 && (
        <section>
          <h3 className="text-[var(--font-size-sm)] font-medium text-[var(--color-danger)]">
            삭제 ({removedKeys.length})
          </h3>
          <ul className="mt-[var(--spacing-1)] flex flex-col gap-[var(--spacing-1)]">
            {removedKeys.map((k) => (
              <li
                key={k}
                className="rounded-[var(--radius-sm)] border-l-2 border-[var(--color-danger)] bg-[var(--color-danger)]/10 px-[var(--spacing-2)] py-[var(--spacing-1)] font-mono text-[var(--font-size-xs)]"
              >
                - {k}: {fmt(diff.removed[k])}
              </li>
            ))}
          </ul>
        </section>
      )}

      {changedKeys.length > 0 && (
        <section>
          <h3 className="text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-700)]">
            변경 ({changedKeys.length})
          </h3>
          <ul className="mt-[var(--spacing-1)] flex flex-col gap-[var(--spacing-1)]">
            {changedKeys.map((k) => {
              const c = diff.changed[k];
              return (
                <li
                  key={k}
                  className="rounded-[var(--radius-sm)] border border-[var(--color-neutral-200)] px-[var(--spacing-2)] py-[var(--spacing-1)] font-mono text-[var(--font-size-xs)]"
                >
                  <div className="text-[var(--color-neutral-700)]">{k}</div>
                  <div className="text-[var(--color-danger)]">- {fmt(c.before)}</div>
                  <div className="text-[var(--color-success)]">+ {fmt(c.after)}</div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {onRollback && (
        <button
          type="button"
          onClick={onRollback}
          aria-label="이 버전으로 되돌리기"
          className="mt-[var(--spacing-2)] self-start rounded-[var(--radius-md)] border border-[var(--color-primary-500)] px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-primary-700)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
        >
          이 버전으로 되돌리기
        </button>
      )}
    </div>
  );
}
