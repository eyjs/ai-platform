'use client';

import { useState } from 'react';

interface Props {
  plaintextKey: string;
  onClose: () => void;
}

export function PlaintextRevealModal({ plaintextKey, onClose }: Props) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(plaintextKey);
      setCopied(true);
    } catch (e) {
      // ignore
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-labelledby="plaintext-reveal-title"
    >
      <div className="w-full max-w-[540px] rounded-[var(--radius-lg)] bg-[var(--surface-card)] p-[var(--spacing-5)] shadow-lg">
        <h2
          id="plaintext-reveal-title"
          className="text-[var(--font-size-lg)] font-semibold text-[var(--color-neutral-900)]"
        >
          API Key 발급 완료
        </h2>
        <p className="mt-[var(--spacing-2)] text-[var(--font-size-sm)] text-[var(--color-danger)]">
          이 키는 <strong>지금 한 번만</strong> 표시됩니다. 안전한 곳에 저장하세요. 닫으면 다시 볼
          수 없습니다.
        </p>
        <div className="mt-[var(--spacing-3)] rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--color-neutral-50)] p-[var(--spacing-3)] font-mono text-[var(--font-size-sm)] break-all">
          {plaintextKey}
        </div>
        <div className="mt-[var(--spacing-4)] flex justify-end gap-[var(--spacing-2)]">
          <button
            type="button"
            onClick={handleCopy}
            aria-label="API Key 복사"
            className="rounded-[var(--radius-md)] border border-[var(--color-primary-500)] px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-primary-700)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
          >
            {copied ? '복사됨' : '복사'}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="rounded-[var(--radius-md)] bg-[var(--color-primary-600)] px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-neutral-50)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
          >
            닫기
          </button>
        </div>
      </div>
    </div>
  );
}
