"""Layer 1: Intent Classifier -- QuestionType 분류.

패턴 매칭 + Profile.intent_hints + LLM 폴백.
전략(QuestionStrategy) 매핑은 L3 StrategyBuilder의 책임.
"""

import asyncio
import logging
from typing import List, Optional

from src.config import settings
from src.domain.agent_profile import AgentProfile, IntentHint
from src.infrastructure.providers.base import LLMProvider
from src.locale.bundle import get_locale
from src.router.execution_plan import QuestionType

logger = logging.getLogger(__name__)

# LLM 분류에서 허용하는 QuestionType (패턴 전용 타입 제외)
_LLM_VALID_TYPES: dict[str, QuestionType] = {
    "STANDALONE": QuestionType.STANDALONE,
    "SAME_DOC_FOLLOWUP": QuestionType.SAME_DOC_FOLLOWUP,
    "ANSWER_BASED_FOLLOWUP": QuestionType.ANSWER_BASED_FOLLOWUP,
    "CROSS_DOC_INTEGRATION": QuestionType.CROSS_DOC_INTEGRATION,
}


class IntentClassifier:
    """질문 의도 분류기."""

    def __init__(self, llm: Optional[LLMProvider] = None):
        self._llm = llm

    async def classify(
        self,
        query: str,
        history: List[dict],
        profile: AgentProfile,
    ) -> tuple[QuestionType, Optional[str]]:
        """질문 유형, 커스텀 인텐트명을 반환한다.

        Returns:
            (question_type, custom_intent_name)
        """
        # 1. 커스텀 Intent 체크
        custom = self._check_custom_intents(query, profile.intent_hints)
        if custom:
            return QuestionType.STANDALONE, custom

        # 2. 패턴 기반 분류
        pattern_type = self._pattern_classify(query, profile.domain_scopes)
        if pattern_type:
            return pattern_type, None

        # 3. 대화 이력 기반 후속 질문 판단
        if history:
            followup_type = self._detect_followup(query)
            if followup_type:
                return followup_type, None

        # 4. LLM 폴백 (설정된 경우 + 대화 이력 있을 때)
        if self._llm and history:
            llm_type = await self._classify_with_llm(query, history)
            if llm_type:
                return llm_type, None

        # 5. 최종 기본: STANDALONE
        return QuestionType.STANDALONE, None

    @staticmethod
    def _check_custom_intents(query: str, hints: List[IntentHint]) -> Optional[str]:
        for hint in hints:
            for pattern in hint.patterns:
                if pattern in query:
                    return hint.name
        return None

    @staticmethod
    def _pattern_classify(query: str, domain_scopes: list[str] = None) -> Optional[QuestionType]:
        locale = get_locale()
        # 길이 가드: 짧은 쿼리만 인사/시스템 패턴 매칭
        if len(query) <= settings.pattern_max_query_length:
            for pattern in locale.compiled_patterns("greeting"):
                if pattern.search(query):
                    return QuestionType.GREETING

            for pattern in locale.compiled_patterns("system_meta"):
                if pattern.search(query):
                    # 도메인 키워드 포함 시 SYSTEM_META가 아님
                    if domain_scopes and any(kw in query for kw in domain_scopes if len(kw) >= 2):
                        return None
                    return QuestionType.SYSTEM_META

        return None

    async def _classify_with_llm(
        self,
        query: str,
        history: List[dict],
    ) -> Optional[QuestionType]:
        """LLM 기반 의도 분류 (패턴 매칭 실패 시 폴백).

        Router LLM에게 질문 + 최근 대화 이력을 전달하여
        QuestionType을 분류한다. 타임아웃 또는 실패 시 None 반환.
        """
        # 최근 2턴만 전달 (토큰 절약)
        recent_history = history[-2:] if len(history) > 2 else history
        history_text = "\n".join(
            f"{t.get('role', 'user')}: {t.get('content', '')[:200]}"
            for t in recent_history
        )

        prompt = (
            "다음 질문의 유형을 분류하세요.\n\n"
            f"최근 대화:\n{history_text}\n\n"
            f"현재 질문: {query}\n\n"
            "유형 목록:\n"
            "- STANDALONE: 독립적인 새 질문 (이전 대화와 무관)\n"
            "- SAME_DOC_FOLLOWUP: 같은 문서에 대한 후속 질문 (이전 답변의 문서를 더 탐색)\n"
            "- ANSWER_BASED_FOLLOWUP: 이전 답변을 기반으로 한 추가 질문\n"
            "- CROSS_DOC_INTEGRATION: 여러 문서를 비교/통합하는 질문\n\n"
            'JSON 형식으로만 답변: {"type": "QUESTION_TYPE"}'
        )

        try:
            result = await asyncio.wait_for(
                self._llm.generate_json(prompt),
                timeout=settings.planner_timeout,
            )
            type_str = result.get("type", "").upper()
            classified = _LLM_VALID_TYPES.get(type_str)
            if classified:
                logger.info(
                    "intent_llm_classify",
                    extra={"query": query[:50], "result": type_str},
                )
                return classified
            logger.warning(
                "intent_llm_invalid_type",
                extra={"type": type_str},
            )
        except asyncio.TimeoutError:
            logger.warning(
                "intent_llm_timeout",
                extra={"query": query[:50]},
            )
        except Exception as e:
            logger.warning(
                "intent_llm_error",
                extra={"error": str(e)},
            )

        return None

    @staticmethod
    def _detect_followup(query: str) -> Optional[QuestionType]:
        """대화 이력 기반 후속 질문 유형 판단."""
        locale = get_locale()

        # 조사/공백이 바로 뒤따르는 경우만 매칭 ("더블" 등 오탐 방지)
        for marker in locale.raw_patterns("followup_markers"):
            if query.startswith(marker):
                return QuestionType.ANSWER_BASED_FOLLOWUP

        # 짧은 마커는 뒤에 조사/공백이 있어야만 매칭
        for marker in locale.raw_patterns("short_followup_markers"):
            if query.startswith(marker):
                return QuestionType.ANSWER_BASED_FOLLOWUP

        for marker in locale.raw_patterns("same_doc_markers"):
            if marker in query:
                return QuestionType.SAME_DOC_FOLLOWUP

        for marker in locale.raw_patterns("comparison_markers"):
            if marker in query:
                return QuestionType.CROSS_DOC_INTEGRATION

        return None
