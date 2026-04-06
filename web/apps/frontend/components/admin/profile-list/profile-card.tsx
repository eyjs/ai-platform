'use client';

import Link from 'next/link';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Toggle } from '@/components/ui/toggle';
import { Button } from '@/components/ui/button';
import type { ProfileListItem } from '@/types/profile';

interface ProfileCardProps {
  profile: ProfileListItem;
  onToggleActive: (id: string, isActive: boolean) => void;
  onDelete: (id: string) => void;
}

const modeBadgeVariant: Record<string, 'primary' | 'secondary' | 'success' | 'warning'> = {
  deterministic: 'primary',
  agentic: 'secondary',
  workflow: 'success',
  hybrid: 'warning',
};

export function ProfileCard({ profile, onToggleActive, onDelete }: ProfileCardProps) {
  return (
    <Card variant="interactive" className="flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <h3 className="truncate text-[var(--font-size-base)] font-semibold text-[var(--color-neutral-900)]">
            {profile.name}
          </h3>
          <p className="font-mono text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
            {profile.id}
          </p>
        </div>
        <Toggle
          checked={profile.isActive}
          onChange={(checked) => onToggleActive(profile.id, checked)}
          size="sm"
        />
      </div>

      <div className="flex flex-wrap gap-1.5">
        <Badge variant={modeBadgeVariant[profile.mode] || 'neutral'}>
          {profile.mode}
        </Badge>
        <Badge variant="neutral">{profile.securityLevelMax}</Badge>
      </div>

      {profile.domainScopes.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {profile.domainScopes.map((scope) => (
            <span
              key={scope}
              className="rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-1.5 py-0.5 text-[10px] text-[var(--color-neutral-600)]"
            >
              {scope}
            </span>
          ))}
        </div>
      )}

      <div className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
        <span>도구 {profile.toolsCount}개</span>
        <span className="mx-2">|</span>
        <span>{profile.routerModel} / {profile.mainModel}</span>
      </div>

      <div className="flex gap-2 pt-1 border-t border-[var(--color-neutral-100)]">
        <Link href={`/admin/profiles/${profile.id}`} className="flex-1">
          <Button variant="secondary" size="sm" fullWidth>
            편집
          </Button>
        </Link>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onDelete(profile.id)}
          className="text-[var(--color-error)] hover:bg-[var(--color-error-light)]"
        >
          삭제
        </Button>
      </div>
    </Card>
  );
}
