"""Layer 2: Mode Selector -- agentic/workflow 모드 결정.

Profile.mode 기반 + HybridTrigger 매칭.
HYBRID 진입은 키워드/intent fast-path 우선, 빗나간 자유입력만 공통 SemanticClassifier로
의미 판단(맥락 기반). 키워드 없는 명백한 의도("헤어질까 고민")도 워크플로우로 진입시킨다.
"""

import logging
from typing import List, Optional

from src.domain.agent_profile import AgentProfile
from src.domain.models import AgentMode
from src.router.execution_plan import QuestionType
from src.router.semantic_classifier import Candidate, SemanticClassifier

logger = logging.getLogger(__name__)

# LLM 진입 분류를 건너뛸 질문 유형(인사·시스템 메타는 워크플로우 진입과 무관 → haiku 절약).
_SKIP_ENTRY_LLM = {QuestionType.GREETING, QuestionType.SYSTEM_META}


class ModeSelector:
    """오케스트레이션 모드 선택기."""

    def __init__(self, classifier: Optional[SemanticClassifier] = None) -> None:
        # 공통 의미 분류기(경량 router_llm). 미주입 시 키워드 fast-path만(하위호환).
        self._classifier = classifier

    async def select(
        self,
        query: str,
        profile: AgentProfile,
        custom_intent: Optional[str] = None,
        *,
        history: Optional[List[dict]] = None,
        question_type: Optional[QuestionType] = None,
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

        # hybrid 모드
        if profile.mode == AgentMode.HYBRID:
            # 1) 키워드/intent fast-path (버튼·명시 트리거 → LLM 0)
            best_trigger = None
            best_score = 0.0
            for trigger in profile.hybrid_triggers:
                score = 0.0
                if custom_intent and custom_intent in trigger.intent_types:
                    score += 2.0
                for pattern in trigger.keyword_patterns:
                    if pattern in query:
                        score += 1.0
                if score > best_score:
                    best_score = score
                    best_trigger = trigger

            if best_trigger and best_score > 0:
                logger.info(
                    "Hybrid -> workflow (keyword/intent, score: %.1f, wf: %s)",
                    best_score, best_trigger.workflow_id,
                )
                return AgentMode.WORKFLOW, best_trigger.workflow_id

            # 2) fast-path 미스 → 공통 분류기로 의미 진입 판단(인사/시스템은 스킵)
            if (
                self._classifier
                and profile.hybrid_triggers
                and question_type not in _SKIP_ENTRY_LLM
            ):
                candidates = [
                    Candidate(label=t.workflow_id, description=t.description or t.workflow_id)
                    for t in profile.hybrid_triggers
                ]
                decision = await self._classifier.classify(
                    query, candidates, context=self._history_context(history),
                )
                if decision.label and any(
                    t.workflow_id == decision.label for t in profile.hybrid_triggers
                ):
                    logger.info(
                        "Hybrid -> workflow (LLM intent, wf: %s, conf: %.2f)",
                        decision.label, decision.confidence,
                    )
                    return AgentMode.WORKFLOW, decision.label

            return AgentMode.AGENTIC, None

        return AgentMode.AGENTIC, None

    @staticmethod
    def _history_context(history: Optional[List[dict]]) -> str:
        if not history:
            return ""
        recent = history[-4:]
        return "\n".join(
            f"{t.get('role', 'user')}: {str(t.get('content', ''))[:150]}"
            for t in recent
        )
