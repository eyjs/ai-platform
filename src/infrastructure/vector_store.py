"""pgvector + PostgreSQL full-text search 하이브리드 저장소.

domain_codes 필터를 지원하는 범용 벡터 검색.
"""

import json
import logging
import re
import uuid
from typing import List, Optional, Tuple

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

from src.domain.models import SECURITY_HIERARCHY

logger = logging.getLogger(__name__)

RRF_K = 60
HYBRID_CANDIDATE_MULTIPLIER = 3
TRIGRAM_FALLBACK_THRESHOLD = 3
FTS_MAX_TOKENS = 100
TSQUERY_MAX_TOKENS = 20
TRIGRAM_MIN_TERM_LEN = 2
TRIGRAM_MIN_SIMILARITY = 0.1
TRIGRAM_MAX_TERMS = 5


class VectorStore:
    """pgvector + PostgreSQL full-text search 하이브리드 저장소."""

    def __init__(self, database_url: str):
        self._database_url = database_url
        self._pool: Optional[asyncpg.Pool] = None

    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        return self._pool

    async def connect(self, min_size: int = 5, max_size: int = 50) -> None:
        self._pool = await asyncpg.create_pool(
            self._database_url,
            min_size=min_size,
            max_size=max_size,
            command_timeout=10,
            init=register_vector,
        )
        logger.info("VectorStore connected (hybrid: vector + full-text + trigram)")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("VectorStore connection closed")

    # -- 문서 관리 --

    async def insert_document(
        self,
        title: str,
        domain_code: str,
        file_name: str | None = None,
        file_hash: str | None = None,
        security_level: str = "PUBLIC",
        source_url: str | None = None,
        external_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """문서 레코드 생성, ID 반환."""
        if not self._pool:
            raise RuntimeError("VectorStore not connected")
        doc_id = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            if file_hash:
                # INSERT ON CONFLICT: race condition 없는 atomic upsert
                row = await conn.fetchrow(
                    """
                    INSERT INTO documents (id, external_id, title, file_name, file_hash,
                        domain_code, security_level, source_url, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (file_hash, domain_code) DO UPDATE
                        SET title = EXCLUDED.title,
                            security_level = EXCLUDED.security_level,
                            source_url = EXCLUDED.source_url,
                            metadata = EXCLUDED.metadata
                    RETURNING id
                    """,
                    uuid.UUID(doc_id), external_id, title, file_name, file_hash,
                    domain_code, security_level, source_url,
                    json.dumps(metadata or {}, ensure_ascii=False),
                )
                return str(row["id"])

            await conn.execute(
                """
                INSERT INTO documents (id, external_id, title, file_name, file_hash,
                    domain_code, security_level, source_url, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                uuid.UUID(doc_id), external_id, title, file_name, file_hash,
                domain_code, security_level, source_url,
                json.dumps(metadata or {}, ensure_ascii=False),
            )
        return doc_id

    async def insert_chunks(
        self, document_id: str, chunks: List[dict], embeddings: List[List[float]],
        domain_code: str = "", security_level: str = "PUBLIC",
    ) -> List[str]:
        """청크 + 임베딩 + tsvector 배치 삽입."""
        if not self._pool:
            raise RuntimeError("VectorStore not connected")

        query = """
            INSERT INTO document_chunks
                (id, document_id, chunk_index, content, token_count, embedding,
                 search_vector, domain_code, security_level, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, to_tsvector('simple', $7),
                    $8, $9, $10)
        """
        chunk_ids = []
        records = []
        for chunk, emb in zip(chunks, embeddings):
            chunk_id = str(uuid.uuid4())
            chunk_ids.append(chunk_id)
            content = chunk["content"]
            # 간단한 한국어 토큰화: 공백 분리
            tokenized = self._tokenize_for_fts(content)
            records.append((
                uuid.UUID(chunk_id),
                uuid.UUID(document_id),
                chunk["chunkIndex"],
                content,
                chunk.get("tokenCount", 0),
                np.array(emb, dtype=np.float32),
                tokenized,
                domain_code or chunk.get("domain_code", ""),
                security_level or chunk.get("security_level", "PUBLIC"),
                json.dumps(chunk.get("metadata", {}), ensure_ascii=False),
            ))

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(query, records)

        logger.info("Inserted %d chunks for document %s", len(chunk_ids), document_id)
        return chunk_ids

    async def delete_document_chunks(self, document_id: str) -> int:
        if not self._pool:
            raise RuntimeError("VectorStore not connected")
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM document_chunks WHERE document_id = $1",
                uuid.UUID(document_id),
            )
        count = int(result.split()[-1])
        logger.info("Deleted %d chunks for document %s", count, document_id)
        return count

    async def get_chunk_count(self, document_id: str) -> int:
        if not self._pool:
            raise RuntimeError("VectorStore not connected")
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM document_chunks "
                "WHERE document_id = $1 AND embedding IS NOT NULL",
                uuid.UUID(document_id),
            )
        return row["cnt"] if row else 0

    # -- 검색 --

    async def search(
        self,
        embedding: List[float],
        limit: int = 10,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        max_security_level: Optional[str] = None,
    ) -> List[dict]:
        """벡터 전용 검색."""
        if not self._pool:
            raise RuntimeError("VectorStore not connected")
        allowed_levels = self._allowed_security_levels(max_security_level)
        query, params = self._build_vector_query(
            embedding, limit, domain_codes, allowed_doc_ids, allowed_levels,
        )
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return self._rows_to_results(rows)

    async def hybrid_search(
        self,
        embedding: List[float],
        text_query: str,
        limit: int = 10,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        vector_weight: float = 0.5,
        max_security_level: Optional[str] = None,
    ) -> List[dict]:
        """벡터 + full-text + trigram RRF 하이브리드 검색."""
        if not self._pool:
            raise RuntimeError("VectorStore not connected")

        allowed_levels = self._allowed_security_levels(max_security_level)
        candidate_limit = limit * HYBRID_CANDIDATE_MULTIPLIER

        async with self._pool.acquire() as conn:
            # 1. 벡터 검색
            vq, vp = self._build_vector_query(
                embedding, candidate_limit, domain_codes, allowed_doc_ids, allowed_levels,
            )
            vector_rows = await conn.fetch(vq, *vp)

            # 2. Full-text 검색
            try:
                fts_rows = await self._fulltext_search(
                    conn, text_query, candidate_limit, domain_codes,
                    allowed_doc_ids, allowed_levels,
                )
            except Exception as e:
                logger.warning("Full-text search failed: %s", e)
                fts_rows = []

            # 3. Trigram fallback
            if len(fts_rows) < TRIGRAM_FALLBACK_THRESHOLD:
                try:
                    trgm_rows = await self._trigram_search(
                        conn, text_query, candidate_limit, domain_codes,
                        allowed_doc_ids, allowed_levels,
                    )
                    if trgm_rows:
                        seen_ids = {str(r["id"]) for r in fts_rows}
                        for row in trgm_rows:
                            if str(row["id"]) not in seen_ids:
                                fts_rows.append(row)
                                seen_ids.add(str(row["id"]))
                except Exception as e:
                    logger.warning("Trigram search failed: %s", e)

        return self._rrf_merge(vector_rows, fts_rows, limit, vector_weight)

    async def get_neighbor_chunks(
        self, document_id: str, chunk_indices: list[int],
    ) -> list[dict]:
        """인접 청크 조회 (맥락 확장용)."""
        if not self._pool or not chunk_indices:
            return []
        valid_indices = [i for i in chunk_indices if i >= 0]
        if not valid_indices:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.document_id, c.content, c.chunk_index, d.file_name
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.document_id = $1 AND c.chunk_index = ANY($2::int[])
                """,
                uuid.UUID(document_id), valid_indices,
            )
        return [
            {
                "chunk_id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "content": row["content"],
                "chunk_index": row["chunk_index"],
                "file_name": row.get("file_name", ""),
            }
            for row in rows
        ]

    # -- ID 매핑 --

    async def get_external_ids(self, aip_doc_ids: list[str]) -> dict[str, str]:
        """ai-platform UUID → KMS external_id 배치 매핑."""
        if not self._pool or not aip_doc_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, external_id FROM documents "
                "WHERE id = ANY($1::uuid[]) AND external_id IS NOT NULL",
                [uuid.UUID(d) for d in aip_doc_ids],
            )
        return {str(row["id"]): row["external_id"] for row in rows}

    async def get_aip_id_by_external(self, external_id: str) -> str | None:
        """KMS external_id → ai-platform UUID 단건 역매핑."""
        if not self._pool:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM documents WHERE external_id = $1",
                external_id,
            )
        return str(row["id"]) if row else None

    async def get_top_chunks_by_doc(
        self, document_id: str, limit: int = 2,
    ) -> list[dict]:
        """document_id로 상위 청크를 chunk_index 순서로 조회 (임베딩 불필요)."""
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.document_id, c.content, c.chunk_index, d.file_name
                FROM document_chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.document_id = $1
                ORDER BY c.chunk_index
                LIMIT $2
                """,
                uuid.UUID(document_id), limit,
            )
        return [
            {
                "chunk_id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "content": row["content"],
                "chunk_index": row["chunk_index"],
                "score": 0.5,
                "file_name": row.get("file_name", ""),
            }
            for row in rows
        ]

    # -- 내부 메서드 --

    @staticmethod
    def _tokenize_for_fts(text: str) -> str:
        """간단한 한국어 토큰화 (공백 기반)."""
        cleaned = re.sub(r"[(),:!&|<>'\\*\"]+", " ", text)
        tokens = [t for t in cleaned.split() if len(t) >= TRIGRAM_MIN_TERM_LEN]
        return " ".join(tokens[:FTS_MAX_TOKENS])

    @staticmethod
    def _sanitize_tsquery(text: str) -> str:
        cleaned = re.sub(r"[(),:!&|<>'\\*\"]+", " ", text)
        tokens = [t for t in cleaned.split() if len(t) >= TRIGRAM_MIN_TERM_LEN]
        if not tokens:
            return ""
        return " | ".join(tokens[:TSQUERY_MAX_TOKENS])

    def _build_vector_query(
        self,
        embedding: List[float],
        limit: int,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        allowed_levels: Optional[List[str]] = None,
    ) -> Tuple[str, list]:
        conditions = ["c.embedding IS NOT NULL"]
        params: list = [np.array(embedding, dtype=np.float32), limit]
        param_idx = 3

        if domain_codes:
            conditions.append(f"c.domain_code = ANY(${param_idx}::text[])")
            params.append(domain_codes)
            param_idx += 1

        if allowed_doc_ids:
            conditions.append(f"c.document_id = ANY(${param_idx}::uuid[])")
            params.append([uuid.UUID(d) for d in allowed_doc_ids])
            param_idx += 1

        if allowed_levels:
            conditions.append(f"c.security_level = ANY(${param_idx}::text[])")
            params.append(allowed_levels)
            param_idx += 1

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT c.id, c.document_id, c.content, c.chunk_index,
                   1 - (c.embedding <=> $1::vector) AS score,
                   d.file_name, d.title
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {where_clause}
            ORDER BY c.embedding <=> $1::vector
            LIMIT $2
        """
        return query, params

    async def _fulltext_search(
        self, conn, text_query: str, limit: int,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        allowed_levels: Optional[List[str]] = None,
    ) -> list:
        tsquery = self._sanitize_tsquery(text_query)
        if not tsquery:
            return []

        conditions = ["c.search_vector @@ to_tsquery('simple', $1)"]
        params: list = [tsquery, limit]
        param_idx = 3

        if domain_codes:
            conditions.append(f"c.domain_code = ANY(${param_idx}::text[])")
            params.append(domain_codes)
            param_idx += 1

        if allowed_doc_ids:
            conditions.append(f"c.document_id = ANY(${param_idx}::uuid[])")
            params.append([uuid.UUID(d) for d in allowed_doc_ids])
            param_idx += 1

        if allowed_levels:
            conditions.append(f"c.security_level = ANY(${param_idx}::text[])")
            params.append(allowed_levels)
            param_idx += 1

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT c.id, c.document_id, c.content, c.chunk_index,
                   ts_rank(c.search_vector, to_tsquery('simple', $1)) AS score,
                   d.file_name, d.title
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {where_clause}
            ORDER BY score DESC
            LIMIT $2
        """
        return await conn.fetch(query, *params)

    async def _trigram_search(
        self, conn, text_query: str, limit: int,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        allowed_levels: Optional[List[str]] = None,
    ) -> list:
        terms = [t for t in text_query.split() if len(t) >= TRIGRAM_MIN_TERM_LEN]
        if not terms:
            return []

        search_text = " ".join(terms[:TRIGRAM_MAX_TERMS])
        conditions = [f"similarity(c.content, $1::text) > {TRIGRAM_MIN_SIMILARITY}"]
        params: list = [search_text, limit]
        param_idx = 3

        if domain_codes:
            conditions.append(f"c.domain_code = ANY(${param_idx}::text[])")
            params.append(domain_codes)
            param_idx += 1

        if allowed_doc_ids:
            conditions.append(f"c.document_id = ANY(${param_idx}::uuid[])")
            params.append([uuid.UUID(d) for d in allowed_doc_ids])
            param_idx += 1

        if allowed_levels:
            conditions.append(f"c.security_level = ANY(${param_idx}::text[])")
            params.append(allowed_levels)
            param_idx += 1

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT c.id, c.document_id, c.content, c.chunk_index,
                   similarity(c.content, $1::text) AS score,
                   d.file_name, d.title
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {where_clause}
            ORDER BY score DESC
            LIMIT $2
        """
        return await conn.fetch(query, *params)

    def _rrf_merge(
        self, vector_rows: list, fts_rows: list, limit: int, vector_weight: float,
    ) -> List[dict]:
        chunk_data: dict[str, dict] = {}
        rrf_scores: dict[str, float] = {}
        fts_weight = 1.0 - vector_weight

        for rank, row in enumerate(vector_rows):
            chunk_id = str(row["id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + (
                vector_weight / (RRF_K + rank + 1)
            )
            if chunk_id not in chunk_data:
                chunk_data[chunk_id] = self._row_to_dict(row)

        for rank, row in enumerate(fts_rows):
            chunk_id = str(row["id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + (
                fts_weight / (RRF_K + rank + 1)
            )
            if chunk_id not in chunk_data:
                chunk_data[chunk_id] = self._row_to_dict(row)

        sorted_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)

        results = []
        for chunk_id in sorted_ids[:limit]:
            data = {**chunk_data[chunk_id], "score": rrf_scores[chunk_id]}
            results.append(data)
        return results

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "chunk_id": str(row["id"]),
            "document_id": str(row["document_id"]),
            "content": row["content"],
            "chunk_index": row["chunk_index"],
            "score": float(row["score"]),
            "file_name": row.get("file_name", ""),
            "title": row.get("title", ""),
        }

    @staticmethod
    def _rows_to_results(rows) -> List[dict]:
        return [
            {
                "chunk_id": str(row["id"]),
                "document_id": str(row["document_id"]),
                "content": row["content"],
                "chunk_index": row["chunk_index"],
                "score": float(row["score"]),
                "file_name": row.get("file_name", ""),
                "title": row.get("title", ""),
            }
            for row in rows
        ]

    @staticmethod
    def _allowed_security_levels(max_level: Optional[str]) -> Optional[List[str]]:
        if not max_level:
            return None
        max_rank = SECURITY_HIERARCHY.get(max_level)
        if max_rank is None:
            logger.warning("Unrecognized security level '%s', denying all", max_level)
            return []
        return [level for level, rank in SECURITY_HIERARCHY.items() if rank <= max_rank]
