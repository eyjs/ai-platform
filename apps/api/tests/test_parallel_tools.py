"""병렬 Tool 실행 테스트."""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock

from src.router.execution_plan import ToolCall, ExecutionPlan, QuestionStrategy, QuestionType
from src.domain.models import AgentMode, SearchScope
from src.agent.state import create_initial_state
from src.observability.trace_logger import RequestTrace
from src.tools.base import ToolResult


def test_tool_call_frozen():
    tc = ToolCall(tool_name="rag_search", params={"query": "test"})
    assert tc.tool_name == "rag_search"
    assert tc.params == {"query": "test"}
    with pytest.raises(AttributeError):
        tc.tool_name = "other"


def test_tool_call_default_params():
    tc = ToolCall(tool_name="fact_lookup")
    assert tc.params == {}


def test_execution_plan_tool_groups():
    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[
            ToolCall("rag_search", {"query": "test"}),
            ToolCall("fact_lookup", {"query": "test"}),
        ]],
    )
    assert len(plan.tool_groups) == 1
    assert len(plan.tool_groups[0]) == 2


def test_execution_plan_empty_tool_groups():
    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
    )
    assert plan.tool_groups == []


@pytest.mark.asyncio
async def test_parallel_execution_faster_than_sequential():
    """2개 도구 병렬 실행이 순차보다 빠른지 검증."""
    from src.agent.nodes import create_execute_tools

    async def slow_execute(tool_name, params, context, scope=None):
        await asyncio.sleep(0.1)
        return ToolResult(success=True, data=[{"chunk_id": f"{tool_name}-1", "content": "c", "score": 0.9}])

    registry = AsyncMock()
    registry.execute = AsyncMock(side_effect=slow_execute)

    execute_tools = create_execute_tools(registry)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[
            ToolCall("rag_search", {"query": "test"}),
            ToolCall("fact_lookup", {"query": "test"}),
        ]],
        question_type=QuestionType.STANDALONE,
    )
    state = create_initial_state("test", plan, "s1")

    t_start = time.time()
    result = await execute_tools(state)
    elapsed = time.time() - t_start

    assert len(result["search_results"]) == 2
    assert set(result["tools_called"]) == {"rag_search", "fact_lookup"}
    # 병렬이면 ~0.1s, 순차면 ~0.2s
    assert elapsed < 0.18, f"Expected parallel execution, took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_parallel_execution_one_failure():
    """한 도구 실패해도 나머지 결과 반환."""
    from src.agent.nodes import create_execute_tools

    async def mixed_execute(tool_name, params, context, scope=None):
        if tool_name == "rag_search":
            return ToolResult(success=True, data=[{"chunk_id": "c1", "content": "c", "score": 0.9}])
        raise RuntimeError("fact_lookup failed")

    registry = AsyncMock()
    registry.execute = AsyncMock(side_effect=mixed_execute)

    execute_tools = create_execute_tools(registry)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[
            ToolCall("rag_search", {"query": "test"}),
            ToolCall("fact_lookup", {"query": "test"}),
        ]],
    )
    state = create_initial_state("test", plan, "s1")

    result = await execute_tools(state)
    assert len(result["search_results"]) == 1
    assert result["tools_called"] == ["rag_search"]


@pytest.mark.asyncio
async def test_sequential_groups():
    """2개 그룹이 순차 실행되는지 검증."""
    from src.agent.nodes import create_execute_tools

    call_order = []

    async def ordered_execute(tool_name, params, context, scope=None):
        call_order.append(tool_name)
        return ToolResult(success=True, data=[])

    registry = AsyncMock()
    registry.execute = AsyncMock(side_effect=ordered_execute)

    execute_tools = create_execute_tools(registry)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[
            [ToolCall("tool_a", {})],
            [ToolCall("tool_b", {})],
        ],
    )
    state = create_initial_state("test", plan, "s1")

    await execute_tools(state)
    assert call_order.index("tool_a") < call_order.index("tool_b")


@pytest.mark.asyncio
async def test_trace_integration():
    """Tool 실행이 RequestTrace에 기록되는지 검증."""
    from src.agent.nodes import create_execute_tools

    registry = AsyncMock()
    registry.execute = AsyncMock(return_value=ToolResult(
        success=True, data=[{"chunk_id": "c1", "content": "c", "score": 0.9}],
    ))

    execute_tools = create_execute_tools(registry)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "test"})]],
    )
    trace = RequestTrace(request_id="r1")
    state = create_initial_state("test", plan, "s1", trace=trace)

    await execute_tools(state)

    tool_nodes = [n for n in trace.nodes if n.node.startswith("tool:")]
    assert len(tool_nodes) == 1
    assert tool_nodes[0].node == "tool:rag_search"
    assert tool_nodes[0].data["success"] is True
    assert tool_nodes[0].duration_ms > 0


@pytest.mark.asyncio
async def test_empty_tool_groups():
    """빈 tool_groups면 빈 결과 반환."""
    from src.agent.nodes import create_execute_tools

    registry = AsyncMock()
    execute_tools = create_execute_tools(registry)

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[],
    )
    state = create_initial_state("test", plan, "s1")

    result = await execute_tools(state)
    assert result["search_results"] == []
    assert result["tools_called"] == []
