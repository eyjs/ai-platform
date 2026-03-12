"""Layer 1: Intent Classifier -- QuestionType 분류.

패턴 매칭 + Profile.intent_hints + LLM 폴백.
전략(QuestionStrategy) 매핑은 L3 StrategyBuilder의 책임.
"""

import logging
import re
from typing import List, Optional

from src.agent.profile import AgentProfile, IntentHint
from src.infrastructure.providers.base import LLMProvider
from src.router.execution_plan import QuestionType

logger = logging.getLogger(__name__)

# 인사/시스템 패턴
GREETING_PATTERNS = [
    r"^(안녕|하이|헬로|반가워|반갑)",
    r"(감사합니다|감사해|감사드|고마워|고맙)",
    r"^(바이바이|잘가|수고)",
]

SYSTEM_META_PATTERNS = [
    r"(너는?\s*누구|뭘\s*할\s*수|기능|도움말)",
    r"(어떤\s*문서|몇\s*개|상태)",
]

PATTERN_MAX_QUERY_LEN = 15


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
        pattern_type = self._pattern_classify(query)
        if pattern_type:
            return pattern_type, None

        # 3. 대화 이력 기반 후속 질문 판단
        if history:
            followup_type = self._detect_followup(query)
            if followup_type:
                return followup_type, None

        # 4. 기본: STANDALONE
        return QuestionType.STANDALONE, None

    @staticmethod
    def _check_custom_intents(query: str, hints: List[IntentHint]) -> Optional[str]:
        for hint in hints:
            for pattern in hint.patterns:
                if pattern in query:
                    return hint.name
        return None

    @staticmethod
    def _pattern_classify(query: str) -> Optional[QuestionType]:
        # 길이 가드: 15자 이하만 인사/시스템 패턴 매칭
        if len(query) <= PATTERN_MAX_QUERY_LEN:
            for pattern in GREETING_PATTERNS:
                if re.search(pattern, query):
                    return QuestionType.GREETING

            for pattern in SYSTEM_META_PATTERNS:
                if re.search(pattern, query):
                    return QuestionType.SYSTEM_META

        return None

    @staticmethod
    def _detect_followup(query: str) -> Optional[QuestionType]:
        """대화 이력 기반 후속 질문 유형 판단."""
        # 조사/공백이 바로 뒤따르는 경우만 매칭 ("더블" 등 오탐 방지)
        followup_markers = ["그러면", "그럼", "그래서", "그건", "이건"]
        for marker in followup_markers:
            if query.startswith(marker):
                return QuestionType.ANSWER_BASED_FOLLOWUP

        # 짧은 마커는 뒤에 조사/공백이 있어야만 매칭
        short_markers = ["더 ", "또 ", "더는", "또한"]
        for marker in short_markers:
            if query.startswith(marker):
                return QuestionType.ANSWER_BASED_FOLLOWUP

        same_doc_markers = ["같은 문서", "이 문서", "위 문서", "방금 문서"]
        for marker in same_doc_markers:
            if marker in query:
                return QuestionType.SAME_DOC_FOLLOWUP

        comparison_markers = ["비교", "차이", "다른 점", "vs"]
        for marker in comparison_markers:
            if marker in query:
                return QuestionType.CROSS_DOC_INTEGRATION

        return None
