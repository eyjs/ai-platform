'use client';

import { useState, useCallback, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useToast } from '@/components/ui/toast';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { useYamlValidation } from '@/hooks/use-yaml-validation';
import { useUnsavedChanges } from '@/hooks/use-unsaved-changes';
import { fetchProfile, createProfile, updateProfile, restoreProfile } from '@/lib/api/bff-profiles';
import { YamlEditor } from './yaml-editor';
import { PreviewPanel } from './preview-panel';
import { EditorToolbar } from './editor-toolbar';
import { EditorStatusBar } from './editor-status-bar';
import { HistoryPanel } from './history-panel';

interface EditorLayoutProps {
  profileId?: string;
}

const DEFAULT_YAML = `id: new-profile
name: New Profile
description: ""
mode: deterministic
security_level_max: PUBLIC

tools:
  - name: rag_search

system_prompt: |
  당신은 도움이 되는 AI 어시스턴트입니다.

response_policy: balanced
guardrails:
  - faithfulness

router_model: sonnet
main_model: sonnet
memory_type: session
memory_ttl_seconds: 3600
`;

export function EditorLayout({ profileId }: EditorLayoutProps) {
  const [yamlContent, setYamlContent] = useState(DEFAULT_YAML);
  const [profileName, setProfileName] = useState('새 Profile');
  const [isSaving, setIsSaving] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [isLoading, setIsLoading] = useState(!!profileId);

  const router = useRouter();
  const { toast } = useToast();
  const { issues, isValid, validate } = useYamlValidation();
  const { hasChanges, setOriginal, checkChanges, markSaved } = useUnsavedChanges();

  const isNew = !profileId;

  // Profile 로드
  useEffect(() => {
    if (!profileId) return;
    setIsLoading(true);
    fetchProfile(profileId)
      .then((profile) => {
        setYamlContent(profile.yamlContent);
        setProfileName(profile.name);
        setOriginal(profile.yamlContent);
        validate(profile.yamlContent);
      })
      .catch((err) => {
        toast(err.message, 'error');
        router.push('/admin/profiles');
      })
      .finally(() => setIsLoading(false));
  }, [profileId, setOriginal, validate, toast, router]);

  const handleChange = useCallback(
    (value: string) => {
      setYamlContent(value);
      checkChanges(value);
      validate(value);
    },
    [checkChanges, validate],
  );

  const handleSave = useCallback(async () => {
    if (!isValid) {
      toast('유효성 오류를 먼저 수정하세요', 'error');
      return;
    }
    setIsSaving(true);
    try {
      if (isNew) {
        const created = await createProfile(yamlContent);
        toast('Profile이 생성되었습니다', 'success');
        markSaved();
        router.push(`/admin/profiles/${created.id}`);
      } else {
        await updateProfile(profileId!, yamlContent);
        toast('Profile이 저장되었습니다', 'success');
        setOriginal(yamlContent);
        markSaved();
      }
    } catch (err) {
      toast(err instanceof Error ? err.message : '저장 실패', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [isNew, isValid, yamlContent, profileId, toast, markSaved, setOriginal, router]);

  const handleRestore = useCallback(
    async (historyId: string) => {
      if (!profileId) return;
      try {
        const restored = await restoreProfile(profileId, historyId);
        setYamlContent(restored.yamlContent);
        setOriginal(restored.yamlContent);
        validate(restored.yamlContent);
        setShowHistory(false);
        toast('버전이 복원되었습니다', 'success');
      } catch (err) {
        toast(err instanceof Error ? err.message : '복원 실패', 'error');
      }
    },
    [profileId, setOriginal, validate, toast],
  );

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <span className="text-[var(--color-neutral-400)]">로딩 중...</span>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <EditorToolbar
        profileName={profileName}
        hasChanges={hasChanges}
        isSaving={isSaving}
        isNew={isNew}
        onSave={handleSave}
        onBack={() => router.push('/admin/profiles')}
        onHistoryToggle={() => setShowHistory(!showHistory)}
      />
      <div className="flex flex-1 overflow-hidden">
        {/* YAML 에디터 (60%) */}
        <YamlEditor
          value={yamlContent}
          onChange={handleChange}
          onSave={handleSave}
          className="w-3/5 border-r border-[var(--color-neutral-200)]"
        />
        {/* 미리보기/테스트 패널 (40%) */}
        <div className="flex w-2/5 flex-col overflow-hidden">
          <Tabs defaultValue="preview" className="flex flex-1 flex-col overflow-hidden">
            <TabsList className="px-4 pt-2 shrink-0">
              <TabsTrigger value="preview">미리보기</TabsTrigger>
              <TabsTrigger value="test">테스트</TabsTrigger>
            </TabsList>
            <TabsContent value="preview" className="flex-1 overflow-y-auto !mt-0">
              <PreviewPanel yamlContent={yamlContent} issues={issues} />
            </TabsContent>
            <TabsContent value="test" className="flex-1 overflow-hidden !mt-0">
              <div className="flex h-full items-center justify-center text-[var(--color-neutral-400)] text-[var(--font-size-sm)]">
                Profile을 저장한 후 테스트할 수 있습니다
              </div>
            </TabsContent>
          </Tabs>
        </div>
      </div>
      <EditorStatusBar issues={issues} isValid={isValid} />
      {profileId && (
        <HistoryPanel
          profileId={profileId}
          isOpen={showHistory}
          onClose={() => setShowHistory(false)}
          onRestore={handleRestore}
        />
      )}
    </div>
  );
}
