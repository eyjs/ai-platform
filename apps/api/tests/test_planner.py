"""Planner 노드 + Plan-and-Execute 기반 타입 테스트."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.nodes import _steps_to_tool_groups
from src.agent.planner import (
    _build_tool_descriptions,
    _validate_steps,
    create_planner,
)
from src.agent.state import create_initial_state
from src.domain.models import AgentMode, SearchScope
from src.router.execution_plan import (
    ExecutionPlan,
    QuestionStrategy,
    QuestionType,
    ToolCall,
)
from src.router.strategy_builder import StrategyBuilder


# --- _steps_to_tool_groups ---


def test_steps_to_tool_groups_single_group():
    """동일 group 번호의 steps가 하나의 리스트로 묶이는지 확인."""
    steps = [
        {"step_id": "s1", "tool": "rag_search", "params": {"query": "q1"}, "group": 1},
        {"step_id": "s2", "tool": "rag_search", "params": {"query": "q2"}, "group": 1},
    ]
    result = _steps_to_tool_groups(steps)
    assert len(result) == 1
    assert len(result[0]) == 2
    assert result[0][0].tool_name == "rag_search"
    assert result[0][0].params == {"query": "q1"}
    assert result[0][1].params == {"query": "q2"}


def test_steps_to_tool_groups_multiple_groups():
    """다른 group 번호는 별도 리스트로 분리, group 순서대로 정렬."""
    steps = [
        {"step_id": "s1", "tool": "rag_search", "params": {"query": "q1"}, "group": 2},
        {"step_id": "s2", "tool": "fact_lookup", "params": {"query": "q2"}, "group": 1},
        {"step_id": "s3", "tool": "rag_search", "params": {"query": "q3"}, "group": 2},
    ]
    result = _steps_to_tool_groups(steps)
    assert len(result) == 2
    # group 1 먼저
    assert len(result[0]) == 1
    assert result[0][0].tool_name == "fact_lookup"
    # group 2
    assert len(result[1]) == 2
    assert result[1][0].tool_name == "rag_search"
    assert result[1][1].tool_name == "rag_search"


def test_steps_to_tool_groups_default_group():
    """group 미지정 시 기본값 1."""
    steps = [
        {"step_id": "s1", "tool": "rag_search", "params": {"query": "q1"}},
    ]
    result = _steps_to_tool_groups(steps)
    assert len(result) == 1
    assert result[0][0].tool_name == "rag_search"


def test_steps_to_tool_groups_empty():
    """빈 steps -> 빈 리스트."""
    assert _steps_to_tool_groups([]) == []


# --- _validate_steps ---


def test_validate_steps_valid():
    """유효한 steps가 그대로 반환되는지 확인."""
    steps = [
        {"step_id": "s1", "tool": "rag_search", "params": {"query": "q1"}, "group": 1},
    ]
    result = _validate_steps(steps, {"rag_search", "fact_lookup"})
    assert len(result) == 1
    assert result[0]["tool"] == "rag_search"
    assert result[0]["step_id"] == "s1"


def test_validate_steps_invalid_tool_filtered():
    """사용 불가능한 도구는 필터링."""
    steps = [
        {"step_id": "s1", "tool": "unknown_tool", "params": {}, "group": 1},
        {"step_id": "s2", "tool": "rag_search", "params": {"query": "q"}, "group": 1},
    ]
    result = _validate_steps(steps, {"rag_search"})
    assert len(result) == 1
    assert result[0]["tool"] == "rag_search"


def test_validate_steps_non_dict_filtered():
    """dict가 아닌 항목은 필터링."""
    steps = ["not a dict", {"step_id": "s1", "tool": "rag_search", "params": {}, "group": 1}]
    result = _validate_steps(steps, {"rag_search"})
    assert len(result) == 1


def test_validate_steps_auto_step_id():
    """step_id 미지정 시 자동 생성."""
    steps = [{"tool": "rag_search", "params": {"query": "q"}, "group": 1}]
    result = _validate_steps(steps, {"rag_search"})
    assert result[0]["step_id"] == "step_1"


# --- _build_tool_descriptions ---


def test_build_tool_descriptions():
    """도구 설명 텍스트 생성 확인."""
    mock_tool = MagicMock()
    mock_tool.name = "rag_search"
    mock_tool.description = "벡터 검색 도구"
    mock_tool.input_schema = {"query": {"type": "string"}}

    result = _build_tool_descriptions([mock_tool])
    assert "rag_search" in result
    assert "벡터 검색 도구" in result
    assert "query" in result


# --- create_planner ---


@pytest.mark.asyncio
async def test_planner_creates_steps():
    """Planner가 정상적으로 steps를 생성하는지 확인."""
    mock_llm = AsyncMock()
    mock_llm.generate_json = AsyncMock(return_value={
        "steps": [
            {"step_id": "s1", "tool": "rag_search", "params": {"query": "test"}, "group": 1},
        ],
        "reasoning": "단일 검색이면 충분",
    })

    mock_tool = MagicMock()
    mock_tool.name = "rag_search"
    mock_tool.description = "벡터 검색"
    mock_tool.input_schema = {"query": {"type": "string"}}
    mock_resolver = MagicMock(return_value=[mock_tool])

    planner = create_planner(mock_llm, mock_resolver)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "test"})]],
        question_type=QuestionType.STANDALONE,
        needs_planning=True,
    )
    state = create_initial_state("test question", plan, "sess-1")

    result = await planner(state)
    assert len(result["planned_steps"]) == 1
    assert result["planned_steps"][0]["tool"] == "rag_search"
    assert "충분" in result["planning_reasoning"]


@pytest.mark.asyncio
async def test_planner_skip_when_not_needed():
    """needs_planning=False 시 빈 dict 반환."""
    mock_llm = AsyncMock()
    mock_resolver = MagicMock()

    planner = create_planner(mock_llm, mock_resolver)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        question_type=QuestionType.GREETING,
        needs_planning=False,
    )
    state = create_initial_state("안녕", plan, "sess-1")

    result = await planner(state)
    assert result == {}
    mock_llm.generate_json.assert_not_called()


@pytest.mark.asyncio
async def test_planner_skip_when_disabled():
    """planner_enabled=False 시 빈 dict 반환."""
    mock_llm = AsyncMock()
    mock_resolver = MagicMock()

    planner = create_planner(mock_llm, mock_resolver)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "q"})]],
        question_type=QuestionType.STANDALONE,
        needs_planning=True,
    )
    state = create_initial_state("test", plan, "sess-1")

    with patch("src.agent.planner.settings") as mock_settings:
        mock_settings.planner_enabled = False
        result = await planner(state)

    assert result == {}


@pytest.mark.asyncio
async def test_planner_timeout_fallback():
    """타임아웃 시 빈 dict 반환 (기존 tool_groups 폴백)."""
    mock_llm = AsyncMock()
    mock_llm.generate_json = AsyncMock(side_effect=asyncio.TimeoutError())

    mock_tool = MagicMock()
    mock_tool.name = "rag_search"
    mock_tool.description = "검색"
    mock_tool.input_schema = {}
    mock_resolver = MagicMock(return_value=[mock_tool])

    planner = create_planner(mock_llm, mock_resolver)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "q"})]],
        question_type=QuestionType.STANDALONE,
        needs_planning=True,
    )
    state = create_initial_state("test", plan, "sess-1")

    result = await planner(state)
    assert result == {}


@pytest.mark.asyncio
async def test_planner_invalid_json_fallback():
    """generate_json 예외 시 빈 dict 반환."""
    mock_llm = AsyncMock()
    mock_llm.generate_json = AsyncMock(side_effect=ValueError("invalid json"))

    mock_tool = MagicMock()
    mock_tool.name = "rag_search"
    mock_tool.description = "검색"
    mock_tool.input_schema = {}
    mock_resolver = MagicMock(return_value=[mock_tool])

    planner = create_planner(mock_llm, mock_resolver)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "q"})]],
        question_type=QuestionType.STANDALONE,
        needs_planning=True,
    )
    state = create_initial_state("test", plan, "sess-1")

    result = await planner(state)
    assert result == {}


@pytest.mark.asyncio
async def test_planner_profile_isolation():
    """Planner에 문서 내용/tenant_id가 전달되지 않는지 확인."""
    captured_prompt = None

    mock_llm = AsyncMock()

    async def capture_generate_json(prompt, **kwargs):
        nonlocal captured_prompt
        captured_prompt = prompt
        return {
            "steps": [{"step_id": "s1", "tool": "rag_search", "params": {"query": "q"}, "group": 1}],
            "reasoning": "test",
        }

    mock_llm.generate_json = capture_generate_json

    mock_tool = MagicMock()
    mock_tool.name = "rag_search"
    mock_tool.description = "검색"
    mock_tool.input_schema = {}
    mock_resolver = MagicMock(return_value=[mock_tool])

    planner = create_planner(mock_llm, mock_resolver)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(domain_codes=["secret_domain"]),
        tool_groups=[[ToolCall("rag_search", {"query": "q"})]],
        question_type=QuestionType.STANDALONE,
        needs_planning=True,
    )
    state = create_initial_state("test question", plan, "sess-tenant-123")

    await planner(state)

    # Planner 프롬프트에 도메인 코드나 세션 ID가 포함되면 안됨
    assert captured_prompt is not None
    assert "secret_domain" not in captured_prompt
    assert "sess-tenant-123" not in captured_prompt


@pytest.mark.asyncio
async def test_planner_skip_no_tools():
    """tool_groups가 비어있으면 Planner 스킵."""
    mock_llm = AsyncMock()
    mock_resolver = MagicMock()

    planner = create_planner(mock_llm, mock_resolver)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[],
        question_type=QuestionType.STANDALONE,
        needs_planning=True,
    )
    state = create_initial_state("test", plan, "sess-1")

    result = await planner(state)
    assert result == {}


# --- needs_planning 판단 ---


def test_needs_planning_standalone():
    """STANDALONE 질문은 planning 필요."""
    result = StrategyBuilder._determine_needs_planning(
        QuestionType.STANDALONE,
        MagicMock(planning_disabled=False),
    )
    assert result is True


def test_needs_planning_greeting():
    """GREETING은 planning 불필요."""
    result = StrategyBuilder._determine_needs_planning(
        QuestionType.GREETING,
        MagicMock(planning_disabled=False),
    )
    assert result is False


def test_needs_planning_system_meta():
    """SYSTEM_META는 planning 불필요."""
    result = StrategyBuilder._determine_needs_planning(
        QuestionType.SYSTEM_META,
        MagicMock(planning_disabled=False),
    )
    assert result is False


def test_needs_planning_profile_disabled():
    """프로필에서 planning_disabled=True면 planning 불필요."""
    result = StrategyBuilder._determine_needs_planning(
        QuestionType.STANDALONE,
        MagicMock(planning_disabled=True),
    )
    assert result is False


def test_needs_planning_global_disabled():
    """글로벌 킬스위치(planner_enabled=False)면 planning 불필요."""
    with patch("src.router.strategy_builder.settings") as mock_settings:
        mock_settings.planner_enabled = False
        result = StrategyBuilder._determine_needs_planning(
            QuestionType.STANDALONE,
            MagicMock(planning_disabled=False),
        )
    assert result is False
