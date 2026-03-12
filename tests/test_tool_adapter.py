"""Tool Protocol -> LangChain StructuredTool 변환 테스트."""

import pytest

from src.agent.tool_adapter import convert_tools_to_langchain
from src.domain.models import SearchScope
from src.tools.base import AgentContext, ToolResult


class FakeTool:
    name = "fake_search"
    description = "테스트용 검색 도구"
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "검색어"}},
        "required": ["query"],
    }

    async def execute(self, params, context):
        return ToolResult(success=True, data=[{"content": f"결과: {params['query']}"}])


class FakeScopedTool:
    name = "scoped_search"
    description = "스코프 인식 검색 도구"
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "검색어"}},
        "required": ["query"],
    }

    async def execute(self, params, context, scope):
        return ToolResult(
            success=True,
            data=[{"content": f"scoped: {params['query']}, domains={scope.domain_codes}"}],
        )


def test_convert_basic_tool():
    tools = convert_tools_to_langchain(
        [FakeTool()],
        context=AgentContext(session_id="test"),
        scope=SearchScope(),
    )
    assert len(tools) == 1
    assert tools[0].name == "fake_search"
    assert tools[0].description == "테스트용 검색 도구"


def test_convert_multiple_tools():
    tools = convert_tools_to_langchain(
        [FakeTool(), FakeScopedTool()],
        context=AgentContext(session_id="test"),
        scope=SearchScope(domain_codes=["ga"]),
    )
    assert len(tools) == 2
    assert tools[0].name == "fake_search"
    assert tools[1].name == "scoped_search"


@pytest.mark.asyncio
async def test_converted_tool_invocation():
    tools = convert_tools_to_langchain(
        [FakeTool()],
        context=AgentContext(session_id="test"),
        scope=SearchScope(),
    )
    result = await tools[0].ainvoke({"query": "테스트"})
    assert "결과" in result


@pytest.mark.asyncio
async def test_converted_scoped_tool_invocation():
    tools = convert_tools_to_langchain(
        [FakeScopedTool()],
        context=AgentContext(session_id="test"),
        scope=SearchScope(domain_codes=["ga"]),
    )
    result = await tools[0].ainvoke({"query": "테스트"})
    assert "scoped" in result
    assert "ga" in result


@pytest.mark.asyncio
async def test_converted_tool_error_handling():
    class FailTool:
        name = "fail_tool"
        description = "항상 실패하는 도구"
        input_schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

        async def execute(self, params, context):
            return ToolResult(success=False, error="connection timeout")

    tools = convert_tools_to_langchain(
        [FailTool()],
        context=AgentContext(session_id="test"),
        scope=SearchScope(),
    )
    result = await tools[0].ainvoke({"query": "test"})
    assert "Error: connection timeout" in result
