"""graph_enrich 노드 단위 테스트."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.graph_enrich import create_graph_enrich, GRAPH_CONTEXT_HEADER
from src.domain.models import SearchScope
from src.router.execution_plan import ExecutionPlan, QuestionType


def _make_plan(security_level_max="PUBLIC"):
    return ExecutionPlan(
        mode="deterministic",
        scope=SearchScope(security_level_max=security_level_max),
    )


def _make_state(search_results=None, question="자동차보험 보장 내용", security_level_max="PUBLIC"):
    return {
        "question": question,
        "search_results": search_results or [],
        "plan": _make_plan(security_level_max),
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
    security_level="",
):
    return {
        "id": doc_id,
        "fileName": file_name,
        "securityLevel": security_level,
        "relationLabel": relation_label,
        "relationType": "REFERENCE",
        "properties": {"reason": reason, "strength": strength},
    }


def _aip_map_entry(aip_id, security_level="PUBLIC"):
    """get_aip_ids_by_externals 반환 형식 헬퍼."""
    return {"aip_id": aip_id, "security_level": security_level}


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
    """reason 3자 이상이지만 ai-platform에 없는 문서 -> 스킵."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-r1",
                file_name="전혀무관한이름.pdf",
                reason="상위 약관 참조",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={})

    node = create_graph_enrich(client, store)
    state = _make_state([_make_search_result()], question="xyz")
    result = await node(state)

    assert result == {}


@pytest.mark.asyncio
async def test_ontology_priority_reason_with_aip_mapping():
    """reason 유효 + ai-platform에 존재하는 문서 -> 발견."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-r1",
                file_name="전혀무관한이름.pdf",
                reason="상위 약관 참조",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-r1": _aip_map_entry("aip-r1"),
    })
    store.get_top_chunks_by_doc = AsyncMock(return_value=[
        {
            "chunk_id": "chunk-r1",
            "document_id": "aip-r1",
            "content": "관련 내용",
            "chunk_index": 0,
            "score": 0.5,
            "file_name": "전혀무관한이름.pdf",
        },
    ])

    node = create_graph_enrich(client, store)
    state = _make_state([_make_search_result()], question="xyz")
    result = await node(state)

    assert result != {}
    assert result["graph_enrichment"]["discovered"] >= 1


@pytest.mark.asyncio
async def test_ontology_priority_strength():
    """strength >= 4 + ai-platform에 존재 -> 발견."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-r1",
                file_name="전혀무관한이름.pdf",
                strength="5",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-r1": _aip_map_entry("aip-r1"),
    })
    store.get_top_chunks_by_doc = AsyncMock(return_value=[
        {
            "chunk_id": "chunk-r1",
            "document_id": "aip-r1",
            "content": "관련 내용",
            "chunk_index": 0,
            "score": 0.5,
            "file_name": "전혀무관한이름.pdf",
        },
    ])

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
                reason="",
                strength="",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={})

    node = create_graph_enrich(client, store)
    state = _make_state([_make_search_result()], question="자동차보험 보장")
    result = await node(state)

    assert result == {}


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
                strength="5.0",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-r1": _aip_map_entry("aip-r1"),
    })
    store.get_top_chunks_by_doc = AsyncMock(return_value=[
        {
            "chunk_id": "chunk-r1",
            "document_id": "aip-r1",
            "content": "내용",
            "chunk_index": 0,
            "score": 0.5,
            "file_name": "전혀무관.pdf",
        },
    ])

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
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-2": _aip_map_entry("aip-2"),
    })

    existing_result = _make_search_result(doc_id="aip-2", file_name="보장내용.pdf")
    state = _make_state(
        [_make_search_result(), existing_result],
        question="자동차보험 보장",
    )

    node = create_graph_enrich(client, store)
    result = await node(state)

    assert result != {}
    assert result["graph_enrichment"]["enriched"] == 1
    graph_results = [
        r for r in result["search_results"]
        if r.get("source") == "graph"
    ]
    assert len(graph_results) >= 1
    assert "[관계:" in graph_results[0]["content"]
    assert graph_results[0]["title"] == "보장내용.pdf"
    assert graph_results[0]["method"] == "graph"


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
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-new": _aip_map_entry("aip-new"),
    })
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
    assert graph_results[0]["title"] == "신규문서.pdf"
    assert graph_results[0]["method"] == "graph"


@pytest.mark.asyncio
async def test_kms_api_failure_degradation():
    """API 예외 -> 기존 결과 유지, 빈 dict 반환."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(side_effect=Exception("connection refused"))

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={})

    state = _make_state([_make_search_result()])
    node = create_graph_enrich(client, store)
    result = await node(state)

    assert result == {}


@pytest.mark.asyncio
async def test_max_5_seeds():
    """6개 문서 -> 5개만 처리."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={"relatedDocuments": []})

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={
        f"aip-{i}": f"kms-{i}" for i in range(6)
    })
    store.get_aip_ids_by_externals = AsyncMock(return_value={})

    results = [_make_search_result(doc_id=f"aip-{i}") for i in range(6)]
    state = _make_state(results)
    node = create_graph_enrich(client, store)
    await node(state)

    assert client.get_rag_context.call_count == 5


# --- P0 보안: ai-platform DB security_level 기반 필터링 ---


@pytest.mark.asyncio
async def test_security_filter_blocks_confidential_for_viewer():
    """VIEWER(PUBLIC) -> CONFIDENTIAL 문서 그래프 결과에서 제외 (DB security_level 기반)."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-secret",
                file_name="기밀문서.pdf",
                reason="관련 약관",
                # securityLevel 없음 — DB에서 CONFIDENTIAL로 확인
            ),
            _make_related_doc(
                doc_id="kms-public",
                file_name="공개문서.pdf",
                reason="공개 참조",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-secret": _aip_map_entry("aip-secret", "CONFIDENTIAL"),
        "kms-public": _aip_map_entry("aip-public", "PUBLIC"),
    })
    store.get_top_chunks_by_doc = AsyncMock(return_value=[
        {
            "chunk_id": "chunk-pub",
            "document_id": "aip-public",
            "content": "공개 내용",
            "chunk_index": 0,
            "score": 0.5,
            "file_name": "공개문서.pdf",
        },
    ])

    state = _make_state(
        [_make_search_result()],
        question="약관 내용",
        security_level_max="PUBLIC",
    )
    node = create_graph_enrich(client, store)
    result = await node(state)

    assert result != {}
    all_files = [r.get("file_name") for r in result["search_results"]]
    assert "기밀문서.pdf" not in all_files
    assert "공개문서.pdf" in all_files


@pytest.mark.asyncio
async def test_security_filter_kms_response_takes_priority():
    """KMS 응답의 securityLevel이 있으면 DB보다 우선 적용."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-doc",
                file_name="문서.pdf",
                reason="참조",
                security_level="SECRET",  # KMS 응답에 명시
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    # DB에는 PUBLIC이지만 KMS 응답이 SECRET
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-doc": _aip_map_entry("aip-doc", "PUBLIC"),
    })

    state = _make_state(
        [_make_search_result()],
        question="문서",
        security_level_max="INTERNAL",
    )
    node = create_graph_enrich(client, store)
    result = await node(state)

    # SECRET > INTERNAL 이므로 필터링되어야 함
    assert result == {}


@pytest.mark.asyncio
async def test_security_filter_allows_for_approver():
    """APPROVER(SECRET) -> CONFIDENTIAL 문서 접근 가능."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-conf",
                file_name="대외비문서.pdf",
                reason="관련 약관",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-conf": _aip_map_entry("aip-conf", "CONFIDENTIAL"),
    })
    store.get_top_chunks_by_doc = AsyncMock(return_value=[
        {
            "chunk_id": "chunk-conf",
            "document_id": "aip-conf",
            "content": "대외비 내용",
            "chunk_index": 0,
            "score": 0.5,
            "file_name": "대외비문서.pdf",
        },
    ])

    state = _make_state(
        [_make_search_result()],
        question="약관 내용",
        security_level_max="SECRET",
    )
    node = create_graph_enrich(client, store)
    result = await node(state)

    assert result != {}
    all_files = [r.get("file_name") for r in result["search_results"]]
    assert "대외비문서.pdf" in all_files


# --- P0 데이터 정합성: KMS UUID 제거 ---


@pytest.mark.asyncio
async def test_unmapped_kms_doc_not_added():
    """ai-platform에 없는 문서 -> graph_results에 추가하지 않음."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-unmapped",
                file_name="미동기화.pdf",
                reason="중요한 참조",
                strength="8",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={})

    state = _make_state([_make_search_result()], question="약관")
    node = create_graph_enrich(client, store)
    result = await node(state)

    assert result == {}


# --- P1 필드 누락 테스트 ---


@pytest.mark.asyncio
async def test_graph_results_have_title_and_method():
    """graph_results에 title, method 필드가 존재."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-new",
                file_name="테스트문서.pdf",
                reason="참조",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-new": _aip_map_entry("aip-new"),
    })
    store.get_top_chunks_by_doc = AsyncMock(return_value=[
        {
            "chunk_id": "chunk-1",
            "document_id": "aip-new",
            "content": "내용",
            "chunk_index": 0,
            "score": 0.5,
            "file_name": "테스트문서.pdf",
        },
    ])

    state = _make_state([_make_search_result()], question="테스트")
    node = create_graph_enrich(client, store)
    result = await node(state)

    graph_items = [r for r in result["search_results"] if r.get("source") == "graph"]
    for item in graph_items:
        assert "title" in item
        assert item["title"] == "테스트문서.pdf"
        assert "method" in item
        assert item["method"] == "graph"


# --- P2 타임아웃 테스트 ---


@pytest.mark.asyncio
async def test_timeout_returns_empty():
    """graph_enrich 전체 타임아웃 -> 빈 dict 반환."""
    client = MagicMock()
    client.is_configured = True

    async def slow_rag_context(*args, **kwargs):
        await asyncio.sleep(30)
        return {"relatedDocuments": []}

    client.get_rag_context = slow_rag_context

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})

    state = _make_state([_make_search_result()])
    node = create_graph_enrich(client, store)

    result = await asyncio.wait_for(node(state), timeout=20)
    assert result == {}


# --- P2 strength 표시 테스트 ---


@pytest.mark.asyncio
async def test_strength_display_format():
    """strength 없을 때 '미지정', 있을 때 'N/10' 형식."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-2",
                file_name="보장내용.pdf",
                reason="동일 약관 참조",
                strength="",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-2": _aip_map_entry("aip-2"),
    })

    existing_result = _make_search_result(doc_id="aip-2", file_name="보장내용.pdf")
    state = _make_state([_make_search_result(), existing_result], question="보장")

    node = create_graph_enrich(client, store)
    result = await node(state)

    graph_items = [r for r in result["search_results"] if r.get("source") == "graph"]
    assert len(graph_items) >= 1
    assert "강도: 미지정" in graph_items[0]["content"]
    assert "?/10" not in graph_items[0]["content"]


@pytest.mark.asyncio
async def test_strength_display_with_value():
    """strength가 있으면 'N/10' 형식."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-2",
                file_name="보장내용.pdf",
                reason="동일 약관 참조",
                strength="7",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-2": _aip_map_entry("aip-2"),
    })

    existing_result = _make_search_result(doc_id="aip-2", file_name="보장내용.pdf")
    state = _make_state([_make_search_result(), existing_result], question="보장")

    node = create_graph_enrich(client, store)
    result = await node(state)

    graph_items = [r for r in result["search_results"] if r.get("source") == "graph"]
    assert len(graph_items) >= 1
    assert "강도: 7/10" in graph_items[0]["content"]


@pytest.mark.asyncio
async def test_strength_display_zero():
    """strength=0은 유효한 값이므로 '0/10'으로 표시되어야 한다."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-2",
                file_name="보장내용.pdf",
                reason="동일 약관 참조",
                strength="0",
            ),
        ],
    })

    vs = MagicMock()
    vs.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    vs.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-2": {"aip_id": "aip-2", "security_level": "PUBLIC"},
    })
    vs.get_top_chunks_by_doc = AsyncMock(return_value=[
        {"chunk_id": "c1", "content": "보장 내용", "score": 0.5},
    ])

    node = create_graph_enrich(client, vs)
    state = _make_state(search_results=[_make_search_result(doc_id="aip-1")])
    result = await node(state)

    graph_items = [r for r in result["search_results"] if r.get("source") == "graph"]
    assert len(graph_items) >= 1
    assert "강도: 0/10" in graph_items[0]["content"]


# --- P1 병렬화: KMS API 병렬 호출 검증 ---


@pytest.mark.asyncio
async def test_parallel_kms_calls():
    """여러 시드에 대해 KMS API가 병렬로 호출되는지 검증."""
    call_times = []

    async def tracked_rag_context(kms_id, **kwargs):
        call_times.append(asyncio.get_event_loop().time())
        await asyncio.sleep(0.1)
        return {"relatedDocuments": []}

    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = tracked_rag_context

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={
        "aip-0": "kms-0", "aip-1": "kms-1", "aip-2": "kms-2",
    })
    store.get_aip_ids_by_externals = AsyncMock(return_value={})

    results = [_make_search_result(doc_id=f"aip-{i}") for i in range(3)]
    state = _make_state(results)
    node = create_graph_enrich(client, store)
    await node(state)

    assert len(call_times) == 3
    assert call_times[-1] - call_times[0] < 0.05


# --- P1 배치 역매핑 검증 ---


@pytest.mark.asyncio
async def test_batch_reverse_mapping():
    """get_aip_ids_by_externals가 배치로 호출되는지 검증."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(doc_id="kms-r1", file_name="a.pdf", reason="참조1"),
            _make_related_doc(doc_id="kms-r2", file_name="b.pdf", reason="참조2"),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-r1": _aip_map_entry("aip-r1"),
        "kms-r2": _aip_map_entry("aip-r2"),
    })
    store.get_top_chunks_by_doc = AsyncMock(return_value=[
        {
            "chunk_id": "c1",
            "document_id": "aip-r1",
            "content": "내용",
            "chunk_index": 0,
            "score": 0.5,
            "file_name": "a.pdf",
        },
    ])

    state = _make_state([_make_search_result()], question="약관")
    node = create_graph_enrich(client, store)
    await node(state)

    store.get_aip_ids_by_externals.assert_called_once()
    call_args = store.get_aip_ids_by_externals.call_args[0][0]
    assert set(call_args) == {"kms-r1", "kms-r2"}


@pytest.mark.asyncio
async def test_security_filter_passes_to_get_top_chunks():
    """get_top_chunks_by_doc에 max_security_level이 전달되는지 검증."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-new",
                file_name="문서.pdf",
                reason="참조",
                security_level="PUBLIC",
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-new": _aip_map_entry("aip-new", "PUBLIC"),
    })
    store.get_top_chunks_by_doc = AsyncMock(return_value=[
        {
            "chunk_id": "c1",
            "document_id": "aip-new",
            "content": "내용",
            "chunk_index": 0,
            "score": 0.5,
            "file_name": "문서.pdf",
        },
    ])

    state = _make_state(
        [_make_search_result()],
        question="문서",
        security_level_max="INTERNAL",
    )
    node = create_graph_enrich(client, store)
    await node(state)

    store.get_top_chunks_by_doc.assert_called_once()
    call_kwargs = store.get_top_chunks_by_doc.call_args
    assert call_kwargs.kwargs.get("max_security_level") == "INTERNAL" or \
        call_kwargs[1].get("max_security_level") == "INTERNAL"


# --- P0 보안: INTERNAL 문서가 DB security_level로 필터링 ---


@pytest.mark.asyncio
async def test_security_filter_db_internal_blocked_for_viewer():
    """VIEWER -> DB에 INTERNAL인 문서가 graph_enrich에서 제외."""
    client = MagicMock()
    client.is_configured = True
    client.get_rag_context = AsyncMock(return_value={
        "relatedDocuments": [
            _make_related_doc(
                doc_id="kms-internal",
                file_name="사내용문서.pdf",
                reason="관련 참조",
                # KMS 응답에 securityLevel 없음
            ),
        ],
    })

    store = AsyncMock()
    store.get_external_ids = AsyncMock(return_value={"aip-1": "kms-1"})
    # DB에는 INTERNAL
    store.get_aip_ids_by_externals = AsyncMock(return_value={
        "kms-internal": _aip_map_entry("aip-internal", "INTERNAL"),
    })

    state = _make_state(
        [_make_search_result()],
        question="사내용",
        security_level_max="PUBLIC",  # VIEWER
    )
    node = create_graph_enrich(client, store)
    result = await node(state)

    # INTERNAL > PUBLIC 이므로 필터링
    assert result == {}
