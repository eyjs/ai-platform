"""Layer 2: Mode Selector -- agentic/workflow 모드 결정.

Profile.mode 기반 + HybridTrigger 매칭.
"""

import logging
from typing import Optional

from src.domain.agent_profile import AgentProfile
from src.domain.models import AgentMode

logger = logging.getLogger(__name__)


class ModeSelector:
    """오케스트레이션 모드 선택기."""

    def select(
        self,
        query: str,
        profile: AgentProfile,
        custom_intent: Optional[str] = None,
    ) -> tuple[AgentMode, Optional[str]]:
        """모드와 워크플로우 ID를 반환한다.

        Returns:
            (mode, workflow_id)
        """
        if profile.mode == AgentMode.DETERMINISTIC:
            return AgentMode.DETERMINISTIC, None

        if profile.mode == AgentMode.AGENTIC:
            return AgentMode.AGENTIC, None

        if profile.mode == AgentMode.WORKFLOW:
            return AgentMode.WORKFLOW, profile.workflow_id

        # hybrid 모드: 모든 트리거 점수 비교 후 최고점 선택
        if profile.mode == AgentMode.HYBRID:
            best_trigger = None
            best_score = 0

            for trigger in profile.hybrid_triggers:
                score = 0
                # intent 매칭 (가중치 2.0)
                if custom_intent and custom_intent in trigger.intent_types:
                    score += 2.0

                # keyword 매칭 (가중치 1.0 per match)
                for pattern in trigger.keyword_patterns:
                    if pattern in query:
                        score += 1.0

                if score > best_score:
                    best_score = score
                    best_trigger = trigger

            if best_trigger and best_score > 0:
                logger.info(
                    "Hybrid -> workflow (score: %.1f, wf: %s)",
                    best_score, best_trigger.workflow_id,
                )
                return AgentMode.WORKFLOW, best_trigger.workflow_id

            return AgentMode.AGENTIC, None

        return AgentMode.AGENTIC, None
