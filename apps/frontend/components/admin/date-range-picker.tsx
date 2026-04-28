'use client';

import { useState } from 'react';
import { cn } from '@/lib/cn';
import { Button } from '@/components/ui/button';

type PresetKey = 'today' | '7d' | '30d' | 'custom';

const presets: Array<{ key: PresetKey; label: string }> = [
  { key: 'today', label: '오늘' },
  { key: '7d', label: '7일' },
  { key: '30d', label: '30일' },
  { key: 'custom', label: '직접 선택' },
];

function getPresetRange(key: PresetKey): { start: string; end: string } {
  const end = new Date();
  const start = new Date();
  if (key === '7d') start.setDate(end.getDate() - 7);
  else if (key === '30d') start.setDate(end.getDate() - 30);
  return {
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  };
}

export interface DateRangePickerProps {
  onChange: (startDate: string, endDate: string) => void;
  className?: string;
}

export function DateRangePicker({ onChange, className }: DateRangePickerProps) {
  const [activePreset, setActivePreset] = useState<PresetKey>('today');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');

  const handlePreset = (key: PresetKey) => {
    setActivePreset(key);
    if (key !== 'custom') {
      const range = getPresetRange(key);
      onChange(range.start, range.end);
    }
  };

  const handleCustomApply = () => {
    if (customStart && customEnd) {
      onChange(customStart, customEnd);
    }
  };

  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      {presets.map((preset) => (
        <Button
          key={preset.key}
          variant={activePreset === preset.key ? 'primary' : 'ghost'}
          size="sm"
          onClick={() => handlePreset(preset.key)}
          aria-label={`기간 ${preset.label}`}
        >
          {preset.label}
        </Button>
      ))}
      {activePreset === 'custom' && (
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={customStart}
            onChange={(e) => setCustomStart(e.target.value)}
            className={cn(
              'h-8 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)]',
              'bg-[var(--surface-input)] px-2 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]',
              'focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-500)]',
            )}
            aria-label="시작 날짜"
          />
          <span className="text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">~</span>
          <input
            type="date"
            value={customEnd}
            onChange={(e) => setCustomEnd(e.target.value)}
            className={cn(
              'h-8 rounded-[var(--radius-md)] border border-[var(--color-neutral-200)]',
              'bg-[var(--surface-input)] px-2 text-[var(--font-size-sm)] text-[var(--color-neutral-700)]',
              'focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-500)]',
            )}
            aria-label="종료 날짜"
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={handleCustomApply}
            disabled={!customStart || !customEnd}
            aria-label="날짜 범위 적용"
          >
            적용
          </Button>
        </div>
      )}
    </div>
  );
}
