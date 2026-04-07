"""이웃 확장 단위 테스트."""

import pytest
from unittest.mock import AsyncMock


def _chunk(chunk_id: str, doc_id: str, idx: int, score: float) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "chunk_index": idx,
        "score": score,
        "content": f"content-{chunk_id}",
    }


@pytest.mark.asyncio
async def test_empty_input():
    from src.tools.internal.neighbor_expander import expand_neighbors
    store = AsyncMock()
    result = await expand_neighbors(store, [])
    assert result == []
    store.get_neighbor_chunks.assert_not_called()


@pytest.mark.asyncio
async def test_expands_top_n():
    from src.tools.internal.neighbor_expander import expand_neighbors
    store = AsyncMock()
    store.get_neighbor_chunks.return_value = [
        {"chunk_id": "nb1", "document_id": "d1", "content": "neighbor", "chunk_index": 0},
    ]
    candidates = [_chunk("c1", "d1", 1, 0.9)]
    result = await expand_neighbors(store, candidates)
    assert len(result) == 2
    assert result[1]["chunk_id"] == "nb1"
    assert result[1]["score"] == pytest.approx(0.9 * 0.8)


@pytest.mark.asyncio
async def test_no_duplicate_neighbors():
    from src.tools.internal.neighbor_expander import expand_neighbors
    store = AsyncMock()
    store.get_neighbor_chunks.return_value = [
        {"chunk_id": "c2", "document_id": "d1", "content": "already", "chunk_index": 2},
    ]
    candidates = [
        _chunk("c1", "d1", 1, 0.9),
        _chunk("c2", "d1", 2, 0.8),
    ]
    result = await expand_neighbors(store, candidates)
    chunk_ids = [c["chunk_id"] for c in result]
    assert chunk_ids.count("c2") == 1


@pytest.mark.asyncio
async def test_missing_chunk_index_skipped():
    from src.tools.internal.neighbor_expander import expand_neighbors
    store = AsyncMock()
    candidates = [{"chunk_id": "c1", "document_id": "d1", "score": 0.9, "content": "x"}]
    result = await expand_neighbors(store, candidates)
    assert len(result) == 1
    store.get_neighbor_chunks.assert_not_called()


@pytest.mark.asyncio
async def test_only_top_n_expanded():
    from src.tools.internal.neighbor_expander import expand_neighbors, NEIGHBOR_EXPAND_TOP_N
    store = AsyncMock()
    store.get_neighbor_chunks.return_value = []
    candidates = [_chunk(f"c{i}", f"d{i}", i, 0.9 - i * 0.01) for i in range(10)]
    await expand_neighbors(store, candidates)
    assert store.get_neighbor_chunks.call_count == NEIGHBOR_EXPAND_TOP_N
