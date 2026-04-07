"""graph_enrich: KMS 지식그래프 보강 LangGraph 노드.

ai-worker graph_traverse.py의 온톨로지 우선 필터링 + 이중 모드 탐색을
ai-platform 아키텍처에 맞게 포팅.

두 가지 역할:
1. 보강: 이미 search_results에 있는 문서에 온톨로지 메타데이터 헤더 추가
2. 발견: 그래프 관계로 새 문서를 찾아 벡터 검색 후 추가
"""

import asyncio

from src.agent.state import AgentState
from src.domain.models import SECURITY_HIERARCHY
from src.observability.logging import get_logger

logger = get_logger(__name__)

GRAPH_CONTEXT_HEADER = (
    "[관계: {relation_label} | 사유: {reason} | 강도: {strength_display} | 문서: {file_name}]"
)

MAX_SEEDS = 5
GRAPH_ENRICH_TIMEOUT_SECONDS = 15


def _is_relevant(file_name: str, keywords: list[str]) -> bool:
    """파일명이 키워드와 관련 있는지 판단한다."""
    if not keywords:
        return True
    name_lower = file_name.lower()
    return any(kw.lower() in name_lower for kw in keywords)


def _format_strength(strength) -> str:
    """strength 값을 표시 문자열로 변환한다."""
    if strength is None or str(strength).strip() == "":
        return "미지정"
    return f"{strength}/10"


def _check_security_level(doc_security: str, max_level: str) -> bool:
    """문서 보안등급이 max_level 이하인지 확인한다."""
    doc_rank = SECURITY_HIERARCHY.get(doc_security)
    max_rank = SECURITY_HIERARCHY.get(max_level)
    if doc_rank is None or max_rank is None:
        return False
    return doc_rank <= max_rank


def create_graph_enrich(kms_client, vector_store):
    """graph_enrich 노드 팩토리.

    Args:
        kms_client: KmsGraphClient 인스턴스
        vector_store: VectorStore 인스턴스 (ID 매핑 + 벡터 검색)
    """

    async def _fetch_rag_context(kms_id: str) -> tuple[str, dict | None]:
        """KMS API 단건 호출. (kms_id, response) 튜플 반환."""
        try:
            ctx = await kms_client.get_rag_context(
                kms_id, depth=1, max_documents=999,
            )
            return kms_id, ctx
        except Exception as e:
            logger.warning("graph_enrich_seed_error", kms_id=kms_id, error=str(e))
            return kms_id, None

    async def _graph_enrich_inner(state: AgentState) -> dict:
        """타임아웃 없는 내부 구현."""
        if not kms_client.is_configured:
            return {}

        search_results = state.get("search_results", [])
        if not search_results:
            return {}

        # 보안등급 상한 확인
        plan = state.get("plan")
        max_security_level = (
            plan.scope.security_level_max if plan else None
        )

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

        # 2. ai-platform UUID -> KMS external_id 매핑
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

        # 3. KMS 그래프 탐색 — 병렬 호출
        kms_ids_to_fetch = [
            (aip_id, id_map[aip_id])
            for aip_id in seed_aip_ids
            if aip_id in id_map
        ]

        rag_tasks = [_fetch_rag_context(kms_id) for _, kms_id in kms_ids_to_fetch]
        rag_responses = await asyncio.gather(*rag_tasks)

        # kms_id -> aip_id 역매핑
        kms_to_aip = {kms_id: aip_id for aip_id, kms_id in kms_ids_to_fetch}

        # 4. 관련 문서 KMS ID 수집 (배치 역매핑 준비)
        all_related_kms_ids: list[str] = []
        rag_results_by_kms: dict[str, dict] = {}

        for kms_id, ctx in rag_responses:
            if not ctx:
                continue
            rag_results_by_kms[kms_id] = ctx
            for related in ctx.get("relatedDocuments", []):
                related_kms_id = related.get("id", "")
                if related_kms_id and related_kms_id != kms_id:
                    all_related_kms_ids.append(related_kms_id)

        # 배치 역매핑: KMS ID -> {aip_id, security_level}
        unique_related_kms_ids = list(set(all_related_kms_ids))
        kms_to_aip_raw = await vector_store.get_aip_ids_by_externals(
            unique_related_kms_ids,
        ) if unique_related_kms_ids else {}

        # 5. 결과 처리
        for kms_id, ctx in rag_responses:
            if not ctx:
                continue

            aip_id = kms_to_aip.get(kms_id, "")

            for related in ctx.get("relatedDocuments", []):
                related_kms_id = related.get("id", "")
                if not related_kms_id or related_kms_id == kms_id:
                    continue

                if related_kms_id in processed_in_graph:
                    continue
                processed_in_graph.add(related_kms_id)

                related_file_name = related.get("fileName", "")

                # P0 보안: ai-platform DB의 security_level로 필터링
                # KMS 응답에 securityLevel이 없을 수 있으므로 DB 기준으로 검증
                aip_info = kms_to_aip_raw.get(related_kms_id, {})
                related_aip_id = aip_info.get("aip_id") if aip_info else None
                related_security = (
                    related.get("securityLevel")  # KMS 응답 우선
                    or (aip_info.get("security_level") if aip_info else "")
                )
                if max_security_level and related_security:
                    if not _check_security_level(related_security, max_security_level):
                        logger.debug(
                            "graph_enrich_security_filtered",
                            related_kms_id=related_kms_id,
                            security_level=related_security,
                            max_level=max_security_level,
                        )
                        continue

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

                # P2 strength 표시 개선
                ontology_meta = GRAPH_CONTEXT_HEADER.format(
                    relation_label=relation_label or "관련",
                    reason=reason or "미지정",
                    strength_display=_format_strength(strength),
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
                        "title": related_file_name,
                        "method": "graph",
                    })
                    enriched_count += 1
                else:
                    # 발견 모드: 벡터 검색으로 청크 가져오기
                    if not related_aip_id:
                        # P0: ai-platform에 없는 문서 -> 추가하지 않음 (KMS UUID 혼용 방지)
                        logger.debug(
                            "graph_enrich_skip_unmapped",
                            related_kms_id=related_kms_id,
                            file_name=related_file_name,
                        )
                        continue

                    chunks = await vector_store.get_top_chunks_by_doc(
                        related_aip_id, limit=2,
                        max_security_level=max_security_level,
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
                                "title": related_file_name,
                                "method": "graph",
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
                            "title": related_file_name,
                            "method": "graph",
                        })
                        discovered_count += 1

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

    async def graph_enrich(state: AgentState) -> dict:
        """graph_enrich 노드. 전체 15초 타임아웃 가드 적용."""
        try:
            return await asyncio.wait_for(
                _graph_enrich_inner(state),
                timeout=GRAPH_ENRICH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "graph_enrich_timeout",
                timeout_seconds=GRAPH_ENRICH_TIMEOUT_SECONDS,
            )
            return {}

    return graph_enrich
