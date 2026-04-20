'use client';

interface Props {
  keyName: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function RevokeConfirmModal({ keyName, onConfirm, onCancel }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-labelledby="revoke-title"
    >
      <div className="w-full max-w-[480px] rounded-[var(--radius-lg)] bg-[var(--surface-card)] p-[var(--spacing-5)]">
        <h2
          id="revoke-title"
          className="text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]"
        >
          API Key 폐기
        </h2>
        <p className="mt-[var(--spacing-2)] text-[var(--font-size-sm)] text-[var(--color-neutral-600)]">
          <strong>{keyName}</strong> 을(를) 폐기하면 즉시 이 키로는 API 호출이 불가능해집니다.
          진행하시겠습니까?
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
            aria-label="폐기 확인"
            className="rounded-[var(--radius-md)] bg-[var(--color-danger)] px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-neutral-50)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-danger)]"
          >
            폐기
          </button>
        </div>
      </div>
    </div>
  );
}
