'use client';

interface Props {
  historyId: string;
  version?: number;
  onConfirm: () => void;
  onCancel: () => void;
}

export function RollbackConfirmModal({ historyId, version, onConfirm, onCancel }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rollback-title"
    >
      <div className="w-full max-w-[480px] rounded-[var(--radius-lg)] bg-[var(--surface-card)] p-[var(--spacing-5)]">
        <h2
          id="rollback-title"
          className="text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]"
        >
          버전 롤백
        </h2>
        <p className="mt-[var(--spacing-2)] text-[var(--font-size-sm)] text-[var(--color-neutral-600)]">
          {version ? (
            <>버전 v{version} (<code className="font-mono">{historyId.slice(0, 8)}</code>)</>
          ) : (
            <>이력 ID <code className="font-mono">{historyId.slice(0, 8)}</code></>
          )}
          으로 롤백합니다.
          현재 상태는 새 이력 엔트리로 저장되어 언제든 복원 가능합니다.
        </p>
        <div className="mt-[var(--spacing-4)] flex justify-end gap-[var(--spacing-2)]">
          <button
            type="button"
            onClick={onCancel}
            aria-label="취소"
            className="rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-neutral-700)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
          >
            취소
          </button>
          <button
            type="button"
            onClick={onConfirm}
            aria-label="롤백 확인"
            className="rounded-[var(--radius-md)] bg-[var(--color-primary-600)] px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-neutral-50)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
          >
            롤백
          </button>
        </div>
      </div>
    </div>
  );
}
