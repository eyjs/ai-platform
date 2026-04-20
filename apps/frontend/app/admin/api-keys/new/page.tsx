'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ApiKeyForm } from '@/components/admin/api-keys/api-key-form';
import { PlaintextRevealModal } from '@/components/admin/api-keys/plaintext-reveal-modal';
import { createApiKey } from '@/lib/api/bff-api-keys';
import type { ApiKeyCreateRequest } from '@/types/api-key';

export default function NewApiKeyPage() {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plaintext, setPlaintext] = useState<string | null>(null);

  async function handleSubmit(dto: ApiKeyCreateRequest) {
    setSubmitting(true);
    setError(null);
    try {
      const res = await createApiKey(dto);
      setPlaintext(res.plaintext_key);
    } catch (e) {
      setError(e instanceof Error ? e.message : '발급 실패');
    } finally {
      setSubmitting(false);
    }
  }

  function handleClose() {
    setPlaintext(null);
    router.push('/admin/api-keys');
  }

  return (
    <div className="flex flex-col gap-[var(--spacing-4)] p-[var(--spacing-5)]">
      <h1 className="text-[var(--font-size-2xl)] font-semibold text-[var(--color-neutral-900)]">
        API Key 신규 발급
      </h1>

      {error && (
        <div className="rounded-[var(--radius-md)] bg-[var(--color-danger)]/10 p-[var(--spacing-3)] text-[var(--color-danger)]">
          {error}
        </div>
      )}

      <ApiKeyForm onSubmit={handleSubmit} submitting={submitting} />

      {plaintext && (
        <PlaintextRevealModal plaintextKey={plaintext} onClose={handleClose} />
      )}
    </div>
  );
}
