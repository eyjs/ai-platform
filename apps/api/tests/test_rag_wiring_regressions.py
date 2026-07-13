"""RAG 파이프라인 배선 회귀 테스트.

2026-07-13 검토에서 발견된 결함들의 회귀 방지:
1. 비스트리밍 경로에서 graph_enrich 결과가 프롬프트 슬라이스에 잘려나감 (정렬 누락)
2. build_source_dicts 절대 임계(0.3)가 RRF(~0.01) 스케일 출처를 전멸시킴
3. strategy.max_vector_chunks가 rag_search까지 배선되지 않음
4. rewrite_query가 LLM 출력 steps를 무검증 실행 (KeyError·프로필 권한 우회)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent.nodes import (
    _inject_strategy_params,
    build_source_dicts,
    create_execute_tools,
    create_generate_with_context,
    create_rewrite_query,
)
from src.agent.state import create_initial_state
from src.domain.models import AgentMode, SearchScope
from src.domain.execution_plan import (
    ExecutionPlan,
    QuestionStrategy,
    QuestionType,
    ToolCall,
)
from src.tools.base import ToolResult


def _make_plan(max_vector_chunks=5, tools=("rag_search",)):
    return ExecutionPlan(
        mode=AgentMode.DETERMINISTIC,
        scope=SearchScope(),
        tool_groups=[[ToolCall(t, {"query": "q"}) for t in tools]],
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True, max_vector_chunks=max_vector_chunks),
        system_prompt="시스템 프롬프트",
    )


def _make_state(plan, **overrides):
    state = create_initial_state("테스트 질문", plan, "sess-1")
    state.update(overrides)
    return state


# --- 1. 비스트리밍 프롬프트 조립: 정렬 후 슬라이스 ---


@pytest.mark.asyncio
async def test_generate_with_context_sorts_before_slice():
    """뒤에 append된 고점수 graph 청크가 프롬프트에 들어가야 한다.

    회귀 방지: 정렬 없이 results[:max_chunks]로 자르면 graph_enrich가
    뒤에 붙인 청크는 rag가 top_k를 채우는 한 항상 잘려나갔다.
    """
    captured = {}

    async def fake_generate(prompt, **kwargs):
        captured["prompt"] = prompt
        return "답변"

    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=fake_generate)

    plan = _make_plan(max_vector_chunks=2)
    state = _make_state(
        plan,
        search_results=[
            {"chunk_id": "r1", "document_id": "d1", "content": "RAG 최상위",
             "score": 0.9, "rerank_score": 0.9, "file_name": "a.pdf"},
            {"chunk_id": "r2", "document_id": "d1", "content": "RAG 하위",
             "score": 0.4, "rerank_score": 0.4, "file_name": "a.pdf"},
            # graph_enrich가 뒤에 append한 더 높은 점수의 청크
            {"chunk_id": "g1", "document_id": "d2", "content": "그래프 발견 청크",
             "score": 0.7, "source": "graph", "file_name": "b.pdf"},
        ],
    )

    node = create_generate_with_context(llm)
    result = await node(state)

    assert result["answer"] == "답변"
    assert "그래프 발견 청크" in captured["prompt"]
    assert "RAG 하위" not in captured["prompt"]  # max_chunks=2에서 밀려남


# --- 2. build_source_dicts 스케일 인지 ---


def test_build_source_dicts_rrf_scale_keeps_sources():
    """리랭킹 안 탄 RRF(~0.01) 결과도 출처가 전멸하면 안 된다."""
    results = [
        {"chunk_id": "c1", "document_id": "d1", "content": "내용1",
         "score": 0.013, "file_name": "a.pdf"},
        {"chunk_id": "c2", "document_id": "d2", "content": "내용2",
         "score": 0.009, "file_name": "b.pdf"},
    ]
    sources = build_source_dicts(results)
    assert len(sources) == 2


def test_build_source_dicts_reranked_keeps_threshold():
    """리랭킹된(fused 0~1) 결과는 기존 0.3 임계 유지."""
    results = [
        {"chunk_id": "c1", "document_id": "d1", "content": "내용1",
         "score": 0.8, "rerank_score": 0.9, "file_name": "a.pdf"},
        {"chunk_id": "c2", "document_id": "d2", "content": "내용2",
         "score": 0.1, "rerank_score": 0.1, "file_name": "b.pdf"},
    ]
    sources = build_source_dicts(results)
    assert len(sources) == 1
    assert sources[0]["document_id"] == "d1"


def test_build_source_dicts_sorted_by_score():
    """뒤에 append된 고점수 문서가 출처 상한(MAX_SOURCES) 안에 들어야 한다."""
    results = [
        {"chunk_id": f"c{i}", "document_id": f"d{i}", "content": f"내용{i}",
         "score": 0.5, "rerank_score": 0.5, "file_name": f"{i}.pdf"}
        for i in range(5)
    ]
    # graph_enrich가 뒤에 붙인 최고점 문서
    results.append({
        "chunk_id": "g1", "document_id": "d-graph", "content": "그래프",
        "score": 0.95, "file_name": "g.pdf",
    })
    sources = build_source_dicts(results)
    assert sources[0]["document_id"] == "d-graph"


# --- 3. strategy.max_vector_chunks 배선 ---


def _fake_rag_tool():
    tool = MagicMock()
    tool.input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_vector_chunks": {"type": "integer"},
        },
        "required": ["query"],
    }
    return tool


def test_inject_strategy_params_fills_declared_missing():
    """스키마가 선언한 max_vector_chunks가 계획에 없으면 전략값 주입."""
    registry = MagicMock()
    registry.get = MagicMock(return_value=_fake_rag_tool())
    strategy = QuestionStrategy(needs_rag=True, max_vector_chunks=10)

    tc = ToolCall("rag_search", {"query": "q"})
    injected = _inject_strategy_params(tc, registry, strategy)

    assert injected.params["max_vector_chunks"] == 10
    assert injected.params["query"] == "q"
    assert tc.params == {"query": "q"}  # 원본 불변


def test_inject_strategy_params_respects_explicit_value():
    """계획이 이미 지정한 값은 덮어쓰지 않는다."""
    registry = MagicMock()
    registry.get = MagicMock(return_value=_fake_rag_tool())
    strategy = QuestionStrategy(needs_rag=True, max_vector_chunks=10)

    tc = ToolCall("rag_search", {"query": "q", "max_vector_chunks": 3})
    injected = _inject_strategy_params(tc, registry, strategy)
    assert injected.params["max_vector_chunks"] == 3


def test_inject_strategy_params_skips_undeclared_tool():
    """스키마에 max_vector_chunks가 없는 도구에는 주입하지 않는다."""
    tool = MagicMock()
    tool.input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    registry = MagicMock()
    registry.get = MagicMock(return_value=tool)
    strategy = QuestionStrategy(needs_rag=True, max_vector_chunks=10)

    tc = ToolCall("fact_lookup", {"query": "q"})
    injected = _inject_strategy_params(tc, registry, strategy)
    assert "max_vector_chunks" not in injected.params


@pytest.mark.asyncio
async def test_execute_tools_wires_strategy_to_rag_search():
    """CROSS_DOC(max_vector_chunks=10) 전략이 rag_search params까지 도달."""
    seen_params = {}

    async def capture_execute(tool_name, params, context, scope=None):
        seen_params[tool_name] = params
        return ToolResult(success=True, data=[])

    registry = AsyncMock()
    registry.execute = AsyncMock(side_effect=capture_execute)
    registry.get = MagicMock(return_value=_fake_rag_tool())

    plan = _make_plan(max_vector_chunks=10)
    state = _make_state(plan)

    node = create_execute_tools(registry)
    await node(state)

    assert seen_params["rag_search"]["max_vector_chunks"] == 10


# --- 4. rewrite_query 인가 경계 ---


@pytest.mark.asyncio
async def test_rewrite_query_drops_hallucinated_tool():
    """프로필 도구 집합 밖 도구는 실행 계획에서 제거 → rag_search 폴백."""
    llm = MagicMock()
    llm.generate_json = AsyncMock(return_value={
        "steps": [
            {"step_id": "bad", "tool": "saju_lookup",
             "params": {"query": "누출 시도"}, "group": 1},
        ],
        "reasoning": "환각",
    })

    plan = _make_plan(tools=("rag_search",))
    state = _make_state(plan, search_results=[{"score": 0.1}], retry_count=1)

    node = create_rewrite_query(llm)
    result = await node(state)

    steps = result["planned_steps"]
    assert all(s["tool"] == "rag_search" for s in steps)
    assert steps[0]["params"]["query"] == "테스트 질문"  # 원 질문 폴백


@pytest.mark.asyncio
async def test_rewrite_query_survives_malformed_steps():
    """tool 키 누락·비dict step이 KeyError 크래시를 내면 안 된다."""
    llm = MagicMock()
    llm.generate_json = AsyncMock(return_value={
        "steps": [
            {"step_id": "no_tool_key", "params": {"query": "q"}},
            "이건 문자열",
            {"tool": "rag_search", "params": {"query": "정상 재작성"}},
        ],
        "reasoning": "부분 유효",
    })

    plan = _make_plan(tools=("rag_search",))
    state = _make_state(plan, search_results=[{"score": 0.1}], retry_count=1)

    node = create_rewrite_query(llm)
    result = await node(state)

    steps = result["planned_steps"]
    assert len(steps) == 1
    assert steps[0]["tool"] == "rag_search"
    assert steps[0]["params"]["query"] == "정상 재작성"


@pytest.mark.asyncio
async def test_rewrite_query_valid_steps_pass_through():
    """허용 도구의 유효한 재작성은 그대로 통과 (기존 동작 보존)."""
    llm = MagicMock()
    llm.generate_json = AsyncMock(return_value={
        "steps": [
            {"step_id": "retry_1", "tool": "rag_search",
             "params": {"query": "재작성 쿼리"}, "group": 1},
        ],
        "reasoning": "재작성",
    })

    plan = _make_plan(tools=("rag_search", "fact_lookup"))
    state = _make_state(plan, search_results=[{"score": 0.1}], retry_count=1)

    node = create_rewrite_query(llm)
    result = await node(state)

    assert result["planned_steps"][0]["params"]["query"] == "재작성 쿼리"
    assert "retry:" in result["planning_reasoning"]
