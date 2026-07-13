"""kms_sync 부분 상태 복구 + ingest NUL 소독 회귀 테스트.

실사고(2026-07-13): OCR 파싱본의 NUL(0x00) 바이트로 청크 insert 가 실패해
문서 행만 남았고(청크 0), 재시도는 "이미 적재됨"으로 오판·스킵해 영구 빈손.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.pipeline.ingest import IngestPipeline
from src.services.kms_sync import KmsSyncService


# --- ingest: NUL 바이트 소독 ---


def _make_pipeline():
    store = AsyncMock()
    store.insert_document = AsyncMock(return_value="doc-1")
    store.delete_document_chunks = AsyncMock(return_value=0)
    store.insert_chunks = AsyncMock(return_value=["c1"])
    embedder = AsyncMock()

    async def embed(texts):
        return [[0.1] * 4 for _ in texts]

    embedder.embed_batch = AsyncMock(side_effect=embed)
    settings = MagicMock()
    settings.chunk_size = 1000
    settings.chunk_overlap = 200
    settings.embed_batch_size = 64
    settings.embed_max_batch_size = 128
    settings.embed_concurrency = 2
    return IngestPipeline(store, embedder, settings), store


@pytest.mark.asyncio
async def test_ingest_strips_nul_bytes():
    """파싱본의 0x00 이 청크/임베딩 입력에 남으면 안 된다 (PG TEXT 제약)."""
    pipeline, store = _make_pipeline()
    result = await pipeline.ingest_text(
        title="약관", content="제1조\x00 보장내용\x00입니다", domain_code="보험",
    )
    assert result["status"] == "success"
    chunks = store.insert_chunks.call_args[0][1]
    assert all("\x00" not in c["content"] for c in chunks)


# --- kms_sync: 청크 0개 문서는 "적재됨" 이 아니다 ---


def _make_sync(chunk_count: int):
    settings = MagicMock()
    settings.kms_api_url = "http://kms-api:3000/api"
    settings.kms_internal_key = "k"
    settings.docforge_url = ""
    settings.docforge_internal_key = ""
    settings.default_tenant_id = "default"

    store = AsyncMock()
    pipeline = AsyncMock()
    pipeline.ingest_text = AsyncMock(return_value={
        "document_id": "aip-1", "status": "success", "chunks": 3, "markdown": "md",
    })

    svc = KmsSyncService(settings, store, pipeline)
    svc._fetch_document_meta = AsyncMock(return_value={
        "fileName": "약관.pdf", "title": "약관.pdf",
        "mimeType": "application/pdf", "securityLevel": "PUBLIC",
        "status": "DRAFT",
    })
    svc._get_existing = AsyncMock(return_value={
        "id": "aip-1", "domain_code": "보험",
        "security_level": "PUBLIC", "chunk_count": chunk_count,
    })
    svc._download_file = AsyncMock(return_value=b"%PDF-fake")
    svc._delete_by_external_id = AsyncMock(return_value=1)
    svc._set_external_id = AsyncMock()
    svc._post_parse_result = AsyncMock()

    @asynccontextmanager
    async def _no_lock(document_id):
        yield

    svc._doc_lock = _no_lock
    return svc, pipeline


@pytest.mark.asyncio
async def test_zero_chunk_existing_reingests():
    """문서 행만 있고 청크 0개(부분 상태)면 스킵하지 않고 재적재한다."""
    svc, pipeline = _make_sync(chunk_count=0)
    result = await svc.sync_document(
        "kms-1", data={"domainCodes": ["보험"]}, event="document.updated",
    )
    pipeline.ingest_text.assert_awaited_once()
    assert result.get("chunks") == 3


@pytest.mark.asyncio
async def test_chunked_existing_still_skips():
    """정상 적재(청크 존재) + 무변경이면 기존 멱등 스킵 유지."""
    svc, pipeline = _make_sync(chunk_count=10)
    result = await svc.sync_document(
        "kms-1", data={"domainCodes": ["보험"]}, event="document.updated",
    )
    pipeline.ingest_text.assert_not_awaited()
    assert result["status"] == "skipped"
