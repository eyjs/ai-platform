"""FlowSNS HTTP Client 테스트."""

from __future__ import annotations

import pytest
import httpx

from src.tools.internal.flowsns.flowsns_client import FlowSNSClient, FlowSNSClientError


@pytest.fixture
def client():
    return FlowSNSClient(
        base_url="http://localhost:3001",
        api_key="fsk_test_key_1234567890abcdef",
        timeout=5.0,
    )


class TestFlowSNSClient:
    """FlowSNSClient 단위 테스트."""

    def test_init_strips_trailing_slash(self):
        c = FlowSNSClient(base_url="http://localhost:3001/", api_key="fsk_test")
        assert c._base_url == "http://localhost:3001"

    def test_init_stores_api_key(self, client: FlowSNSClient):
        assert client._api_key == "fsk_test_key_1234567890abcdef"

    def test_init_stores_timeout(self, client: FlowSNSClient):
        assert client._timeout == 5.0

    @pytest.mark.asyncio
    async def test_get_creates_client_lazily(self, client: FlowSNSClient):
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_no_error_when_not_opened(self, client: FlowSNSClient):
        await client.close()  # should not raise

    @pytest.mark.asyncio
    async def test_handle_response_404(self, client: FlowSNSClient):
        response = httpx.Response(
            404,
            json={"message": "Not found"},
            request=httpx.Request("GET", "http://localhost:3001/test"),
        )
        with pytest.raises(FlowSNSClientError) as exc_info:
            client._handle_response(response)
        assert exc_info.value.status_code == 404
        assert "Not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_handle_response_500(self, client: FlowSNSClient):
        response = httpx.Response(
            500,
            text="Internal Server Error",
            request=httpx.Request("GET", "http://localhost:3001/test"),
        )
        with pytest.raises(FlowSNSClientError) as exc_info:
            client._handle_response(response)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_handle_response_204(self, client: FlowSNSClient):
        response = httpx.Response(
            204,
            request=httpx.Request("DELETE", "http://localhost:3001/test"),
        )
        result = client._handle_response(response)
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_response_200_json(self, client: FlowSNSClient):
        response = httpx.Response(
            200,
            json={"id": "abc", "title": "Test"},
            request=httpx.Request("GET", "http://localhost:3001/test"),
        )
        result = client._handle_response(response)
        assert result == {"id": "abc", "title": "Test"}

    @pytest.mark.asyncio
    async def test_handle_response_error_with_error_field(self, client: FlowSNSClient):
        response = httpx.Response(
            401,
            json={"error": "Unauthorized", "statusCode": 401},
            request=httpx.Request("GET", "http://localhost:3001/test"),
        )
        with pytest.raises(FlowSNSClientError) as exc_info:
            client._handle_response(response)
        assert "Unauthorized" in exc_info.value.detail
