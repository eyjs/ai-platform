'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';

export interface FeedbackFilterValue {
  only_negative: boolean;
  date_from?: string;
  date_to?: string;
}

interface FeedbackFiltersProps {
  value: FeedbackFilterValue;
  onChange: (next: FeedbackFilterValue) => void;
}

export function FeedbackFilters({ value, onChange }: FeedbackFiltersProps) {
  // 로컬 상태 — "적용" 버튼 눌러야 외부 반영 (쓸데없는 fetch 방지)
  const [onlyNegative, setOnlyNegative] = useState(value.only_negative);
  const [dateFrom, setDateFrom] = useState(value.date_from ?? '');
  const [dateTo, setDateTo] = useState(value.date_to ?? '');

  const handleApply = () => {
    onChange({
      only_negative: onlyNegative,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    });
  };

  const handleReset = () => {
    setOnlyNegative(false);
    setDateFrom('');
    setDateTo('');
    onChange({ only_negative: false });
  };

  return (
    <div
      className="flex flex-wrap items-end gap-3 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--surface-card)] px-4 py-3"
      role="group"
      aria-label="피드백 필터"
    >
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
