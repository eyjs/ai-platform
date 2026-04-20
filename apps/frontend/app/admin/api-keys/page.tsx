'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ApiKeyTable } from '@/components/admin/api-keys/api-key-table';
import { RevokeConfirmModal } from '@/components/admin/api-keys/revoke-confirm-modal';
import { RotateConfirmModal } from '@/components/admin/api-keys/rotate-confirm-modal';
import { PlaintextRevealModal } from '@/components/admin/api-keys/plaintext-reveal-modal';
import {
  listApiKeys,
  revokeApiKey,
  rotateApiKey,
} from '@/lib/api/bff-api-keys';
import type { ApiKey } from '@/types/api-key';

export default function ApiKeysPage() {
  const [items, setItems] = useState<ApiKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<ApiKey | null>(null);
  const [rotateTarget, setRotateTarget] = useState<ApiKey | null>(null);
  const [plaintext, setPlaintext] = useState<string | null>(null);

  async function refresh() {
    try {
      const data = await listApiKeys();
      setItems(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : '조회 실패');
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleRevoke() {
    if (!revokeTarget) return;
    try {
      await revokeApiKey(revokeTarget.id);
      setRevokeTarget(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : '폐기 실패');
    }
  }

  async function handleRotate() {
    if (!rotateTarget) return;
    try {
      const res = await rotateApiKey(rotateTarget.id);
      setRotateTarget(null);
      setPlaintext(res.plaintext_key);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : '회전 실패');
    }
  }

  return (
    <div className="flex flex-col gap-[var(--spacing-4)] p-[var(--spacing-5)]">
      <div className="flex items-center justify-between">
        <h1 className="text-[var(--font-size-2xl)] font-semibold text-[var(--color-neutral-900)]">
          API Keys
        </h1>
        <Link
          href="/admin/api-keys/new"
          aria-label="신규 API Key 발급"
          className="rounded-[var(--radius-md)] bg-[var(--color-primary-600)] px-[var(--spacing-3)] py-[var(--spacing-2)] text-[var(--color-neutral-50)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
        >
          신규 발급
        </Link>
      </div>

      {error && (
        <div className="rounded-[var(--radius-md)] bg-[var(--color-danger)]/10 p-[var(--spacing-3)] text-[var(--color-danger)]">
          {error}
        </div>
      )}

      {items === null ? (
        <p className="text-[var(--color-neutral-500)]">로딩 중…</p>
      ) : (
        <ApiKeyTable
          items={items}
          onRevoke={(id) => setRevokeTarget(items.find((i) => i.id === id) ?? null)}
          onRotate={(id) => setRotateTarget(items.find((i) => i.id === id) ?? null)}
        />
      )}

      {revokeTarget && (
        <RevokeConfirmModal
          keyName={revokeTarget.name}
          onConfirm={handleRevoke}
          onCancel={() => setRevokeTarget(null)}
        />
      )}
      {rotateTarget && (
        <RotateConfirmModal
          keyName={rotateTarget.name}
          onConfirm={handleRotate}
          onCancel={() => setRotateTarget(null)}
        />
      )}
      {plaintext && (
        <PlaintextRevealModal
          plaintextKey={plaintext}
          onClose={() => setPlaintext(null)}
        />
      )}
    </div>
  );
}
