"""Webhook 수신 라우터 테스트."""

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.gateway.webhook_router import _verify_signature, receive_kms_webhook


class TestVerifySignature:
    """HMAC-SHA256 서명 검증 테스트."""

    def test_valid_signature(self):
        secret = "test_secret_key"
        body = b'{"event": "document.created", "data": {"documentId": "123"}}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert _verify_signature(body, f"sha256={sig}", secret) is True

    def test_invalid_signature(self):
        body = b'{"event": "document.created"}'
        assert _verify_signature(body, "sha256=invalid", "secret") is False

    def test_missing_signature(self):
        body = b'{"event": "document.created"}'
        assert _verify_signature(body, None, "secret") is False

    def test_empty_signature(self):
        body = b'{"event": "document.created"}'
        assert _verify_signature(body, "", "secret") is False

    def test_tampered_body(self):
        secret = "test_secret_key"
        body = b'{"event": "document.created"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        tampered = b'{"event": "document.deleted"}'
        assert _verify_signature(tampered, f"sha256={sig}", secret) is False


def _signed_request(payload: dict, secret: str, job_queue):
    """서명된 KMS webhook 요청을 흉내내는 경량 Request 스텁."""
    body = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Webhook-Signature": f"sha256={sig}"}
    app = SimpleNamespace(state=SimpleNamespace(job_queue=job_queue))
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda k, d=None: headers.get(k, d)),
        body=AsyncMock(return_value=body),
        app=app,
    )


class TestEventRouting:
    """이벤트별 kms_sync 큐 enqueue 라우팅 테스트."""

    @pytest.mark.asyncio
    async def test_document_parsed_enqueues_content_sync(self):
        """document.parsed(신규) 는 content_sync 액션으로 enqueue 한다 (레이어 분리)."""
        secret = "s"
        job_queue = AsyncMock()
        payload = {
            "event": "document.parsed",
            "data": {"documentId": "doc-9", "fileName": "약관.pdf"},
        }
        with patch("src.gateway.webhook_router.settings") as st:
            st.kms_webhook_secret = secret
            resp = await receive_kms_webhook(_signed_request(payload, secret, job_queue))

        assert resp["status"] == "accepted"
        job_queue.enqueue.assert_awaited_once()
        kwargs = job_queue.enqueue.call_args.kwargs
        assert kwargs["queue_name"] == "kms_sync"
        assert kwargs["payload"]["action"] == "content_sync"
        assert kwargs["payload"]["document_id"] == "doc-9"

    @pytest.mark.asyncio
    async def test_file_uploaded_still_enqueues_sync(self):
        """기존 document.file_uploaded 경로는 그대로 sync 액션으로 유지된다 (병행)."""
        secret = "s"
        job_queue = AsyncMock()
        payload = {
            "event": "document.file_uploaded",
            "data": {"documentId": "doc-1"},
        }
        with patch("src.gateway.webhook_router.settings") as st:
            st.kms_webhook_secret = secret
            await receive_kms_webhook(_signed_request(payload, secret, job_queue))

        kwargs = job_queue.enqueue.call_args.kwargs
        assert kwargs["payload"]["action"] == "sync"
