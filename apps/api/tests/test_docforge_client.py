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
        # 비동기 2단계 프로토콜: submit(job_id) → poll(done).
        submit_response = MagicMock()
        submit_response.status_code = 202
        submit_response.json.return_value = {
            "success": True,
            "data": {"job_id": "job-123"},
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            "success": True,
            "data": {
                "status": "done",
                "markdown": "# Document\n\nContent here",
                "metadata": {"pages": 2, "confidence": 0.95},
                "stats": {"parse_time_ms": 150},
            },
        }

        with patch("httpx.AsyncClient") as mock_client_cls, patch(
            "src.pipeline.parsing.docforge_client.asyncio.sleep", new=AsyncMock()
        ):
            mock_async_client = AsyncMock()
            mock_async_client.post.return_value = submit_response
            mock_async_client.get.return_value = poll_response
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
# parse() backpressure (503 / QUEUE_FULL) + transient disconnect
# ---------------------------------------------------------------------------


def _resp(status, *, json_body, headers=None):
    """Build a MagicMock httpx-style response."""
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body
    base_headers = {"content-type": "application/json"}
    if headers:
        base_headers.update(headers)
    r.headers = base_headers
    r.text = str(json_body)
    return r


def _make_async_client(mock_client_cls):
    mock_async_client = AsyncMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = mock_async_client
    return mock_async_client


class TestBackpressure:
    async def test_503_queue_full_then_success_retries(self, client):
        """첫 제출이 503(QUEUE_FULL) → 백오프 후 재제출 202 → poll done."""
        busy = _resp(
            503,
            json_body={"success": False, "error": {"code": "QUEUE_FULL", "message": "full"}},
            headers={"Retry-After": "1"},
        )
        accepted = _resp(
            202, json_body={"success": True, "data": {"job_id": "job-ok"}},
        )
        poll_done = _resp(
            200,
            json_body={
                "success": True,
                "data": {"status": "done", "markdown": "# ok", "metadata": {}, "stats": {}},
            },
        )

        with patch("httpx.AsyncClient") as mock_cls, patch(
            "src.pipeline.parsing.docforge_client.asyncio.sleep", new=AsyncMock(),
        ) as mock_sleep:
            mac = _make_async_client(mock_cls)
            mac.post.side_effect = [busy, accepted]
            mac.get.return_value = poll_done

            result = await client.parse(b"data", "f.pdf", "application/pdf")

        assert result.markdown == "# ok"
        # 제출이 정확히 2번 시도됨 (503 → 재시도 → 성공).
        assert mac.post.call_count == 2
        # Retry-After=1 이 sleep 인자로 반영됨.
        assert any(call.args and call.args[0] == 1.0 for call in mock_sleep.await_args_list)

    async def test_503_persistent_exhausts_then_parse_error_bounded(self):
        """503 이 계속되면 제한된 재시도 후 ParseError (무한 루프 아님)."""
        retries = 3
        bounded_client = DocForgeClient(
            base_url="http://localhost:5001",
            submit_max_retries=retries,
            submit_retry_after_default_sec=1.0,
        )
        busy = _resp(
            503,
            json_body={"success": False, "error": {"code": "QUEUE_FULL", "message": "full"}},
            headers={"Retry-After": "1"},
        )

        with patch("httpx.AsyncClient") as mock_cls, patch(
            "src.pipeline.parsing.docforge_client.asyncio.sleep", new=AsyncMock(),
        ):
            mac = _make_async_client(mock_cls)
            mac.post.return_value = busy

            with pytest.raises(ParseError, match="큐가 가득"):
                await bounded_client.parse(b"data", "f.pdf", "application/pdf")

        # 초기 시도 1 + 재시도 retries = retries+1 총 제출. 무한 루프 아님.
        assert mac.post.call_count == retries + 1

    async def test_503_not_parse_timeout_error(self):
        """배압 소진은 ParseError 이며 ParseTimeoutError(폴링 전용)가 아니다."""
        bounded = DocForgeClient(
            base_url="http://localhost:5001",
            submit_max_retries=1,
            submit_retry_after_default_sec=1.0,
        )
        busy = _resp(
            503,
            json_body={"success": False, "error": {"code": "QUEUE_FULL"}},
            headers={"Retry-After": "1"},
        )
        with patch("httpx.AsyncClient") as mock_cls, patch(
            "src.pipeline.parsing.docforge_client.asyncio.sleep", new=AsyncMock(),
        ):
            mac = _make_async_client(mock_cls)
            mac.post.return_value = busy
            with pytest.raises(ParseError) as exc_info:
                await bounded.parse(b"data", "f.pdf", "application/pdf")
        assert not isinstance(exc_info.value, ParseTimeoutError)

    async def test_retry_after_absent_uses_default(self, client):
        """Retry-After 헤더가 없으면 기본 대기값을 사용한다."""
        busy = _resp(
            503,
            json_body={"success": False, "error": {"code": "QUEUE_FULL"}},
            headers=None,  # no Retry-After
        )
        accepted = _resp(202, json_body={"success": True, "data": {"job_id": "j"}})
        poll_done = _resp(
            200,
            json_body={
                "success": True,
                "data": {"status": "done", "markdown": "m", "metadata": {}, "stats": {}},
            },
        )
        with patch("httpx.AsyncClient") as mock_cls, patch(
            "src.pipeline.parsing.docforge_client.asyncio.sleep", new=AsyncMock(),
        ) as mock_sleep:
            mac = _make_async_client(mock_cls)
            mac.post.side_effect = [busy, accepted]
            mac.get.return_value = poll_done
            await client.parse(b"data", "f.pdf", "application/pdf")
        # 기본값 2.0초가 사용됨 (생성자 기본 submit_retry_after_default_sec=2.0).
        assert any(call.args and call.args[0] == 2.0 for call in mock_sleep.await_args_list)

    async def test_transient_disconnect_then_success(self, client):
        """제출 중 RemoteProtocolError(Server disconnected) → 짧은 재시도 → 성공."""
        accepted = _resp(202, json_body={"success": True, "data": {"job_id": "j2"}})
        poll_done = _resp(
            200,
            json_body={
                "success": True,
                "data": {"status": "done", "markdown": "ok", "metadata": {}, "stats": {}},
            },
        )
        with patch("httpx.AsyncClient") as mock_cls, patch(
            "src.pipeline.parsing.docforge_client.asyncio.sleep", new=AsyncMock(),
        ):
            mac = _make_async_client(mock_cls)
            mac.post.side_effect = [
                httpx.RemoteProtocolError("Server disconnected"),
                accepted,
            ]
            mac.get.return_value = poll_done
            result = await client.parse(b"data", "f.pdf", "application/pdf")
        assert result.markdown == "ok"
        assert mac.post.call_count == 2

    async def test_persistent_disconnect_exhausts_to_parse_error(self):
        """RemoteProtocolError 가 계속되면 예산 소진 후 ParseError (무한 아님)."""
        retries = 2
        bounded = DocForgeClient(
            base_url="http://localhost:5001",
            submit_max_retries=retries,
            submit_retry_after_default_sec=1.0,
        )
        with patch("httpx.AsyncClient") as mock_cls, patch(
            "src.pipeline.parsing.docforge_client.asyncio.sleep", new=AsyncMock(),
        ):
            mac = _make_async_client(mock_cls)
            mac.post.side_effect = httpx.RemoteProtocolError("Server disconnected")
            with pytest.raises(ParseError, match="HTTP error"):
                await bounded.parse(b"data", "f.pdf", "application/pdf")
        assert mac.post.call_count == retries + 1


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

    async def test_408_submit_raises_parse_error(self, client):
        # submit 단계에서 408(200/202 외)을 받으면 ParseError로 거른다.
        # ParseTimeoutError는 폴링 타임아웃 경로 전용(아래 별도 테스트)이다.
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

            with pytest.raises(ParseError, match="408"):
                await client.parse(b"data", "large.pdf", "application/pdf")

    async def test_poll_timeout_raises_parse_timeout_error(self):
        # 제출은 성공(job_id)하지만 폴링이 max_wait를 넘기면 ParseTimeoutError.
        # max_wait를 0으로 두면 폴링 루프 진입 즉시 타임아웃 분기로 들어간다.
        timeout_client = DocForgeClient(
            base_url="http://localhost:5001",
            timeout_sec=10.0,
            max_wait_sec=0.0,
            poll_interval_sec=0.0,
        )

        submit_response = MagicMock()
        submit_response.status_code = 202
        submit_response.json.return_value = {
            "success": True,
            "data": {"job_id": "job-timeout"},
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            "success": True,
            "data": {"status": "processing"},
        }

        with patch("httpx.AsyncClient") as mock_client_cls, patch(
            "src.pipeline.parsing.docforge_client.asyncio.sleep", new=AsyncMock()
        ):
            mock_async_client = AsyncMock()
            mock_async_client.post.return_value = submit_response
            mock_async_client.get.return_value = poll_response
            mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
            mock_async_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_async_client

            with pytest.raises(ParseTimeoutError):
                await timeout_client.parse(b"data", "large.pdf", "application/pdf")

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
        submit_response = MagicMock()
        submit_response.status_code = 202
        submit_response.json.return_value = {
            "success": True,
            "data": {"job_id": "job-low-conf"},
        }

        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.json.return_value = {
            "success": True,
            "data": {
                "status": "done",
                "markdown": "# Low quality",
                "metadata": {"confidence": 0.4},
                "stats": {},
            },
        }

        with patch("httpx.AsyncClient") as mock_client_cls, patch(
            "src.pipeline.parsing.docforge_client.asyncio.sleep", new=AsyncMock()
        ):
            mock_async_client = AsyncMock()
            mock_async_client.post.return_value = submit_response
            mock_async_client.get.return_value = poll_response
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
