"""DocForge API 클라이언트.

DocForge 파싱 서비스에 파일을 전송하고 마크다운 결과를 수신한다.
httpx.AsyncClient로 multipart/form-data 전송.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from src.observability.logging import get_logger

logger = get_logger(__name__)


class ParseError(Exception):
    """DocForge 호출 실패 예외."""


class ParseTimeoutError(ParseError):
    """DocForge 서버 측 파싱 타임아웃 (HTTP 408)."""


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
        internal_key: str = "",
        max_wait_sec: float = 3600.0,
        poll_interval_sec: float = 2.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # Per-HTTP-request timeout. With the async queue each request (submit /
        # poll) returns quickly, so this no longer bounds the whole parse.
        self._timeout = timeout_sec
        self._internal_key = internal_key
        # Total time to wait for an async parse job to finish before giving up.
        self._max_wait = max_wait_sec
        self._poll_interval = poll_interval_sec

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
            마크다운 + 메타데이터 + 통계.

        Raises
        ------
        ParseError:
            DocForge 호출 실패 (타임아웃, 네트워크, 서버 에러).
        """
        submit_url = f"{self._base_url}/v1/parse/async"
        t0 = time.time()

        try:
            headers = {}
            if self._internal_key:
                headers["X-Internal-Key"] = self._internal_key

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # 1) 제출 — job_id 즉시 수신 (연결 유지 없음)
                resp = await client.post(
                    submit_url,
                    files={"file": (file_name, file_bytes, mime_type)},
                    headers=headers,
                )
                if resp.status_code not in (200, 202):
                    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    error_info = body.get("error", {})
                    error_msg = error_info.get("message", resp.text[:200])
                    error_code = error_info.get("code", "")
                    logger.error(
                        "docforge_submit_failed",
                        status=resp.status_code,
                        error_code=error_code,
                        file_name=file_name,
                        mime_type=mime_type,
                        error=error_msg,
                    )
                    raise ParseError(f"DocForge submit returned {resp.status_code}: {error_msg}")

                body = resp.json()
                if not body.get("success"):
                    error_msg = body.get("error", {}).get("message", "unknown error")
                    raise ParseError(f"DocForge submit failed: {error_msg}")
                job_id = body["data"]["job_id"]

                # 2) 폴링 — 짧은 요청 반복, 완료/실패까지
                poll_url = f"{self._base_url}/v1/parse/async/{job_id}"
                while True:
                    if time.time() - t0 > self._max_wait:
                        raise ParseTimeoutError(
                            f"파일이 너무 크거나 복잡하여 파싱 시간이 초과되었습니다 "
                            f"({self._max_wait:.0f}s): {file_name}"
                        )
                    await asyncio.sleep(self._poll_interval)
                    presp = await client.get(poll_url, headers=headers)
                    if presp.status_code == 404:
                        raise ParseError(f"DocForge 작업이 만료/소실되었습니다: {job_id}")
                    if presp.status_code != 200:
                        raise ParseError(f"DocForge poll returned {presp.status_code}")
                    pbody = presp.json()
                    data = pbody.get("data", {})
                    status = data.get("status")
                    if status in ("queued", "processing"):
                        continue
                    if status == "failed":
                        emsg = pbody.get("error", {}).get("message", "파싱 실패")
                        logger.error(
                            "docforge_parse_failed",
                            file_name=file_name, mime_type=mime_type, error=emsg,
                        )
                        raise ParseError(f"DocForge parse failed: {emsg}")
                    if status != "done":
                        raise ParseError(f"DocForge 알 수 없는 상태: {status}")
                    break

            latency_ms = (time.time() - t0) * 1000
            markdown = data.get("markdown", "")
            metadata = data.get("metadata", {})
            stats = data.get("stats", {})

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
            headers = {}
            if self._internal_key:
                headers["X-Internal-Key"] = self._internal_key

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                body = resp.json()
                return body.get("success", False)
            return False
        except httpx.HTTPError:
            return False
