"""Layer 1: Intent Classifier — QuestionType 분류.

패턴 매칭 + Profile.intent_hints + LLM 폴백.
"""

import logging
import re
from typing import List, Optional

from src.agent.profile import AgentProfile, IntentHint
from src.infrastructure.providers.base import LLMProvider
from src.router.execution_plan import QuestionType, QuestionStrategy

logger = logging.getLogger(__name__)

# QuestionType별 기본 전략
STRATEGY_MATRIX: dict[QuestionType, QuestionStrategy] = {
    QuestionType.GREETING: QuestionStrategy(needs_rag=False, history_turns=0),
    QuestionType.SYSTEM_META: QuestionStrategy(needs_rag=False, history_turns=0),
    QuestionType.ANSWER_REFERENCE: QuestionStrategy(needs_rag=False, history_turns=3, boost_recent=True),
    QuestionType.STANDALONE: QuestionStrategy(needs_rag=True, history_turns=0),
    QuestionType.SAME_DOC_FOLLOWUP: QuestionStrategy(needs_rag=True, history_turns=3, boost_recent=True, max_vector_chunks=3),
    QuestionType.ANSWER_BASED_FOLLOWUP: QuestionStrategy(needs_rag=True, history_turns=5, boost_recent=True),
    QuestionType.CROSS_DOC_INTEGRATION: QuestionStrategy(needs_rag=True, history_turns=3, max_vector_chunks=8),
    QuestionType.TOPIC_SWITCH: QuestionStrategy(needs_rag=True, history_turns=0),
}

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


class IntentClassifier:
    """질문 의도 분류기."""

    def __init__(self, llm: Optional[LLMProvider] = None):
        self._llm = llm

    async def classify(
        self,
        query: str,
        history: List[dict],
        profile: AgentProfile,
    ) -> tuple[QuestionType, QuestionStrategy, Optional[str]]:
        """질문 유형, 전략, 커스텀 인텐트명을 반환한다.

        Returns:
            (question_type, strategy, custom_intent_name)
        """
        # 1. 커스텀 Intent 체크
        custom = self._check_custom_intents(query, profile.intent_hints)
        if custom:
            return QuestionType.STANDALONE, STRATEGY_MATRIX[QuestionType.STANDALONE], custom

        # 2. 패턴 기반 분류
        pattern_type = self._pattern_classify(query, history)
        if pattern_type:
            return pattern_type, STRATEGY_MATRIX[pattern_type], None

        # 3. 대화 이력 기반 후속 질문 판단
        if history:
            followup_type = self._detect_followup(query, history)
            if followup_type:
                return followup_type, STRATEGY_MATRIX[followup_type], None

        # 4. 기본: STANDALONE
        return QuestionType.STANDALONE, STRATEGY_MATRIX[QuestionType.STANDALONE], None

    @staticmethod
    def _check_custom_intents(query: str, hints: List[IntentHint]) -> Optional[str]:
        for hint in hints:
            for pattern in hint.patterns:
                if pattern in query:
                    return hint.name
        return None

    @staticmethod
    def _pattern_classify(query: str, history: List[dict]) -> Optional[QuestionType]:
        # 길이 가드: 15자 이하만 인사/시스템 패턴 매칭
        if len(query) <= 15:
            for pattern in GREETING_PATTERNS:
                if re.search(pattern, query):
                    return QuestionType.GREETING

            for pattern in SYSTEM_META_PATTERNS:
                if re.search(pattern, query):
                    return QuestionType.SYSTEM_META

        return None

    @staticmethod
    def _detect_followup(query: str, history: List[dict]) -> Optional[QuestionType]:
        """대화 이력 기반 후속 질문 유형 판단."""
        followup_markers = ["그러면", "그럼", "그래서", "그건", "이건", "더", "또"]
        for marker in followup_markers:
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
