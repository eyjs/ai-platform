"""RAG Search Tool: 5-layer 파이프라인 오케스트레이터.

L1 쿼리확장 -> 멀티쿼리 검색 -> L2 노이즈필터 -> L3 이웃확장 -> L4 리랭킹 -> L5 가드
"""

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
        t_start = time.time()

        # L1. 쿼리 확장
        if self._router_llm:
            queries = await expand_queries(self._router_llm, query)
        else:
            queries = [query]

        # 멀티쿼리 임베딩 (배치)
        embeddings = await self._embedder.embed_batch(queries)

        # 멀티쿼리 하이브리드 검색 + 합산
        candidates = await self._multi_query_search(
            queries, embeddings, scope,
        )

        if not candidates:
            return ToolResult.ok([], method="rag_search", chunks_found=0)

        # L2. 노이즈 필터
        candidates = filter_noise(candidates)

        # L3. 인접 청크 확장
        candidates = await expand_neighbors(self._store, candidates)

        # L4. 리랭킹
        if self._reranker and len(candidates) > top_k:
            results = await rerank_3tier(
                self._reranker, query, candidates, top_k,
            )
        else:
            results = candidates[:top_k]

        # L5. 결과 가드
        results = guard_results(results)

        total_ms = (time.time() - t_start) * 1000
        logger.info(
            "rag_pipeline_complete",
            queries=len(queries),
            final=len(results),
            latency_ms=round(total_ms, 1),
        )

        return ToolResult.ok(
            results,
            method="rag_search",
            chunks_found=len(results),
        )

    async def _multi_query_search(
        self,
        queries: list[str],
        embeddings: list[list[float]],
        scope: SearchScope,
    ) -> list[dict]:
        """멀티쿼리 검색 후 합산. chunk_id 기준 최고 점수 유지."""
        domain_codes = scope.domain_codes if scope.domain_codes else None
        all_results: dict[str, dict] = {}

        for query, embedding in zip(queries, embeddings):
            results = await self._store.hybrid_search(
                embedding=embedding,
                text_query=query,
                limit=CANDIDATE_POOL_SIZE,
                domain_codes=domain_codes,
                allowed_doc_ids=scope.allowed_doc_ids,
                max_security_level=scope.security_level_max,
            )
            for r in results:
                cid = r["chunk_id"]
                if cid not in all_results or r["score"] > all_results[cid]["score"]:
                    all_results[cid] = r

        return sorted(
            all_results.values(),
            key=lambda x: x["score"],
            reverse=True,
        )
