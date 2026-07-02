"""RAG Search Tool: 5-layer 파이프라인 오케스트레이터.

Adaptive L1: probe 검색 → 고품질이면 확장 스킵
L1 쿼리확장 -> 멀티쿼리 검색 -> L2 노이즈필터 -> L3 이웃확장 -> L4 리랭킹 -> L5 가드
"""

import asyncio
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
from src.tools.internal.reranker_pipeline import rerank_3tier
from src.tools.internal.result_guard import guard_results

logger = get_logger(__name__)

CANDIDATE_POOL_SIZE = 50
PROBE_SKIP_THRESHOLD = 0.012  # RRF 스코어 범위: ~0.007-0.016

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
    ):
        self._embedder = embedding_provider
        self._store = vector_store
        self._reranker = reranker
        self._router_llm = router_llm
        self._default_top_k = default_top_k

    async def execute(
        self,
        params: dict,
        context: AgentContext,
        scope: SearchScope,
    ) -> ToolResult:
        query = params.get("query", "")
        if not query:
            return ToolResult.fail("query is required")

        top_k = params.get("max_vector_chunks", self._default_top_k)
        disclosure_level = params.get(
            "disclosure_level", DEFAULT_DISCLOSURE_LEVEL,
        )
        t_start = time.time()

        # Level 1: 메타데이터 전용 (본문 로드 없음)
        if disclosure_level == 1:
            return await self._execute_level1(query, scope, t_start)

        # Level 2: 기존 5-layer 파이프라인 (기본값)
        results, query_embedding = await self._execute_full_pipeline(
            query, scope, top_k,
        )

        # Level 3: Level 2 + references 조건부 로드 (임베딩 재사용)
        if disclosure_level == 3 and results:
            results = await self._append_references(
                query, scope, results, t_start,
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
    ) -> tuple[list[dict], list[float]]:
        """기존 5-layer RAG 파이프라인 (Level 2 기본 경로).

        Returns:
            (results, query_embedding): 검색 결과와 원본 쿼리 임베딩 (Level 3 재사용용)
        """
        # L1. Adaptive 쿼리 확장: probe -> 조건부 확장
        queries, probe_candidates = await self._adaptive_expand(query, scope)

        # 멀티쿼리 임베딩 (배치)
        embeddings = await self._embedder.embed_batch(queries)
        query_embedding = embeddings[0]  # 원본 쿼리 임베딩 보존

        # 멀티쿼리 하이브리드 검색 + 합산
        if probe_candidates is not None and len(queries) == 1:
            candidates = probe_candidates
        else:
            candidates = await self._multi_query_search(
                queries, embeddings, scope,
            )

        if not candidates:
            # 2-tuple 계약 유지 (빈 리스트만 반환하면 호출부 언팩이 깨진다)
            return [], query_embedding

        # L3. 인접 청크 확장 (cascade: 약신호 노이즈 컷을 리랭커 앞에서 하지 않는다 → 고-recall)
        candidates = await expand_neighbors(self._store, candidates)

        # L4. 리랭킹 (전체 후보 풀을 받아 정밀 판정)
        # 리랭킹은 "정제" 단계다 — 실패해도 이미 성공한 검색(candidates)을 폐기하면 안 된다.
        # 어떤 reranker 구현/융합 로직이 raise 하든 벡터 점수 순 top_k 로 degrade 하여
        # 검색 레이어의 가용성을 보장한다(provider 레벨 degrade와 별개의 심층 방어).
        if self._reranker and len(candidates) > top_k:
            try:
                results = await rerank_3tier(
                    self._reranker, query, candidates, top_k,
                )
            except Exception as e:
                logger.warning(
                    "rerank_failed_degrade_to_vector_order",
                    error=str(e),
                    candidates=len(candidates),
                    top_k=top_k,
                )
                results = candidates[:top_k]
        else:
            results = candidates[:top_k]

        # L2'. 노이즈 필터 (C12): 약신호 RRF 단계가 아니라 리랭킹 후 융합 점수 기준으로 적용.
        #      리랭커가 살릴 수 있는 청크를 검색 점수만으로 미리 잘라내던 문제를 제거.
        results = filter_noise(results)

        # L5. 결과 가드
        return guard_results(results), query_embedding

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
    ) -> tuple[list[str], list[dict] | None]:
        """Probe 검색 후 조건부 쿼리 확장.

        Returns:
            (queries, probe_candidates):
            - 확장 스킵: ([원본], probe 결과)
            - 확장 실행: ([원본, 변형1, 변형2], None)
            - LLM 없음: ([원본], None)
        """
        if not self._router_llm:
            return [query], None

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
            return queries, None

        top_score = probe_results[0]["score"]
        if top_score >= PROBE_SKIP_THRESHOLD:
            logger.info(
                "adaptive_expansion_skipped",
                top_score=round(top_score, 4),
                threshold=PROBE_SKIP_THRESHOLD,
            )
            sorted_results = sorted(probe_results, key=lambda x: x["score"], reverse=True)
            return [query], sorted_results

        # 품질 부족 → 확장 실행
        logger.info(
            "adaptive_expansion_triggered",
            top_score=round(top_score, 4),
            threshold=PROBE_SKIP_THRESHOLD,
        )
        queries = await expand_queries(self._router_llm, query)
        return queries, None

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
        merged: dict[str, dict] = {}
        for results in all_results_lists:
            for r in results:
                cid = r["chunk_id"]
                if cid in merged:
                    merged[cid] = {**merged[cid], "score": merged[cid]["score"] + r["score"]}
                else:
                    merged[cid] = dict(r)

        return sorted(
            merged.values(),
            key=lambda x: x["score"],
            reverse=True,
        )
