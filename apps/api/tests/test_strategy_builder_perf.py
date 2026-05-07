"""Strategy Builder performance improvement tests.

P0-1: STANDALONE history_turns=3
P0-2: CROSS_DOC_INTEGRATION max_vector_chunks=10
"""

import pytest

from src.router.strategy_builder import STRATEGY_MATRIX, StrategyBuilder
from src.router.execution_plan import QuestionType, QuestionStrategy


class TestStrategyMatrixValues:
    """STRATEGY_MATRIX 값 검증."""

    def test_standalone_history_turns_is_3(self):
        """P0-1: STANDALONE 타입은 history_turns=3이어야 한다."""
        strategy = STRATEGY_MATRIX[QuestionType.STANDALONE]
        assert strategy.history_turns == 3

    def test_standalone_needs_rag(self):
        """STANDALONE은 RAG 검색이 필요하다."""
        strategy = STRATEGY_MATRIX[QuestionType.STANDALONE]
        assert strategy.needs_rag is True

    def test_cross_doc_max_vector_chunks_is_10(self):
        """P0-2: CROSS_DOC_INTEGRATION은 max_vector_chunks=10이어야 한다."""
        strategy = STRATEGY_MATRIX[QuestionType.CROSS_DOC_INTEGRATION]
        assert strategy.max_vector_chunks == 10

    def test_cross_doc_history_turns(self):
        """CROSS_DOC_INTEGRATION은 history_turns=3이다."""
        strategy = STRATEGY_MATRIX[QuestionType.CROSS_DOC_INTEGRATION]
        assert strategy.history_turns == 3

    def test_same_doc_followup_unchanged(self):
        """SAME_DOC_FOLLOWUP은 기존값 유지 (회귀 방지)."""
        strategy = STRATEGY_MATRIX[QuestionType.SAME_DOC_FOLLOWUP]
        assert strategy.history_turns == 3
        assert strategy.max_vector_chunks == 3

    def test_greeting_no_history(self):
        """GREETING은 history_turns=0 유지."""
        strategy = STRATEGY_MATRIX[QuestionType.GREETING]
        assert strategy.history_turns == 0
        assert strategy.needs_rag is False


class TestStrategyBuilderBuild:
    """StrategyBuilder.build()의 conversation_context 생성 검증."""

    def _make_profile(self):
        from src.domain.agent_profile import AgentProfile
        return AgentProfile(
            id="test",
            name="Test",
            domain_scopes=["보험"],
            system_prompt="테스트 시스템 프롬프트",
        )

    def test_standalone_with_history_populates_context(self):
        """STANDALONE + history 제공 시 conversation_context가 채워진다."""
        builder = StrategyBuilder()
        profile = self._make_profile()
        strategy = STRATEGY_MATRIX[QuestionType.STANDALONE]
        history = [
            {"role": "user", "content": "보험 가입 방법 알려줘"},
            {"role": "assistant", "content": "보험 가입은 다음과 같습니다..."},
            {"role": "user", "content": "면책 사항은?"},
            {"role": "assistant", "content": "면책 사항은..."},
        ]

        plan = builder.build(
            profile=profile,
            question_type=QuestionType.STANDALONE,
            strategy=strategy,
            mode="deterministic",
            tools=[],
            query="더 자세히 알려줘",
            history=history,
        )

        # history_turns=3이므로 최근 3턴이 포함되어야 한다
        assert plan.conversation_context != ""
        assert "면책 사항은" in plan.conversation_context

    def test_standalone_without_history_empty_context(self):
        """STANDALONE + history 없을 때 conversation_context는 비어있다."""
        builder = StrategyBuilder()
        profile = self._make_profile()
        strategy = STRATEGY_MATRIX[QuestionType.STANDALONE]

        plan = builder.build(
            profile=profile,
            question_type=QuestionType.STANDALONE,
            strategy=strategy,
            mode="deterministic",
            tools=[],
            query="보험 가입 방법",
            history=[],
        )

        assert plan.conversation_context == ""

    def test_external_context_still_forces_min_3(self):
        """external_context가 있으면 history_turns가 최소 3으로 보장된다 (회귀 방지)."""
        builder = StrategyBuilder()
        profile = self._make_profile()
        # GREETING은 history_turns=0이지만 external_context가 있으면 3으로 강제
        strategy = STRATEGY_MATRIX[QuestionType.GREETING]
        history = [
            {"role": "user", "content": "안녕"},
            {"role": "assistant", "content": "안녕하세요"},
            {"role": "user", "content": "사주 분석해줘"},
            {"role": "assistant", "content": "사주 분석 결과입니다"},
        ]

        plan = builder.build(
            profile=profile,
            question_type=QuestionType.GREETING,
            strategy=strategy,
            mode="deterministic",
            tools=[],
            query="더 알려줘",
            history=history,
            external_context="사주 컨텍스트",
        )

        # external_context로 인해 history가 포함되어야 한다
        assert plan.conversation_context != ""
