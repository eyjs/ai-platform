'use client';

import {
  createContext,
  useContext,
  useState,
  type ReactNode,
} from 'react';
import { cn } from '@/lib/cn';

interface TabsContextValue {
  activeTab: string;
  setActiveTab: (value: string) => void;
  variant: 'underline' | 'pill';
}

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabsContext() {
  const context = useContext(TabsContext);
  if (!context) throw new Error('Tabs components must be used within Tabs');
  return context;
}

export interface TabsProps {
  defaultValue: string;
  value?: string;
  onValueChange?: (value: string) => void;
  variant?: 'underline' | 'pill';
  children: ReactNode;
  className?: string;
}

export function Tabs({
  defaultValue,
  value,
  onValueChange,
  variant = 'underline',
  children,
  className,
}: TabsProps) {
  const [internalValue, setInternalValue] = useState(defaultValue);
  const activeTab = value ?? internalValue;

  const setActiveTab = (newValue: string) => {
    if (!value) setInternalValue(newValue);
    onValueChange?.(newValue);
  };

  return (
    <TabsContext.Provider value={{ activeTab, setActiveTab, variant }}>
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  );
}

export function TabsList({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const { variant } = useTabsContext();
  return (
    <div
      role="tablist"
      className={cn(
        'flex',
        variant === 'underline' && 'border-b border-[var(--color-neutral-200)] gap-4',
        variant === 'pill' &&
          'gap-1 rounded-[var(--radius-md)] bg-[var(--color-neutral-100)] p-1',
        className,
      )}
    >
      {children}
    </div>
  );
}

export function TabsTrigger({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  const { activeTab, setActiveTab, variant } = useTabsContext();
  const isActive = activeTab === value;

  return (
    <button
      role="tab"
      aria-selected={isActive}
      onClick={() => setActiveTab(value)}
      className={cn(
        'text-[var(--font-size-sm)] font-medium transition-colors',
        variant === 'underline' && [
          'pb-2 border-b-2 -mb-px',
          isActive
            ? 'border-[var(--color-primary-500)] text-[var(--color-primary-600)]'
            : 'border-transparent text-[var(--color-neutral-500)] hover:text-[var(--color-neutral-700)]',
        ],
        variant === 'pill' && [
          'px-3 py-1.5 rounded-[var(--radius-sm)]',
          isActive
            ? 'bg-[var(--surface-card)] text-[var(--color-neutral-900)] shadow-[var(--shadow-xs)]'
            : 'text-[var(--color-neutral-500)] hover:text-[var(--color-neutral-700)]',
        ],
        className,
      )}
    >
      {children}
    </button>
  );
}

export function TabsContent({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  const { activeTab } = useTabsContext();
  if (activeTab !== value) return null;
  return (
    <div role="tabpanel" className={cn('mt-3', className)}>
      {children}
    </div>
  );
}
