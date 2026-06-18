"""directive 주입 경로 테스트 (task-130).

검증 항목:
  (1) directive가 system_prompt에 합류 (volatile 위치 — plan.system_prompt 끝에 append)
  (2) context(grounding)가 external_context 경로로 흐름 (strategy_builder L107-114)
  (3) directive 미지정 시 기존 거동 유지 (하위호환)
"""

from __future__ import annotations

import pytest

from src.domain.models import AgentMode, SearchScope
from src.gateway.models import ChatRequest
from src.gateway.routes.helpers import _inject_directive
from src.router.execution_plan import ExecutionPlan


# ---------------------------------------------------------------------------
# (1) directive → system_prompt volatile append
# ---------------------------------------------------------------------------

class TestInjectDirective:
    """_inject_directive 단위 테스트."""

    def test_directive_appended_to_existing_system_prompt(self):
        """directive가 있으면 plan.system_prompt 끝 volatile 위치에 append된다."""
        plan = ExecutionPlan(
            mode=AgentMode.AGENTIC,
            scope=SearchScope(),
            system_prompt="너는 묘묘, 츤데레 고양이 신령이다.",
        )
        _inject_directive(plan, "지금은 사용자 의도를 되묻는 단계야.")
        assert "너는 묘묘" in plan.system_prompt
        assert "이 턴 지시" in plan.system_prompt
        assert "사용자 의도를 되묻는 단계" in plan.system_prompt
        # directive는 base prompt 뒤에 와야 한다 (volatile = 끝 위치)
        assert plan.system_prompt.index("너는 묘묘") < plan.system_prompt.index("이 턴 지시")

    def test_directive_only_when_no_base_system_prompt(self):
        """base system_prompt가 없을 때 directive만으로 system_prompt가 구성된다."""
        plan = ExecutionPlan(mode=AgentMode.AGENTIC, scope=SearchScope(), system_prompt="")
        _inject_directive(plan, "결과 상담 단계.")
        assert plan.system_prompt == "결과 상담 단계."

    def test_directive_separator_present(self):
        """volatile 구분자 '--- 이 턴 지시 ---'가 삽입된다."""
        plan = ExecutionPlan(
            mode=AgentMode.AGENTIC,
            scope=SearchScope(),
            system_prompt="base",
        )
        _inject_directive(plan, "수집 안내 단계.")
        assert "--- 이 턴 지시 ---" in plan.system_prompt

    def test_directive_is_stripped(self):
        """directive 앞뒤 공백이 제거된다."""
        plan = ExecutionPlan(mode=AgentMode.AGENTIC, scope=SearchScope(), system_prompt="base")
        _inject_directive(plan, "  hello directive  ")
        assert plan.system_prompt.endswith("hello directive")


# ---------------------------------------------------------------------------
# (3) directive 미지정 → 기존 거동 유지 (하위호환)
# ---------------------------------------------------------------------------

class TestDirectiveAbsent:
    """directive가 없을 때 plan이 변경되지 않는다."""

    def test_none_directive_no_change(self):
        """directive=None 이면 plan.system_prompt 무변경."""
        original = "너는 묘묘, 츤데레 고양이 신령이다."
        plan = ExecutionPlan(
            mode=AgentMode.AGENTIC,
            scope=SearchScope(),
            system_prompt=original,
        )
        _inject_directive(plan, None)
        assert plan.system_prompt == original

    def test_empty_directive_no_change(self):
        """directive="" 이면 plan.system_prompt 무변경."""
        original = "너는 묘묘."
        plan = ExecutionPlan(mode=AgentMode.AGENTIC, scope=SearchScope(), system_prompt=original)
        _inject_directive(plan, "")
        assert plan.system_prompt == original

    def test_whitespace_only_directive_no_change(self):
        """directive가 공백만이면 plan.system_prompt 무변경."""
        original = "너는 묘묘."
        plan = ExecutionPlan(mode=AgentMode.AGENTIC, scope=SearchScope(), system_prompt=original)
        _inject_directive(plan, "   \n  ")
        assert plan.system_prompt == original


# ---------------------------------------------------------------------------
# (2) context → external_context 경로 확인 (ChatRequest 모델 레벨)
# ---------------------------------------------------------------------------

class TestChatRequestModel:
    """ChatRequest 모델 필드 존재 및 기본값 검증."""

    def test_context_field_exists(self):
        """context 필드가 존재하며 Optional[str]이고 기본값은 None이다."""
        req = ChatRequest(question="안녕")
        assert req.context is None

    def test_directive_field_exists(self):
        """directive 필드가 존재하며 Optional[str]이고 기본값은 None이다."""
        req = ChatRequest(question="안녕")
        assert req.directive is None

    def test_context_accepted(self):
        """context 값을 받을 수 있다."""
        req = ChatRequest(question="사주 봐줘", context="[사주 데이터] 갑자년생...")
        assert req.context == "[사주 데이터] 갑자년생..."

    def test_directive_accepted(self):
        """directive 값을 받을 수 있다."""
        req = ChatRequest(question="어떤 걸 원해?", directive="의도 되묻기 단계. 간결하게 질문해.")
        assert req.directive == "의도 되묻기 단계. 간결하게 질문해."

    def test_both_context_and_directive_accepted(self):
        """context + directive를 동시에 받을 수 있다."""
        req = ChatRequest(
            question="hello",
            context="grounding text",
            directive="this turn instruction",
        )
        assert req.context == "grounding text"
        assert req.directive == "this turn instruction"

    def test_existing_fields_unchanged(self):
        """기존 필드(question, chatbot_id, session_id, metadata)가 여전히 동작한다."""
        req = ChatRequest(
            question="테스트",
            chatbot_id="myo",
            session_id="sess-001",
            metadata={"user_id": "u1"},
        )
        assert req.question == "테스트"
        assert req.chatbot_id == "myo"
        assert req.session_id == "sess-001"
        assert req.metadata == {"user_id": "u1"}


# ---------------------------------------------------------------------------
# context→external_context 경로: strategy_builder 통합 검증
# ---------------------------------------------------------------------------

class TestContextFlowsToExternalContext:
    """context가 strategy_builder external_context로 흐르는 경로를 검증한다.

    실제 DB/LLM 없이 strategy_builder.build()에 external_context를 넘겨
    system_prompt에 참고 컨텍스트 블록이 포함됨을 확인한다.
    """

    def test_external_context_appears_in_system_prompt(self):
        """external_context가 있으면 system_prompt에 '참고 컨텍스트' 블록이 생성된다."""
        from src.router.strategy_builder import StrategyBuilder
        from src.router.execution_plan import QuestionType, QuestionStrategy
        from src.domain.agent_profile import AgentProfile

        builder = StrategyBuilder()

        # 최소 AgentProfile 생성
        profile = AgentProfile(
            id="test-profile",
            name="TestBot",
            domain_scopes=["test"],
            system_prompt="너는 테스트봇.",
        )

        strategy = QuestionStrategy(needs_rag=False, history_turns=0)
        plan = builder.build(
            profile=profile,
            question_type=QuestionType.STANDALONE,
            strategy=strategy,
            mode=AgentMode.AGENTIC,
            tools=[],
            query="테스트 질문",
            external_context="[사주 데이터] 갑자년 병오월생",
        )

        assert "참고 컨텍스트" in plan.system_prompt
        assert "갑자년 병오월생" in plan.system_prompt
        # base system_prompt가 앞에 있어야 한다 (cacheable 쪽)
        assert plan.system_prompt.index("너는 테스트봇") < plan.system_prompt.index("참고 컨텍스트")

    def test_no_external_context_no_injection(self):
        """external_context가 없으면 참고 컨텍스트 블록이 없다."""
        from src.router.strategy_builder import StrategyBuilder
        from src.router.execution_plan import QuestionType, QuestionStrategy
        from src.domain.agent_profile import AgentProfile

        builder = StrategyBuilder()
        profile = AgentProfile(
            id="test-profile",
            name="TestBot",
            domain_scopes=["test"],
            system_prompt="너는 테스트봇.",
        )

        strategy = QuestionStrategy(needs_rag=False, history_turns=0)
        plan = builder.build(
            profile=profile,
            question_type=QuestionType.STANDALONE,
            strategy=strategy,
            mode=AgentMode.AGENTIC,
            tools=[],
            query="테스트 질문",
            external_context="",
        )

        assert "참고 컨텍스트" not in plan.system_prompt


# ---------------------------------------------------------------------------
# directive가 volatile(plan 수준), context가 cacheable(strategy_builder 수준)임을 통합 검증
# ---------------------------------------------------------------------------

class TestCachingPositionSeparation:
    """directive=volatile vs context=cacheable 위치 분리 검증."""

    def test_context_precedes_directive_in_final_system_prompt(self):
        """최종 system_prompt에서 context(참고 컨텍스트)는 directive(이 턴 지시)보다 앞에 온다.

        context → strategy_builder (cacheable 영역, system_prompt 중간)
        directive → _inject_directive (volatile 영역, system_prompt 끝)
        """
        from src.router.strategy_builder import StrategyBuilder
        from src.router.execution_plan import QuestionType, QuestionStrategy
        from src.domain.agent_profile import AgentProfile

        builder = StrategyBuilder()
        profile = AgentProfile(
            id="test-profile",
            name="TestBot",
            domain_scopes=["test"],
            system_prompt="너는 묘묘.",
        )

        strategy = QuestionStrategy(needs_rag=False, history_turns=0)
        plan = builder.build(
            profile=profile,
            question_type=QuestionType.STANDALONE,
            strategy=strategy,
            mode=AgentMode.AGENTIC,
            tools=[],
            query="사주 봐줘",
            external_context="[사주 grounding] 갑자년생",
        )

        # directive(volatile) 주입
        _inject_directive(plan, "의도 되묻기 단계 — 짧게 질문해.")

        assert "참고 컨텍스트" in plan.system_prompt
        assert "이 턴 지시" in plan.system_prompt
        # cacheable(참고 컨텍스트) < volatile(이 턴 지시)
        assert plan.system_prompt.index("참고 컨텍스트") < plan.system_prompt.index("이 턴 지시")
