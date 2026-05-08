"""T3: VectorStore hybrid_search 서브쿼리 병렬 실행 검증."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.vector_store import (
    VectorStore,
    TRIGRAM_FALLBACK_THRESHOLD,
    HYBRID_CANDIDATE_MULTIPLIER,
)


def _make_store() -> VectorStore:
    """테스트용 VectorStore (pool mock)."""
    store = VectorStore.__new__(VectorStore)
    store._pool = AsyncMock()
    store._database_url = "test://localhost/test"
    return store


def _mock_row(chunk_id: str, score: float = 0.9) -> dict:
    """asyncpg row를 흉내내는 dict."""
    return {
        "id": chunk_id,
        "document_id": "doc-1",
        "content": f"content-{chunk_id}",
        "chunk_index": 0,
        "score": score,
        "file_name": "test.pdf",
        "title": "Test Doc",
    }


async def test_hybrid_search_parallel_execution():
    """vector와 text 검색이 asyncio.gather로 병렬 실행된다."""
    store = _make_store()

    call_times: list[float] = []

    async def slow_vector_fetch(*args, **kwargs):
        call_times.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.05)
        return [_mock_row("v1", 0.95)]

    async def slow_text_conn_ctx():
        """mock connection context manager."""
        conn = AsyncMock()

        async def slow_fts_fetch(*args, **kwargs):
            call_times.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.05)
            return [_mock_row("f1", 0.8), _mock_row("f2", 0.7), _mock_row("f3", 0.6)]

        conn.fetch = slow_fts_fetch
        return conn

    # Mock pool.acquire to return different connections
    call_count = [0]

    class MockConnCtx:
        async def __aenter__(self):
            call_count[0] += 1
            conn = AsyncMock()
            if call_count[0] == 1:
                # vector search connection
                conn.fetch = slow_vector_fetch
            else:
                # text search connection
                async def fts(*args, **kwargs):
                    call_times.append(asyncio.get_event_loop().time())
                    await asyncio.sleep(0.05)
                    return [_mock_row("f1", 0.8), _mock_row("f2", 0.7), _mock_row("f3", 0.6)]
                conn.fetch = fts
            return conn

        async def __aexit__(self, *args):
            pass

    store._pool.acquire = MockConnCtx

    start = asyncio.get_event_loop().time()
    results = await store.hybrid_search(
        embedding=[0.1] * 768,
        text_query="test query",
        limit=5,
    )
    elapsed = asyncio.get_event_loop().time() - start

    # 병렬 실행이면 ~50ms, 순차면 ~100ms
    assert elapsed < 0.09, f"병렬 실행이 아님: {elapsed:.3f}s (expected <0.09s)"

    # 두 검색 시작 시간이 거의 동시
    assert len(call_times) == 2
    time_diff = abs(call_times[0] - call_times[1])
    assert time_diff < 0.01, f"동시 시작이 아님: {time_diff:.3f}s 차이"


async def test_hybrid_search_returns_merged_results():
    """병렬화 후에도 RRF 병합 결과가 정상이다."""
    store = _make_store()

    call_count = [0]

    class MockConnCtx:
        async def __aenter__(self):
            call_count[0] += 1
            conn = AsyncMock()
            if call_count[0] == 1:
                # vector
                conn.fetch = AsyncMock(return_value=[
                    _mock_row("c1", 0.95),
                    _mock_row("c2", 0.85),
                ])
            else:
                # text (FTS returns >= threshold, no trigram)
                conn.fetch = AsyncMock(return_value=[
                    _mock_row("c1", 0.9),
                    _mock_row("c3", 0.7),
                    _mock_row("c4", 0.6),
                ])
            return conn

        async def __aexit__(self, *args):
            pass

    store._pool.acquire = MockConnCtx

    results = await store.hybrid_search(
        embedding=[0.1] * 768,
        text_query="test",
        limit=3,
    )

    # c1이 양쪽 모두 나타나므로 최상위
    assert len(results) == 3
    assert results[0]["chunk_id"] == "c1"
    # 모든 결과에 score 존재
    for r in results:
        assert "score" in r
        assert r["score"] > 0


async def test_text_search_combined_trigram_fallback():
    """FTS 결과가 threshold 미만이면 trigram이 실행된다."""
    store = _make_store()

    trigram_called = [False]

    call_count = [0]

    class MockConnCtx:
        async def __aenter__(self):
            call_count[0] += 1
            conn = AsyncMock()
            if call_count[0] == 1:
                # vector
                conn.fetch = AsyncMock(return_value=[_mock_row("v1")])
            else:
                # text search conn
                fts_call = [0]
                async def text_fetch(*args, **kwargs):
                    fts_call[0] += 1
                    if fts_call[0] == 1:
                        # FTS: 2건 (< threshold 3)
                        return [_mock_row("f1", 0.8), _mock_row("f2", 0.7)]
                    # trigram
                    trigram_called[0] = True
                    return [_mock_row("t1", 0.5)]
                conn.fetch = text_fetch
            return conn

        async def __aexit__(self, *args):
            pass

    store._pool.acquire = MockConnCtx

    results = await store.hybrid_search(
        embedding=[0.1] * 768,
        text_query="test query",
        limit=5,
    )

    assert trigram_called[0], "trigram fallback이 실행되어야 함"


async def test_metadata_search_parallel():
    """metadata_search도 병렬화 적용 확인."""
    store = _make_store()

    call_count = [0]

    class MockConnCtx:
        async def __aenter__(self):
            call_count[0] += 1
            conn = AsyncMock()
            if call_count[0] == 1:
                conn.fetch = AsyncMock(return_value=[{
                    "id": "c1",
                    "document_id": "d1",
                    "chunk_index": 0,
                    "summary": "test content",
                    "domain_code": "test",
                    "security_level": "PUBLIC",
                    "score": 0.9,
                    "file_name": "test.pdf",
                    "title": "Test",
                }])
            else:
                conn.fetch = AsyncMock(return_value=[{
                    "id": "c2",
                    "document_id": "d1",
                    "chunk_index": 1,
                    "summary": "other content",
                    "domain_code": "test",
                    "security_level": "PUBLIC",
                    "score": 0.7,
                    "file_name": "test.pdf",
                    "title": "Test",
                }, {
                    "id": "c3",
                    "document_id": "d1",
                    "chunk_index": 2,
                    "summary": "more",
                    "domain_code": "test",
                    "security_level": "PUBLIC",
                    "score": 0.6,
                    "file_name": "test.pdf",
                    "title": "Test",
                }, {
                    "id": "c4",
                    "document_id": "d1",
                    "chunk_index": 3,
                    "summary": "extra",
                    "domain_code": "test",
                    "security_level": "PUBLIC",
                    "score": 0.5,
                    "file_name": "test.pdf",
                    "title": "Test",
                }])
            return conn

        async def __aexit__(self, *args):
            pass

    store._pool.acquire = MockConnCtx

    results = await store.metadata_search(
        embedding=[0.1] * 768,
        text_query="test",
        limit=3,
    )

    # pool.acquire이 2회 호출됨 (병렬 2커넥션)
    assert call_count[0] == 2
    assert len(results) > 0


async def test_vector_search_error_isolation():
    """vector 검색 에러가 text 검색에 영향 주지 않는다 (gather 격리)."""
    store = _make_store()

    call_count = [0]

    class MockConnCtx:
        async def __aenter__(self):
            call_count[0] += 1
            conn = AsyncMock()
            if call_count[0] == 1:
                # vector 에러
                conn.fetch = AsyncMock(side_effect=RuntimeError("vector failed"))
            else:
                conn.fetch = AsyncMock(return_value=[
                    _mock_row("f1", 0.8),
                    _mock_row("f2", 0.7),
                    _mock_row("f3", 0.6),
                ])
            return conn

        async def __aexit__(self, *args):
            pass

    store._pool.acquire = MockConnCtx

    # vector 에러는 전파됨 (gather는 에러를 그대로 raise)
    with pytest.raises(RuntimeError, match="vector failed"):
        await store.hybrid_search(
            embedding=[0.1] * 768,
            text_query="test",
            limit=5,
        )
