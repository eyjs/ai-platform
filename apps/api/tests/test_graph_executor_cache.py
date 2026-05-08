"""T1: GraphExecutor의 agentic graph 캐시 hit/miss 통합 검증."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.graph_executor import GraphExecutor
from src.domain.models import AgentMode, SearchScope
from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, ToolCall
from src.router.graph_cache import GraphCache


def _make_executor(chat_model=None, graph_cache=None) -> GraphExecutor:
    """테스트용 GraphExecutor를 생성한다."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="test answer")
    mock_registry = MagicMock()
    # registry.get은 mock tool instance를 반환
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_registry.get.return_value = mock_tool

    return GraphExecutor(
        main_llm=mock_llm,
        tool_registry=mock_registry,
        guardrails={},
        chat_model=chat_model or MagicMock(),
        graph_cache=graph_cache,
    )


def _agentic_plan(
    system_prompt: str = "You are a test assistant.",
    tools: list[str] | None = None,
) -> ExecutionPlan:
    """agentic 모드의 ExecutionPlan을 생성한다."""
    tool_names = tools or ["test_tool"]
    return ExecutionPlan(
        mode=AgentMode.AGENTIC,
        scope=SearchScope(),
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True),
        tool_groups=[[ToolCall(t, {}) for t in tool_names]],
        system_prompt=system_prompt,
    )


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
async def test_cache_miss_builds_and_stores(mock_convert, mock_build):
    """캐시 미스 시 build_agentic_graph를 호출하고 캐시에 저장한다."""
    # mock tool
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    # mock graph app
    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    cache = GraphCache(ttl_seconds=60, max_entries=10)
    executor = _make_executor(graph_cache=cache)

    plan = _agentic_plan()
    await executor.execute("hello", plan, "sess-1")

    # build가 1회 호출됨
    mock_build.assert_called_once()

    # 캐시에 저장됨
    assert cache.size == 1


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
async def test_cache_hit_skips_build(mock_convert, mock_build):
    """동일 (system_prompt, tool_names) 요청은 캐시 히트로 build를 건너뛴다."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    cache = GraphCache(ttl_seconds=60, max_entries=10)
    executor = _make_executor(graph_cache=cache)

    plan = _agentic_plan()

    # 첫 번째 호출 (miss)
    await executor.execute("hello", plan, "sess-1")
    assert mock_build.call_count == 1

    # 두 번째 호출 (hit)
    await executor.execute("world", plan, "sess-2")
    assert mock_build.call_count == 1  # 증가하지 않음

    assert cache.size == 1


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
async def test_different_prompt_causes_cache_miss(mock_convert, mock_build):
    """system_prompt가 다르면 캐시 미스로 새로 빌드한다."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    cache = GraphCache(ttl_seconds=60, max_entries=10)
    executor = _make_executor(graph_cache=cache)

    plan_a = _agentic_plan(system_prompt="You are assistant A.")
    plan_b = _agentic_plan(system_prompt="You are assistant B.")

    await executor.execute("hello", plan_a, "sess-1")
    await executor.execute("hello", plan_b, "sess-2")

    assert mock_build.call_count == 2
    assert cache.size == 2


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
async def test_different_tools_causes_cache_miss(mock_convert, mock_build):
    """tool_names가 다르면 캐시 미스로 새로 빌드한다."""
    call_count = [0]

    def make_tool(name):
        t = MagicMock()
        t.name = name
        return t

    def dynamic_convert(tool_instances, context, scope):
        call_count[0] += 1
        if call_count[0] == 1:
            return [make_tool("tool_a")]
        return [make_tool("tool_b")]

    mock_convert.side_effect = dynamic_convert

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    cache = GraphCache(ttl_seconds=60, max_entries=10)
    executor = _make_executor(graph_cache=cache)

    plan_a = _agentic_plan(tools=["tool_a"])
    plan_b = _agentic_plan(tools=["tool_b"])

    await executor.execute("hello", plan_a, "sess-1")
    await executor.execute("hello", plan_b, "sess-2")

    assert mock_build.call_count == 2
    assert cache.size == 2


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
async def test_invalidate_clears_cache_for_profile(mock_convert, mock_build):
    """profile_id 기반 invalidate가 해당 캐시를 제거한다."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    cache = GraphCache(ttl_seconds=60, max_entries=10)
    executor = _make_executor(graph_cache=cache)

    plan = _agentic_plan()
    plan.profile_id = "profile_x"

    await executor.execute("hello", plan, "sess-1")
    assert cache.size == 1

    # invalidate
    removed = cache.invalidate("profile_x")
    assert removed == 1
    assert cache.size == 0

    # 다시 호출하면 miss -> rebuild
    await executor.execute("world", plan, "sess-2")
    assert mock_build.call_count == 2
    assert cache.size == 1
