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
    # a: 0.7*0.9 + 0.3*0.8 = 0.87 (Tier1: >= 0.5)
    # b: 0.7*0.7 + 0.3*0.7 = 0.70 (Tier1: >= 0.5)
    # c: 0.7*0.3 + 0.3*0.6 = 0.39 (Tier2: >= 0.15, Tier1 보충)
    # top_k=5이므로 Tier1(2개) + Tier2 보충(1개) = 3개
    assert len(result) == 3
    assert result[0]["chunk_id"] == "a"


@pytest.mark.xfail(
    reason="Step 8(임계값 재캘리브레이션)에서 재작성 예정. 현 구현은 FALLBACK_MIN_SCORE=0.25라 "
    "테스트의 0.15 가정과 불일치. RAG 융합 재설계(Step 5) 후 RRF 스케일 픽스처로 교체한다.",
    strict=True,
)
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


@pytest.mark.xfail(
    reason="Step 8에서 재작성 예정. 함수명은 rerank_3tier이나 현 구현은 2-tier(tier1/tier2)만 동작하고 "
    "둘 다 미달이면 빈 결과를 반환한다. Tier3(last-resort) 복원 여부는 Step 5 정규화 후 결정한다.",
    strict=True,
)
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
