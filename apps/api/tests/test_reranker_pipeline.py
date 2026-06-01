"""리랭킹 파이프라인 단위 테스트 (스케일 정규화 융합).

벡터 점수(_chunk의 두번째 인자)는 운영의 RRF 스케일(~0.008-0.016)로 둔다.
rerank_3tier은 후보 집합 내 min-max로 벡터 점수를 [0,1] 정규화한 뒤
fused = 0.7*reranker + 0.3*vector_norm 으로 융합한다.
"""

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
    candidates = [_chunk("a", 0.016), _chunk("b", 0.012), _chunk("c", 0.008)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)
    # vec min-max: a=1.0, b=0.5, c=0.0
    # a: 0.7*0.9 + 0.3*1.0 = 0.93 (Tier1)
    # b: 0.7*0.7 + 0.3*0.5 = 0.64 (Tier1)
    # c: 0.7*0.3 + 0.3*0.0 = 0.21 (< 0.25, 제외)
    assert len(result) == 2
    assert result[0]["chunk_id"] == "a"
    assert result[0]["vector_score"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_tier2_fallback():
    from src.tools.internal.reranker_pipeline import rerank_3tier
    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": 0, "score": 0.28},
        {"index": 1, "score": 0.20},
        {"index": 2, "score": 0.05},
    ]
    candidates = [_chunk("a", 0.016), _chunk("b", 0.012), _chunk("c", 0.008)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)
    # vec min-max: a=1.0, b=0.5, c=0.0
    # a: 0.7*0.28 + 0.3 = 0.496 (Tier2, <0.5)
    # b: 0.7*0.20 + 0.15 = 0.29 (Tier2)
    # c: 0.7*0.05 + 0   = 0.035 (< 0.25, 제외)
    # Tier1 비어있음 → Tier2로 폴백
    assert len(result) == 2
    assert result[0]["chunk_id"] == "a"


@pytest.mark.asyncio
async def test_all_irrelevant_returns_empty():
    """[설계] 모든 후보가 저품질(fallback 미만)이면 빈 결과. last-resort 미적용(환각 방지)."""
    from src.tools.internal.reranker_pipeline import rerank_3tier
    reranker = AsyncMock()
    reranker.rerank.return_value = [{"index": i, "score": 0.05} for i in range(5)]
    # 모두 동일 RRF → 벡터 변별력 없음(vspan=0) → fused = 0.7*0.05 = 0.035 < 0.25
    candidates = [_chunk(str(i), 0.01) for i in range(5)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)
    assert result == []


@pytest.mark.asyncio
async def test_vector_signal_breaks_ties():
    """[C8 회귀] 리랭커 점수가 동일하면 정규화된 벡터 신호가 순위를 가른다.

    구버그에서는 0.3*(0.016-0.008)=0.0024로 사실상 무시됐다. 정규화 후 0.3 차이.
    """
    from src.tools.internal.reranker_pipeline import rerank_3tier
    reranker = AsyncMock()
    reranker.rerank.return_value = [
        {"index": 0, "score": 0.5},
        {"index": 1, "score": 0.5},  # 동점
    ]
    candidates = [_chunk("a", 0.016), _chunk("b", 0.008)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)
    # a: 0.7*0.5 + 0.3*1.0 = 0.65, b: 0.7*0.5 + 0.3*0.0 = 0.35
    assert result[0]["chunk_id"] == "a"
    # 벡터 신호가 0으로 묻히지 않고 실질적 차이를 만든다 (구버그면 ~0.0024)
    assert result[0]["score"] - result[1]["score"] > 0.1


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
async def test_fused_score_single_candidate():
    """후보가 1개면 벡터 변별력이 없어(vspan=0) fused = 0.7*reranker, vector_score=0."""
    from src.tools.internal.reranker_pipeline import rerank_3tier
    reranker = AsyncMock()
    reranker.rerank.return_value = [{"index": 0, "score": 0.8}]
    candidates = [_chunk("a", 0.012)]
    result = await rerank_3tier(reranker, "질문", candidates, top_k=5)
    assert result[0]["score"] == pytest.approx(0.7 * 0.8)
    assert result[0]["vector_score"] == pytest.approx(0.0)
    assert result[0]["rerank_score"] == pytest.approx(0.8)
