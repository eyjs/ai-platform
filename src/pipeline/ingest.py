"""Knowledge Pipeline: 문서 수집 파이프라인.

텍스트/URL → 청킹 → 임베딩 → VectorStore 저장.
자체 PostgreSQL에 문서 관리. KMS 의존 없음.
"""

import gc
import hashlib
import re
import time
from typing import List, Optional

import tiktoken

from src.config import Settings
from src.infrastructure.providers.base import EmbeddingProvider
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
from src.pipeline.chunker import MarkdownChunker, TextChunker

logger = get_logger(__name__)

SMALL_DOC_THRESHOLD = 3000


class IngestPipeline:
    """문서 수집 파이프라인. KMS 의존 없음."""

    def __init__(
        self,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        settings: Settings,
    ):
        self._store = vector_store
        self._embedder = embedding_provider
        self._chunker = TextChunker(settings.chunk_size, settings.chunk_overlap)
        self._md_chunker = MarkdownChunker(settings.chunk_size, settings.chunk_overlap)
        self._settings = settings

    async def ingest_text(
        self,
        title: str,
        content: str,
        domain_code: str,
        file_name: Optional[str] = None,
        security_level: str = "PUBLIC",
        source_url: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """텍스트 문서 수집."""
        file_hash = hashlib.sha256(content.encode()).hexdigest()

        # 1. 문서 레코드 생성
        doc_id = await self._store.insert_document(
            title=title,
            domain_code=domain_code,
            file_name=file_name or f"{title}.txt",
            file_hash=file_hash,
            security_level=security_level,
            source_url=source_url,
            metadata=metadata,
        )

        # 2. 청킹
        if len(content) < SMALL_DOC_THRESHOLD:
            encoding = tiktoken.get_encoding("cl100k_base")
            chunks = [{
                "chunkIndex": 0,
                "content": content,
                "tokenCount": len(encoding.encode(content)),
            }]
        else:
            has_heading = bool(re.match(r'^#{1,6}\s+\S', content.lstrip()))
            if has_heading or (file_name and file_name.endswith(".md")):
                chunks = self._md_chunker.split(content)
            else:
                chunks = self._chunker.split(content)

        # 2.5 문서명 헤더
        for chunk in chunks:
            header = f"[문서: {title}]\n"
            chunk["content"] = header + chunk["content"]

        logger.info("chunked", title=title, chunks=len(chunks))

        # 3. 임베딩 (VectorStore 저장 전에 완료 — 실패 시 고아 chunk 방지)
        texts = [c["content"] for c in chunks]
        t_embed = time.time()
        try:
            embeddings = await self._embed_in_batches(texts)
        except Exception:
            logger.error("embedding_failed", title=title, doc_id=doc_id)
            raise
        embed_ms = (time.time() - t_embed) * 1000
        logger.info("embedded", title=title, chunks=len(chunks), latency_ms=round(embed_ms, 1))

        if len(embeddings) != len(chunks):
            raise RuntimeError(
                f"Embedding count mismatch: {len(embeddings)} embeddings for {len(chunks)} chunks"
            )

        del texts
        gc.collect()

        # 4. 기존 청크 삭제 + 새 청크 삽입 (atomic)
        t_store = time.time()
        await self._store.delete_document_chunks(doc_id)
        chunk_ids = await self._store.insert_chunks(
            doc_id, chunks, embeddings,
            domain_code=domain_code,
            security_level=security_level,
        )
        store_ms = (time.time() - t_store) * 1000

        chunk_count = len(chunks)
        chars = len(content)
        del chunks, embeddings, chunk_ids
        gc.collect()

        logger.info(
            "ingested",
            title=title,
            chunks=chunk_count,
            chars=chars,
            embed_ms=round(embed_ms, 1),
            store_ms=round(store_ms, 1),
        )

        return {
            "document_id": doc_id,
            "title": title,
            "status": "success",
            "chunks": chunk_count,
            "chars": chars,
        }

    def _calculate_batch_size(self, total_chunks: int) -> int:
        if total_chunks < 100:
            return 32
        elif total_chunks < 500:
            return self._settings.embed_batch_size
        else:
            return self._settings.embed_max_batch_size

    async def _embed_in_batches(self, texts: List[str]) -> List[List[float]]:
        import asyncio

        batch_size = self._calculate_batch_size(len(texts))
        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]

        if len(batches) > 5:
            logger.info("Embedding %d batches (%d texts total)", len(batches), len(texts))

        async def _embed_one(batch: List[str]) -> List[List[float]]:
            return await self._embedder.embed_batch(batch)

        results = await asyncio.gather(*[_embed_one(b) for b in batches])
        all_embeddings: List[List[float]] = []
        for r in results:
            all_embeddings.extend(r)
        return all_embeddings
