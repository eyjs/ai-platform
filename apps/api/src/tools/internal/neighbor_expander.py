"""인접 청크 확장. 상위 청크의 앞뒤 맥락을 보강한다."""

from src.observability.logging import get_logger

logger = get_logger(__name__)

NEIGHBOR_EXPAND_TOP_N = 5
NEIGHBOR_SCORE_DECAY = 0.8


async def expand_neighbors(
    vector_store, candidates: list[dict],
) -> list[dict]:
    """상위 N개 청크의 인접 청크를 가져와 후보에 추가."""
    if not candidates:
        return candidates

    seen = {c["chunk_id"] for c in candidates}
    expanded = list(candidates)

    for chunk in candidates[:NEIGHBOR_EXPAND_TOP_N]:
        idx = chunk.get("chunk_index")
        if idx is None:
            continue

        neighbors = await vector_store.get_neighbor_chunks(
            chunk["document_id"], [idx - 1, idx + 1],
        )

        for nb in neighbors:
            if nb["chunk_id"] not in seen:
                nb["score"] = chunk["score"] * NEIGHBOR_SCORE_DECAY
                expanded.append(nb)
                seen.add(nb["chunk_id"])

    added = len(expanded) - len(candidates)
    if added:
        logger.debug("neighbor_expand", added=added, total=len(expanded))

    return expanded
