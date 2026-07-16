'use client';

import { Badge } from '@/components/ui/badge';
import { TOOLS_CAPABILITY, hasToolsCapability } from './llm-engine-format';

export interface CapabilityBadgesProps {
  capabilities: string[];
}

/**
 * capabilities 배지 집합.
 * tools가 없으면 agentic 프로필에서 bind_tools가 실패하므로 눈에 띄게 표시한다.
 */
export function CapabilityBadges({ capabilities }: CapabilityBadgesProps) {
  const canUseTools = hasToolsCapability(capabilities);

  if (capabilities.length === 0) {
    return (
      <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
        정보 없음
      </span>
    );
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {capabilities.map((capability) => (
        <Badge
          key={capability}
          size="sm"
          variant={capability === TOOLS_CAPABILITY ? 'primary' : 'neutral'}
        >
          {capability}
        </Badge>
      ))}
      {!canUseTools && (
        <Badge size="sm" variant="warning" title="tools 미지원 — agentic 프로필 사용 불가">
          ⚠ tools 없음
        </Badge>
      )}
    </span>
  );
}
