"""3-tier 리랭킹 + 벡터-리랭커 융합 스코어."""

from src.observability.logging import get_logger

logger = get_logger(__name__)

PREFERRED_MIN_SCORE = 0.5
FALLBACK_MIN_SCORE = 0.25
RERANKER_WEIGHT = 0.7
VECTOR_SCORE_WEIGHT = 0.3
SLIDING_WINDOW_SIZE = 1500


async def rerank_3tier(
    reranker,
    query: str,
    candidates: list[dict],
    top_k: int,
) -> list[dict]:
    """3-tier 리랭킹 + 융합 스코어."""
    # 1. 슬라이딩 윈도우
    documents = [_sliding_window(c["content"]) for c in candidates]

    # 2. CrossEncoder 리랭킹
    reranked = await reranker.rerank(query, documents, top_k=len(candidates))

    # 3. 융합 스코어
    scored = []
    for item in reranked:
        orig = candidates[item["index"]]
        fused = (
            RERANKER_WEIGHT * item["score"]
            + VECTOR_SCORE_WEIGHT * orig["score"]
        )
        scored.append({"data": orig, "fused_score": fused})

    scored.sort(key=lambda x: x["fused_score"], reverse=True)

    # 4. 3-tier 필터링 (Tier1이 top_k 미만이면 Tier2로 보충)
    tier1 = [s for s in scored if s["fused_score"] >= PREFERRED_MIN_SCORE]
    tier2 = [s for s in scored if FALLBACK_MIN_SCORE <= s["fused_score"] < PREFERRED_MIN_SCORE]

    if tier1:
        results = tier1[:top_k]
        if len(results) < top_k and tier2:
            results.extend(tier2[:top_k - len(results)])
    elif tier2:
        results = tier2[:top_k]
    else:
        results = []

    logger.info(
        "rerank_3tier",
        input=len(candidates),
        tier1=len(tier1),
        tier2=len(tier2),
        output=len(results),
    )

    return [{**r["data"], "score": r["fused_score"]} for r in results]


def _sliding_window(text: str) -> str:
    """긴 텍스트를 윈도우 크기로 자른다."""
    if len(text) > SLIDING_WINDOW_SIZE:
        return text[:SLIDING_WINDOW_SIZE]
    return text
