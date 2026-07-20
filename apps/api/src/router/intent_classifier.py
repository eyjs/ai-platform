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

# LLM 분류가 낼 수 있는 QuestionType 전체. GREETING·SYSTEM_META도 여기 있다 —
# 예전엔 이 둘만 전역 정규식(_pattern_classify)이 전담했는데, 그 정규식이 도메인·언어에
# 튜닝돼 "고맙게도"를 인사로, "이 특약 기능이 뭐죠?"를 시스템질문으로 오판했다
# (진단 V3/V5). "인사인가·시스템질문인가"는 의미 판단이라 LLM이 문맥으로 봐야 강건하다.
_LLM_VALID_TYPES: dict[str, QuestionType] = {
    "STANDALONE": QuestionType.STANDALONE,
    "GREETING": QuestionType.GREETING,
    "SYSTEM_META": QuestionType.SYSTEM_META,
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
        # (Phase 2 대상: 이 키워드 매칭을 프로필 신호+LLM으로 전환 예정)
        custom = self._check_custom_intents(query, profile.intent_hints)

        # ① 이력 기반 고신뢰 후속 신호(대명사 "그건"/"이건" 등) — 결정적 단축.
        #    이력 맥락에서 대명사는 고신뢰이고 결정성이 품질보다 중요한 "극소수" 신호다.
        #    미스하면 ②의 LLM이 커버한다. 인사/비교/시스템 판단은 여기서 하지 않는다 —
        #    그건 의미 판단이라 정규식이 도메인·언어에 취약했고(진단 V3/V5/V6), LLM이 맡는다.
        if history:
            followup_type = self._detect_followup(query)
            if followup_type:
                return followup_type, custom

        # ② 구조·의미 유형 전부 LLM이 판단한다 — 인사·시스템질문·비교·독립 모두.
        #    이력이 없어도 돈다(V7). 프로필 신호를 함께 줘 "이 챗봇이 뭘 하는지" 문맥에서
        #    판단하게 한다("이 특약 기능이 뭐죠?"를 시스템질문으로 오판하지 않도록).
        if self._llm:
            llm_type = await self._classify_with_llm(query, history, profile)
            if llm_type:
                # GREETING/SYSTEM_META는 도메인 질문이 아니므로 라벨을 버린다.
                if llm_type in (QuestionType.GREETING, QuestionType.SYSTEM_META):
                    return llm_type, None
                return llm_type, custom

        # ③ LLM 미배선/실패 시에만 기본값. 안전 폴백이지 판단이 아니다(RAG 켜는 쪽).
        return QuestionType.STANDALONE, custom

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

    async def _classify_with_llm(
        self,
        query: str,
        history: List[dict],
        profile: AgentProfile,
    ) -> Optional[QuestionType]:
        """LLM 기반 유형 분류 — 인사·시스템질문·비교·독립·후속을 모두 판단하는 주 판단자.

        전역 정규식(_pattern_classify/_detect_comparison)을 걷어내고 이리로 흡수했다.
        정규식은 도메인·언어에 튜닝돼 "고맙게도"를 인사로, "차이나타운"을 비교로 오판했다
        (진단 V3/V5/V6). "인사인가·비교인가"는 의미 판단이라 LLM이 문맥으로 봐야 강건하다.

        **프로필 신호를 준다**(RAG가 청크를 주듯): 이 챗봇이 뭘 하는지 알아야
        "이 특약 기능이 뭐죠?"를 도메인 질문(STANDALONE)으로 보고 챗봇 기능 질문
        (SYSTEM_META)으로 오판하지 않는다.

        **첫 턴엔 후속 유형을 후보에서 뺀다**: 이력이 없으면 SAME_DOC/ANSWER_BASED는
        정의상 불가능한데 후보로 주면 LLM이 그리로 환각한다. GREETING/SYSTEM_META/
        CROSS_DOC은 이력과 무관하므로 첫 턴에도 후보에 둔다.

        타임아웃·실패 시 None → 호출부가 STANDALONE으로 안전 폴백(RAG 켜는 쪽).
        """
        # 프로필 신호 — "이 챗봇이 무엇을 하는가". SYSTEM_META vs 도메인질문 구별의 근거.
        profile_line = f"이 챗봇: {profile.name}"
        if profile.description:
            profile_line += f" — {profile.description}"
        if profile.domain_scopes:
            profile_line += f" (담당 도메인: {', '.join(profile.domain_scopes)})"

        # 이력·비이력 공통 유형 (의미·구조 판단, 도메인 무관)
        common_types = (
            "- GREETING: 인사·감사·작별 등 사교적 발화 (이 챗봇 도메인 질문이 아님)\n"
            "- SYSTEM_META: 이 챗봇 **자체**의 정체·기능·상태를 묻는 질문\n"
            "  ('너 뭐야', '뭘 할 수 있어', '무슨 문서 갖고 있어'). 도메인 내용 질문은 여기 아님\n"
            "- STANDALONE: 이 챗봇 도메인에 대한 독립적인 단일 질문\n"
            "- CROSS_DOC_INTEGRATION: 둘 이상의 대상을 비교·통합해야 답하는 질문\n"
            "  (예: 'A와 B 중에 뭐가 나아?', 'A랑 B 어떻게 달라?')\n"
        )

        recent_history = history[-2:] if len(history) > 2 else history
        history_text = "\n".join(
            f"{t.get('role', 'user')}: {t.get('content', '')[:200]}"
            for t in recent_history
        )

        if history_text:
            context_block = f"최근 대화:\n{history_text}\n\n"
            type_lines = common_types + (
                "- SAME_DOC_FOLLOWUP: 직전 답변의 같은 문서를 더 탐색하는 후속 질문\n"
                "- ANSWER_BASED_FOLLOWUP: 직전 답변을 기반으로 한 추가 질문\n"
            )
        else:
            context_block = "이전 대화 없음 (첫 질문).\n\n"
            type_lines = common_types  # 후속 유형은 불가능하므로 제외

        prompt = (
            "다음 질문의 유형을 분류하세요.\n\n"
            f"{profile_line}\n\n"
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

        return None
