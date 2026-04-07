"""Tool System 테스트."""

from src.tools.base import AgentContext, ToolDefinition, ToolResult


def test_tool_result_ok():
    result = ToolResult.ok(data={"chunks": 5}, method="rag_search")
    assert result.success is True
    assert result.data == {"chunks": 5}
    assert result.metadata["method"] == "rag_search"


def test_tool_result_fail():
    result = ToolResult.fail("timeout")
    assert result.success is False
    assert result.error == "timeout"


def test_tool_definition():
    defn = ToolDefinition(
        name="rag_search",
        description="RAG 검색",
        input_schema={"type": "object"},
    )
    assert defn.timeout_seconds == 5
    assert defn.cost_tier == "free"


def test_agent_context():
    ctx = AgentContext(session_id="s1", user_id="u1", user_role="EDITOR")
    assert ctx.user_role == "EDITOR"
    assert ctx.prior_doc_ids == []
