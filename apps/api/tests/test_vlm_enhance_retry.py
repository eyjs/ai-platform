"""vlm_enhance 재시도 시맨틱 테스트 (I/F 결함 Fix 1).

검증 계약:
  - transient(연결 오류/5xx) → VlmTransientError raise → QueueWorker 백오프 재시도
  - permanent(4xx / success:false 바디) → 즉시 FAILED 콜백 + 정상 반환(재시도 없음)
  - 재시도 여지가 있는 transient 실패 → FAILED 콜백을 보내지 않음(PROCESSING 유지)
  - 마지막 시도의 transient 실패 → FAILED 콜백 후 raise(잡 회계와 KMS 상태 일치)
  - QueueWorker._process_job 이 payload 에 job_attempts/job_max_attempts 주입
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.services.kms_sync import KmsSyncService, VlmTransientError

_PDF_META = {
    "fileName": "약관.pdf",
    "fileType": "pdf",
    "mimeType": "application/pdf",
}


def _make_service() -> KmsSyncService:
    settings = SimpleNamespace(
        kms_api_url="http://kms-api:4000/api",
        kms_internal_key="test-key",
        docforge_url="http://docforge:5051",
        docforge_internal_key="",
        default_tenant_id="default",
    )
    svc = KmsSyncService(settings, AsyncMock(), AsyncMock())  # type: ignore[arg-type]
    svc._fetch_document_meta = AsyncMock(return_value=dict(_PDF_META))  # type: ignore[method-assign]
    svc._download_file = AsyncMock(return_value=b"%PDF-1.4 stub")  # type: ignore[method-assign]
    svc._post_vlm_status = AsyncMock()  # type: ignore[method-assign]
    svc._post_parse_result = AsyncMock()  # type: ignore[method-assign]
    return svc


def _payload(attempts: int = 0, max_attempts: int = 3) -> dict:
    return {
        "document_id": "doc-1",
        "job_attempts": attempts,
        "job_max_attempts": max_attempts,
    }


def _vlm_statuses(svc: KmsSyncService) -> list[str]:
    return [call.args[1] for call in svc._post_vlm_status.call_args_list]


# ---------------------------------------------------------------------------
# enhance_document 시맨틱
# ---------------------------------------------------------------------------


class TestTransientRetrySemantics:
    @pytest.mark.asyncio
    async def test_transient_mid_attempt_raises_without_failed_callback(self):
        svc = _make_service()
        svc._docforge_parse_vlm = AsyncMock(  # type: ignore[method-assign]
            side_effect=VlmTransientError("docforge 5xx: 502"),
        )
        with pytest.raises(VlmTransientError):
            await svc.enhance_document("doc-1", _payload(attempts=0, max_attempts=3))
        # PROCESSING 만 보고 — FAILED 콜백 없음 (재시도 여지 있음)
        assert _vlm_statuses(svc) == ["PROCESSING"]

    @pytest.mark.asyncio
    async def test_transient_last_attempt_reports_failed_then_raises(self):
        svc = _make_service()
        svc._docforge_parse_vlm = AsyncMock(  # type: ignore[method-assign]
            side_effect=VlmTransientError("connect timeout"),
        )
        with pytest.raises(VlmTransientError):
            await svc.enhance_document("doc-1", _payload(attempts=2, max_attempts=3))
        assert _vlm_statuses(svc) == ["PROCESSING", "FAILED"]

    @pytest.mark.asyncio
    async def test_permanent_reports_failed_and_returns_normally(self):
        svc = _make_service()
        svc._docforge_parse_vlm = AsyncMock(return_value=None)  # type: ignore[method-assign]
        result = await svc.enhance_document("doc-1", _payload())
        assert result["status"] == "failed"
        assert _vlm_statuses(svc) == ["PROCESSING", "FAILED"]

    @pytest.mark.asyncio
    async def test_download_transient_propagates(self):
        svc = _make_service()
        svc._download_file = AsyncMock(  # type: ignore[method-assign]
            side_effect=VlmTransientError("kms file download: timeout"),
        )
        with pytest.raises(VlmTransientError):
            await svc.enhance_document("doc-1", _payload(attempts=0))
        assert _vlm_statuses(svc) == ["PROCESSING"]

    @pytest.mark.asyncio
    async def test_success_posts_done(self):
        svc = _make_service()
        svc._docforge_parse_vlm = AsyncMock(return_value="# 보강된 본문")  # type: ignore[method-assign]
        result = await svc.enhance_document("doc-1", _payload())
        assert result["status"] == "enhanced"
        svc._post_parse_result.assert_awaited_once()
        assert svc._post_parse_result.call_args.kwargs.get("vlm_status") == "DONE"


# ---------------------------------------------------------------------------
# _docforge_parse_vlm 오류 분류
# ---------------------------------------------------------------------------


def _svc_with_response(response: httpx.Response) -> KmsSyncService:
    svc = _make_service()

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            if isinstance(response, Exception):
                raise response
            return response

    import src.services.kms_sync as module

    svc._fake_client_cls = _FakeClient
    module_httpx = module.httpx
    svc._patch_target = (module_httpx, "AsyncClient", _FakeClient)
    return svc


class TestDocforgeErrorClassification:
    @pytest.mark.asyncio
    async def test_5xx_is_transient(self, monkeypatch):
        svc = _svc_with_response(httpx.Response(502, text="bad gateway"))
        monkeypatch.setattr(*svc._patch_target)
        with pytest.raises(VlmTransientError, match="5xx"):
            await svc._docforge_parse_vlm(b"pdf", "a.pdf", "application/pdf")

    @pytest.mark.asyncio
    async def test_4xx_is_permanent_none(self, monkeypatch):
        svc = _svc_with_response(httpx.Response(415, text="unsupported"))
        monkeypatch.setattr(*svc._patch_target)
        assert await svc._docforge_parse_vlm(b"pdf", "a.pdf", "application/pdf") is None

    @pytest.mark.asyncio
    async def test_success_false_is_permanent_none(self, monkeypatch):
        svc = _svc_with_response(
            httpx.Response(200, json={"success": False, "error": {"code": "PARSE_ERROR"}}),
        )
        monkeypatch.setattr(*svc._patch_target)
        assert await svc._docforge_parse_vlm(b"pdf", "a.pdf", "application/pdf") is None

    @pytest.mark.asyncio
    async def test_connect_error_is_transient(self, monkeypatch):
        svc = _svc_with_response(httpx.ConnectError("connection refused"))
        monkeypatch.setattr(*svc._patch_target)
        with pytest.raises(VlmTransientError):
            await svc._docforge_parse_vlm(b"pdf", "a.pdf", "application/pdf")

    @pytest.mark.asyncio
    async def test_ok_returns_markdown(self, monkeypatch):
        svc = _svc_with_response(
            httpx.Response(200, json={"success": True, "data": {"markdown": "# md"}}),
        )
        monkeypatch.setattr(*svc._patch_target)
        assert await svc._docforge_parse_vlm(b"pdf", "a.pdf", "application/pdf") == "# md"


# ---------------------------------------------------------------------------
# QueueWorker payload 주입
# ---------------------------------------------------------------------------


class TestQueueWorkerAttemptInjection:
    @pytest.mark.asyncio
    async def test_process_job_injects_attempt_info(self):
        from src.infrastructure.job_queue import QueueWorker

        seen: dict = {}

        async def handler(payload: dict) -> dict:
            seen.update(payload)
            return {"ok": True}

        queue = AsyncMock()
        worker = QueueWorker(queue=queue, queue_name="vlm_enhance", handler=handler)
        await worker._process_job(
            {"id": "job-1", "payload": {"document_id": "d1"}, "attempts": 2, "max_attempts": 3},
        )
        assert seen["job_attempts"] == 2
        assert seen["job_max_attempts"] == 3
        queue.complete.assert_awaited_once()
