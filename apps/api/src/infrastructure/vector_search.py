"""벡터 검색 파이프라인 Mixin.

VectorStore 의 base 로만 사용한다 (self._pool 은 VectorStore.__init__ 이 설정).
이 모듈은 vector_store.py 를 import 하지 않는다 (단방향 의존).
"""

import asyncio
import logging
import re
import uuid
from typing import Callable, List, Optional, Tuple

import numpy as np

from src.domain.models import SECURITY_HIERARCHY, UNPLACED_DOMAIN

logger = logging.getLogger(__name__)

RRF_K = 60
HYBRID_CANDIDATE_MULTIPLIER = 3
TRIGRAM_FALLBACK_THRESHOLD = 3
FTS_MAX_TOKENS = 100
TSQUERY_MAX_TOKENS = 20
TRIGRAM_MIN_TERM_LEN = 2
TRIGRAM_MIN_SIMILARITY = 0.1
TRIGRAM_MAX_TERMS = 5


class VectorSearchMixin:
    """검색 파이프라인. VectorStore 의 base 로만 사용(self._pool 은 VectorStore.__init__ 설정)."""

    _pool: "Optional[object]"  # 명료성 어노테이션(런타임 무영향)

    async def search(
        self,
        embedding: List[float],
        limit: int = 10,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        max_security_level: Optional[str] = None,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[dict]:
        """벡터 전용 검색."""
        if not self._pool:
            raise RuntimeError("VectorStore not connected")
        allowed_levels = self._allowed_security_levels(max_security_level)
        query, params = self._build_vector_query(
            embedding, limit, domain_codes, allowed_doc_ids, allowed_levels,
            tenant_id=tenant_id, session_id=session_id,
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
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[dict]:
        """벡터 + full-text + trigram RRF 하이브리드 검색.

        vector와 text(FTS+trigram) 검색을 별도 커넥션에서 병렬 실행한다.
        """
        if not self._pool:
            raise RuntimeError("VectorStore not connected")

        allowed_levels = self._allowed_security_levels(max_security_level)
        candidate_limit = limit * HYBRID_CANDIDATE_MULTIPLIER

        # vector와 text(FTS + trigram fallback)를 병렬 실행
        vector_rows, fts_rows = await asyncio.gather(
            self._vector_search_task(
                embedding, candidate_limit, domain_codes,
                allowed_doc_ids, allowed_levels, tenant_id=tenant_id,
                session_id=session_id,
            ),
            self._text_search_combined(
                text_query, candidate_limit, domain_codes,
                allowed_doc_ids, allowed_levels, tenant_id=tenant_id,
                session_id=session_id,
            ),
        )

        return self._rrf_merge(vector_rows, fts_rows, limit, vector_weight)

    async def metadata_search(
        self,
        embedding: List[float],
        text_query: str,
        limit: int = 10,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        max_security_level: Optional[str] = None,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[dict]:
        """메타데이터 전용 검색 (content 제외). Progressive Disclosure Level 1.

        hybrid_search()와 동일한 벡터+FTS+trigram RRF 로직이지만
        SELECT 절에서 content, embedding을 제외하여 I/O를 절감한다.
        vector와 text(FTS+trigram) 검색을 별도 커넥션에서 병렬 실행한다.
        """
        if not self._pool:
            raise RuntimeError("VectorStore not connected")

        allowed_levels = self._allowed_security_levels(max_security_level)
        candidate_limit = limit * HYBRID_CANDIDATE_MULTIPLIER

        vector_rows, fts_rows = await asyncio.gather(
            self._vector_search_task(
                embedding, candidate_limit, domain_codes, allowed_doc_ids,
                allowed_levels, metadata_only=True, tenant_id=tenant_id,
                session_id=session_id,
            ),
            self._text_search_combined(
                text_query, candidate_limit, domain_codes,
                allowed_doc_ids, allowed_levels, metadata_only=True, tenant_id=tenant_id,
                session_id=session_id,
            ),
        )

        return self._rrf_merge(
            vector_rows, fts_rows, limit, 0.5,
            row_converter=self._row_to_metadata_dict,
        )

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

    async def _vector_search_task(
        self,
        embedding: List[float],
        candidate_limit: int,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        allowed_levels: Optional[List[str]] = None,
        metadata_only: bool = False,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list:
        """벡터 검색을 독립 커넥션에서 실행한다 (병렬용)."""
        vq, vp = self._build_vector_query(
            embedding, candidate_limit, domain_codes,
            allowed_doc_ids, allowed_levels, metadata_only=metadata_only,
            tenant_id=tenant_id, session_id=session_id,
        )
        async with self._pool.acquire() as conn:
            return await conn.fetch(vq, *vp)

    async def _text_search_combined(
        self,
        text_query: str,
        candidate_limit: int,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        allowed_levels: Optional[List[str]] = None,
        metadata_only: bool = False,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list:
        """FTS + trigram fallback을 독립 커넥션에서 순차 실행한다 (병렬용).

        trigram은 FTS 결과 수에 의존하므로 하나의 커넥션 내 순차 처리.
        """
        async with self._pool.acquire() as conn:
            try:
                fts_rows = await self._fulltext_search(
                    conn, text_query, candidate_limit, domain_codes,
                    allowed_doc_ids, allowed_levels, metadata_only=metadata_only,
                    tenant_id=tenant_id, session_id=session_id,
                )
            except Exception as e:
                logger.warning("Full-text search failed: %s", e)
                fts_rows = []

            if len(fts_rows) < TRIGRAM_FALLBACK_THRESHOLD:
                try:
                    trgm_rows = await self._trigram_search(
                        conn, text_query, candidate_limit, domain_codes,
                        allowed_doc_ids, allowed_levels, metadata_only=metadata_only,
                        tenant_id=tenant_id, session_id=session_id,
                    )
                    if trgm_rows:
                        seen_ids = {str(r["id"]) for r in fts_rows}
                        for row in trgm_rows:
                            if str(row["id"]) not in seen_ids:
                                fts_rows.append(row)
                                seen_ids.add(str(row["id"]))
                except Exception as e:
                    logger.warning("Trigram search failed: %s", e)

            return fts_rows

    def _build_vector_query(
        self,
        embedding: List[float],
        limit: int,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        allowed_levels: Optional[List[str]] = None,
        metadata_only: bool = False,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Tuple[str, list]:
        conditions = ["c.embedding IS NOT NULL", f"c.domain_code <> '{UNPLACED_DOMAIN}'"]
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

        if tenant_id:
            conditions.append(f"c.tenant_id = ${param_idx}::text")
            params.append(tenant_id)
            param_idx += 1

        if session_id:
            # 세션 스코프 격리(Step26): 세션 업로드 문서는 documents.metadata에
            # session_id로 태깅됨. additive 필터 — session_id 없으면 적용 안 함.
            conditions.append(f"d.metadata->>'session_id' = ${param_idx}::text")
            params.append(session_id)
            param_idx += 1

        where_clause = " AND ".join(conditions)
        columns = self._select_columns(metadata_only, "1 - (c.embedding <=> $1::vector)")
        query = f"""
            SELECT {columns}
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
        metadata_only: bool = False,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list:
        tsquery = self._sanitize_tsquery(text_query)
        if not tsquery:
            return []

        conditions = [
            "c.search_vector @@ to_tsquery('simple', $1)",
            f"c.domain_code <> '{UNPLACED_DOMAIN}'",
        ]
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

        if tenant_id:
            conditions.append(f"c.tenant_id = ${param_idx}::text")
            params.append(tenant_id)
            param_idx += 1

        if session_id:
            conditions.append(f"d.metadata->>'session_id' = ${param_idx}::text")
            params.append(session_id)
            param_idx += 1

        where_clause = " AND ".join(conditions)
        score_expr = "ts_rank(c.search_vector, to_tsquery('simple', $1))"
        columns = self._select_columns(metadata_only, score_expr)
        query = f"""
            SELECT {columns}
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
        metadata_only: bool = False,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list:
        terms = [t for t in text_query.split() if len(t) >= TRIGRAM_MIN_TERM_LEN]
        if not terms:
            return []

        search_text = " ".join(terms[:TRIGRAM_MAX_TERMS])
        conditions = [
            f"similarity(c.content, $1::text) > {TRIGRAM_MIN_SIMILARITY}",
            f"c.domain_code <> '{UNPLACED_DOMAIN}'",
        ]
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

        if tenant_id:
            conditions.append(f"c.tenant_id = ${param_idx}::text")
            params.append(tenant_id)
            param_idx += 1

        if session_id:
            conditions.append(f"d.metadata->>'session_id' = ${param_idx}::text")
            params.append(session_id)
            param_idx += 1

        where_clause = " AND ".join(conditions)
        columns = self._select_columns(metadata_only, "similarity(c.content, $1::text)")
        query = f"""
            SELECT {columns}
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {where_clause}
            ORDER BY score DESC
            LIMIT $2
        """
        return await conn.fetch(query, *params)

    def _rrf_merge(
        self, vector_rows: list, fts_rows: list, limit: int,
        vector_weight: float,
        row_converter: Callable | None = None,
    ) -> List[dict]:
        converter = row_converter or self._row_to_dict
        chunk_data: dict[str, dict] = {}
        rrf_scores: dict[str, float] = {}
        fts_weight = 1.0 - vector_weight

        for rank, row in enumerate(vector_rows):
            chunk_id = str(row["id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + (
                vector_weight / (RRF_K + rank + 1)
            )
            if chunk_id not in chunk_data:
                chunk_data[chunk_id] = converter(row)

        for rank, row in enumerate(fts_rows):
            chunk_id = str(row["id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + (
                fts_weight / (RRF_K + rank + 1)
            )
            if chunk_id not in chunk_data:
                chunk_data[chunk_id] = converter(row)

        sorted_ids = sorted(rrf_scores, key=lambda cid: rrf_scores[cid], reverse=True)

        results = []
        for chunk_id in sorted_ids[:limit]:
            data = {**chunk_data[chunk_id], "score": rrf_scores[chunk_id]}
            results.append(data)
        return results

    @staticmethod
    def _parse_chunk_metadata(row) -> dict:
        """chunk.metadata(JSONB) 안전 파싱 — 섹션 계층(AST-lite) 등."""
        raw = row.get("metadata")
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            import json as _json
            return _json.loads(raw)
        except (ValueError, TypeError):
            return {}

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
            "metadata": VectorSearchMixin._parse_chunk_metadata(row),
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
                "metadata": VectorSearchMixin._parse_chunk_metadata(row),
            }
            for row in rows
        ]

    @staticmethod
    def _select_columns(metadata_only: bool, score_expr: str) -> str:
        """검색 쿼리의 SELECT 절 생성. metadata_only=True이면 content 제외."""
        if metadata_only:
            return f"""c.id, c.document_id, c.chunk_index,
                   SUBSTRING(c.content, 1, 250) AS summary,
                   c.domain_code, c.security_level,
                   {score_expr} AS score,
                   d.file_name, d.title"""
        return f"""c.id, c.document_id, c.content, c.chunk_index,
                   c.metadata,
                   {score_expr} AS score,
                   d.file_name, d.title"""

    @staticmethod
    def _row_to_metadata_dict(row) -> dict:
        """메타데이터 전용 행 변환 (content 없음)."""
        return {
            "doc_id": str(row["document_id"]),
            "chunk_id": str(row["id"]),
            "title": row.get("title", ""),
            "summary": row.get("summary", ""),
            "domain_code": row.get("domain_code", ""),
            "score": float(row["score"]),
            "security_level": row.get("security_level", ""),
            "file_name": row.get("file_name", ""),
        }

    @staticmethod
    def _allowed_security_levels(max_level: Optional[str]) -> Optional[List[str]]:
        if not max_level:
            return None
        max_rank = SECURITY_HIERARCHY.get(max_level)
        if max_rank is None:
            logger.warning("Unrecognized security level '%s', denying all", max_level)
            return []
        return [level for level, rank in SECURITY_HIERARCHY.items() if rank <= max_rank]
