'use client';

interface Props {
  keyName: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function RotateConfirmModal({ keyName, onConfirm, onCancel }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rotate-title"
    >
      <div className="w-full max-w-[480px] rounded-[var(--radius-lg)] bg-[var(--surface-card)] p-[var(--spacing-5)]">
        <h2
          id="rotate-title"
          className="text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]"
        >
          API Key 회전
        </h2>
        <p className="mt-[var(--spacing-2)] text-[var(--font-size-sm)] text-[var(--color-neutral-600)]">
          <strong>{keyName}</strong> 키를 회전하면 기존 키는 즉시 폐기되고 신규 키가 발급됩니다.
          신규 평문은 <strong>1회만</strong> 표시됩니다.
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
            aria-label="회전 확인"
            className="rounded-[var(--radius-md)] bg-[var(--color-primary-600)] px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-neutral-50)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
          >
            회전
          </button>
        </div>
      </div>
    </div>
  );
}
