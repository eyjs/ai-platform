"""RAG Search Tool: 5-layer 파이프라인 오케스트레이터.

Adaptive L1: probe 검색 → 고품질이면 확장 스킵
L1 쿼리확장 -> 멀티쿼리 검색 -> L2 노이즈필터 -> L3 이웃확장 -> L4 리랭킹 -> L5 가드
"""

import asyncio
import dataclasses
import time
from typing import Optional

from src.infrastructure.providers.base import EmbeddingProvider, LLMProvider, RerankerProvider
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
from src.domain.models import SearchScope
from src.domain.agent_context import AgentContext
from src.tools.base import ToolResult
from src.tools.internal.query_expander import expand_queries
from src.tools.internal.noise_filter import filter_noise
from src.tools.internal.neighbor_expander import expand_neighbors
from src.tools.internal.entity_filter import EntityDocIndex, EntityMatch
from src.tools.internal.reranker_pipeline import rerank_3tier
from src.tools.internal.result_guard import guard_results

logger = get_logger(__name__)

CANDIDATE_POOL_SIZE = 50
PROBE_SKIP_THRESHOLD = 0.012  # RRF 스코어 범위: ~0.007-0.016

# 트레이스 상세: 청크 스니펫 길이 (메타+스니펫 저장 정책)
TRACE_SNIPPET_CHARS = 200


def _chunk_snippet(r: dict) -> dict:
    """트레이스용 청크 요약 (메타 + 200자 스니펫). 전문은 chunk_id로 조회.

    출신(origin)·귀속(found_by)을 함께 실어 "이 청크가 어떻게 후보에
    들어왔나"를 역추적 가능하게 한다.
    """
    snippet = {
        "chunk_id": r.get("chunk_id"),
        "document_id": r.get("document_id"),
        "score": round(r.get("score", 0.0), 4),
        "snippet": (r.get("content") or "")[:TRACE_SNIPPET_CHARS],
    }
    if r.get("found_by"):
        snippet["found_by"] = r["found_by"]
    if r.get("origin"):
        snippet["origin"] = r["origin"]
    return snippet

# Progressive Disclosure 상수
DEFAULT_DISCLOSURE_LEVEL = 2
REFERENCE_LOAD_THRESHOLD = 0.015
MAX_REFERENCE_DOCS = 3


class RAGSearchTool:
    """RAG 검색 도구 (ScopedTool). 5-layer 파이프라인."""

    name = "rag_search"
    description = "문서 벡터 검색 + 키워드 검색 하이브리드"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색 쿼리"},
            "max_vector_chunks": {
                "type": "integer",
                "description": "반환할 최대 청크 수 (미지정 시 전략 기본값)",
            },
            "min_rerank_score": {
                "type": "number",
                "description": "리랭커 관련도 하한 (미지정 시 전역 기본값). 프로필별 오버라이드.",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        reranker: Optional[RerankerProvider] = None,
        router_llm: Optional[LLMProvider] = None,
        default_top_k: int = 5,
        min_rerank_score: float = 0.0,
    ):
        self._embedder = embedding_provider
        self._store = vector_store
        self._reranker = reranker
        self._router_llm = router_llm
        self._default_top_k = default_top_k
        # 리랭커 절대 관련도 하한 — 무관 청크(sigmoid≈0.5)를 컨텍스트에서 제외.
        self._min_rerank_score = min_rerank_score
        # 깔때기 1단계: 질문 기반 엔티티 메타필터 인덱스 (코퍼스 문서명에서 유도, TTL 갱신)
        self._entity_index = EntityDocIndex()

    async def _entity_scope(
        self, query: str, scope: SearchScope,
    ) -> tuple[SearchScope, Optional[EntityMatch]]:
        """질문에 코퍼스 문서 별칭이 등장하면 해당 문서들로 스코프를 좁힌다.

        - 이미 문서가 고정된 스코프(SAME_DOC 후속 등)는 건드리지 않는다.
        - 매칭 없으면 원본 스코프 그대로 (필터 없음 = 기존 동작).
        """
        if scope.allowed_doc_ids is not None:
            return scope, None
        try:
            if self._entity_index.is_stale:
                self._entity_index.build(await self._store.list_document_names())
            match = self._entity_index.match(query)
        except Exception as e:  # noqa: BLE001 - 필터는 최적화 — 실패해도 검색은 계속
            logger.warning("entity_filter_error", error=str(e))
            return scope, None
        if not match.doc_ids:
            return scope, None
        logger.info(
            "entity_filter_applied",
            aliases=match.aliases,
            docs=len(match.doc_ids),
        )
        return dataclasses.replace(
            scope, allowed_doc_ids=sorted(match.doc_ids),
        ), match

    async def execute(
        self,
        params: dict,
        context: AgentContext,
        scope: SearchScope,
    ) -> ToolResult:
        query = params.get("query", "")
        if not query:
            return ToolResult.fail("query is required")

        # 시스템 경계 검증: 플래너 LLM이 극단값을 넣어도 1~20으로 클램프
        try:
            top_k = int(params.get("max_vector_chunks", self._default_top_k))
        except (TypeError, ValueError):
            top_k = self._default_top_k
        top_k = max(1, min(top_k, 20))
        disclosure_level = params.get(
            "disclosure_level", DEFAULT_DISCLOSURE_LEVEL,
        )
        # 관련도 하한: 프로필별 오버라이드(params) 우선, 없으면 전역 기본값(생성자).
        try:
            min_rerank = float(params.get("min_rerank_score", self._min_rerank_score))
        except (TypeError, ValueError):
            min_rerank = self._min_rerank_score
        t_start = time.time()

        # Level 1: 메타데이터 전용 (본문 로드 없음)
        if disclosure_level == 1:
            return await self._execute_level1(query, scope, t_start)

        # 깔때기 1단계: 질문의 엔티티(상품명·문서유형)로 후보 문서 축소 (P2 메타필터)
        effective_scope, entity_match = await self._entity_scope(query, scope)

        # Level 2: 기존 5-layer 파이프라인 (기본값)
        results, query_embedding, trace_detail = await self._execute_full_pipeline(
            query, effective_scope, top_k, min_rerank,
        )

        if entity_match:
            trace_detail.setdefault("filter", {})["entity_filter"] = {
                "aliases": entity_match.aliases,
                "docs": len(entity_match.doc_ids),
                "fallback": False,
            }
            if not results:
                # 필터가 과하게 좁혔을 가능성 — 무필터로 1회 폴백 (recall 보증)
                logger.info("entity_filter_fallback", aliases=entity_match.aliases)
                results, query_embedding, trace_detail = await self._execute_full_pipeline(
                    query, scope, top_k, min_rerank,
                )
                trace_detail.setdefault("filter", {})["entity_filter"] = {
                    "aliases": entity_match.aliases,
                    "docs": len(entity_match.doc_ids),
                    "fallback": True,
                }
                effective_scope = scope

        # Level 3: Level 2 + references 조건부 로드 (임베딩 재사용)
        if disclosure_level == 3 and results:
            results = await self._append_references(
                query, effective_scope, results, t_start,
                query_embedding=query_embedding,
            )

        total_ms = (time.time() - t_start) * 1000
        logger.info(
            "rag_pipeline_complete",
            disclosure_level=disclosure_level,
            final=len(results),
            latency_ms=round(total_ms, 1),
        )

        return ToolResult.ok(
            results,
            method="rag_search",
            chunks_found=len(results),
            disclosure_level=disclosure_level,
            trace_detail=trace_detail,
        )

    async def _execute_level1(
        self, query: str, scope: SearchScope, t_start: float,
    ) -> ToolResult:
        """Progressive Disclosure Level 1: 메타데이터 전용 검색."""
        embedding = (await self._embedder.embed_batch([query]))[0]
        domain_codes = scope.domain_codes if scope.domain_codes else None

        metadata_results = await self._store.metadata_search(
            embedding=embedding,
            text_query=query,
            limit=CANDIDATE_POOL_SIZE,
            domain_codes=domain_codes,
            allowed_doc_ids=scope.allowed_doc_ids,
            max_security_level=scope.security_level_max,
            tenant_id=scope.tenant_id,
            session_id=scope.session_id,
        )

        total_ms = (time.time() - t_start) * 1000
        logger.info(
            "disclosure_level_applied",
            disclosure_level=1,
            docs_loaded=0,
            results=len(metadata_results),
            latency_ms=round(total_ms, 1),
        )

        return ToolResult.ok(
            metadata_results,
            method="rag_search",
            chunks_found=len(metadata_results),
            disclosure_level=1,
        )

    async def _execute_full_pipeline(
        self, query: str, scope: SearchScope, top_k: int,
        min_rerank_score: float | None = None,
    ) -> tuple[list[dict], list[float], dict]:
        """기존 5-layer RAG 파이프라인 (Level 2 기본 경로).

        Returns:
            (results, query_embedding, trace_detail):
              - results: 최종 검색 결과
              - query_embedding: 원본 쿼리 임베딩 (Level 3 재사용용)
              - trace_detail: 파이프라인 관측 상세(필터기준·확장쿼리·리랭킹 입출력 청크)
        """
        # 메타데이터 필터 기준 (scope) — "어떤 기준으로 필터링했나"
        trace_detail: dict = {
            "filter": {
                "domain_codes": list(scope.domain_codes) if scope.domain_codes else None,
                "security_level_max": scope.security_level_max,
                "allowed_doc_ids": len(scope.allowed_doc_ids) if scope.allowed_doc_ids else None,
                "tenant_id": scope.tenant_id,
            },
            "expanded_queries": [query],
            "candidates": [],  # 리랭킹 입력 후보 — "무엇을 기반으로 리랭킹했나"
            "reranked": [],    # 리랭킹 최종 결과 — "어떤 청크를 잡았나"
        }

        # L1. Adaptive 쿼리 확장: probe -> 조건부 확장
        queries, probe_candidates, probe_meta = await self._adaptive_expand(query, scope)
        trace_detail["expanded_queries"] = queries

        # 멀티쿼리 임베딩 (배치)
        embeddings = await self._embedder.embed_batch(queries)
        query_embedding = embeddings[0]  # 원본 쿼리 임베딩 보존

        # 멀티쿼리 하이브리드 검색 + 합산
        if probe_candidates is not None and len(queries) == 1:
            candidates = probe_candidates
            for c in candidates:
                c["found_by"] = ["원본"]
        else:
            candidates = await self._multi_query_search(
                queries, embeddings, scope,
            )

        if not candidates:
            # 계약 유지 (호출부 3-tuple 언팩)
            trace_detail["stages"] = {
                "expansion": {**probe_meta, "queries": len(queries)},
                "retrieval": {"candidates": 0},
            }
            return [], query_embedding, trace_detail

        n_retrieved = len(candidates)

        # L3. 인접 청크 확장 (cascade: 약신호 노이즈 컷을 리랭커 앞에서 하지 않는다 → 고-recall)
        candidates = await expand_neighbors(self._store, candidates)
        # 리랭킹 입력 후보 풀 스냅샷 (상한 CANDIDATE_POOL_SIZE)
        trace_detail["candidates"] = [
            _chunk_snippet(c) for c in candidates[:CANDIDATE_POOL_SIZE]
        ]

        # L4. 리랭킹 (전체 후보 풀을 받아 정밀 판정)
        # 리랭킹은 "정제" 단계다 — 실패해도 이미 성공한 검색(candidates)을 폐기하면 안 된다.
        # 어떤 reranker 구현/융합 로직이 raise 하든 벡터 점수 순 top_k 로 degrade 하여
        # 검색 레이어의 가용성을 보장한다(provider 레벨 degrade와 별개의 심층 방어).
        rerank_audit: list[dict] = []
        if self._reranker and len(candidates) > top_k:
            try:
                results, rerank_audit = await rerank_3tier(
                    self._reranker, query, candidates, top_k,
                    min_rerank_score=(
                        self._min_rerank_score if min_rerank_score is None
                        else min_rerank_score
                    ),
                )
                trace_detail["reranked_by"] = "reranker_3tier"
            except Exception as e:
                logger.warning(
                    "rerank_failed_degrade_to_vector_order",
                    error=str(e),
                    candidates=len(candidates),
                    top_k=top_k,
                )
                results = candidates[:top_k]
                trace_detail["reranked_by"] = "vector_order_degraded"
        else:
            results = candidates[:top_k]
            trace_detail["reranked_by"] = "vector_order"

        n_rerank_out = len(results)

        # L2'. 노이즈 필터 (C12): 약신호 RRF 단계가 아니라 리랭킹 후 융합 점수 기준으로 적용.
        #      리랭커가 살릴 수 있는 청크를 검색 점수만으로 미리 잘라내던 문제를 제거.
        #      주의: MIN_KEEP_COUNT(5)는 최소 컨텍스트 보장이므로 top_k ≤ 5에서는
        #      의도적으로 무동작 — 이 필터는 top_k > 5(예: CROSS_DOC=10)의 긴 꼬리
        #      절단 전용이다. 절대 임계 노이즈 컷은 rerank_3tier의 tier가 담당한다.
        results = filter_noise(results)

        # 노이즈컷 탈락자를 감사에 반영 — "채택됐다가 마지막에 잘린" 청크 식별
        if len(results) < n_rerank_out and rerank_audit:
            surviving = {r.get("chunk_id") for r in results}
            for entry in rerank_audit:
                if entry["fate"] == "selected" and entry["chunk_id"] not in surviving:
                    entry["fate"] = "noise_cut"

        # L5. 결과 가드
        results = guard_results(results)
        trace_detail["reranked"] = [_chunk_snippet(r) for r in results]
        if rerank_audit:
            # 전 후보 판정 기록 (fused 내림차순, 상한 CANDIDATE_POOL_SIZE)
            trace_detail["rerank_audit"] = rerank_audit[:CANDIDATE_POOL_SIZE]
        # 파이프라인 단계별 관측 — "각 레이어에서 무엇이 얼마나 걸러졌나"
        trace_detail["stages"] = {
            "expansion": {**probe_meta, "queries": len(queries)},
            "retrieval": {"candidates": n_retrieved},
            "neighbor": {"added": len(candidates) - n_retrieved},
            "rerank": {
                "input": len(candidates),
                "output": n_rerank_out,
                "by": trace_detail.get("reranked_by", ""),
            },
            "noise_filter": {"before": n_rerank_out, "after": len(results)},
        }
        return results, query_embedding, trace_detail

    async def _append_references(
        self,
        query: str,
        scope: SearchScope,
        results: list[dict],
        t_start: float,
        *,
        query_embedding: list[float] | None = None,
    ) -> list[dict]:
        """Level 3: 상위 결과 score가 낮을 때 참조 문서 추가 로드."""
        top_score = results[0]["score"] if results else 0.0
        if top_score > REFERENCE_LOAD_THRESHOLD:
            return results

        # 임베딩 재사용 (Level 2에서 이미 계산된 것 활용)
        embedding = query_embedding or (await self._embedder.embed_batch([query]))[0]
        domain_codes = scope.domain_codes if scope.domain_codes else None

        metadata = await self._store.metadata_search(
            embedding=embedding,
            text_query=query,
            limit=CANDIDATE_POOL_SIZE,
            domain_codes=domain_codes,
            allowed_doc_ids=scope.allowed_doc_ids,
            max_security_level=scope.security_level_max,
            tenant_id=scope.tenant_id,
            session_id=scope.session_id,
        )

        # 기존 결과에 없는 doc_id 추출
        existing_doc_ids = {r.get("document_id", "") for r in results}
        ref_doc_ids = []
        for m in metadata:
            doc_id = m.get("doc_id", "")
            if doc_id and doc_id not in existing_doc_ids:
                ref_doc_ids.append(doc_id)
                existing_doc_ids.add(doc_id)
            if len(ref_doc_ids) >= MAX_REFERENCE_DOCS:
                break

        if not ref_doc_ids:
            return results

        ref_chunks = await self._store.fetch_chunks_by_doc_ids(
            ref_doc_ids,
            limit_per_doc=2,
            max_security_level=scope.security_level_max,
        )

        logger.info(
            "disclosure_level_applied",
            disclosure_level=3,
            docs_loaded=len(ref_doc_ids),
            ref_chunks=len(ref_chunks),
            latency_ms=round((time.time() - t_start) * 1000, 1),
        )

        return results + ref_chunks

    async def _adaptive_expand(
        self, query: str, scope: SearchScope,
    ) -> tuple[list[str], list[dict] | None, dict]:
        """Probe 검색 후 조건부 쿼리 확장.

        Returns:
            (queries, probe_candidates, probe_meta):
            - 확장 스킵: ([원본], probe 결과, {mode: probe_skip})
            - 확장 실행: ([원본, 변형1, 변형2], None, {mode: expanded})
            - LLM 없음: ([원본], None, {mode: no_llm})
            probe_meta 는 트레이스 관측용(어떤 판단으로 확장 여부가 갈렸나).
        """
        if not self._router_llm:
            return [query], None, {"mode": "no_llm"}

        # Probe: 원본 쿼리 1회 검색
        probe_embedding = (await self._embedder.embed_batch([query]))[0]
        domain_codes = scope.domain_codes if scope.domain_codes else None
        probe_results = await self._store.hybrid_search(
            embedding=probe_embedding,
            text_query=query,
            limit=CANDIDATE_POOL_SIZE,
            domain_codes=domain_codes,
            allowed_doc_ids=scope.allowed_doc_ids,
            max_security_level=scope.security_level_max,
            tenant_id=scope.tenant_id,
            session_id=scope.session_id,
        )

        if not probe_results:
            # 결과 없음 → 확장 실행
            queries = await expand_queries(self._router_llm, query)
            return queries, None, {"mode": "expanded", "probe_top": 0.0}

        top_score = probe_results[0]["score"]
        if top_score >= PROBE_SKIP_THRESHOLD:
            logger.info(
                "adaptive_expansion_skipped",
                top_score=round(top_score, 4),
                threshold=PROBE_SKIP_THRESHOLD,
            )
            sorted_results = sorted(probe_results, key=lambda x: x["score"], reverse=True)
            return [query], sorted_results, {
                "mode": "probe_skip", "probe_top": round(top_score, 4),
            }

        # 품질 부족 → 확장 실행
        logger.info(
            "adaptive_expansion_triggered",
            top_score=round(top_score, 4),
            threshold=PROBE_SKIP_THRESHOLD,
        )
        queries = await expand_queries(self._router_llm, query)
        return queries, None, {"mode": "expanded", "probe_top": round(top_score, 4)}

    async def _multi_query_search(
        self,
        queries: list[str],
        embeddings: list[list[float]],
        scope: SearchScope,
    ) -> list[dict]:
        """멀티쿼리 검색 후 합산. chunk_id 기준 최고 점수 유지.

        각 쿼리에 대한 hybrid_search를 asyncio.gather로 병렬 실행한다.
        """
        domain_codes = scope.domain_codes if scope.domain_codes else None

        tasks = [
            self._store.hybrid_search(
                embedding=embedding,
                text_query=query,
                limit=CANDIDATE_POOL_SIZE,
                domain_codes=domain_codes,
                allowed_doc_ids=scope.allowed_doc_ids,
                max_security_level=scope.security_level_max,
                tenant_id=scope.tenant_id,
                session_id=scope.session_id,
            )
            for query, embedding in zip(queries, embeddings)
        ]

        all_results_lists = await asyncio.gather(*tasks)

        # C11 수정: 변형 쿼리 간 corroboration 보존을 위해 RRF 점수를 합산(SUM)한다.
        # 기존 MAX는 여러 변형이 함께 찾아낸 청크(강한 관련성 신호)를 한 변형만 찾은
        # 청크와 동일 취급해 쿼리확장 효과를 알고리즘적으로 상쇄시켰다. RRF는 본래
        # 여러 랭킹의 기여를 합산하도록 설계된 기법이므로 SUM이 정합한다.
        # found_by: 어느 쿼리(원본/변형N)가 이 청크를 찾았는지 귀속 — 역방향 분석용.
        merged: dict[str, dict] = {}
        for qi, results in enumerate(all_results_lists):
            label = "원본" if qi == 0 else f"변형{qi}"
            for r in results:
                cid = r["chunk_id"]
                if cid in merged:
                    merged[cid] = {
                        **merged[cid],
                        "score": merged[cid]["score"] + r["score"],
                        "found_by": [*merged[cid].get("found_by", []), label],
                    }
                else:
                    merged[cid] = {**r, "found_by": [label]}

        return sorted(
            merged.values(),
            key=lambda x: x["score"],
            reverse=True,
        )
