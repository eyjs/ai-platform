'use client';

import { createContext, useContext, type ReactNode } from 'react';
import type { JsonSchema } from '@/lib/profile/schema-meta';
import type { IssueMap } from '@/hooks/use-profile-validation';
import type { ProfileConfig, ProfileField } from '@/types/profile';
import type { DgxModelsResponse } from '@/types/profile';

export interface ProfileFormContextValue {
  config: ProfileConfig;
  /** BFF 에서 받아온 JSON Schema. 필드 설명·enum·범위의 유일한 출처. */
  schema: JsonSchema;
  issuesByField: IssueMap;
  /** 값이 undefined 면 해당 키를 YAML 에서 제거한다. */
  setField: (key: ProfileField, value: unknown) => void;
  /** 기존 프로필 편집 중인가 (id 잠금 등에 사용). */
  isExistingProfile: boolean;
  modelsResponse: DgxModelsResponse | null;
  isModelsLoading: boolean;
}

const ProfileFormContext = createContext<ProfileFormContextValue | null>(null);

export function ProfileFormProvider({
  value,
  children,
}: {
  value: ProfileFormContextValue;
  children: ReactNode;
}) {
  return <ProfileFormContext.Provider value={value}>{children}</ProfileFormContext.Provider>;
}

export function useProfileForm(): ProfileFormContextValue {
  const context = useContext(ProfileFormContext);
  if (!context) {
    throw new Error('프로필 폼 컴포넌트는 ProfileFormProvider 안에서만 사용할 수 있습니다');
  }
  return context;
}
