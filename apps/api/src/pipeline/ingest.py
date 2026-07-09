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
from src.infrastructure.providers.base import EmbeddingProvider, ParsingProvider
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
        parsing_provider: Optional[ParsingProvider] = None,
    ):
        self._store = vector_store
        self._embedder = embedding_provider
        self._parser = parsing_provider
        self._chunker = TextChunker(settings.chunk_size, settings.chunk_overlap)
        self._md_chunker = MarkdownChunker(settings.chunk_size, settings.chunk_overlap)
        self._settings = settings

    async def ingest_text(
        self,
        title: str,
        content: Optional[str] = None,
        domain_code: str = "",
        file_name: Optional[str] = None,
        security_level: str = "PUBLIC",
        source_url: Optional[str] = None,
        metadata: Optional[dict] = None,
        file_bytes: Optional[bytes] = None,
        mime_type: Optional[str] = None,
        external_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> dict:
        """문서 수집. file_bytes가 있으면 파서로 마크다운 변환 후 청킹."""

        # 파일 바이너리 → 파서 → 마크다운 텍스트
        if file_bytes and mime_type:
            if not self._parser:
                raise RuntimeError("ParsingProvider not configured. Set AIP_PARSER_PROVIDER.")
            t_parse = time.time()
            content = await self._parser.parse(file_bytes, mime_type)
            parse_ms = (time.time() - t_parse) * 1000
            logger.info("parsed", title=title, mime_type=mime_type, chars=len(content), latency_ms=round(parse_ms, 1))

        if not content or not content.strip():
            raise ValueError("content or file_bytes is required")

        file_hash = hashlib.sha256(content.encode()).hexdigest()

        # 1. 문서 레코드 생성
        doc_id = await self._store.insert_document(
            title=title,
            domain_code=domain_code,
            file_name=file_name or f"{title}.txt",
            file_hash=file_hash,
            security_level=security_level,
            source_url=source_url,
            external_id=external_id,
            metadata=metadata,
            tenant_id=tenant_id,
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

        # 2.5 문서명 헤더 (+ 섹션 경로 — AST-lite 메타가 있으면 임베딩/FTS/LLM 컨텍스트에 노출)
        for chunk in chunks:
            section_path = (chunk.get("metadata") or {}).get("section_path")
            if section_path:
                header = f"[문서: {title} | 섹션: {' > '.join(section_path)}]\n"
            else:
                header = f"[문서: {title}]\n"
            chunk["content"] = header + chunk["content"]

        logger.info("chunked", title=title, chunks=len(chunks))

        # 3. 임베딩 (VectorStore 저장 전에 완료 — 실패 시 고아 chunk 방지)
        texts = [c["content"] for c in chunks]
        t_embed = time.time()
        try:
            embeddings = await self._embed_in_batches(texts)
        except Exception as e:
            logger.error("embedding_failed", title=title, doc_id=doc_id, error=repr(e))
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
            tenant_id=tenant_id,
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
            # 조립된 마크다운 원문 — KMS 콜백(원본↔MD 비교 표시)용으로 끌어올린다.
            # 청크엔 "[문서: title]" 헤더가 붙지만 content 는 깨끗한 파싱 결과다.
            "markdown": content,
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
            logger.info("embedding_batches", batches=len(batches), total_texts=len(texts))

        # 동시 배치 요청을 제한한다. 모든 배치를 한꺼번에 gather하면 단일 임베딩
        # 서버가 과부하로 교착(CLOSE_WAIT)된다. 작은 세마포어로 건강하게 유지.
        sem = asyncio.Semaphore(max(1, self._settings.embed_concurrency))

        async def _embed_one(idx: int, batch: List[str]) -> List[List[float]]:
            async with sem:
                try:
                    return await self._embedder.embed_batch(batch)
                except Exception as e:
                    # 실패 원인을 배치 컨텍스트와 함께 표면화 (last_error 공백 방지)
                    raise RuntimeError(
                        f"embed_batch 실패 (배치 {idx + 1}/{len(batches)}, "
                        f"{len(batch)}개 텍스트): {type(e).__name__}: {e}"
                    ) from e

        results = await asyncio.gather(
            *[_embed_one(i, b) for i, b in enumerate(batches)]
        )
        all_embeddings: List[List[float]] = []
        for r in results:
            all_embeddings.extend(r)
        return all_embeddings
