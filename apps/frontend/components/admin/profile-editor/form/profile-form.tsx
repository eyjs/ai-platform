'use client';

import { useMemo } from 'react';
import type { IssueMap } from '@/hooks/use-profile-validation';
import type { JsonSchema } from '@/lib/profile/schema-meta';
import type { DgxModelsResponse, ProfileConfig, ProfileField } from '@/types/profile';
import { ProfileFormProvider } from './form-context';
import { AdvancedSection } from './sections/advanced-section';
import { BasicSection } from './sections/basic-section';
import { KnowledgeSection } from './sections/knowledge-section';
import { MemorySection } from './sections/memory-section';
import { ModelSection } from './sections/model-section';
import { OrchestrationSection } from './sections/orchestration-section';
import { PromptSection } from './sections/prompt-section';
import { ToolsSection } from './sections/tools-section';

/** 섹션별 담당 필드. 여기에 없는 키는 폼이 그리지 않지만 저장 시 그대로 보존된다. */
const SECTION_FIELDS = {
  basic: ['id', 'name', 'description'],
  orchestration: ['mode', 'workflow_id', 'hybrid_triggers'],
  knowledge: [
    'domain_scopes',
    'category_scopes',
    'include_common',
    'security_level_max',
    'rag_min_rerank_score',
  ],
  tools: ['tools'],
  prompt: ['system_prompt', 'response_policy', 'guardrails', 'empty_response_fallback'],
  model: ['main_model'],
  memory: [
    'memory_type',
    'memory_ttl_seconds',
    'memory_scopes',
    'memory_project_id',
    'memory_max_turns',
    'memory_retention_days',
  ],
  advanced: [
    'max_tool_calls',
    'agent_timeout_seconds',
    'planning_disabled',
    'max_output_tokens',
    'context_adapter',
    'cache',
    'cache_padding_text',
    'intent_hints',
    'workflow_action_endpoint',
    'workflow_action_headers',
  ],
} as const satisfies Record<string, readonly ProfileField[]>;

type SectionKey = keyof typeof SECTION_FIELDS;

const RENDERED_FIELDS: ReadonlySet<string> = new Set(Object.values(SECTION_FIELDS).flat());

function countErrors(issuesByField: IssueMap, fields: readonly ProfileField[]): number {
  return fields.reduce((total, field) => {
    const issues = issuesByField[field] ?? [];
    return total + issues.filter((issue) => issue.severity === 'error').length;
  }, 0);
}

interface ProfileFormProps {
  config: ProfileConfig;
  schema: JsonSchema;
  issuesByField: IssueMap;
  onFieldChange: (key: ProfileField, value: unknown) => void;
  isExistingProfile: boolean;
  modelsResponse: DgxModelsResponse | null;
  isModelsLoading: boolean;
}

export function ProfileForm({
  config,
  schema,
  issuesByField,
  onFieldChange,
  isExistingProfile,
  modelsResponse,
  isModelsLoading,
}: ProfileFormProps) {
  const errorCounts = useMemo(() => {
    const counts: Record<SectionKey, number> = {
      basic: 0,
      orchestration: 0,
      knowledge: 0,
      tools: 0,
      prompt: 0,
      model: 0,
      memory: 0,
      advanced: 0,
    };
    for (const key of Object.keys(SECTION_FIELDS) as SectionKey[]) {
      counts[key] = countErrors(issuesByField, SECTION_FIELDS[key]);
    }
    return counts;
  }, [issuesByField]);

  // 폼이 그리지 않는 키. 저장 시 사라지지 않는다는 것을 사용자에게 알린다.
  const unrenderedKeys = useMemo(
    () => Object.keys(config).filter((key) => !RENDERED_FIELDS.has(key)),
    [config],
  );

  return (
    <ProfileFormProvider
      value={{
        config,
        schema,
        issuesByField,
        setField: onFieldChange,
        isExistingProfile,
        modelsResponse,
        isModelsLoading,
      }}
    >
      <div className="flex flex-col gap-3 p-4">
        <BasicSection errorCount={errorCounts.basic} />
        <OrchestrationSection errorCount={errorCounts.orchestration} />
        <KnowledgeSection errorCount={errorCounts.knowledge} />
        <ToolsSection errorCount={errorCounts.tools} />
        <PromptSection errorCount={errorCounts.prompt} />
        <ModelSection errorCount={errorCounts.model} />
        <MemorySection errorCount={errorCounts.memory} />
        <AdvancedSection errorCount={errorCounts.advanced} />

        {unrenderedKeys.length > 0 && (
          <p className="px-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)]">
            이 폼에서 편집하지 않는 키는 그대로 보존됩니다 (YAML 탭에서 편집):{' '}
            <span className="font-[family-name:var(--font-mono)]">{unrenderedKeys.join(', ')}</span>
          </p>
        )}
      </div>
    </ProfileFormProvider>
  );
}
