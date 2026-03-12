"""Layer 2: Mode Selector -- agentic/workflow 모드 결정.

Profile.mode 기반 + HybridTrigger 매칭.
"""

import logging
from typing import Optional

from src.agent.profile import AgentProfile
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
        if profile.mode == AgentMode.AGENTIC:
            return AgentMode.AGENTIC, None

        if profile.mode == AgentMode.WORKFLOW:
            return AgentMode.WORKFLOW, profile.workflow_id

        # hybrid 모드: 트리거 매칭
        if profile.mode == AgentMode.HYBRID:
            for trigger in profile.hybrid_triggers:
                if custom_intent and custom_intent in trigger.intent_types:
                    logger.info("Hybrid -> workflow (intent: %s)", custom_intent)
                    return AgentMode.WORKFLOW, trigger.workflow_id

                for pattern in trigger.keyword_patterns:
                    if pattern in query:
                        logger.info("Hybrid -> workflow (keyword: %s)", pattern)
                        return AgentMode.WORKFLOW, trigger.workflow_id

            return AgentMode.AGENTIC, None

        return AgentMode.AGENTIC, None
