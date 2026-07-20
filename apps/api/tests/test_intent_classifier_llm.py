"""Intent Classifier LLM fallback tests.

P1-3: LLM-based intent classification when pattern matching fails.
"""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.router.intent_classifier import IntentClassifier
from src.domain.execution_plan import QuestionType


def _make_profile():
    """최소 AgentProfile을 생성한다."""
    from src.domain.agent_profile import AgentProfile
    return AgentProfile(
        id="test",
        name="Test",
        domain_scopes=["보험"],
        intent_hints=[],
    )


def _make_history():
    """기본 대화 이력."""
    return [
        {"role": "user", "content": "자동차보험 보장 범위가 뭐야?"},
        {"role": "assistant", "content": "자동차보험의 보장 범위는 대인, 대물, 자기신체..."},
    ]


class TestProfileSignalInjection:
    """★재설계 핵심: 분류 LLM이 프로필 신호를 컨텍스트로 받는다 (RAG처럼).

    "이 챗봇이 뭘 하는지" 알아야 "이 특약 기능이 뭐죠?"를 도메인 질문(STANDALONE)으로
    보고 챗봇 기능 질문(SYSTEM_META)으로 오판하지 않는다(진단 V5의 근본 처방).
    """

    async def test_profile_signals_are_in_prompt(self):
        from src.domain.agent_profile import AgentProfile

        profile = AgentProfile(
            id="ins", name="보험 상담봇", description="자동차보험 약관 안내",
            domain_scopes=["보험"], intent_hints=[],
        )
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={"type": "STANDALONE"})

        classifier = IntentClassifier(llm=mock_llm)
        await classifier.classify("이 특약 기능이 뭐죠?", [], profile)

        prompt = mock_llm.generate_json.call_args.args[0]
        assert "보험 상담봇" in prompt
        assert "자동차보험 약관 안내" in prompt
        assert "보험" in prompt  # domain_scopes

    async def test_greeting_and_system_meta_in_type_list(self):
        """GREETING·SYSTEM_META가 프롬프트 유형 후보에 있어야 LLM이 그리로 분류할 수 있다."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={"type": "STANDALONE"})

        classifier = IntentClassifier(llm=mock_llm)
        await classifier.classify("아무 질문", [], _make_profile())

        prompt = mock_llm.generate_json.call_args.args[0]
        assert "GREETING" in prompt
        assert "SYSTEM_META" in prompt

    async def test_system_meta_classified_by_llm(self):
        """"너 뭐 할 수 있어?"처럼 챗봇 자체를 묻는 질문은 SYSTEM_META."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={"type": "SYSTEM_META"})

        classifier = IntentClassifier(llm=mock_llm)
        qtype, custom = await classifier.classify("너 뭐 할 수 있어?", [], _make_profile())

        assert qtype == QuestionType.SYSTEM_META
        assert custom is None


class TestLLMFallbackClassification:
    """LLM 폴백 분류 결과 테스트."""

    async def test_classify_llm_same_doc_followup(self):
        """LLM이 SAME_DOC_FOLLOWUP 반환 시 정상 분류."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "SAME_DOC_FOLLOWUP"},
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()
        history = _make_history()

        qtype, custom = await classifier.classify(
            "면책 사항은?", history, profile,
        )
        assert qtype == QuestionType.SAME_DOC_FOLLOWUP
        assert custom is None

    async def test_classify_llm_answer_based_followup(self):
        """LLM이 ANSWER_BASED_FOLLOWUP 반환 시 정상 분류."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "ANSWER_BASED_FOLLOWUP"},
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()
        history = _make_history()

        qtype, custom = await classifier.classify(
            "그 중에서 대물은 얼마까지 보장돼?", history, profile,
        )
        assert qtype == QuestionType.ANSWER_BASED_FOLLOWUP

    async def test_classify_llm_cross_doc_integration(self):
        """LLM이 CROSS_DOC_INTEGRATION 반환 시 정상 분류."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "CROSS_DOC_INTEGRATION"},
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()
        history = _make_history()

        qtype, custom = await classifier.classify(
            "자동차보��이랑 화재보험 비교해줘", history, profile,
        )
        assert qtype == QuestionType.CROSS_DOC_INTEGRATION

    async def test_classify_llm_standalone(self):
        """LLM이 STANDALONE 반환 시 정상 분류."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "STANDALONE"},
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()
        history = _make_history()

        qtype, custom = await classifier.classify(
            "건강보험료 얼마야?", history, profile,
        )
        assert qtype == QuestionType.STANDALONE


class TestLLMFallbackErrorHandling:
    """LLM 폴백 에러 핸들링 테스트."""

    async def test_classify_llm_timeout_falls_back(self):
        """LLM 타임아웃 시 STANDALONE으로 폴백."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            side_effect=asyncio.TimeoutError(),
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()
        history = _make_history()

        with patch("src.router.intent_classifier.settings") as mock_settings:
            mock_settings.planner_timeout = 0.1
            mock_settings.pattern_max_query_length = 30
            qtype, custom = await classifier.classify(
                "보험금 청구 방법이 어떻게 돼?", history, profile,
            )

        assert qtype == QuestionType.STANDALONE
        assert custom is None

    async def test_classify_llm_exception_falls_back(self):
        """LLM 예외 발생 시 STANDALONE으로 폴백."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            side_effect=RuntimeError("Connection refused"),
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()
        history = _make_history()

        qtype, custom = await classifier.classify(
            "보험금 청구 방법이 어떻게 돼?", history, profile,
        )
        assert qtype == QuestionType.STANDALONE

    async def test_classify_llm_invalid_type_falls_back(self):
        """LLM이 유효하지 않은 타입 반환 시 STANDALONE으로 폴백."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "INVALID_TYPE"},
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()
        history = _make_history()

        qtype, custom = await classifier.classify(
            "보험금 청구 방법이 어떻게 돼?", history, profile,
        )
        assert qtype == QuestionType.STANDALONE

    async def test_classify_llm_greeting_accepted(self):
        """★재설계: GREETING은 이제 LLM이 판단하는 유효 타입이다.

        예전엔 GREETING/SYSTEM_META를 전역 정규식이 전담하고 LLM 목록엔 없어서,
        LLM이 GREETING을 내면 STANDALONE으로 폴백했다. 정규식이 "고맙게도"를 인사로
        오판하는 등 도메인·언어에 취약해(진단 V3/V5) 판단을 LLM으로 흡수했다.
        """
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={"type": "GREETING"})

        classifier = IntentClassifier(llm=mock_llm)
        qtype, custom = await classifier.classify("안녕하세요", [], _make_profile())

        assert qtype == QuestionType.GREETING
        assert custom is None  # 인사는 도메인 질문이 아니므로 라벨 버림


class TestLLMFallbackSkipConditions:
    """LLM 호출이 스킵되는 조건 테스트."""

    async def test_no_llm_returns_standalone(self):
        """LLM 미제공 시 STANDALONE 반환 (기존 동작 유지)."""
        classifier = IntentClassifier(llm=None)
        profile = _make_profile()
        history = _make_history()

        qtype, custom = await classifier.classify(
            "보험금 청구 방법", history, profile,
        )
        assert qtype == QuestionType.STANDALONE

    async def test_first_turn_still_calls_llm(self):
        """★V7 해소: 이력이 없어도 LLM이 돈다.

        예전엔 `if self._llm and history:`라 첫 턴엔 LLM이 통째로 죽었고, 키워드가
        안 걸리면 무조건 STANDALONE이었다. 그러면 마커 없는 첫 턴 비교 질문
        ("실손보험과 암보험 중에 뭐가 나아?")을 영영 못 잡는다.
        """
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "CROSS_DOC_INTEGRATION"},
        )

        classifier = IntentClassifier(llm=mock_llm)
        qtype, _ = await classifier.classify(
            "실손보험과 암보험 중에 뭐가 나아?", [], _make_profile(),
        )

        mock_llm.generate_json.assert_called_once()
        assert qtype == QuestionType.CROSS_DOC_INTEGRATION


    async def test_first_turn_prompt_excludes_followup_types(self):
        """첫 턴엔 후속 유형이 정의상 불가능하다 — 후보로 주면 LLM이 환각한다."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={"type": "STANDALONE"})

        classifier = IntentClassifier(llm=mock_llm)
        await classifier.classify("보험금 청구 방법", [], _make_profile())

        prompt = mock_llm.generate_json.call_args.args[0]
        assert "FOLLOWUP" not in prompt
        assert "첫 질문" in prompt


    async def test_history_turn_prompt_includes_followup_types(self):
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={"type": "STANDALONE"})

        classifier = IntentClassifier(llm=mock_llm)
        # 대명사("그건")를 피한다 — 그건 고신뢰 단축(_detect_followup)이 먼저 잡아
        # LLM까지 오지 않는다(설계대로). 여기선 애매한 입력이어야 LLM 경로를 탄다.
        await classifier.classify("얼마야", _make_history(), _make_profile())

        prompt = mock_llm.generate_json.call_args.args[0]
        assert "SAME_DOC_FOLLOWUP" in prompt

    async def test_greeting_now_decided_by_llm_not_regex(self):
        """★재설계: "안녕"도 이제 정규식이 아니라 LLM이 GREETING으로 판단한다.

        전역 greeting 정규식을 삭제했으므로 LLM이 호출되고, 그 판단이 결과가 된다.
        """
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={"type": "GREETING"})

        classifier = IntentClassifier(llm=mock_llm)
        qtype, _ = await classifier.classify("안녕", _make_history(), _make_profile())

        assert qtype == QuestionType.GREETING
        mock_llm.generate_json.assert_called_once()  # 정규식 단축 없음 → LLM이 판단

    async def test_custom_intent_does_not_decide_structure(self):
        """★도메인 라벨과 구조 유형은 직교 — 라벨이 붙어도 구조는 LLM이 정한다.

        예전엔 `if custom: return STANDALONE, custom`으로 단축해, "보험료 얼마야?"가
        직전 답변에 이어지는 후속질문이어도 STANDALONE으로 확정됐다. 라벨은 "무엇에
        대한 질문인가"이고 구조는 "어떻게 검색할 질문인가"라 서로 다른 축이다.
        """
        from src.domain.agent_profile import AgentProfile, IntentHint

        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "SAME_DOC_FOLLOWUP"},
        )

        profile = AgentProfile(
            id="test",
            name="Test",
            domain_scopes=["보험"],
            intent_hints=[
                IntentHint(
                    name="INSURANCE_INQUIRY",
                    patterns=["보험료"],
                    description="보험료 질문",
                ),
            ],
        )
        classifier = IntentClassifier(llm=mock_llm)

        qtype, custom = await classifier.classify(
            "보험료 얼마야?", _make_history(), profile,
        )

        mock_llm.generate_json.assert_called_once()
        assert qtype == QuestionType.SAME_DOC_FOLLOWUP  # 구조는 LLM 판단
        assert custom == "INSURANCE_INQUIRY"            # 라벨은 보존


class TestComparisonDetection:
    """비교 신호 감지 — 이력·커스텀 인텐트와 직교 (첫 질문 비교 오분류 회귀)."""

    async def test_comparison_first_question_via_llm(self):
        """★재설계: 첫 질문 비교도 마커가 아니라 LLM이 CROSS_DOC로 판단한다.

        comparison_markers 정규식을 삭제했다 — "차이"가 차이나타운에 걸리는 오탐(V6)
        때문. 마커 없는 비교("A와 B 중 뭐가 나아?")까지 LLM이 잡는 게 재설계 목표다.
        """
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={"type": "CROSS_DOC_INTEGRATION"})

        classifier = IntentClassifier(llm=mock_llm)
        qtype, _ = await classifier.classify(
            "New간편간병보험이랑 참좋은더보장간병보험 가입 조건 차이만 표로 비교해줘",
            [], _make_profile(),
        )
        assert qtype == QuestionType.CROSS_DOC_INTEGRATION

    async def test_comparison_preserves_custom_intent_label(self):
        """구조 유형(CROSS_DOC)과 도메인 라벨(custom)은 직교 — 라벨은 보존된다."""
        from src.domain.agent_profile import AgentProfile, IntentHint
        profile = AgentProfile(
            id="test", name="Test", domain_scopes=["보험"],
            intent_hints=[IntentHint(
                name="INSURANCE_INQUIRY", patterns=["보험"], description="보험 문의",
            )],
        )
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(return_value={"type": "CROSS_DOC_INTEGRATION"})

        classifier = IntentClassifier(llm=mock_llm)
        qtype, custom = await classifier.classify(
            "두 보험 상품 차이점 알려줘", [], profile,
        )
        assert qtype == QuestionType.CROSS_DOC_INTEGRATION
        assert custom == "INSURANCE_INQUIRY"  # 커스텀 인텐트 라벨은 보존

    async def test_non_comparison_custom_intent_still_standalone(self):
        """비교 신호 없는 커스텀 인텐트 질문은 기존대로 STANDALONE."""
        from src.domain.agent_profile import AgentProfile, IntentHint
        profile = AgentProfile(
            id="test", name="Test", domain_scopes=["보험"],
            intent_hints=[IntentHint(
                name="INSURANCE_INQUIRY", patterns=["보험"], description="보험 문의",
            )],
        )
        classifier = IntentClassifier(llm=None)
        qtype, custom = await classifier.classify(
            "간병보험 가입 나이 알려줘", [], profile,
        )
        assert qtype == QuestionType.STANDALONE
        assert custom == "INSURANCE_INQUIRY"
