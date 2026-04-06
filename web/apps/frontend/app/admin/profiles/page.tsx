'use client';

import { useState, useEffect, useMemo, useCallback } from 'react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { ProfileCard } from '@/components/admin/profile-list/profile-card';
import { ProfileFilters } from '@/components/admin/profile-list/profile-filters';
import { DeleteConfirmModal } from '@/components/admin/profile-list/delete-confirm-modal';
import {
  fetchProfiles,
  activateProfile,
  deactivateProfile,
  deleteProfile,
} from '@/lib/api/bff-profiles';
import type { ProfileListItem } from '@/types/profile';

export default function ProfileListPage() {
  const [profiles, setProfiles] = useState<ProfileListItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [modeFilter, setModeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<ProfileListItem | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const { toast } = useToast();

  const loadProfiles = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchProfiles();
      setProfiles(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : '목록 로딩 실패');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

  const filtered = useMemo(() => {
    let result = profiles;
    if (search) {
      const lower = search.toLowerCase();
      result = result.filter(
        (p) =>
          p.name.toLowerCase().includes(lower) ||
          p.id.toLowerCase().includes(lower),
      );
    }
    if (modeFilter) {
      result = result.filter((p) => p.mode === modeFilter);
    }
    if (statusFilter === 'active') {
      result = result.filter((p) => p.isActive);
    } else if (statusFilter === 'inactive') {
      result = result.filter((p) => !p.isActive);
    }
    return result;
  }, [profiles, search, modeFilter, statusFilter]);

  const handleToggleActive = async (id: string, isActive: boolean) => {
    try {
      const updated = isActive
        ? await activateProfile(id)
        : await deactivateProfile(id);
      setProfiles((prev) =>
        prev.map((p) => (p.id === id ? { ...p, isActive: updated.isActive } : p)),
      );
      toast(isActive ? '활성화되었습니다' : '비활성화되었습니다', 'success');
    } catch {
      toast('상태 변경 실패', 'error');
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await deleteProfile(deleteTarget.id);
      setProfiles((prev) => prev.filter((p) => p.id !== deleteTarget.id));
      toast('삭제되었습니다', 'success');
      setDeleteTarget(null);
    } catch {
      toast('삭제 실패', 'error');
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
          Profiles
        </h1>
        <Link href="/admin/profiles/new">
          <Button variant="primary">+ 새 Profile</Button>
        </Link>
      </div>

      <div className="mb-6">
        <ProfileFilters
          search={search}
          onSearchChange={setSearch}
          modeFilter={modeFilter}
          onModeFilterChange={setModeFilter}
          statusFilter={statusFilter}
          onStatusFilterChange={setStatusFilter}
        />
      </div>

      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <Skeleton key={i} height="200px" />
          ))}
        </div>
      ) : error ? (
        <div className="flex flex-col items-center gap-3 py-12">
          <p className="text-[var(--color-error)]">{error}</p>
          <Button variant="secondary" onClick={loadProfiles}>
            재시도
          </Button>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-12 text-center">
          {profiles.length === 0 ? (
            <>
              <p className="text-[var(--font-size-lg)] text-[var(--color-neutral-500)]">
                아직 Profile이 없습니다
              </p>
              <Link href="/admin/profiles/new">
                <Button variant="primary">첫 Profile 생성하기</Button>
              </Link>
            </>
          ) : (
            <>
              <p className="text-[var(--color-neutral-500)]">
                검색 결과가 없습니다
              </p>
              <Button
                variant="ghost"
                onClick={() => {
                  setSearch('');
                  setModeFilter('');
                  setStatusFilter('');
                }}
              >
                필터 초기화
              </Button>
            </>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4">
          {filtered.map((profile) => (
            <ProfileCard
              key={profile.id}
              profile={profile}
              onToggleActive={handleToggleActive}
              onDelete={(id) =>
                setDeleteTarget(profiles.find((p) => p.id === id) || null)
              }
            />
          ))}
        </div>
      )}

      <DeleteConfirmModal
        isOpen={!!deleteTarget}
        profileName={deleteTarget?.name || ''}
        isDeleting={isDeleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
      />
    </div>
  );
}
