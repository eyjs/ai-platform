"""RAG 검색 파이프라인 통합 테스트."""

import pytest
from unittest.mock import AsyncMock

from src.domain.models import SearchScope
from src.domain.agent_context import AgentContext


def _make_context() -> AgentContext:
    return AgentContext(session_id="s1", user_id="u1", user_role="EDITOR")


def _make_scope() -> SearchScope:
    return SearchScope(domain_codes=["INS"], security_level_max="INTERNAL")


def _mock_chunk(chunk_id: str, score: float) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "doc1",
        "content": f"content-{chunk_id}",
        "chunk_index": 0,
        "score": score,
        "file_name": "test.pdf",
        "title": "test",
    }


@pytest.mark.asyncio
async def test_full_pipeline_with_reranker():
    """전체 파이프라인: 확장 -> 검색 -> 노이즈 -> 이웃 -> 리랭킹 -> 가드."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    embedder.embed_batch.return_value = [[0.1] * 10, [0.2] * 10, [0.3] * 10]

    store = AsyncMock()
    # probe top-1 = 0.3 (< 0.4) → 확장 트리거
    store.hybrid_search.return_value = [
        _mock_chunk(f"c{i}", 0.3 - i * 0.01) for i in range(10)
    ]
    store.get_neighbor_chunks.return_value = []

    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": 0, "score": 0.9},
        {"index": 1, "score": 0.8},
        {"index": 2, "score": 0.7},
        {"index": 3, "score": 0.6},
        {"index": 4, "score": 0.5},
    ]

    router_llm = AsyncMock()
    router_llm.generate_json.return_value = ["변형1", "변형2"]

    # embed_batch: probe 1회 + 확장 3쿼리 1회
    embedder.embed_batch.side_effect = [
        [[0.1] * 10],
        [[0.1] * 10, [0.2] * 10, [0.3] * 10],
    ]

    tool = RAGSearchTool(
        embedding_provider=embedder,
        vector_store=store,
        reranker=reranker,
        router_llm=router_llm,
    )

    result = await tool.execute(
        {"query": "보험금 청구"},
        _make_context(),
        _make_scope(),
    )

    assert result.success is True
    assert len(result.data) > 0
    assert len(embedder.embed_batch.call_args_list[1][0][0]) == 3


@pytest.mark.asyncio
async def test_pipeline_without_reranker():
    """리랭커 없을 때 top_k 절단."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    embedder.embed_batch.return_value = [[0.1] * 10]

    store = AsyncMock()
    store.hybrid_search.return_value = [
        _mock_chunk(f"c{i}", 0.9 - i * 0.1) for i in range(10)
    ]
    store.get_neighbor_chunks.return_value = []

    router_llm = AsyncMock()
    router_llm.generate_json.side_effect = Exception("LLM down")

    tool = RAGSearchTool(
        embedding_provider=embedder,
        vector_store=store,
        reranker=None,
        router_llm=router_llm,
    )

    result = await tool.execute(
        {"query": "테스트"},
        _make_context(),
        _make_scope(),
    )

    assert result.success is True
    assert len(result.data) <= 5


@pytest.mark.asyncio
async def test_empty_query():
    from src.tools.internal.rag_search import RAGSearchTool

    tool = RAGSearchTool(
        embedding_provider=AsyncMock(),
        vector_store=AsyncMock(),
        router_llm=AsyncMock(),
    )
    result = await tool.execute({"query": ""}, _make_context(), _make_scope())
    assert result.success is False


@pytest.mark.asyncio
async def test_adaptive_skips_expansion_on_high_probe_score():
    """probe top-1 점수가 임계값 이상이면 쿼리 확장 스킵."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    embedder.embed_batch.return_value = [[0.1] * 10]

    store = AsyncMock()
    # probe 결과: top-1 점수 0.5 (>= 0.4 임계값)
    store.hybrid_search.return_value = [
        _mock_chunk(f"c{i}", 0.9 - i * 0.05) for i in range(10)
    ]
    store.get_neighbor_chunks.return_value = []

    router_llm = AsyncMock()
    router_llm.generate_json.return_value = ["변형1", "변형2"]

    tool = RAGSearchTool(
        embedding_provider=embedder,
        vector_store=store,
        reranker=None,
        router_llm=router_llm,
    )

    result = await tool.execute(
        {"query": "보험금 청구"},
        _make_context(),
        _make_scope(),
    )

    assert result.success is True
    # 확장 스킵 → embed_batch에 원본 1개만 전달
    router_llm.generate_json.assert_not_called()
    assert len(embedder.embed_batch.call_args[0][0]) == 1


@pytest.mark.asyncio
async def test_adaptive_expands_on_low_probe_score():
    """probe top-1 점수가 낮으면 쿼리 확장 실행."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    # 첫 호출: probe 임베딩, 둘째 호출: 확장된 3쿼리 임베딩
    embedder.embed_batch.side_effect = [
        [[0.1] * 10],
        [[0.1] * 10, [0.2] * 10, [0.3] * 10],
    ]

    store = AsyncMock()
    # probe 결과: top-1 점수 0.2 (< 0.4 임계값)
    store.hybrid_search.return_value = [
        _mock_chunk(f"c{i}", 0.2 - i * 0.01) for i in range(10)
    ]
    store.get_neighbor_chunks.return_value = []

    router_llm = AsyncMock()
    router_llm.generate_json.return_value = ["변형1", "변형2"]

    tool = RAGSearchTool(
        embedding_provider=embedder,
        vector_store=store,
        reranker=None,
        router_llm=router_llm,
    )

    result = await tool.execute(
        {"query": "보험금 청구"},
        _make_context(),
        _make_scope(),
    )

    assert result.success is True
    # 확장 실행됨
    router_llm.generate_json.assert_called_once()
    # 두 번째 embed_batch 호출에 3개 쿼리
    assert len(embedder.embed_batch.call_args_list[1][0][0]) == 3


@pytest.mark.asyncio
async def test_adaptive_no_llm_skips_probe():
    """router_llm 없으면 probe도 확장도 스킵 — 원본만 검색."""
    from src.tools.internal.rag_search import RAGSearchTool

    embedder = AsyncMock()
    embedder.embed_batch.return_value = [[0.1] * 10]

    store = AsyncMock()
    store.hybrid_search.return_value = [
        _mock_chunk(f"c{i}", 0.9 - i * 0.05) for i in range(10)
    ]
    store.get_neighbor_chunks.return_value = []

    tool = RAGSearchTool(
        embedding_provider=embedder,
        vector_store=store,
        router_llm=None,
    )

    result = await tool.execute(
        {"query": "테스트"},
        _make_context(),
        _make_scope(),
    )

    assert result.success is True
    embedder.embed_batch.assert_called_once()
    assert len(embedder.embed_batch.call_args[0][0]) == 1
