'use client';

import { Input } from '@/components/ui/input';
import { Dropdown } from '@/components/ui/dropdown';

interface ProfileFiltersProps {
  search: string;
  onSearchChange: (value: string) => void;
  modeFilter: string;
  onModeFilterChange: (value: string) => void;
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
}

const modeOptions = [
  { value: '', label: '모든 모드' },
  { value: 'deterministic', label: 'Deterministic' },
  { value: 'agentic', label: 'Agentic' },
  { value: 'workflow', label: 'Workflow' },
  { value: 'hybrid', label: 'Hybrid' },
];

const statusOptions = [
  { value: '', label: '모든 상태' },
  { value: 'active', label: '활성' },
  { value: 'inactive', label: '비활성' },
];

export function ProfileFilters({
  search,
  onSearchChange,
  modeFilter,
  onModeFilterChange,
  statusFilter,
  onStatusFilterChange,
}: ProfileFiltersProps) {
  return (
    <div className="flex flex-wrap gap-3">
      <div className="flex-1 min-w-[200px]">
        <Input
          placeholder="Profile 검색..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          size="md"
        />
      </div>
      <Dropdown
        options={modeOptions}
        value={modeFilter}
        onChange={onModeFilterChange}
        placeholder="모드 필터"
        className="w-40"
      />
      <Dropdown
        options={statusOptions}
        value={statusFilter}
        onChange={onStatusFilterChange}
        placeholder="상태 필터"
        className="w-32"
      />
    </div>
  );
}
