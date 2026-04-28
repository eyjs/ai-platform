'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';

export interface FeedbackFilterValue {
  only_negative: boolean;
  date_from?: string;
  date_to?: string;
  profile_id?: string;
  keyword?: string;
}

interface ProfileOption {
  id: string;
  name: string;
}

interface FeedbackFiltersProps {
  value: FeedbackFilterValue;
  onChange: (next: FeedbackFilterValue) => void;
  profiles?: ProfileOption[];
}

export function FeedbackFilters({ value, onChange, profiles = [] }: FeedbackFiltersProps) {
  const [onlyNegative, setOnlyNegative] = useState(value.only_negative);
  const [dateFrom, setDateFrom] = useState(value.date_from ?? '');
  const [dateTo, setDateTo] = useState(value.date_to ?? '');
  const [profileId, setProfileId] = useState(value.profile_id ?? '');
  const [keyword, setKeyword] = useState(value.keyword ?? '');

  const handleApply = () => {
    onChange({
      only_negative: onlyNegative,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      profile_id: profileId || undefined,
      keyword: keyword || undefined,
    });
  };

  const handleReset = () => {
    setOnlyNegative(false);
    setDateFrom('');
    setDateTo('');
    setProfileId('');
    setKeyword('');
    onChange({ only_negative: false });
  };

  return (
    <div
      className="flex flex-wrap items-end gap-3 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-4 py-3"
      role="group"
      aria-label="피드백 필터"
    >
      {profiles.length > 0 && (
        <label className="flex flex-col gap-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
          <span>Profile</span>
          <select
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
            aria-label="Profile 필터"
            className="h-9 rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] bg-[var(--surface-page)] px-2 text-[var(--font-size-sm)] text-[var(--color-neutral-800)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary-500)]"
          >
            <option value="">전체</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
      )}
      <label className="flex flex-col gap-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
        <span>키워드</span>
        <input
          type="text"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="질문/응답 검색"
          aria-label="키워드 검색"
          className="h-9 w-40 rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] bg-[var(--surface-page)] px-2 text-[var(--font-size-sm)] text-[var(--color-neutral-800)] placeholder:text-[var(--color-neutral-400)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary-500)]"
        />
      </label>
      <label className="flex items-center gap-2 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]">
        <input
          type="checkbox"
          checked={onlyNegative}
          onChange={(e) => setOnlyNegative(e.target.checked)}
          aria-label="싫어요만 보기"
          className="h-4 w-4 rounded-[var(--radius-sm)] border-[var(--color-neutral-300)] text-[var(--color-primary-500)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary-500)]"
        />
        <span>싫어요만</span>
      </label>
      <label className="flex flex-col gap-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
        <span>시작일</span>
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          aria-label="시작일"
          className="h-9 rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] bg-[var(--surface-page)] px-2 text-[var(--font-size-sm)] text-[var(--color-neutral-800)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary-500)]"
        />
      </label>
      <label className="flex flex-col gap-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
        <span>종료일</span>
        <input
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          aria-label="종료일"
          className="h-9 rounded-[var(--radius-md)] border border-[var(--color-neutral-300)] bg-[var(--surface-page)] px-2 text-[var(--font-size-sm)] text-[var(--color-neutral-800)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary-500)]"
        />
      </label>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="primary" onClick={handleApply}>
          적용
        </Button>
        <Button size="sm" variant="ghost" onClick={handleReset}>
          초기화
        </Button>
      </div>
    </div>
  );
}
