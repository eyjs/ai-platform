'use client';

export interface RoleOverrideListProps {
  roleOverrides: Record<string, string>;
  defaultModel: string | null;
}

/**
 * 역할별 모델 오버라이드.
 * 값이 빈 문자열이면 오버라이드가 없다는 뜻 → 기본 모델을 쓴다.
 */
export function RoleOverrideList({ roleOverrides, defaultModel }: RoleOverrideListProps) {
  const entries = Object.entries(roleOverrides);
  if (entries.length === 0) return null;

  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--color-neutral-200)] bg-[var(--color-neutral-50)] p-3">
      <p className="text-[var(--font-size-xs)] font-semibold text-[var(--color-neutral-700)]">
        역할별 모델 오버라이드
      </p>
      <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-2">
        {entries.map(([role, model]) => (
          <div key={role}>
            <dt className="text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
              {role}
            </dt>
            <dd className="mt-0.5 font-[family-name:var(--font-mono)] text-[var(--font-size-xs)] text-[var(--color-neutral-800)]">
              {model || (
                <span className="font-[family-name:var(--font-sans)] text-[var(--color-neutral-500)]">
                  기본값{defaultModel ? ` (${defaultModel})` : ''}
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
