"""T2: rag_search._multi_query_search 병렬 실행 검증."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.internal.rag_search import RAGSearchTool
from src.domain.models import SearchScope


def _make_tool() -> RAGSearchTool:
    """mock 의존성으로 RAGSearchTool 생성."""
    store = AsyncMock()
    router_llm = MagicMock()
    embedder = MagicMock()
    reranker = MagicMock()
    return RAGSearchTool(
        embedding_provider=embedder,
        vector_store=store,
        reranker=reranker,
        router_llm=router_llm,
    )


def _scope() -> SearchScope:
    return SearchScope(
        domain_codes=["test"],
        allowed_doc_ids=None,
        security_level_max="public",
    )


async def test_multi_query_search_uses_gather():
    """_multi_query_search가 asyncio.gather로 병렬 호출한다."""
    tool = _make_tool()

    call_times: list[float] = []

    async def slow_hybrid_search(**kwargs):
        call_times.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.05)  # 50ms delay
        return [
            {"chunk_id": f"c-{kwargs['text_query']}", "score": 0.9, "content": "x"},
        ]

    tool._store.hybrid_search = slow_hybrid_search

    queries = ["q1", "q2", "q3"]
    embeddings = [[0.1], [0.2], [0.3]]

    start = asyncio.get_event_loop().time()
    results = await tool._multi_query_search(queries, embeddings, _scope())
    elapsed = asyncio.get_event_loop().time() - start

    # 3개 쿼리가 병렬이면 ~50ms, 순차면 ~150ms
    assert elapsed < 0.12, f"병렬 실행이 아님: {elapsed:.3f}s (expected <0.12s)"

    # 3개 결과가 merge됨
    assert len(results) == 3


async def test_multi_query_search_merges_by_chunk_id():
    """동일 chunk_id는 최고 점수만 유지한다."""
    tool = _make_tool()

    async def mock_hybrid_search(**kwargs):
        if kwargs["text_query"] == "q1":
            return [
                {"chunk_id": "c1", "score": 0.9, "content": "x"},
                {"chunk_id": "c2", "score": 0.7, "content": "y"},
            ]
        elif kwargs["text_query"] == "q2":
            return [
                {"chunk_id": "c1", "score": 0.95, "content": "x"},  # higher score
                {"chunk_id": "c3", "score": 0.6, "content": "z"},
            ]
        return []

    tool._store.hybrid_search = mock_hybrid_search

    results = await tool._multi_query_search(
        ["q1", "q2"], [[0.1], [0.2]], _scope(),
    )

    # c1, c2, c3 = 3개 고유 chunk
    assert len(results) == 3

    # c1은 최고 점수 0.95
    c1 = next(r for r in results if r["chunk_id"] == "c1")
    assert c1["score"] == pytest.approx(0.95)

    # 점수 내림차순 정렬
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


async def test_multi_query_search_empty_queries():
    """빈 쿼리 리스트면 빈 결과 반환."""
    tool = _make_tool()
    results = await tool._multi_query_search([], [], _scope())
    assert results == []


async def test_multi_query_search_single_query():
    """단일 쿼리도 정상 동작한다."""
    tool = _make_tool()

    async def mock_hybrid_search(**kwargs):
        return [{"chunk_id": "c1", "score": 0.9, "content": "x"}]

    tool._store.hybrid_search = mock_hybrid_search

    results = await tool._multi_query_search(
        ["q1"], [[0.1]], _scope(),
    )
    assert len(results) == 1
    assert results[0]["chunk_id"] == "c1"
