"""Knowledge Pipeline: 문서 수집 파이프라인.

텍스트/URL → 청킹 → 임베딩 → VectorStore 저장.
자체 PostgreSQL에 문서 관리. KMS 의존 없음.
"""

import gc
import hashlib
import logging
import re
from typing import List, Optional

import tiktoken

from src.config import Settings
from src.infrastructure.providers.base import EmbeddingProvider
from src.infrastructure.vector_store import VectorStore
from src.pipeline.chunker import MarkdownChunker, TextChunker

logger = logging.getLogger(__name__)

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

        # 2. 기존 청크 삭제 (재수집 대비)
        await self._store.delete_document_chunks(doc_id)

        # 3. 청킹
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

        # 3.5 문서명 헤더
        for chunk in chunks:
            header = f"[문서: {title}]\n"
            chunk["content"] = header + chunk["content"]

        logger.info("Chunked '%s': %d chunks", title, len(chunks))

        # 4. 임베딩
        texts = [c["content"] for c in chunks]
        embeddings = await self._embed_in_batches(texts)

        del texts
        gc.collect()

        # 5. VectorStore 저장
        chunk_ids = await self._store.insert_chunks(
            doc_id, chunks, embeddings,
            domain_code=domain_code,
            security_level=security_level,
        )

        chunk_count = len(chunks)
        chars = len(content)
        del chunks, embeddings, chunk_ids
        gc.collect()

        logger.info("Ingested '%s': %d chunks, %d chars", title, chunk_count, chars)

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
        batch_size = self._calculate_batch_size(len(texts))
        all_embeddings: List[List[float]] = []
        total_batches = (len(texts) + batch_size - 1) // batch_size
        for i in range(0, len(texts), batch_size):
            batch_num = i // batch_size + 1
            batch = texts[i : i + batch_size]
            if total_batches > 5:
                logger.info("Embedding batch %d/%d (%d texts)", batch_num, total_batches, len(batch))
            embeddings = await self._embedder.embed_batch(batch)
            all_embeddings.extend(embeddings)
        return all_embeddings
