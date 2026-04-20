'use client';

import { useState } from 'react';
import type { ApiKeyCreateRequest, SecurityLevel } from '@/types/api-key';

interface Props {
  onSubmit: (dto: ApiKeyCreateRequest) => Promise<void> | void;
  submitting?: boolean;
}

const LEVELS: SecurityLevel[] = ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL'];

export function ApiKeyForm({ onSubmit, submitting }: Props) {
  const [name, setName] = useState('');
  const [allowedProfiles, setAllowedProfiles] = useState('');
  const [perMin, setPerMin] = useState(60);
  const [perDay, setPerDay] = useState(10000);
  const [level, setLevel] = useState<SecurityLevel>('PUBLIC');
  const [expiresAt, setExpiresAt] = useState('');

  async function handle(e: React.FormEvent) {
    e.preventDefault();
    const profiles = allowedProfiles
      .split(',')
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    await onSubmit({
      name: name.trim(),
      allowed_profiles: profiles,
      rate_limit_per_min: perMin,
      rate_limit_per_day: perDay,
      security_level_max: level,
      expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
    });
  }

  return (
    <form onSubmit={handle} className="flex flex-col gap-[var(--spacing-4)] max-w-[560px]">
      <label className="flex flex-col gap-[var(--spacing-1)]">
        <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">이름 *</span>
        <input
          aria-label="API Key 이름"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] px-[var(--spacing-3)] py-[var(--spacing-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
        />
      </label>

      <label className="flex flex-col gap-[var(--spacing-1)]">
        <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
          허용 Profile (쉼표 구분; 비우면 전체)
        </span>
        <input
          aria-label="허용 Profile"
          value={allowedProfiles}
          onChange={(e) => setAllowedProfiles(e.target.value)}
          placeholder="general-chat, customer-support"
          className="rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] px-[var(--spacing-3)] py-[var(--spacing-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
        />
      </label>

      <div className="flex gap-[var(--spacing-3)]">
        <label className="flex flex-1 flex-col gap-[var(--spacing-1)]">
          <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
            Rate/분
          </span>
          <input
            aria-label="분당 요청 한도"
            type="number"
            min={1}
            max={10000}
            value={perMin}
            onChange={(e) => setPerMin(parseInt(e.target.value, 10) || 0)}
            className="rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] px-[var(--spacing-3)] py-[var(--spacing-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
          />
        </label>
        <label className="flex flex-1 flex-col gap-[var(--spacing-1)]">
          <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
            Rate/일
          </span>
          <input
            aria-label="일별 요청 한도"
            type="number"
            min={1}
            max={10000000}
            value={perDay}
            onChange={(e) => setPerDay(parseInt(e.target.value, 10) || 0)}
            className="rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] px-[var(--spacing-3)] py-[var(--spacing-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
          />
        </label>
      </div>

      <label className="flex flex-col gap-[var(--spacing-1)]">
        <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
          Security Level
        </span>
        <select
          aria-label="Security Level"
          value={level}
          onChange={(e) => setLevel(e.target.value as SecurityLevel)}
          className="rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] px-[var(--spacing-3)] py-[var(--spacing-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
        >
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-[var(--spacing-1)]">
        <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
          만료일 (선택)
        </span>
        <input
          aria-label="만료일"
          type="date"
          value={expiresAt}
          onChange={(e) => setExpiresAt(e.target.value)}
          className="rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] px-[var(--spacing-3)] py-[var(--spacing-2)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)]"
        />
      </label>

      <button
        type="submit"
        disabled={submitting || !name.trim()}
        aria-label="발급"
        className="mt-[var(--spacing-2)] self-start rounded-[var(--radius-md)] bg-[var(--color-primary-600)] px-[var(--spacing-4)] py-[var(--spacing-2)] text-[var(--color-neutral-50)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-primary-500)] disabled:opacity-50"
      >
        {submitting ? '발급 중…' : '발급'}
      </button>
    </form>
  );
}
