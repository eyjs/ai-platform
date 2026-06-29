"""task-101: 캐싱 호출부 분리 검증.

세 호출부(engine._generate_dynamic, nodes, strategy_builder)가
persona+grounding → cacheable_system, 날짜 → volatile_system 으로 올바르게 분리하는지 검증.
LLM 은 AsyncMock 으로 모킹.
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, call


# ── 1. strategy_builder: 날짜 → volatile_system_prompt 분리 ─────────────────


def _make_profile(system_prompt: str = "페르소나 텍스트") -> MagicMock:
    profile = MagicMock()
    profile.system_prompt = system_prompt
    profile.security_level_max = "PUBLIC"
    profile.domain_scopes = []
    profile.include_common = False
    profile.category_scopes = None
    profile.guardrails = []
    profile.context_adapter = None
    profile.response_policy = "balanced"
    profile.max_tool_calls = 5
    profile.agent_timeout_seconds = 30
    return profile


def test_strategy_builder_date_goes_to_volatile():
    """날짜 주입이 volatile_system_prompt 에 들어가고 system_prompt(cacheable)에는 없다."""
    from src.router.strategy_builder import StrategyBuilder
    from src.domain.execution_plan import QuestionType, QuestionStrategy
    from src.domain.models import AgentMode

    builder = StrategyBuilder()
    strategy = QuestionStrategy(needs_rag=False, history_turns=0)
    profile = _make_profile("You are a saju guide.")

    plan = builder.build(
        profile=profile,
        question_type=QuestionType.STANDALONE,
        strategy=strategy,
        mode=AgentMode.DETERMINISTIC,
        tools=[],
        query="테스트",
    )

    # system_prompt(cacheable)에는 날짜가 없어야 한다
    assert "[오늘 날짜]" not in plan.system_prompt
    assert "올해" not in plan.system_prompt

    # volatile_system_prompt 에 날짜가 있어야 한다
    assert "[오늘 날짜]" in plan.volatile_system_prompt
    today = datetime.now()
    assert str(today.year) in plan.volatile_system_prompt


def test_strategy_builder_persona_stays_cacheable():
    """persona 텍스트(profile.system_prompt)가 system_prompt(cacheable)에 유지된다."""
    from src.router.strategy_builder import StrategyBuilder
    from src.domain.execution_plan import QuestionType, QuestionStrategy
    from src.domain.models import AgentMode

    builder = StrategyBuilder()
    strategy = QuestionStrategy(needs_rag=False, history_turns=0)
    profile = _make_profile("UniquePersonaMarker42")

    plan = builder.build(
        profile=profile,
        question_type=QuestionType.STANDALONE,
        strategy=strategy,
        mode=AgentMode.DETERMINISTIC,
        tools=[],
        query="테스트",
    )

    assert "UniquePersonaMarker42" in plan.system_prompt


def test_strategy_builder_external_context_in_cacheable():
    """external_context 가 system_prompt(cacheable)에 포함되고 volatile 에는 없다."""
    from src.router.strategy_builder import StrategyBuilder
    from src.domain.execution_plan import QuestionType, QuestionStrategy
    from src.domain.models import AgentMode

    builder = StrategyBuilder()
    strategy = QuestionStrategy(needs_rag=False, history_turns=0)
    profile = _make_profile("페르소나")

    plan = builder.build(
        profile=profile,
        question_type=QuestionType.STANDALONE,
        strategy=strategy,
        mode=AgentMode.DETERMINISTIC,
        tools=[],
        query="테스트",
        external_context="사주 grounding 데이터",
    )

    assert "사주 grounding 데이터" in plan.system_prompt
    assert "사주 grounding 데이터" not in plan.volatile_system_prompt


def test_strategy_builder_no_system_prompt_no_volatile():
    """system_prompt 가 없으면 volatile_system_prompt 도 없다."""
    from src.router.strategy_builder import StrategyBuilder
    from src.domain.execution_plan import QuestionType, QuestionStrategy
    from src.domain.models import AgentMode

    builder = StrategyBuilder()
    strategy = QuestionStrategy(needs_rag=False, history_turns=0)
    profile = _make_profile("")  # 빈 system_prompt

    plan = builder.build(
        profile=profile,
        question_type=QuestionType.STANDALONE,
        strategy=strategy,
        mode=AgentMode.DETERMINISTIC,
        tools=[],
        query="테스트",
    )

    assert plan.system_prompt == ""
    assert plan.volatile_system_prompt == ""


# ── 2. nodes: cacheable_system/volatile_system 키워드 인자 사용 ───────────────


def _make_plan(system_prompt: str = "페르소나", volatile: str = "") -> MagicMock:
    """테스트용 ExecutionPlan 목업."""
    from src.domain.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType
    from src.domain.models import AgentMode, SearchScope
    return ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        system_prompt=system_prompt,
        volatile_system_prompt=volatile,
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=False),
    )


@pytest.mark.asyncio
async def test_nodes_generate_with_context_uses_cacheable_volatile():
    """generate_with_context 노드가 cacheable_system/volatile_system 키워드로 LLM 호출."""
    from src.agent.nodes import create_generate_with_context

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="답변")

    generate_fn = create_generate_with_context(mock_llm)

    plan = _make_plan(system_prompt="캐시 가능 페르소나", volatile="[오늘 날짜] 2026년 6월 18일.")
    state = {
        "is_streaming": False,
        "plan": plan,
        "question": "사주 알려줘",
        "search_results": [],
    }

    result = await generate_fn(state)

    assert result["answer"] == "답변"
    mock_llm.generate.assert_called_once()
    _, kwargs = mock_llm.generate.call_args
    assert kwargs.get("cacheable_system") == "캐시 가능 페르소나"
    assert kwargs.get("volatile_system") == "[오늘 날짜] 2026년 6월 18일."
    # 구 system= 인자는 사용하지 않아야 한다
    assert "system" not in kwargs


@pytest.mark.asyncio
async def test_nodes_direct_generate_uses_cacheable_volatile():
    """direct_generate 노드가 cacheable_system/volatile_system 키워드로 LLM 호출."""
    from src.agent.nodes import create_direct_generate

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="직접 답변")

    direct_fn = create_direct_generate(mock_llm)

    plan = _make_plan(system_prompt="페르소나B", volatile="날짜 정보")
    state = {
        "question": "오늘 운세",
        "plan": plan,
    }

    result = await direct_fn(state)

    assert result["answer"] == "직접 답변"
    mock_llm.generate.assert_called_once()
    _, kwargs = mock_llm.generate.call_args
    assert kwargs.get("cacheable_system") == "페르소나B"
    assert kwargs.get("volatile_system") == "날짜 정보"
    assert "system" not in kwargs


@pytest.mark.asyncio
async def test_nodes_regenerate_volatile_includes_guardrail_feedback():
    """regenerate 노드: guardrail 피드백이 volatile_system 에 포함된다."""
    from src.agent.nodes import create_regenerate

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="개선된 답변")

    regen_fn = create_regenerate(mock_llm)

    plan = _make_plan(system_prompt="페르소나C", volatile="날짜")
    state = {
        "plan": plan,
        "question": "질문",
        "search_results": [],
        "guardrail_results": {
            "faithfulness": {"action": "warn", "score": 0.3},
        },
    }

    result = await regen_fn(state)

    assert result["answer"] == "개선된 답변"
    mock_llm.generate.assert_called_once()
    _, kwargs = mock_llm.generate.call_args
    # cacheable 은 순수 페르소나
    assert kwargs.get("cacheable_system") == "페르소나C"
    # volatile 에 guardrail 피드백이 포함
    assert "IMPORTANT" in kwargs.get("volatile_system", "")
    assert "faithfulness" in kwargs.get("volatile_system", "")


# ── 3. engine._generate_dynamic: grounding → cacheable, 날짜 → volatile ───────


def _make_step(system: str = "묘묘 페르소나", prompt: str = "어떻게 생각해?") -> MagicMock:
    step = MagicMock()
    step.system = system
    step.prompt = prompt
    step.id = "test_step"
    return step


@pytest.mark.asyncio
async def test_engine_generate_dynamic_grounding_goes_to_cacheable():
    """_generate_dynamic: adapter.enrich 결과가 cacheable_system 에 포함된다."""
    from src.workflow.step_executors import generate_dynamic

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="묘묘 통찰")

    # grounding 데이터를 반환하는 adapter mock
    mock_adapter = AsyncMock()
    mock_adapter.enrich = AsyncMock(return_value={"saju_block": "사주 그라운딩 데이터"})

    step = _make_step(system="묘묘 페르소나 시스템")
    collected = {"_adapter": "saju", "topic": "연애"}

    await generate_dynamic(
        step, collected, llm=mock_llm, context_adapters={"saju": mock_adapter},
    )

    mock_llm.generate.assert_called_once()
    _, kwargs = mock_llm.generate.call_args
    cacheable = kwargs.get("cacheable_system", "")

    # persona 와 grounding 이 cacheable 에 포함
    assert "묘묘 페르소나 시스템" in cacheable
    assert "사주 그라운딩 데이터" in cacheable

    # 날짜는 volatile
    volatile = kwargs.get("volatile_system", "")
    assert "[오늘 날짜]" in volatile

    # saju_id/session_id 는 cacheable 에 없어야 함
    assert "saju_id" not in cacheable
    assert "session_id" not in cacheable


@pytest.mark.asyncio
async def test_engine_generate_dynamic_date_in_volatile_not_cacheable():
    """_generate_dynamic: 날짜가 volatile_system 에 있고 cacheable_system 에는 없다."""
    from src.workflow.step_executors import generate_dynamic

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="답변")

    step = _make_step(system="고정 페르소나")
    collected = {}

    await generate_dynamic(step, collected, llm=mock_llm, context_adapters={})

    _, kwargs = mock_llm.generate.call_args
    cacheable = kwargs.get("cacheable_system", "")
    volatile = kwargs.get("volatile_system", "")

    today = datetime.now()
    assert str(today.year) in volatile
    assert "[오늘 날짜]" in volatile
    # 날짜가 cacheable 에 포함되지 않는지 확인
    assert "[오늘 날짜]" not in cacheable


@pytest.mark.asyncio
async def test_engine_generate_dynamic_cacheable_min_4096_tokens():
    """_generate_dynamic: cacheable_system 이 4096 토큰 이상(≈16384자)이 됨을 확인."""
    from src.workflow.step_executors import generate_dynamic

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="답변")

    # 짧은 페르소나(패딩 필요)
    step = _make_step(system="짧은 페르소나")
    collected = {}

    await generate_dynamic(step, collected, llm=mock_llm, context_adapters={})

    _, kwargs = mock_llm.generate.call_args
    cacheable = kwargs.get("cacheable_system", "")

    # char/4 기준 4096 토큰 = 16384자
    assert len(cacheable) >= 16384, (
        f"cacheable_system 이 너무 짧음: {len(cacheable)}자 (최소 16384자 필요)"
    )


@pytest.mark.asyncio
async def test_engine_generate_dynamic_no_uuid_in_cacheable():
    """cacheable_system 에 session_id / saju_id 가 포함되지 않는다."""
    from src.workflow.step_executors import generate_dynamic

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="답변")

    step = _make_step(system="페르소나")
    collected = {
        "session_id": "sess-abc-123",
        "saju_id": "saju-xyz-456",
        "_hidden_keys": ["saju_id"],
        "topic": "직업",
    }

    await generate_dynamic(step, collected, llm=mock_llm, context_adapters={})

    _, kwargs = mock_llm.generate.call_args
    cacheable = kwargs.get("cacheable_system", "")

    assert "sess-abc-123" not in cacheable
    assert "saju-xyz-456" not in cacheable


@pytest.mark.asyncio
async def test_engine_generate_dynamic_no_llm_returns_fallback():
    """LLM 미주입 시 step.prompt 폴백 반환 (기존 동작 유지)."""
    from src.workflow.step_executors import generate_dynamic

    step = _make_step(prompt="폴백 메시지")
    collected = {}

    result = await generate_dynamic(step, collected, llm=None, context_adapters={})
    assert result == "폴백 메시지"


# ── 4. execution_plan: volatile_system_prompt 필드 존재 확인 ─────────────────


def test_execution_plan_has_volatile_system_prompt_field():
    """ExecutionPlan 에 volatile_system_prompt 필드가 기본값 '' 로 존재한다."""
    from src.domain.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType
    from src.domain.models import AgentMode, SearchScope

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
    )
    assert hasattr(plan, "volatile_system_prompt")
    assert plan.volatile_system_prompt == ""


def test_execution_plan_volatile_system_prompt_settable():
    """ExecutionPlan 생성 시 volatile_system_prompt 를 지정할 수 있다."""
    from src.domain.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType
    from src.domain.models import AgentMode, SearchScope

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        volatile_system_prompt="[오늘 날짜] 2026년 6월 18일.",
    )
    assert plan.volatile_system_prompt == "[오늘 날짜] 2026년 6월 18일."
