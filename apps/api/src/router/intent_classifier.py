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
from src.domain.execution_plan import QuestionType
from src.router.token_match import matches as token_matches
from src.router.token_match import tokenize

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

        구조 유형은 **키워드가 아니라 LLM이 정한다**. 키워드는 단축(고신뢰 명시 신호)일
        뿐이고, 애매하면 판단을 LLM에 맡긴다 — 문자열 규칙으로는 "실손보험과 암보험 중에
        뭐가 나아?" 같은 질문을 영영 못 잡는다(마커가 없다). 예전 구조는 정반대였다:
        LLM이 6단계 중 5번째 폴백이었고 `history`가 있어야만 돌아서, **첫 턴엔 LLM이
        아예 안 돌고**(진단 V7) 키워드가 하나라도 걸리면 그걸로 확정됐다.

        Returns:
            (question_type, custom_intent_name)
        """
        # 도메인 라벨 — 구조 유형과 **직교**한다. 라벨이 붙었다고 구조가 정해지지 않으므로
        # 여기서 return하지 않는다(예전엔 `if custom: return STANDALONE`으로 단축해,
        # "보험료 얼마야?"가 후속질문이어도 STANDALONE으로 확정됐다).
        custom = self._check_custom_intents(query, profile.intent_hints)

        # ① 명시적 비교 마커 — 이력 불필요("A랑 B 차이 비교"는 첫 질문으로도 온다).
        #    LLM보다 빠르고 결정적이라 단축한다. 마커가 없는 비교는 ④가 잡는다.
        #    (실사고: 두 상품 비교가 INSURANCE_INQUIRY→STANDALONE으로 강등 →
        #    검색 청크 5개가 한 상품에 쏠려 "문서에 없음" 오답).
        comparison_type = self._detect_comparison(query)
        if comparison_type:
            return comparison_type, custom

        # ② 인사·시스템 메타 — 도메인 질문 자체가 아니라 라벨도 버린다.
        pattern_type = self._pattern_classify(query, profile.domain_scopes)
        if pattern_type:
            return pattern_type, None

        # ③ 이력 기반 고신뢰 후속 신호(대명사 등).
        if history:
            followup_type = self._detect_followup(query)
            if followup_type:
                return followup_type, custom

        # ④ 나머지는 전부 LLM이 판단한다 — 이력이 없어도 돈다(V7 해소).
        #    첫 턴이라 후속 유형이 불가능한 경우는 프롬프트가 후보에서 제외한다.
        if self._llm:
            llm_type = await self._classify_with_llm(query, history)
            if llm_type:
                return llm_type, custom

        # ⑤ LLM 미배선/실패 시에만 기본값. 안전 폴백이지 판단이 아니다.
        return QuestionType.STANDALONE, custom

    @staticmethod
    def _detect_comparison(query: str) -> Optional[QuestionType]:
        """비교/통합 질문 신호 — 대화 이력과 무관한 구조 신호.

        V6 수정: 부분문자열 → 토큰 경계. 비교 감지는 전 계층 위로 승격돼 있어(위 2번)
        오탐 비용이 특히 크다 — "차이" substring이 **차이나타운**에 걸려
        "차이나타운 화재보험 있어요?"가 불필요한 다문서 비교 전략으로 승격됐다(진단 V6).
        """
        locale = get_locale()
        tokens = tokenize(query)
        for marker in locale.raw_patterns("comparison_markers"):
            if token_matches(query, marker, tokens=tokens):
                return QuestionType.CROSS_DOC_INTEGRATION
        return None

    @staticmethod
    def _check_custom_intents(query: str, hints: List[IntentHint]) -> Optional[str]:
        """커스텀 인텐트 매칭 (V4 수정: 부분문자열 → 토큰 경계).

        예전엔 `pattern in query`라 flowsns-ops의 1글자 패턴 "건"이 조건·안건·건강·
        물건에 전부 걸려 "이 조건이 궁금해요"가 TASK로 오태깅됐다(진단 V4).
        1글자 패턴은 token_match가 로드 자체를 거부한다.
        """
        tokens = tokenize(query)
        for hint in hints:
            for pattern in hint.patterns:
                if token_matches(query, pattern, tokens=tokens):
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
        """LLM 기반 구조 유형 분류 — 애매한 질문의 **주 판단자**.

        키워드 단축(비교 마커·대명사 패턴)이 안 걸린 질문은 전부 여기로 온다.
        이력이 없어도 호출된다(V7) — 첫 턴 비교 질문("실손보험과 암보험 중에 뭐가
        나아?")은 마커가 없어 문자열 규칙으로는 영영 못 잡기 때문이다.

        **첫 턴엔 후속 유형을 후보에서 뺀다**: 이력이 없으면 SAME_DOC_FOLLOWUP·
        ANSWER_BASED_FOLLOWUP은 정의상 불가능한데, 후보로 주면 LLM이 그리로 환각한다.

        타임아웃·실패 시 None → 호출부가 STANDALONE으로 안전 폴백.
        """
        # 최근 2턴만 전달 (토큰 절약)
        recent_history = history[-2:] if len(history) > 2 else history
        history_text = "\n".join(
            f"{t.get('role', 'user')}: {t.get('content', '')[:200]}"
            for t in recent_history
        )

        if history_text:
            context_block = f"최근 대화:\n{history_text}\n\n"
            type_lines = (
                "- STANDALONE: 독립적인 새 질문 (이전 대화와 무관)\n"
                "- SAME_DOC_FOLLOWUP: 같은 문서에 대한 후속 질문 (이전 답변의 문서를 더 탐색)\n"
                "- ANSWER_BASED_FOLLOWUP: 이전 답변을 기반으로 한 추가 질문\n"
                "- CROSS_DOC_INTEGRATION: 여러 문서를 비교/통합하는 질문\n"
            )
        else:
            # 첫 질문 — 후속 유형은 후보에서 제외한다(불가능한 선택지를 주지 않는다).
            context_block = "이전 대화 없음 (첫 질문).\n\n"
            type_lines = (
                "- STANDALONE: 하나의 대상에 대한 단일 질문\n"
                "- CROSS_DOC_INTEGRATION: 둘 이상의 대상을 비교·통합해야 답할 수 있는 질문\n"
                "  (예: 'A와 B 중에 뭐가 나아?', 'A랑 B 어떻게 달라?')\n"
            )

        prompt = (
            "다음 질문의 유형을 분류하세요.\n\n"
            f"{context_block}"
            f"현재 질문: {query}\n\n"
            "유형 목록:\n"
            f"{type_lines}\n"
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

        # 비교 신호는 _detect_comparison(이력 불필요)으로 승격 — 여기서 중복 검사 안 함

        return None
