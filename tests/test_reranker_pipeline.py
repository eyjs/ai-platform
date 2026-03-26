"""3-tier 리랭킹 파이프라인 단위 테스트."""

import pytest
from unittest.mock import AsyncMock


def _chunk(chunk_id: str, score: float) -> dict:
    return {"chunk_id": chunk_id, "score": score, "content": f"content-{chunk_id}"}


@pytest.mark.asyncio
async def test_tier1_high_quality():
    from src.tools.internal.reranker_pipeline import rerank_3tier
    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": 0, "score": 0.9},
        {"index": 1, "score": 0.7},
        {"index": 2, "score": 0.3},
    ]
    candidates = [_chunk("a", 0.8), _chunk("b", 0.7), _chunk("c", 0.6)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)
    # a: 0.7*0.9 + 0.3*0.8 = 0.87
    # b: 0.7*0.7 + 0.3*0.7 = 0.70
    # c: 0.7*0.3 + 0.3*0.6 = 0.39 (< 0.5)
    assert len(result) == 2
    assert result[0]["chunk_id"] == "a"


@pytest.mark.asyncio
async def test_tier2_fallback():
    from src.tools.internal.reranker_pipeline import rerank_3tier
    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": 0, "score": 0.3},
        {"index": 1, "score": 0.2},
        {"index": 2, "score": 0.01},
    ]
    candidates = [_chunk("a", 0.4), _chunk("b", 0.3), _chunk("c", 0.2)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)
    # a: 0.7*0.3 + 0.3*0.4 = 0.33 (> 0.15)
    # b: 0.7*0.2 + 0.3*0.3 = 0.23 (> 0.15)
    # c: 0.7*0.01 + 0.3*0.2 = 0.067 (< 0.15)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_tier3_last_resort():
    from src.tools.internal.reranker_pipeline import rerank_3tier
    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": i, "score": 0.01} for i in range(5)
    ]
    candidates = [_chunk(str(i), 0.01) for i in range(5)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_top_k_limits_output():
    from src.tools.internal.reranker_pipeline import rerank_3tier
    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": i, "score": 0.9} for i in range(10)
    ]
    candidates = [_chunk(str(i), 0.9) for i in range(10)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=3)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_sliding_window_truncates():
    from src.tools.internal.reranker_pipeline import rerank_3tier, SLIDING_WINDOW_SIZE
    reranker = AsyncMock()
    reranker.rerank.return_value = [{"index": 0, "score": 0.9}]
    long_content = "x" * (SLIDING_WINDOW_SIZE + 500)
    candidates = [{"chunk_id": "c1", "score": 0.9, "content": long_content}]
    await rerank_3tier(reranker, "질문", candidates, top_k=5)
    call_args = reranker.rerank.call_args
    passed_doc = call_args[0][1][0]
    assert len(passed_doc) == SLIDING_WINDOW_SIZE


@pytest.mark.asyncio
async def test_fused_score_in_output():
    from src.tools.internal.reranker_pipeline import rerank_3tier
    reranker = AsyncMock()
    reranker.rerank.return_value = [{"index": 0, "score": 0.8}]
    candidates = [_chunk("a", 0.6)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)
    expected_fused = 0.7 * 0.8 + 0.3 * 0.6
    assert result[0]["score"] == pytest.approx(expected_fused)
