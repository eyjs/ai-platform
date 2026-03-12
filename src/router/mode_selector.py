"""Layer 2: Mode Selector — agentic/workflow 모드 결정.

Profile.mode 기반 + HybridTrigger 매칭.
"""

import logging
from typing import Optional

from src.agent.profile import AgentProfile

logger = logging.getLogger(__name__)


class ModeSelector:
    """오케스트레이션 모드 선택기."""

    def select(
        self,
        query: str,
        profile: AgentProfile,
        custom_intent: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        """모드와 워크플로우 ID를 반환한다.

        Returns:
            (mode, workflow_id) — mode: "agentic" | "workflow"
        """
        if profile.mode == "agentic":
            return "agentic", None

        if profile.mode == "workflow":
            return "workflow", profile.workflow_id

        # hybrid 모드: 트리거 매칭
        if profile.mode == "hybrid":
            for trigger in profile.hybrid_triggers:
                # 커스텀 인텐트 매칭
                if custom_intent and custom_intent in trigger.intent_types:
                    logger.info("Hybrid → workflow (intent: %s)", custom_intent)
                    return "workflow", trigger.workflow_id

                # 키워드 매칭
                for pattern in trigger.keyword_patterns:
                    if pattern in query:
                        logger.info("Hybrid → workflow (keyword: %s)", pattern)
                        return "workflow", trigger.workflow_id

            # 트리거 미매칭: agentic 폴백
            return "agentic", None

        return "agentic", None
