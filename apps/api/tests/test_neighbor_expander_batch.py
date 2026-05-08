"""T2: neighbor_expander 배치 IN 쿼리 통합 검증."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.tools.internal.neighbor_expander import expand_neighbors, NEIGHBOR_EXPAND_TOP_N


def _chunk(chunk_id: str, doc_id: str, idx: int, score: float) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "chunk_index": idx,
        "score": score,
        "content": f"content-{chunk_id}",
    }


async def test_same_doc_batched_single_call():
    """동일 document_id의 여러 청크는 인덱스를 묶어 1회 호출한다."""
    store = AsyncMock()
    store.get_neighbor_chunks.return_value = [
        {"chunk_id": "nb0", "document_id": "d1", "content": "n0", "chunk_index": 0},
        {"chunk_id": "nb4", "document_id": "d1", "content": "n4", "chunk_index": 4},
    ]

    candidates = [
        _chunk("c1", "d1", 1, 0.9),
        _chunk("c2", "d1", 3, 0.8),
    ]

    result = await expand_neighbors(store, candidates)

    # 같은 document_id이므로 get_neighbor_chunks 1회만 호출
    assert store.get_neighbor_chunks.call_count == 1

    # 호출된 인덱스 목록 확인 (chunk_index 0,2,4 = neighbors of idx=1 and idx=3)
    call_args = store.get_neighbor_chunks.call_args
    doc_id_arg = call_args[0][0]
    indices_arg = call_args[0][1]
    assert doc_id_arg == "d1"
    assert sorted(indices_arg) == [0, 2, 4]

    # 원본 2개 + neighbor 2개 = 4개
    assert len(result) == 4


async def test_different_docs_parallel_calls():
    """서로 다른 document_id는 각각 1회씩 병렬 호출한다."""
    store = AsyncMock()
    store.get_neighbor_chunks.return_value = []

    candidates = [
        _chunk("c1", "d1", 1, 0.9),
        _chunk("c2", "d2", 1, 0.85),
        _chunk("c3", "d3", 1, 0.80),
    ]

    await expand_neighbors(store, candidates)

    # 3개 다른 document_id -> 3회 호출
    assert store.get_neighbor_chunks.call_count == 3


async def test_dedup_neighbor_indices():
    """동일 document_id 내 중복 인덱스는 제거되어 전송된다."""
    store = AsyncMock()
    store.get_neighbor_chunks.return_value = []

    # chunk_index=2, chunk_index=4 -> neighbors: 1,3 와 3,5 -> 3이 중복
    candidates = [
        _chunk("c1", "d1", 2, 0.9),
        _chunk("c2", "d1", 4, 0.85),
    ]

    await expand_neighbors(store, candidates)

    call_args = store.get_neighbor_chunks.call_args
    indices_arg = call_args[0][1]
    # 중복 제거: {1, 3, 5} (sorted)
    assert sorted(indices_arg) == [1, 3, 5]


async def test_negative_index_excluded():
    """chunk_index=0의 neighbor idx=-1은 제외된다."""
    store = AsyncMock()
    store.get_neighbor_chunks.return_value = [
        {"chunk_id": "nb1", "document_id": "d1", "content": "n1", "chunk_index": 1},
    ]

    candidates = [_chunk("c0", "d1", 0, 0.9)]

    await expand_neighbors(store, candidates)

    call_args = store.get_neighbor_chunks.call_args
    indices_arg = call_args[0][1]
    # idx=0 -> neighbors: -1 (excluded), 1 (included)
    assert indices_arg == [1]


async def test_score_uses_highest_parent():
    """동일 neighbor_index를 여러 parent가 참조하면 최고 점수 기준으로 decay."""
    store = AsyncMock()
    store.get_neighbor_chunks.return_value = [
        {"chunk_id": "nb2", "document_id": "d1", "content": "n2", "chunk_index": 2},
    ]

    # c1 (idx=1, score=0.9) -> neighbor idx=2
    # c3 (idx=3, score=0.7) -> neighbor idx=2
    # 최고 parent score = 0.9
    candidates = [
        _chunk("c1", "d1", 1, 0.9),
        _chunk("c3", "d1", 3, 0.7),
    ]

    result = await expand_neighbors(store, candidates)

    nb = next(r for r in result if r["chunk_id"] == "nb2")
    assert nb["score"] == pytest.approx(0.9 * 0.8)


async def test_empty_candidates():
    """빈 candidates면 store 호출 없이 즉시 반환."""
    store = AsyncMock()
    result = await expand_neighbors(store, [])
    assert result == []
    store.get_neighbor_chunks.assert_not_called()


async def test_all_missing_chunk_index():
    """모든 청크에 chunk_index가 없으면 store 호출 없이 반환."""
    store = AsyncMock()
    candidates = [
        {"chunk_id": "c1", "document_id": "d1", "score": 0.9, "content": "x"},
        {"chunk_id": "c2", "document_id": "d2", "score": 0.8, "content": "y"},
    ]
    result = await expand_neighbors(store, candidates)
    assert len(result) == 2
    store.get_neighbor_chunks.assert_not_called()
