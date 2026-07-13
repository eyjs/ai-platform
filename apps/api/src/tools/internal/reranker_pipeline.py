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
) -> tuple[list[dict], list[dict]]:
    """2-tier 리랭킹 + 스케일 정규화 융합 스코어.

    C8 수정: 벡터(RRF) 점수는 ~0.01 스케일, 리랭커는 0~1 스케일이라 0.7/0.3을
    그대로 가중합하면 벡터 항(0.3 × ~0.01 ≈ 0.003)이 0으로 묻혀 "30% 벡터"가
    허구가 된다. 후보 집합 내 min-max로 벡터 점수를 [0,1]로 정규화한 뒤 융합해
    벡터 신호가 실제로 ranking에 기여하게 한다.

    Returns:
        (results, audit): results 는 채택 top_k. audit 은 **전 후보**의 판정
        기록(fused 내림차순) — "왜 이 청크가 채택/탈락했나"의 역방향 분석 근거.
        audit fate: selected(채택) / capacity(티어 통과·정원 밖) / tier_fail(티어 미달)
    """
    # 1. 슬라이딩 윈도우
    documents = [_sliding_window(c["content"]) for c in candidates]

    # 2. CrossEncoder 리랭킹
    reranked = await reranker.rerank(query, documents, top_k=len(candidates))

    # 3. 벡터(RRF) 점수 min-max 정규화 (후보 집합 내) → 리랭커와 같은 [0,1] 스케일로
    vec_raw = [candidates[item["index"]]["score"] for item in reranked]
    vmin = min(vec_raw) if vec_raw else 0.0
    vmax = max(vec_raw) if vec_raw else 0.0
    vspan = vmax - vmin

    # 4. 융합 스코어 (정규화된 벡터 점수 사용)
    scored = []
    for item in reranked:
        orig = candidates[item["index"]]
        rerank_score = item["score"]
        # 후보가 1개거나 모두 동일 RRF면 벡터 변별력이 없으므로 0 (리랭커 단독)
        vector_score = (orig["score"] - vmin) / vspan if vspan > 1e-9 else 0.0
        fused = RERANKER_WEIGHT * rerank_score + VECTOR_SCORE_WEIGHT * vector_score
        scored.append({
            "data": orig,
            "rerank_score": rerank_score,
            "vector_score": vector_score,
            "fused_score": fused,
        })

    scored.sort(key=lambda x: x["fused_score"], reverse=True)

    # 5. tier 필터링 — 임계값은 정규화된 [0,1] fused 점수 기준.
    #    (C10: 운영 리랭커 출력 분포로 경험적 재캘리브레이션 대상 — observability 로깅 필요)
    tier1 = [s for s in scored if s["fused_score"] >= PREFERRED_MIN_SCORE]
    tier2 = [s for s in scored if FALLBACK_MIN_SCORE <= s["fused_score"] < PREFERRED_MIN_SCORE]

    if tier1:
        results = tier1[:top_k]
        if len(results) < top_k and tier2:
            results = results + tier2[:top_k - len(results)]
    elif tier2:
        results = tier2[:top_k]
    else:
        # 모든 후보가 fallback 미만이면 빈 결과. RAG 충실성(faithfulness)을 위해
        # 저품질 컨텍스트를 강제 반환하지 않는다(환각 방지) — last-resort tier 미적용.
        results = []

    logger.info(
        "rerank_3tier",
        input=len(candidates),
        tier1=len(tier1),
        tier2=len(tier2),
        output=len(results),
    )

    # 전 후보 판정 감사 — 채택/탈락과 그 사유를 후보 단위로 남긴다 (역방향 분석)
    selected_ids = {id(r) for r in results}
    audit = []
    for r in scored:
        fused = r["fused_score"]
        tier = 1 if fused >= PREFERRED_MIN_SCORE else (
            2 if fused >= FALLBACK_MIN_SCORE else 0
        )
        if id(r) in selected_ids:
            fate = "selected"
        elif tier > 0:
            fate = "capacity"   # 티어는 통과했으나 top_k 정원 밖
        else:
            fate = "tier_fail"  # FALLBACK_MIN_SCORE 미달
        audit.append({
            "chunk_id": r["data"].get("chunk_id"),
            "document_id": r["data"].get("document_id"),
            "rerank_score": round(r["rerank_score"], 4),
            "vector_score": round(r["vector_score"], 4),
            "fused": round(fused, 4),
            "tier": tier,
            "fate": fate,
        })

    return [
        {
            **r["data"],
            "score": r["fused_score"],
            "rerank_score": r["rerank_score"],
            "vector_score": r["vector_score"],
        }
        for r in results
    ], audit


def _sliding_window(text: str) -> str:
    """긴 텍스트를 윈도우 크기로 자른다."""
    if len(text) > SLIDING_WINDOW_SIZE:
        return text[:SLIDING_WINDOW_SIZE]
    return text
