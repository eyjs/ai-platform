"""RAG Search Tool: embed -> hybrid_search -> rerank -> ToolResult."""

import time
from typing import List, Optional

from src.infrastructure.providers.base import EmbeddingProvider, RerankerProvider
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
from src.domain.models import SearchScope
from src.tools.base import AgentContext, ToolResult

logger = get_logger(__name__)


class RAGSearchTool:
    """RAG 검색 도구 (ScopedTool)."""

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
        default_top_k: int = 5,
    ):
        self._embedder = embedding_provider
        self._store = vector_store
        self._reranker = reranker
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

        # 1. 임베딩
        t_embed = time.time()
        embedding = await self._embedder.embed(query)
        embed_ms = (time.time() - t_embed) * 1000
        logger.info("rag_embed", query_len=len(query), latency_ms=round(embed_ms, 1))

        # 2. 하이브리드 검색
        domain_codes = scope.domain_codes if scope.domain_codes else None
        t_search = time.time()
        results = await self._store.hybrid_search(
            embedding=embedding,
            text_query=query,
            limit=top_k * 3,  # 리랭킹용 후보
            domain_codes=domain_codes,
            allowed_doc_ids=scope.allowed_doc_ids,
            max_security_level=scope.security_level_max,
        )
        search_ms = (time.time() - t_search) * 1000
        logger.info(
            "rag_hybrid_search",
            candidates=len(results),
            domains=domain_codes,
            latency_ms=round(search_ms, 1),
        )

        if not results:
            return ToolResult.ok([], method="rag_search", chunks_found=0)

        # 3. 리랭킹
        if self._reranker and len(results) > top_k:
            t_rerank = time.time()
            documents = [r["content"] for r in results]
            reranked = await self._reranker.rerank(query, documents, top_k=top_k)
            results = [results[item["index"]] for item in reranked]
            rerank_ms = (time.time() - t_rerank) * 1000
            logger.info(
                "rag_rerank",
                input_count=len(documents),
                output_count=len(results),
                latency_ms=round(rerank_ms, 1),
            )
        else:
            results = results[:top_k]

        return ToolResult.ok(
            results,
            method="rag_search",
            chunks_found=len(results),
        )
