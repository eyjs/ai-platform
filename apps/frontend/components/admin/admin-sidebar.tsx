'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/cn';
import { useAuth } from '@/lib/auth/auth-context';
import { Avatar } from '@/components/ui/avatar';

const menuItems = [
  {
    label: '대시보드',
    href: '/admin/dashboard',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
      </svg>
    ),
  },
  {
    label: 'Profiles',
    href: '/admin/profiles',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
      </svg>
    ),
  },
  {
    label: 'API Keys',
    href: '/admin/api-keys',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 7a4 4 0 11-8 0 4 4 0 018 0zm6 6l-4.5 4.5-2-2L12 18.5 10 20l-2-2 2-2L8.5 14.5 14 9l7 4z" />
      </svg>
    ),
  },
  {
    label: '피드백',
    href: '/admin/feedback',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9A2 2 0 0019.66 9H14z" />
      </svg>
    ),
  },
];

const bottomItems = [
  {
    label: '채팅',
    href: '/',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
      </svg>
    ),
  },
];

export function AdminSidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      className={cn(
        'flex h-full flex-col border-r border-[var(--color-neutral-200)] bg-[var(--surface-sidebar)]',
        'transition-[width] duration-[var(--duration-normal)]',
        collapsed ? 'w-[var(--sidebar-width-collapsed)]' : 'w-[var(--sidebar-width)]',
      )}
    >
      {/* 헤더 */}
      <div className="flex items-center justify-between border-b border-[var(--color-neutral-200)] px-4 py-4">
        {!collapsed && (
          <span className="text-[var(--font-size-lg)] font-bold text-[var(--color-neutral-900)]">
            AI Platform
          </span>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex h-8 w-8 items-center justify-center rounded-[var(--radius-md)] text-[var(--color-neutral-400)] hover:bg-[var(--color-neutral-200)]"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={collapsed ? "M9 5l7 7-7 7" : "M15 19l-7-7 7-7"} />
          </svg>
        </button>
      </div>

      {/* 메인 메뉴 */}
      <nav className="flex-1 px-2 py-3">
        <div className="flex flex-col gap-1">
          {menuItems.map((item) => {
            const isActive = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'flex items-center gap-3 rounded-[var(--radius-md)] px-3 py-2 text-[var(--font-size-sm)]',
                  'transition-colors',
                  isActive
                    ? 'bg-[var(--color-primary-50)] text-[var(--color-primary-700)] font-medium'
                    : 'text-[var(--color-neutral-600)] hover:bg-[var(--color-neutral-200)] hover:text-[var(--color-neutral-800)]',
                )}
                title={collapsed ? item.label : undefined}
              >
                {item.icon}
                {!collapsed && <span>{item.label}</span>}
              </Link>
            );
          })}
        </div>

        <div className="my-3 border-t border-[var(--color-neutral-200)]" />

        <div className="flex flex-col gap-1">
          {bottomItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="flex items-center gap-3 rounded-[var(--radius-md)] px-3 py-2 text-[var(--font-size-sm)] text-[var(--color-neutral-600)] hover:bg-[var(--color-neutral-200)]"
              title={collapsed ? item.label : undefined}
            >
              {item.icon}
              {!collapsed && <span>{item.label}</span>}
            </Link>
          ))}
        </div>
      </nav>

      {/* 사용자 정보 */}
      <div className="border-t border-[var(--color-neutral-200)] p-3">
        <div className="flex items-center gap-3">
          <Avatar
            variant="initials"
            initials={user?.displayName?.charAt(0) || '?'}
            size="sm"
          />
          {!collapsed && (
            <div className="flex-1 min-w-0">
              <p className="truncate text-[var(--font-size-sm)] font-medium text-[var(--color-neutral-800)]">
                {user?.displayName || 'User'}
              </p>
              <p className="truncate text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
                {user?.email}
              </p>
            </div>
          )}
          <button
            onClick={logout}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--radius-md)] text-[var(--color-neutral-400)] hover:bg-[var(--color-neutral-200)] hover:text-[var(--color-error)]"
            title="로그아웃"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
          </button>
        </div>
      </div>
    </aside>
  );
}
