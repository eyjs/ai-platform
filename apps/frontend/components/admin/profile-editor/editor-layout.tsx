'use client';

import { useState, useCallback, useEffect, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { useToast } from '@/components/ui/toast';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { useProfileSchema } from '@/hooks/use-profile-schema';
import { useDgxModels } from '@/hooks/use-dgx-models';
import { useProfileValidation } from '@/hooks/use-profile-validation';
import { useUnsavedChanges } from '@/hooks/use-unsaved-changes';
import { fetchProfile, createProfile, updateProfile, restoreProfile } from '@/lib/api/bff-profiles';
import { buildDefaultConfig } from '@/lib/profile/schema-meta';
import { parseProfileYaml, serializeProfileYaml, setProfileField } from '@/lib/profile/profile-yaml';
import type { ProfileConfig, ProfileField } from '@/types/profile';
import { ProfileForm } from './form/profile-form';
import { YamlEditor } from './yaml-editor';
import { PreviewPanel } from './preview-panel';
import { EditorToolbar } from './editor-toolbar';
import { EditorStatusBar } from './editor-status-bar';
import { HistoryPanel } from './history-panel';

interface EditorLayoutProps {
  profileId?: string;
}

type EditorTab = 'form' | 'yaml';

/**
 * Profile 편집기.
 *
 * 폼이 주 편집 화면이고 YAML 은 보조 탭이다. 둘은 같은 상태(config)를 공유한다:
 * - 폼 편집 → config 갱신 → js-yaml 로 직렬화해 YAML 텍스트 갱신
 * - YAML 편집 → 파싱 성공 시 config 갱신, 실패 시 폼 탭 잠금 + 파싱 오류 노출
 *
 * 저장 포맷은 기존과 동일한 `{ yamlContent }` 다.
 */
export function EditorLayout({ profileId }: EditorLayoutProps) {
  const [config, setConfig] = useState<ProfileConfig | null>(null);
  const [yamlContent, setYamlContent] = useState('');
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<EditorTab>('form');
  const [isSaving, setIsSaving] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [isProfileLoading, setIsProfileLoading] = useState(!!profileId);
  const [loadError, setLoadError] = useState<string | null>(null);

  const router = useRouter();
  const { toast } = useToast();
  const { schema, isLoading: isSchemaLoading, error: schemaError } = useProfileSchema();
  const { models, isLoading: isModelsLoading } = useDgxModels();
  const { hasChanges, setOriginal, checkChanges, markSaved } = useUnsavedChanges();

  const isNew = !profileId;
  const validation = useProfileValidation(schema, config, models);

  /** 기존 Profile 로드. 받은 YAML 텍스트는 그대로 보존한다 (재직렬화하면 즉시 '변경됨'이 된다). */
  useEffect(() => {
    if (!profileId) return;
    let isActive = true;
    setIsProfileLoading(true);

    fetchProfile(profileId)
      .then((profile) => {
        if (!isActive) return;
        const { config: parsed, error } = parseProfileYaml(profile.yamlContent);
        setYamlContent(profile.yamlContent);
        setConfig(parsed);
        setYamlError(error);
        setOriginal(profile.yamlContent);
        if (error) setActiveTab('yaml');
      })
      .catch((err: unknown) => {
        if (!isActive) return;
        const message = err instanceof Error ? err.message : 'Profile 을 불러오지 못했습니다';
        setLoadError(message);
        toast(message, 'error');
      })
      .finally(() => {
        if (isActive) setIsProfileLoading(false);
      });

    return () => {
      isActive = false;
    };
  }, [profileId, setOriginal, toast]);

  /**
   * 신규 Profile 초기값. 스키마 default 로 만들고 main_model 만 DGX 의 activeDefault 로 채운다.
   * 모델 목록을 못 받았으면 main_model 을 비워 둔다 — 없는 모델 이름을 지어내지 않는다.
   */
  useEffect(() => {
    if (!isNew || !schema || config !== null || isModelsLoading) return;

    const base = buildDefaultConfig(schema);
    const initial =
      models && models.source === 'dgx' && models.activeDefault
        ? setProfileField(base, 'main_model', models.activeDefault)
        : base;

    const text = serializeProfileYaml(initial);
    setConfig(initial);
    setYamlContent(text);
    setOriginal(text);
  }, [isNew, schema, config, models, isModelsLoading, setOriginal]);

  /** 폼 편집 → config 갱신 → YAML 재직렬화. */
  const handleFieldChange = useCallback(
    (key: ProfileField, value: unknown) => {
      setConfig((current) => {
        if (!current) return current;
        const next = setProfileField(current, key, value);
        const text = serializeProfileYaml(next);
        setYamlContent(text);
        checkChanges(text);
        setYamlError(null);
        return next;
      });
    },
    [checkChanges],
  );

  /** YAML 편집 → 파싱 성공 시에만 config 갱신. 실패해도 텍스트는 유지한다. */
  const handleYamlChange = useCallback(
    (text: string) => {
      setYamlContent(text);
      checkChanges(text);
      const { config: parsed, error } = parseProfileYaml(text);
      setYamlError(error);
      if (parsed) setConfig(parsed);
    },
    [checkChanges],
  );

  const handleSave = useCallback(async () => {
    if (yamlError) {
      toast('YAML 파싱 오류를 먼저 수정하세요', 'error');
      return;
    }
    if (!validation.isValid) {
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
  }, [
    yamlError,
    validation.isValid,
    isNew,
    yamlContent,
    profileId,
    toast,
    markSaved,
    setOriginal,
    router,
  ]);

  const handleRestore = useCallback(
    async (historyId: string) => {
      if (!profileId) return;
      try {
        const restored = await restoreProfile(profileId, historyId);
        const { config: parsed, error } = parseProfileYaml(restored.yamlContent);
        setYamlContent(restored.yamlContent);
        setConfig(parsed);
        setYamlError(error);
        setOriginal(restored.yamlContent);
        setShowHistory(false);
        toast('버전이 복원되었습니다', 'success');
      } catch (err) {
        toast(err instanceof Error ? err.message : '복원 실패', 'error');
      }
    },
    [profileId, setOriginal, toast],
  );

  const profileName = useMemo(() => {
    if (isNew) return '새 Profile';
    return typeof config?.name === 'string' && config.name ? config.name : (profileId ?? '');
  }, [isNew, config, profileId]);

  const isLoading = isProfileLoading || isSchemaLoading || (isNew && isModelsLoading);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="text-[var(--color-neutral-400)]">로딩 중...</span>
      </div>
    );
  }

  // 스키마가 없으면 폼도 검증도 성립하지 않는다. 추측으로 필드를 그리지 않는다.
  if (schemaError || !schema) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <p className="text-[var(--font-size-sm)] font-medium text-[var(--color-error)]">
          Profile 스키마를 불러오지 못해 편집기를 열 수 없습니다
        </p>
        <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
          {schemaError ?? '스키마 응답이 비어 있습니다'}
        </p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
        <p className="text-[var(--font-size-sm)] font-medium text-[var(--color-error)]">
          Profile 을 불러오지 못했습니다
        </p>
        <p className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">{loadError}</p>
      </div>
    );
  }

  const isFormBlocked = yamlError !== null || config === null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <EditorToolbar
        profileName={profileName}
        hasChanges={hasChanges}
        isSaving={isSaving}
        isNew={isNew}
        profileId={profileId}
        onSave={handleSave}
        onBack={() => router.push('/admin/profiles')}
        onHistoryToggle={() => setShowHistory(!showHistory)}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* 편집 영역 (60%) — 폼이 기본, YAML 은 보조 */}
        <div className="flex w-3/5 flex-col overflow-hidden border-r border-[var(--color-neutral-200)]">
          <Tabs
            defaultValue="form"
            value={isFormBlocked ? 'yaml' : activeTab}
            onValueChange={(value) => setActiveTab(value === 'yaml' ? 'yaml' : 'form')}
            className="flex flex-1 flex-col overflow-hidden"
          >
            <TabsList className="shrink-0 px-4 pt-2">
              <TabsTrigger value="form" disabled={isFormBlocked}>
                폼
              </TabsTrigger>
              <TabsTrigger value="yaml">YAML</TabsTrigger>
            </TabsList>

            {yamlError && (
              <p
                role="alert"
                className="mx-4 mt-2 rounded-[var(--radius-sm)] border border-red-200 bg-[var(--color-error-light)] px-3 py-2 text-[var(--font-size-xs)] text-[var(--color-error)]"
              >
                {yamlError} — YAML 을 고치면 폼 탭이 다시 열립니다
              </p>
            )}

            <TabsContent value="form" className="!mt-0 flex-1 overflow-y-auto">
              {config && (
                <ProfileForm
                  config={config}
                  schema={schema}
                  issuesByField={validation.issuesByField}
                  onFieldChange={handleFieldChange}
                  isExistingProfile={!isNew}
                  modelsResponse={models}
                  isModelsLoading={isModelsLoading}
                />
              )}
            </TabsContent>

            <TabsContent value="yaml" className="!mt-0 flex-1 overflow-hidden">
              <YamlEditor
                value={yamlContent}
                onChange={handleYamlChange}
                onSave={handleSave}
                className="h-full"
              />
            </TabsContent>
          </Tabs>
        </div>

        {/* 미리보기/테스트 패널 (40%) */}
        <div className="flex w-2/5 flex-col overflow-hidden">
          <Tabs defaultValue="preview" className="flex flex-1 flex-col overflow-hidden">
            <TabsList className="shrink-0 px-4 pt-2">
              <TabsTrigger value="preview">미리보기</TabsTrigger>
              <TabsTrigger value="test">테스트</TabsTrigger>
            </TabsList>
            <TabsContent value="preview" className="!mt-0 flex-1 overflow-y-auto">
              <PreviewPanel config={config} issues={validation.issues} />
            </TabsContent>
            <TabsContent value="test" className="!mt-0 flex-1 overflow-hidden">
              <div className="flex h-full items-center justify-center text-[var(--font-size-sm)] text-[var(--color-neutral-400)]">
                Profile을 저장한 후 테스트할 수 있습니다
              </div>
            </TabsContent>
          </Tabs>
        </div>
      </div>

      <EditorStatusBar issues={validation.issues} isValid={validation.isValid && !yamlError} />

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
