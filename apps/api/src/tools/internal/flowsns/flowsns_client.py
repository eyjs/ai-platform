"""FlowSNS HTTP Client.

FlowSNS NestJS API와 통신하는 공용 httpx 클라이언트.
모든 flowsns_* Tool이 이 클라이언트를 공유한다.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class FlowSNSClientError(Exception):
    """FlowSNS API 호출 실패."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"FlowSNS API error {status_code}: {detail}")


class FlowSNSClient:
    """FlowSNS API httpx 클라이언트.

    Parameters
    ----------
    base_url : str
        FlowSNS API 기본 URL (예: http://localhost:3001)
    api_key : str
        fsk_ 접두사 API Key (X-API-Key 헤더로 전송)
    timeout : float
        HTTP 요청 타임아웃 (초)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 15.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "X-API-Key": self._api_key,
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self._timeout),
            )
        return self._client

    async def close(self) -> None:
        """클라이언트 연결 정리."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET 요청."""
        client = await self._get_client()
        try:
            response = await client.get(path, params=params)
            return self._handle_response(response)
        except httpx.TimeoutException:
            logger.error("flowsns_timeout", path=path)
            raise FlowSNSClientError(504, f"FlowSNS API timeout: {path}")
        except httpx.ConnectError:
            logger.error("flowsns_connect_error", path=path)
            raise FlowSNSClientError(503, "FlowSNS API unreachable")

    async def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        """POST 요청."""
        client = await self._get_client()
        try:
            response = await client.post(path, json=json)
            return self._handle_response(response)
        except httpx.TimeoutException:
            logger.error("flowsns_timeout", path=path)
            raise FlowSNSClientError(504, f"FlowSNS API timeout: {path}")
        except httpx.ConnectError:
            logger.error("flowsns_connect_error", path=path)
            raise FlowSNSClientError(503, "FlowSNS API unreachable")

    async def patch(self, path: str, json: dict[str, Any] | None = None) -> Any:
        """PATCH 요청."""
        client = await self._get_client()
        try:
            response = await client.patch(path, json=json)
            return self._handle_response(response)
        except httpx.TimeoutException:
            logger.error("flowsns_timeout", path=path)
            raise FlowSNSClientError(504, f"FlowSNS API timeout: {path}")
        except httpx.ConnectError:
            logger.error("flowsns_connect_error", path=path)
            raise FlowSNSClientError(503, "FlowSNS API unreachable")

    def _handle_response(self, response: httpx.Response) -> Any:
        """응답 처리 및 에러 변환."""
        if response.status_code >= 400:
            detail = response.text
            try:
                body = response.json()
                detail = body.get("message", body.get("error", detail))
            except Exception:
                pass
            logger.warning(
                "flowsns_api_error status=%s detail=%s url=%s",
                response.status_code,
                detail,
                response.url,
            )
            raise FlowSNSClientError(response.status_code, str(detail))

        if response.status_code == 204:
            return None

        return response.json()
