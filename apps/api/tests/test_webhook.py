"""Webhook 수신 라우터 테스트."""

import hashlib
import hmac
import json

import pytest
from unittest.mock import AsyncMock, patch

from src.gateway.webhook_router import _verify_signature


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
