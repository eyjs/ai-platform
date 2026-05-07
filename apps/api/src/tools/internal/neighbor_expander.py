"""인접 청크 확장. 상위 청크의 앞뒤 맥락을 보강한다."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from src.observability.logging import get_logger

logger = get_logger(__name__)

NEIGHBOR_EXPAND_TOP_N = 5
NEIGHBOR_SCORE_DECAY = 0.8


async def expand_neighbors(
    vector_store, candidates: list[dict],
) -> list[dict]:
    """상위 N개 청크의 인접 청크를 가져와 후보에 추가.

    동일 document_id의 인덱스를 묶어 단일 IN 쿼리로 통합한다.
    서로 다른 document_id는 asyncio.gather로 병렬 실행한다.
    """
    if not candidates:
        return candidates

    seen = {c["chunk_id"] for c in candidates}
    expanded = list(candidates)

    # document_id별로 (chunk_index, chunk_score) 묶기
    doc_indices: dict[str, list[int]] = defaultdict(list)
    # chunk_index -> 해당 chunk의 score (neighbor에 decay 적용용)
    index_score: dict[tuple[str, int], float] = {}

    for chunk in candidates[:NEIGHBOR_EXPAND_TOP_N]:
        idx = chunk.get("chunk_index")
        if idx is None:
            continue

        doc_id = chunk["document_id"]
        for neighbor_idx in (idx - 1, idx + 1):
            if neighbor_idx >= 0:
                doc_indices[doc_id].append(neighbor_idx)
                # 같은 인덱스가 여러 청크에서 요청될 수 있으므로 최고 점수 유지
                key = (doc_id, neighbor_idx)
                existing = index_score.get(key, 0.0)
                index_score[key] = max(existing, chunk["score"])

    if not doc_indices:
        return expanded

    # document_id별 고유 인덱스로 중복 제거
    deduped: dict[str, list[int]] = {
        doc_id: sorted(set(indices))
        for doc_id, indices in doc_indices.items()
    }

    # document_id별로 병렬 호출
    async def _fetch_for_doc(doc_id: str, indices: list[int]) -> list[dict]:
        return await vector_store.get_neighbor_chunks(doc_id, indices)

    tasks = [
        _fetch_for_doc(doc_id, indices)
        for doc_id, indices in deduped.items()
    ]
    results = await asyncio.gather(*tasks)

    for doc_id, neighbors in zip(deduped.keys(), results):
        for nb in neighbors:
            if nb["chunk_id"] not in seen:
                key = (doc_id, nb["chunk_index"])
                parent_score = index_score.get(key, 0.0)
                nb["score"] = parent_score * NEIGHBOR_SCORE_DECAY
                expanded.append(nb)
                seen.add(nb["chunk_id"])

    added = len(expanded) - len(candidates)
    if added:
        logger.debug("neighbor_expand", added=added, total=len(expanded))

    return expanded
