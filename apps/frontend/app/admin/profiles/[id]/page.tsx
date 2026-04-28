'use client';

import { use, useEffect, useState } from 'react';
import Link from 'next/link';
import { EditorLayout } from '@/components/admin/profile-editor/editor-layout';
import { ProfileStatsPanel } from '@/components/admin/profile-stats-panel';
import { Badge } from '@/components/ui/badge';
import { listApiKeys } from '@/lib/api/bff-api-keys';
import type { ApiKey } from '@/types/api-key';

export default function ProfileEditPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [connectedKeys, setConnectedKeys] = useState<ApiKey[]>([]);

  useEffect(() => {
    listApiKeys()
      .then((keys) => setConnectedKeys(keys.filter((k) => k.allowed_profiles.includes(id))))
      .catch(() => {});
  }, [id]);

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <div className="shrink-0 border-b border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-4 py-3">
        <ProfileStatsPanel profileId={id} />
        {connectedKeys.length > 0 && (
          <div className="mt-3">
            <p className="mb-1 text-[var(--font-size-xs)] font-medium text-[var(--color-neutral-500)]">
              연결된 API Key
            </p>
            <div className="flex flex-wrap gap-1">
              {connectedKeys.map((k) => (
                <Link key={k.id} href={`/admin/api-keys/${k.id}`}>
                  <Badge variant={k.is_active ? 'success' : 'neutral'} size="sm">
                    {k.name}
                  </Badge>
                </Link>
              ))}
            </div>
          </div>
        )}
        <div className="mt-2">
          <Link
            href={`/admin/profiles/${id}/history`}
            className="text-[var(--font-size-xs)] text-[var(--color-primary-600)] hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
            aria-label="변경 이력 보기"
          >
            변경 이력 →
          </Link>
        </div>
      </div>
      <div className="flex-1 overflow-hidden">
        <EditorLayout profileId={id} />
      </div>
    </div>
  );
}
