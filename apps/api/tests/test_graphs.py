"""LangGraph 그래프 빌드 + 실행 테스트."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent.graph_executor import GraphExecutor
from src.agent.graphs import build_deterministic_graph
from src.agent.state import AgentState, create_initial_state
from src.domain.models import AgentMode, SearchScope
from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, ToolCall


def test_build_deterministic_graph_compiles():
    """결정론적 그래프가 정상 컴파일되는지 확인."""
    mock_llm = MagicMock()
    mock_registry = MagicMock()

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
    )
    app = graph.compile()
    assert app is not None


def test_deterministic_graph_has_expected_nodes():
    """결정론적 그래프에 필요한 노드가 모두 있는지 확인."""
    mock_llm = MagicMock()
    mock_registry = MagicMock()

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
    )
    node_names = set(graph.nodes.keys())
    # 기존 노드
    assert "execute_tools" in node_names
    assert "generate_with_context" in node_names
    assert "direct_generate" in node_names
    assert "run_guardrails" in node_names
    assert "build_response" in node_names
    # Plan-and-Execute 신규 노드
    assert "plan_execution" in node_names
    assert "evaluate_results" in node_names
    assert "rewrite_query" in node_names
    assert "regenerate" in node_names


@pytest.mark.asyncio
async def test_deterministic_direct_generate():
    """needs_rag=False -> direct_generate 경로 검증."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="안녕하세요! 무엇을 도와드릴까요?")
    mock_registry = MagicMock()

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
    )
    app = graph.compile()

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        question_type=QuestionType.GREETING,
        strategy=QuestionStrategy(needs_rag=False),
    )
    state = create_initial_state("안녕하세요", plan, "sess-1")

    result = await app.ainvoke(state)
    assert result["answer"] == "안녕하세요! 무엇을 도와드릴까요?"
    assert result["tools_called"] == []
    mock_llm.generate.assert_called_once()


@pytest.mark.asyncio
async def test_deterministic_rag_path():
    """needs_rag=True -> execute_tools -> generate_with_context 경로 검증."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="약관에 따르면 한도는 1억원입니다.")

    # Tool 실행 mock
    from src.tools.base import ToolResult
    mock_registry = AsyncMock()
    mock_registry.execute = AsyncMock(return_value=ToolResult(
        success=True,
        data=[{"document_id": "doc-1", "title": "약관", "content": "대인배상 한도 1억원", "score": 0.9}],
    ))

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
    )
    app = graph.compile()

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(domain_codes=["자동차보험"]),
        tool_groups=[[ToolCall("rag_search", {"query": "대인배상 한도가 얼마야?"})]],
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True, max_vector_chunks=5),
        system_prompt="보험 전문가입니다.",
    )
    state = create_initial_state("대인배상 한도가 얼마야?", plan, "sess-1")

    result = await app.ainvoke(state)
    assert "1억원" in result["answer"]
    assert result["tools_called"] == ["rag_search"]
    assert len(result["search_results"]) == 1
    assert result["sources"][0]["document_id"] == "doc-1"


# --- GraphExecutor 테스트 ---


@pytest.mark.asyncio
async def test_graph_executor_deterministic_direct():
    """GraphExecutor 결정론적 모드 직접 답변."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="안녕하세요!")
    mock_registry = MagicMock()

    executor = GraphExecutor(
        main_llm=mock_llm,
        tool_registry=mock_registry,
        guardrails={},
    )

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        question_type=QuestionType.GREETING,
        strategy=QuestionStrategy(needs_rag=False),
    )

    response = await executor.execute("안녕하세요", plan, "sess-1")
    assert response.answer == "안녕하세요!"
    assert response.trace.mode == "deterministic"
    assert response.trace.tools_called == []


@pytest.mark.asyncio
async def test_graph_executor_deterministic_rag():
    """GraphExecutor 결정론적 모드 RAG 경로."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="한도는 1억입니다.")

    from src.tools.base import ToolResult
    mock_registry = AsyncMock()
    mock_registry.execute = AsyncMock(return_value=ToolResult(
        success=True,
        data=[{"document_id": "d1", "title": "약관", "content": "1억", "score": 0.9}],
    ))

    executor = GraphExecutor(
        main_llm=mock_llm,
        tool_registry=mock_registry,
        guardrails={},
    )

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "대인배상 한도?"})]],
        question_type=QuestionType.STANDALONE,
    )

    response = await executor.execute("대인배상 한도?", plan, "sess-1")
    assert "1억" in response.answer
    assert response.trace.tools_called == ["rag_search"]
    assert len(response.sources) == 1


@pytest.mark.asyncio
async def test_graph_executor_agentic_fallback():
    """chat_model이 없으면 결정론적으로 폴백."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="폴백 답변")

    executor = GraphExecutor(
        main_llm=mock_llm,
        tool_registry=MagicMock(),
        guardrails={},
        chat_model=None,  # 에이전틱 불가
    )

    plan = ExecutionPlan(
        mode=AgentMode.AGENTIC,
        scope=SearchScope(),
        question_type=QuestionType.GREETING,
        strategy=QuestionStrategy(needs_rag=False),
    )

    response = await executor.execute("안녕", plan, "sess-1")
    assert response.answer == "폴백 답변"


@pytest.mark.asyncio
async def test_streaming_bypass_no_double_llm():
    """is_streaming=True -> generate/guardrails/build_response 노드가 바이패스되어
    LLM이 그래프 내부에서 호출되지 않는지 검증 (이중 실행 방지).
    """
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="이 답변은 호출되면 안됨")

    from src.tools.base import ToolResult
    mock_registry = AsyncMock()
    mock_registry.execute = AsyncMock(return_value=ToolResult(
        success=True,
        data=[{"document_id": "d1", "title": "약관", "content": "1억", "score": 0.9}],
    ))

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
    )
    app = graph.compile()

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "질문"})]],
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True, max_vector_chunks=5),
    )
    state = create_initial_state("질문", plan, "sess-1", is_streaming=True)

    result = await app.ainvoke(state)

    # Tool은 실행됨
    assert result["tools_called"] == ["rag_search"]
    assert len(result["search_results"]) == 1

    # LLM generate는 호출되지 않아야 함 (바이패스)
    mock_llm.generate.assert_not_called()

    # answer, sources는 빈 상태 (래퍼에서 직접 처리)
    assert result["answer"] == ""
    assert result["sources"] == []


# --- graph_enrich 통합 테스트 ---


def test_graph_enrich_node_exists_when_kms_configured():
    """kms_graph_client + vector_store 전달 시 graph_enrich 노드 포함."""
    mock_llm = MagicMock()
    mock_registry = MagicMock()
    mock_kms = MagicMock()
    mock_kms.is_configured = True
    mock_vs = MagicMock()

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
        kms_graph_client=mock_kms,
        vector_store=mock_vs,
    )
    node_names = set(graph.nodes.keys())
    assert "graph_enrich" in node_names
    assert "execute_tools" in node_names
    assert "generate_with_context" in node_names


def test_backward_compat_no_kms():
    """kms_graph_client=None -> graph_enrich 노드 없이 동작."""
    mock_llm = MagicMock()
    mock_registry = MagicMock()

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
        kms_graph_client=None,
        vector_store=None,
    )
    node_names = set(graph.nodes.keys())
    assert "graph_enrich" not in node_names
    # 필수 노드 존재
    assert "plan_execution" in node_names
    assert "execute_tools" in node_names
    assert "evaluate_results" in node_names
    assert "rewrite_query" in node_names
    assert "generate_with_context" in node_names
    assert "direct_generate" in node_names
    assert "run_guardrails" in node_names
    assert "regenerate" in node_names
    assert "build_response" in node_names
