"""Adaptive Retry Loop + Guardrail 재생성 테스트."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.nodes import (
    create_evaluate_results,
    create_regenerate,
    create_rewrite_query,
    route_by_evaluation,
    route_by_guardrail,
)
from src.agent.state import AgentState, create_initial_state
from src.domain.models import AgentMode, SearchScope
from src.router.execution_plan import (
    ExecutionPlan,
    QuestionStrategy,
    QuestionType,
    ToolCall,
)


def _make_state(**overrides) -> AgentState:
    """테스트용 AgentState 생성 헬퍼."""
    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "q"})]],
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True, max_vector_chunks=5),
        system_prompt="테스트 시스템 프롬프트",
    )
    state = create_initial_state("테스트 질문", plan, "sess-1")
    state.update(overrides)
    return state


# --- evaluate_results ---


@pytest.mark.asyncio
async def test_evaluate_sufficient_results():
    """score >= 0.4인 결과가 있으면 retry_count 유지."""
    evaluate = create_evaluate_results()
    state = _make_state(
        search_results=[{"score": 0.85, "content": "good result"}],
        retry_count=0,
    )
    result = await evaluate(state)
    assert result["retry_count"] == 0


@pytest.mark.asyncio
async def test_evaluate_insufficient_results():
    """score < 0.4이면 retry_count 증가."""
    evaluate = create_evaluate_results()
    state = _make_state(
        search_results=[{"score": 0.2, "content": "weak result"}],
        retry_count=0,
    )
    result = await evaluate(state)
    assert result["retry_count"] == 1


@pytest.mark.asyncio
async def test_evaluate_empty_results():
    """검색 결과 없으면 retry_count 증가."""
    evaluate = create_evaluate_results()
    state = _make_state(search_results=[], retry_count=0)
    result = await evaluate(state)
    assert result["retry_count"] == 1


@pytest.mark.asyncio
async def test_evaluate_max_retries():
    """retry_count가 max에 도달하면 더 이상 증가하지 않음."""
    evaluate = create_evaluate_results()
    state = _make_state(
        search_results=[{"score": 0.1}],
        retry_count=2,  # == planner_max_retries 기본값
    )
    result = await evaluate(state)
    assert result["retry_count"] == 2  # 증가하지 않음


# --- route_by_evaluation ---


def test_route_evaluation_sufficient():
    """충분한 결과 -> generate_with_context."""
    state = _make_state(
        search_results=[{"score": 0.5}],
        retry_count=0,
    )
    assert route_by_evaluation(state) == "generate_with_context"


def test_route_evaluation_insufficient_first_time():
    """불충분 + retry_count=0 -> generate_with_context (첫 시도는 그냥 진행)."""
    state = _make_state(
        search_results=[{"score": 0.1}],
        retry_count=0,
    )
    assert route_by_evaluation(state) == "generate_with_context"


def test_route_evaluation_insufficient_after_evaluate():
    """불충분 + retry_count=1 (evaluate에서 증가) -> rewrite_query."""
    state = _make_state(
        search_results=[{"score": 0.1}],
        retry_count=1,
    )
    assert route_by_evaluation(state) == "rewrite_query"


def test_route_evaluation_max_retries():
    """max retry 도달 -> generate_with_context (best-effort)."""
    state = _make_state(
        search_results=[{"score": 0.1}],
        retry_count=2,
    )
    assert route_by_evaluation(state) == "generate_with_context"


def test_route_evaluation_empty_results_max():
    """빈 결과 + max retry -> generate_with_context."""
    state = _make_state(search_results=[], retry_count=2)
    assert route_by_evaluation(state) == "generate_with_context"


# --- rewrite_query ---


@pytest.mark.asyncio
async def test_rewrite_query_success():
    """쿼리 재작성 성공 시 새 planned_steps 반환."""
    mock_llm = AsyncMock()
    mock_llm.generate_json = AsyncMock(return_value={
        "steps": [
            {"step_id": "retry_1", "tool": "rag_search",
             "params": {"query": "재작성된 쿼리"}, "group": 1},
        ],
        "reasoning": "더 일반적인 표현 사용",
    })

    rewrite = create_rewrite_query(mock_llm)
    state = _make_state(
        search_results=[{"score": 0.2, "content": "weak"}],
        retry_count=1,
    )
    result = await rewrite(state)
    assert len(result["planned_steps"]) == 1
    assert result["planned_steps"][0]["params"]["query"] == "재작성된 쿼리"
    assert "retry:" in result["planning_reasoning"]


@pytest.mark.asyncio
async def test_rewrite_query_fallback():
    """재작성 실패 시 원래 질문으로 폴백."""
    mock_llm = AsyncMock()
    mock_llm.generate_json = AsyncMock(side_effect=ValueError("parse error"))

    rewrite = create_rewrite_query(mock_llm)
    state = _make_state(
        search_results=[{"score": 0.1}],
        retry_count=1,
    )
    result = await rewrite(state)
    assert len(result["planned_steps"]) == 1
    assert result["planned_steps"][0]["tool"] == "rag_search"
    assert result["planned_steps"][0]["params"]["query"] == "테스트 질문"
    assert "rewrite failed" in result["planning_reasoning"]


@pytest.mark.asyncio
async def test_rewrite_query_timeout():
    """타임아웃 시 원래 질문으로 폴백."""
    import asyncio as aio

    mock_llm = AsyncMock()
    mock_llm.generate_json = AsyncMock(side_effect=aio.TimeoutError())

    rewrite = create_rewrite_query(mock_llm)
    state = _make_state(
        search_results=[],
        retry_count=1,
    )
    result = await rewrite(state)
    assert result["planned_steps"][0]["step_id"] == "retry_fallback"


# --- route_by_guardrail ---


def test_route_guardrail_no_results():
    """guardrail_results 비어있으면 build_response."""
    state = _make_state(guardrail_results={})
    assert route_by_guardrail(state) == "build_response"


def test_route_guardrail_pass():
    """regenerate_needed=False -> build_response."""
    state = _make_state(guardrail_results={
        "faithfulness": {"action": "pass", "score": 0.8, "ms": 10},
        "_regenerate_needed": False,
        "_regen_count": 0,
    })
    assert route_by_guardrail(state) == "build_response"


def test_route_guardrail_regenerate():
    """regenerate_needed=True + regen_count=0 -> regenerate."""
    state = _make_state(guardrail_results={
        "faithfulness": {"action": "warn", "score": 0.3, "ms": 10},
        "_regenerate_needed": True,
        "_regen_count": 0,
    })
    assert route_by_guardrail(state) == "regenerate"


def test_route_guardrail_max_regen():
    """regen_count >= 1 -> build_response (재생성 최대 1회)."""
    state = _make_state(guardrail_results={
        "faithfulness": {"action": "warn", "score": 0.3, "ms": 10},
        "_regenerate_needed": True,
        "_regen_count": 1,
    })
    assert route_by_guardrail(state) == "build_response"


# --- regenerate ---


@pytest.mark.asyncio
async def test_regenerate_creates_new_answer():
    """재생성 노드가 새 답변을 생성하는지 확인."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="개선된 답변입니다.")

    regenerate = create_regenerate(mock_llm)
    state = _make_state(
        answer="원래 답변",
        search_results=[{"content": "참고 문서", "score": 0.5}],
        guardrail_results={
            "faithfulness": {"action": "warn", "score": 0.3, "ms": 10},
            "_regenerate_needed": True,
            "_regen_count": 0,
        },
    )
    result = await regenerate(state)
    assert result["answer"] == "개선된 답변입니다."
    assert result["guardrail_results"]["_regenerate_needed"] is False
    assert result["guardrail_results"]["_regen_count"] == 1


@pytest.mark.asyncio
async def test_regenerate_preserves_guardrail_info():
    """재생성 시 기존 guardrail 정보가 보존되는지 확인."""
    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="better answer")

    regenerate = create_regenerate(mock_llm)
    state = _make_state(
        answer="old answer",
        search_results=[{"content": "doc", "score": 0.6}],
        guardrail_results={
            "faithfulness": {"action": "warn", "score": 0.4, "ms": 15},
            "_regenerate_needed": True,
            "_regen_count": 0,
        },
    )
    result = await regenerate(state)
    # 기존 guardrail 결과 보존
    assert "faithfulness" in result["guardrail_results"]
    assert result["guardrail_results"]["faithfulness"]["action"] == "warn"


# --- 통합 시나리오: 그래프에서 adaptive retry ---


@pytest.mark.asyncio
async def test_full_graph_adaptive_retry():
    """전체 그래프에서 adaptive retry가 동작하는지 통합 테스트.

    시나리오: 첫 검색 score=0.2 (불충분) -> evaluate에서 retry_count=1
    -> 그러나 route_by_evaluation에서 retry_count=1이면 rewrite_query로
    -> rewrite_query에서 새 steps 생성 -> execute_tools 재실행
    -> 두 번째 검색 score=0.8 (충분) -> generate 진행
    """
    from src.agent.graphs import build_deterministic_graph
    from src.tools.base import ToolResult

    call_count = 0

    async def mock_execute(tool_name, params, context, scope):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ToolResult(
                success=True,
                data=[{"document_id": "d1", "content": "weak", "score": 0.2}],
            )
        return ToolResult(
            success=True,
            data=[{"document_id": "d2", "content": "strong result", "score": 0.8, "title": "Doc"}],
        )

    mock_llm = AsyncMock()
    mock_llm.generate = AsyncMock(return_value="최종 답변")
    mock_llm.generate_json = AsyncMock(return_value={
        "steps": [
            {"step_id": "retry_1", "tool": "rag_search",
             "params": {"query": "재작성"}, "group": 1},
        ],
        "reasoning": "rewrite",
    })

    mock_registry = AsyncMock()
    mock_registry.execute = mock_execute
    mock_registry.resolve = MagicMock(return_value=[])  # Planner 스킵

    graph = build_deterministic_graph(
        llm=mock_llm,
        registry=mock_registry,
        guardrails={},
    )
    app = graph.compile()

    plan = ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall("rag_search", {"query": "원래 쿼리"})]],
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True),
        needs_planning=True,
    )
    state = create_initial_state("원래 질문", plan, "sess-1")

    result = await app.ainvoke(state)

    # execute_tools가 2번 호출됨 (1회 원래 + 1회 retry)
    assert call_count == 2
    assert result["answer"] == "최종 답변"
    assert result["retry_count"] == 1
