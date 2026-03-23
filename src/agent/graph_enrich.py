"""graph_enrich: KMS 지식그래프 보강 LangGraph 노드.

ai-worker graph_traverse.py의 온톨로지 우선 필터링 + 이중 모드 탐색을
ai-platform 아키텍처에 맞게 포팅.

두 가지 역할:
1. 보강: 이미 search_results에 있는 문서에 온톨로지 메타데이터 헤더 추가
2. 발견: 그래프 관계로 새 문서를 찾아 벡터 검색 후 추가
"""

from src.agent.state import AgentState
from src.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_CONTEXT_HEADER = (
    "[관계: {relation_label} | 사유: {reason} | 강도: {strength}/10 | 문서: {file_name}]"
)

MAX_SEEDS = 5


def _is_relevant(file_name: str, keywords: list[str]) -> bool:
    """파일명이 키워드와 관련 있는지 판단한다."""
    if not keywords:
        return True
    name_lower = file_name.lower()
    return any(kw.lower() in name_lower for kw in keywords)


def create_graph_enrich(kms_client, vector_store):
    """graph_enrich 노드 팩토리.

    Args:
        kms_client: KmsGraphClient 인스턴스
        vector_store: VectorStore 인스턴스 (ID 매핑 + 벡터 검색)
    """

    async def graph_enrich(state: AgentState) -> dict:
        if not kms_client.is_configured:
            return {}

        search_results = state.get("search_results", [])
        if not search_results:
            return {}

        # 1. 시드 추출 (고유 document_id, 최대 MAX_SEEDS개)
        seen_doc_ids: set[str] = set()
        seed_aip_ids: list[str] = []
        for r in search_results:
            doc_id = r.get("document_id", "")
            if doc_id and doc_id not in seen_doc_ids:
                seen_doc_ids.add(doc_id)
                seed_aip_ids.append(doc_id)

        if not seed_aip_ids:
            return {}

        seed_aip_ids = seed_aip_ids[:MAX_SEEDS]

        # 2. ai-platform UUID → KMS external_id 매핑
        id_map = await vector_store.get_external_ids(seed_aip_ids)
        if not id_map:
            return {}

        # 질문에서 키워드 추출 (간단한 공백 분리)
        question = state.get("question", "")
        keywords = [w for w in question.split() if len(w) >= 2]

        graph_results: list[dict] = []
        traversal_edges: list[dict] = []
        enriched_count = 0
        discovered_count = 0
        processed_in_graph: set[str] = set()

        # 3. 각 시드에 대해 KMS 그래프 탐색
        for aip_id in seed_aip_ids:
            kms_id = id_map.get(aip_id)
            if not kms_id:
                continue

            try:
                ctx = await kms_client.get_rag_context(
                    kms_id, depth=1, max_documents=999,
                )
                if not ctx:
                    continue

                for related in ctx.get("relatedDocuments", []):
                    related_kms_id = related.get("id", "")
                    if not related_kms_id or related_kms_id == kms_id:
                        continue

                    if related_kms_id in processed_in_graph:
                        continue
                    processed_in_graph.add(related_kms_id)

                    related_file_name = related.get("fileName", "")

                    # 온톨로지 메타데이터 추출
                    properties = related.get("properties", {})
                    relation_label = related.get(
                        "relationLabel", related.get("relationType", ""),
                    )
                    reason = properties.get("reason", "")
                    strength = properties.get("strength", "")

                    # 온톨로지 유효성 검증
                    has_valid_reason = bool(
                        reason and reason.strip() and len(reason.strip()) >= 3,
                    )
                    has_valid_strength = False
                    if strength and str(strength).strip():
                        try:
                            has_valid_strength = float(strength) >= 4
                        except (ValueError, TypeError):
                            pass

                    has_ontology = has_valid_reason or has_valid_strength

                    # 온톨로지 우선 필터링
                    if not has_ontology and not _is_relevant(related_file_name, keywords):
                        continue

                    ontology_meta = GRAPH_CONTEXT_HEADER.format(
                        relation_label=relation_label or "관련",
                        reason=reason or "미지정",
                        strength=strength or "?",
                        file_name=related_file_name,
                    )

                    # 시각화용 엣지
                    seed_name = next(
                        (r.get("file_name", "") for r in search_results
                         if r.get("document_id") == aip_id),
                        aip_id[:8],
                    )
                    traversal_edges.append({
                        "source_id": kms_id,
                        "target_id": related_kms_id,
                        "source_name": seed_name,
                        "target_name": related_file_name,
                        "relation": relation_label or "",
                        "reason": reason,
                        "strength": strength,
                    })

                    # 관련 문서가 이미 search_results에 있는지 확인
                    related_aip_id = await vector_store.get_aip_id_by_external(
                        related_kms_id,
                    )
                    already_in_results = (
                        related_aip_id is not None
                        and related_aip_id in seen_doc_ids
                    )

                    if already_in_results:
                        # 보강 모드: 온톨로지 메타 헤더만 추가
                        best_score = max(
                            (r["score"] for r in search_results
                             if r.get("document_id") == related_aip_id),
                            default=0,
                        )
                        graph_results.append({
                            "chunk_id": "",
                            "document_id": related_aip_id,
                            "content": ontology_meta,
                            "score": best_score,
                            "source": "graph",
                            "file_name": related_file_name,
                        })
                        enriched_count += 1
                    else:
                        # 발견 모드: 벡터 검색으로 청크 가져오기
                        if not related_aip_id:
                            # ai-platform에 없는 문서 → 스킵 (on-demand ingest 미지원)
                            if reason or strength:
                                graph_results.append({
                                    "chunk_id": "",
                                    "document_id": related_kms_id,
                                    "content": ontology_meta,
                                    "score": 0.3,
                                    "source": "graph",
                                    "file_name": related_file_name,
                                })
                                discovered_count += 1
                            continue

                        chunks = await vector_store.get_top_chunks_by_doc(
                            related_aip_id, limit=2,
                        )

                        if chunks:
                            for chunk in chunks:
                                graph_results.append({
                                    "chunk_id": chunk["chunk_id"],
                                    "document_id": related_aip_id,
                                    "content": f"{ontology_meta}\n{chunk['content']}",
                                    "score": chunk["score"],
                                    "source": "graph",
                                    "file_name": related_file_name,
                                })
                            discovered_count += 1
                        elif reason or strength:
                            graph_results.append({
                                "chunk_id": "",
                                "document_id": related_aip_id,
                                "content": ontology_meta,
                                "score": 0.3,
                                "source": "graph",
                                "file_name": related_file_name,
                            })
                            discovered_count += 1

            except Exception as e:
                logger.warning(
                    "graph_enrich_seed_error",
                    aip_id=aip_id,
                    kms_id=kms_id,
                    error=str(e),
                )
                continue

        if not graph_results:
            return {}

        logger.info(
            "graph_enrich_complete",
            enriched=enriched_count,
            discovered=discovered_count,
            edges=len(traversal_edges),
        )

        return {
            "search_results": list(search_results) + graph_results,
            "graph_enrichment": {
                "enriched": enriched_count,
                "discovered": discovered_count,
                "edges": traversal_edges,
            },
        }

    return graph_enrich
