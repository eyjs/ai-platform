"""Intent Classifier LLM fallback tests.

P1-3: LLM-based intent classification when pattern matching fails.
"""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.router.intent_classifier import IntentClassifier
from src.router.execution_plan import QuestionType


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

    async def test_classify_llm_greeting_type_rejected(self):
        """LLM이 GREETING 반환해도 유효하지 않으므로 STANDALONE으로 폴백."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "GREETING"},
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()
        history = _make_history()

        qtype, custom = await classifier.classify(
            "보험 관련 질문인데", history, profile,
        )
        # GREETING은 _LLM_VALID_TYPES에 없으므로 STANDALONE으로 폴백
        assert qtype == QuestionType.STANDALONE


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

    async def test_empty_history_skips_llm(self):
        """히스토리가 비어있으면 LLM 호출하지 않고 STANDALONE 반환."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "SAME_DOC_FOLLOWUP"},
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()

        qtype, custom = await classifier.classify(
            "보험금 청구 방법", [], profile,
        )
        assert qtype == QuestionType.STANDALONE
        # LLM이 호출되지 않아야 한다
        mock_llm.generate_json.assert_not_called()

    async def test_pattern_match_greeting_skips_llm(self):
        """패턴 매칭으로 GREETING 분류 시 LLM 호출하지 않는다."""
        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "STANDALONE"},
        )

        classifier = IntentClassifier(llm=mock_llm)
        profile = _make_profile()
        history = _make_history()

        qtype, custom = await classifier.classify(
            "안녕", history, profile,
        )
        assert qtype == QuestionType.GREETING
        mock_llm.generate_json.assert_not_called()

    async def test_custom_intent_skips_llm(self):
        """커스텀 인텐트 매칭 시 LLM 호출하지 않는다."""
        from src.domain.agent_profile import AgentProfile, IntentHint

        mock_llm = AsyncMock()
        mock_llm.generate_json = AsyncMock(
            return_value={"type": "CROSS_DOC_INTEGRATION"},
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
        history = _make_history()

        qtype, custom = await classifier.classify(
            "보험료 얼마야?", history, profile,
        )
        assert qtype == QuestionType.STANDALONE
        assert custom == "INSURANCE_INQUIRY"
        mock_llm.generate_json.assert_not_called()
