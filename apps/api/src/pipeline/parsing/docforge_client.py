"""DocForge API 클라이언트.

DocForge 파싱 서비스에 파일을 전송하고 마크다운 결과를 수신한다.
httpx.AsyncClient로 multipart/form-data 전송.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from src.observability.logging import get_logger

logger = get_logger(__name__)


class ParseError(Exception):
    """DocForge 호출 실패 예외."""


@dataclass(frozen=True)
class DocForgeResult:
    """DocForge API 응답을 ai-platform 내부 형태로 변환한 결과."""

    markdown: str
    metadata: dict
    stats: dict


class DocForgeClient:
    """DocForge 파싱 서비스 클라이언트.

    Parameters
    ----------
    base_url:
        DocForge 서비스 기본 URL (예: http://localhost:5001).
    timeout_sec:
        HTTP 요청 타임아웃 (초).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:5001",
        timeout_sec: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    async def parse(
        self,
        file_bytes: bytes,
        file_name: str,
        mime_type: str,
    ) -> DocForgeResult:
        """파일을 DocForge에 전송하여 파싱 결과를 받는다.

        Parameters
        ----------
        file_bytes:
            파일 바이너리 데이터.
        file_name:
            원본 파일명.
        mime_type:
            MIME type (예: application/pdf, text/csv).

        Returns
        -------
        DocForgeResult:
            마크다운 + 메타데이터 + ��계.

        Raises
        ------
        ParseError:
            DocForge ��출 실패 (타임아웃, 네트워크, 서버 에러).
        """
        url = f"{self._base_url}/v1/parse/sync"
        t0 = time.time()

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    url,
                    files={"file": (file_name, file_bytes, mime_type)},
                )

            latency_ms = (time.time() - t0) * 1000

            if resp.status_code != 200:
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                error_msg = body.get("error", {}).get("message", resp.text[:200])
                logger.error(
                    "docforge_parse_failed",
                    status=resp.status_code,
                    file_name=file_name,
                    mime_type=mime_type,
                    error=error_msg,
                    latency_ms=round(latency_ms, 1),
                )
                raise ParseError(
                    f"DocForge returned {resp.status_code}: {error_msg}"
                )

            body = resp.json()
            if not body.get("success"):
                error_msg = body.get("error", {}).get("message", "unknown error")
                raise ParseError(f"DocForge parse failed: {error_msg}")

            data = body["data"]
            markdown = data.get("markdown", "")
            metadata = data.get("metadata", {})
            stats = data.get("stats", {})

            # confidence 경고
            confidence = metadata.get("confidence")
            if confidence is not None and confidence < 0.6:
                logger.warning(
                    "docforge_low_confidence",
                    file_name=file_name,
                    confidence=confidence,
                    latency_ms=round(latency_ms, 1),
                )

            logger.info(
                "docforge_parse_ok",
                file_name=file_name,
                mime_type=mime_type,
                markdown_len=len(markdown),
                latency_ms=round(latency_ms, 1),
            )

            return DocForgeResult(
                markdown=markdown,
                metadata=metadata,
                stats=stats,
            )

        except httpx.TimeoutException as exc:
            latency_ms = (time.time() - t0) * 1000
            logger.error(
                "docforge_timeout",
                file_name=file_name,
                mime_type=mime_type,
                timeout_sec=self._timeout,
                latency_ms=round(latency_ms, 1),
            )
            raise ParseError(
                f"DocForge timeout after {self._timeout}s: {exc}"
            ) from exc

        except httpx.ConnectError as exc:
            logger.error(
                "docforge_connect_error",
                file_name=file_name,
                url=self._base_url,
                error=str(exc),
            )
            raise ParseError(
                f"DocForge connection failed ({self._base_url}): {exc}"
            ) from exc

        except httpx.HTTPError as exc:
            logger.error(
                "docforge_http_error",
                file_name=file_name,
                error=str(exc),
            )
            raise ParseError(f"DocForge HTTP error: {exc}") from exc

    async def health_check(self) -> bool:
        """DocForge 서비스 가용성 확인.

        Returns
        -------
        bool:
            True이면 서비스 정상.
        """
        url = f"{self._base_url}/v1/health"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                body = resp.json()
                return body.get("success", False)
            return False
        except httpx.HTTPError:
            return False
