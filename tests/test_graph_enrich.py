"""graph_enrich 노드 단위 테스트."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.graph_enrich import create_graph_enrich, GRAPH_CONTEXT_HEADER


def _make_state(search_results=None, question="자동차보험 보장 내용"):
    return {
        "question": question,
        "search_results": search_results or [],
    }


def _make_search_result(doc_id="aip-1", file_name="약관.pdf", score=0.9):
    return {
        "chunk_id": f"chunk-{doc_id}",
        "document_id": doc_id,
        "content": "보장 내용 본문",
        "score": score,
        "file_name": file_name,
    }


def _make_related_doc(
    doc_id="kms-related-1",
    file_name="관련문서.pdf",
    relation_label="REFERENCE",
    reason="",
    strength="",
):
    return {
        "id": doc_id,
        "fileName": file_name,
        "relationLabel": relation_label,
        "relationType": "REFERENCE",
        "properties": {"reason": reason, "strength": strength},
    }


@pytest.mark.asyncio
async def test_bypass_when_not_configured():
    """is_configured=False -> 빈 dict 반환."""
    client = MagicMock()
    client.is_configured = False
    store = MagicMock()

    node = create_graph_enrich(client, store)
    result = await node(_make_state([_make_search_result()]))
    assert result == {}


@pytest.mark.asyncio
async def test_bypass_when_no_search_results():
    """search_results 빈 리스트 -> 빈 dict 반환."""
    client = MagicMock()
    client.is_configured = True
    store = MagicMock()

    node = create_graph_enrich(client, store)
    result = await node(_make_state([]))
    assert result == {}


@pytest.mark.asyncio
async def test_bypass_when_no_external_ids():
    """매핑 결과 없음 -> 빈 dict 반환."""
    client = MagicMock()
    client.is_configured = True
    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={})

    node = create_graph_enrich(client, store)
    result = await node(_make_state([_make_search_result()]))
    assert result == {}


@pytest.mark.asyncio
async def test_ontology_priority_reason():
    """reason 3자 이상 -> 키워드 무관하게 포함."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-r1",
                file_name="전혀무관한이름.pdf",
                reason="상위 약관 참조",  # 유효한 reason
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_id_by_external = AsyncMock(return_value=None)

    node = create_graph_enrich(client, store)
    state = _make_state([_make_search_result()], question="xyz")
    result = await node(state)

    assert result != {}
    assert result["graph_enrichment"]["discovered"] >= 1


@pytest.mark.asyncio
async def test_ontology_priority_strength():
    """strength >= 4 -> 키워드 무관하게 포함."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-r1",
                file_name="전혀무관한이름.pdf",
                strength="5",  # 유효한 strength
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_id_by_external = AsyncMock(return_value=None)

    node = create_graph_enrich(client, store)
    state = _make_state([_make_search_result()], question="xyz")
    result = await node(state)

    assert result != {}
    assert result["graph_enrichment"]["discovered"] >= 1


@pytest.mark.asyncio
async def test_keyword_filter_no_ontology():
    """온톨로지 없고 파일명 매칭 실패 -> 제외."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-r1",
                file_name="전혀무관한이름.pdf",
                reason="",  # 온톨로지 없음
                strength="",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_id_by_external = AsyncMock(return_value=None)

    node = create_graph_enrich(client, store)
    # 질문 키워드가 파일명과 전혀 매칭되지 않음
    state = _make_state([_make_search_result()], question="자동차보험 보장")
    result = await node(state)

    assert result == {}
    # 필터링 단계에서 걸러지므로 역매핑 호출 없어야 함
    store.get_aip_id_by_external.assert_not_called()


@pytest.mark.asyncio
async def test_ontology_strength_float_string():
    """strength가 '5.0' 같은 float 문자열이어도 유효하게 처리."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-r1",
                file_name="전혀무관.pdf",
                strength="5.0",  # float 문자열
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_id_by_external = AsyncMock(return_value=None)

    node = create_graph_enrich(client, store)
    state = _make_state([_make_search_result()], question="xyz")
    result = await node(state)

    assert result != {}
    assert result["graph_enrichment"]["discovered"] >= 1


@pytest.mark.asyncio
async def test_enrichment_mode():
    """이미 search_results에 있는 문서 -> 메타 헤더만 추가."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-2",
                file_name="보장내용.pdf",
                reason="동일 약관 참조",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    # 관련 문서가 이미 search_results에 있는 aip-2로 매핑
    store.get_aip_id_by_external = AsyncMock(return_value="aip-2")

    existing_result = _make_search_result(doc_id="aip-2", file_name="보장내용.pdf")
    state = _make_state(
        [_make_search_result(), existing_result],
        question="자동차보험 보장",
    )

    node = create_graph_enrich(client, store)
    result = await node(state)

    assert result != {}
    assert result["graph_enrichment"]["enriched"] == 1
    # 그래프 결과에 온톨로지 헤더가 포함
    graph_results = [
        r for r in result["search_results"]
        if r.get("source") == "graph"
    ]
    assert len(graph_results) >= 1
    assert "[관계:" in graph_results[0]["content"]


@pytest.mark.asyncio
async def test_discovery_mode():
    """새 문서 -> 벡터 검색 결과 포함."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-new",
                file_name="신규문서.pdf",
                reason="관련 보장 내용",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_id_by_external = AsyncMock(return_value="aip-new")
    store.get_top_chunks_by_doc = AsyncMock(return_value=[
        {
            "chunk_id": "chunk-new",
            "document_id": "aip-new",
            "content": "신규 문서 내용",
            "chunk_index": 0,
            "score": 0.5,
            "file_name": "신규문서.pdf",
        },
    ])

    state = _make_state([_make_search_result()], question="자동차보험 보장")
    node = create_graph_enrich(client, store)
    result = await node(state)

    assert result != {}
    assert result["graph_enrichment"]["discovered"] == 1
    graph_results = [
        r for r in result["search_results"]
        if r.get("source") == "graph"
    ]
    assert len(graph_results) >= 1
    assert "신규 문서 내용" in graph_results[0]["content"]


@pytest.mark.asyncio
async def test_kms_api_failure_degradation():
    """API 예외 -> 기존 결과 유지, 빈 dict 반환."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(side_effect=Exception("connection refused"))

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})

    state = _make_state([_make_search_result()])
    node = create_graph_enrich(client, store)
    result = await node(state)

    # 실패 시 빈 dict 반환 (기존 search_results 변경 없음)
    assert result == {}


@pytest.mark.asyncio
async def test_max_5_seeds():
    """6개 문서 -> 5개만 처리."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={"relatedDocuments": []})

    store = AsyncMock()
    # 6개 문서 모두 매핑
    store.get_external_ids = AsyncMock(return_value={
        f"aip-{i}": f"kms-{i}" for i in range(6)
    })

    results = [_make_search_result(doc_id=f"aip-{i}") for i in range(6)]
    state = _make_state(results)
    node = create_graph_enrich(client, store)
    await node(state)

    # get_rag_context는 최대 5회 호출
    assert client.get_rag_context.call_count == 5
