"""content-mode 인제스트 테스트 (레이어 분리 · 핸드오프 계약 `document.parsed`).

계약(docs/handoff-contract-parse-index.md): KMS+docforge 가 파싱을 소유하고 완료 후
`document.parsed` 를 발행한다. ai-platform 은 파일 다운로드·파싱을 하지 않고 KMS 에서
마크다운을 pull(GET /processing/:id/content)하여 청킹·임베딩만 한다. 마크다운 콜백
(_post_parse_result)도 하지 않는다(KMS 가 마크다운 소유). 기존 file_uploaded 경로는 병행 유지.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.kms_sync import KmsSyncService


def _make_sync(raw_text: str = "# 제1조 보장내용\n\n본문입니다."):
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
        "fileName": "약관.pdf", "title": "약관",
        "securityLevel": "PUBLIC", "status": "ACTIVE",
    })
    svc._fetch_content = AsyncMock(return_value={
        "rawText": raw_text, "parseStatus": "PARSED",
    })
    svc._get_existing = AsyncMock(return_value=None)
    svc._download_file = AsyncMock(return_value=b"%PDF-should-not-be-called")
    svc._delete_stale_by_external_id = AsyncMock(return_value=0)
    svc._set_external_id = AsyncMock()
    svc._post_parse_result = AsyncMock()

    @asynccontextmanager
    async def _no_lock(document_id):
        yield

    svc._doc_lock = _no_lock
    return svc, pipeline


@pytest.mark.asyncio
async def test_content_mode_ingests_pulled_markdown():
    """content-mode 는 KMS 에서 pull 한 rawText 를 content= 로 인제스트한다."""
    svc, pipeline = _make_sync(raw_text="# 제1조\n\n보장내용입니다.")
    result = await svc.sync_content(
        "kms-1",
        data={"fileName": "약관.pdf", "securityLevel": "PUBLIC", "domainCode": ""},
        event="document.parsed",
    )

    pipeline.ingest_text.assert_awaited_once()
    kwargs = pipeline.ingest_text.call_args.kwargs
    # content= 로 전달 (file_bytes/mime_type 없음 → 파싱 스킵)
    assert kwargs["content"] == "# 제1조\n\n보장내용입니다."
    assert "file_bytes" not in kwargs or kwargs.get("file_bytes") is None
    assert "mime_type" not in kwargs or kwargs.get("mime_type") is None
    # external_id 로 KMS 문서 매핑
    assert kwargs["external_id"] == "kms-1"
    assert result.get("chunks") == 3


@pytest.mark.asyncio
async def test_content_mode_no_file_download():
    """content-mode 는 파일 다운로드·docforge 파싱을 하지 않는다 (레이어 분리)."""
    svc, pipeline = _make_sync()
    await svc.sync_content("kms-1", data={}, event="document.parsed")
    svc._download_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_mode_no_markdown_callback():
    """content-mode 는 마크다운 콜백을 하지 않는다 (KMS 가 마크다운 소유)."""
    svc, pipeline = _make_sync()
    await svc.sync_content("kms-1", data={}, event="document.parsed")
    svc._post_parse_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_mode_empty_rawtext_skips():
    """rawText 가 비어있으면 인제스트하지 않고 스킵한다 (아직 파싱 안 됨)."""
    svc, pipeline = _make_sync(raw_text="   ")
    result = await svc.sync_content("kms-1", data={}, event="document.parsed")
    assert result["status"] == "skipped"
    pipeline.ingest_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_mode_missing_document_skips():
    """KMS 에 문서가 없으면 스킵한다."""
    svc, pipeline = _make_sync()
    svc._fetch_document_meta = AsyncMock(return_value=None)
    result = await svc.sync_content("kms-1", data={}, event="document.parsed")
    assert result["status"] == "skipped"
    pipeline.ingest_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_content_mode_unplaced_when_no_domain():
    """배치 전(domainCode 부재)이면 holding 도메인(__unplaced__)으로 적재한다."""
    from src.domain.models import UNPLACED_DOMAIN

    svc, pipeline = _make_sync()
    await svc.sync_content("kms-1", data={}, event="document.parsed")
    kwargs = pipeline.ingest_text.call_args.kwargs
    assert kwargs["domain_code"] == UNPLACED_DOMAIN


@pytest.mark.asyncio
async def test_content_mode_uses_single_domain_code():
    """이벤트 페이로드의 domainCode 가 있으면 해당 도메인으로 적재한다."""
    svc, pipeline = _make_sync()
    await svc.sync_content(
        "kms-1", data={"domainCode": "자동차보험"}, event="document.parsed",
    )
    kwargs = pipeline.ingest_text.call_args.kwargs
    assert kwargs["domain_code"] == "자동차보험"
