"""Tests for DocForgeClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.pipeline.parsing.docforge_client import (
    DocForgeClient,
    DocForgeResult,
    ParseError,
    ParseTimeoutError,
)


@pytest.fixture
def client():
    return DocForgeClient(base_url="http://localhost:5001", timeout_sec=10.0)


# ---------------------------------------------------------------------------
# DocForgeResult
# ---------------------------------------------------------------------------


class TestDocForgeResult:
    def test_frozen(self):
        result = DocForgeResult(markdown="# test", metadata={}, stats={})
        with pytest.raises(AttributeError):
            result.markdown = "changed"  # type: ignore[misc]

    def test_fields(self):
        result = DocForgeResult(
            markdown="# hello",
            metadata={"pages": 1},
            stats={"parse_time_ms": 50},
        )
        assert result.markdown == "# hello"
        assert result.metadata == {"pages": 1}
        assert result.stats == {"parse_time_ms": 50}


# ---------------------------------------------------------------------------
# parse() success
# ---------------------------------------------------------------------------


class TestParseSuccess:
    async def test_parse_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "markdown": "# Document\n\nContent here",
                "metadata": {"pages": 2, "confidence": 0.95},
                "stats": {"parse_time_ms": 150},
            },
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.post.return_value = mock_response
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            result = await client.parse(
                file_bytes=b"%PDF-1.4 content",
                file_name="test.pdf",
                mime_type="application/pdf",
            )

        assert isinstance(result, DocForgeResult)
        assert result.markdown == "# Document\n\nContent here"
        assert result.metadata["confidence"] == 0.95
        assert result.stats["parse_time_ms"] == 150


# ---------------------------------------------------------------------------
# parse() error cases
# ---------------------------------------------------------------------------


class TestParseErrors:
    async def test_timeout_raises_parse_error(self, client):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.post.side_effect = httpx.TimeoutException("timed out")
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            with pytest.raises(ParseError, match="timeout"):
                await client.parse(b"data", "test.pdf", "application/pdf")

    async def test_connect_error_raises_parse_error(self, client):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.post.side_effect = httpx.ConnectError("refused")
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            with pytest.raises(ParseError, match="connection failed"):
                await client.parse(b"data", "test.pdf", "application/pdf")

    async def test_server_error_raises_parse_error(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "success": False,
            "error": {"code": "PARSE_ERROR", "message": "OCR failed"},
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.post.return_value = mock_response
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            with pytest.raises(ParseError, match="500"):
                await client.parse(b"data", "test.pdf", "application/pdf")

    async def test_408_raises_parse_timeout_error(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 408
        mock_response.text = "Request Timeout"
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "success": False,
            "error": {"code": "REQUEST_TIMEOUT", "message": "parsing timed out"},
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.post.return_value = mock_response
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            with pytest.raises(ParseTimeoutError):
                await client.parse(b"data", "large.pdf", "application/pdf")

    async def test_network_error_raises_parse_error(self, client):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.post.side_effect = httpx.HTTPError("network error")
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            with pytest.raises(ParseError, match="HTTP error"):
                await client.parse(b"data", "test.pdf", "application/pdf")


# ---------------------------------------------------------------------------
# Low confidence warning
# ---------------------------------------------------------------------------


class TestLowConfidenceWarning:
    async def test_low_confidence_logs_warning(self, client, caplog):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "markdown": "# Low quality",
                "metadata": {"confidence": 0.4},
                "stats": {},
            },
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.post.return_value = mock_response
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            result = await client.parse(b"data", "test.pdf", "application/pdf")

        assert result.markdown == "# Low quality"
        assert result.metadata["confidence"] == 0.4


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


class TestHealthCheck:
    async def test_health_check_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "data": {"status": "ok"}}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.get.return_value = mock_response
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            assert await client.health_check() is True

    async def test_health_check_failure(self, client):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.get.side_effect = httpx.ConnectError("refused")
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            assert await client.health_check() is False

    async def test_health_check_non_200(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_async_client = AsyncMock()
            mock_async_client.get.return_value = mock_response
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            assert await client.health_check() is False
